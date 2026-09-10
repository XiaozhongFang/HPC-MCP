# HPC-MCP 架構

其他語言：[English](../ARCHITECTURE.md) | [简体中文](../zh-CN/ARCHITECTURE.md) | [日本語](../ja/ARCHITECTURE.md) | [한국어](../ko/ARCHITECTURE.md)

HPC-MCP 是一個 stdio MCP server。MCP 協定是唯一寫入 stdout 的資料；
診斷與稽核記錄走 stderr 或設定的日誌檔。

```text
MCP client
   |
   v
server.py（分派、執行期參數形狀、稽核、生命週期）
   |
   +--> tools/registry.py（對外工具 schema 與轉接器）
   |       |
   |       +--> security/*（純策略決策）
   |       +--> filesystem / shell / slurm services
   |       +--> cluster/topology.py（運算節點採集作業 + sinfo，帶快取）
   |
   +--> ssh/manager.py 與 ssh/sftp.py（固定 argv，行程有界）
               |
               v
          單一已設定的 HPC 主機
```

## 請求流程

1. `server.on_call_tool` 解析工具名，並拒絕非物件、缺失或未知的參數。
2. registry 轉接器驗證純量型別，只轉發通過型別檢查的值。
3. 任何會改動遠端狀態的指令執行前，先由服務層施加策略：
   `path_policy` 對遠端路徑做 canonical 化，`command_policy` 對登入節點指令分類，
   `slurm_policy` 驗證資源。
4. 服務層要求 SSH transport 執行固定 argv 或可信的、已加引號的內部包裝指令稿。
   transport 輸出按位元組上限串流截斷；無論成功、失敗或逾時，子行程都會被回收。
5. 結果以文字內容回傳。每次呼叫都產生一筆帶遞迴憑證脫敏的 ALLOW/DENY 稽核記錄。

## 隔離邊界

遠端 `root` 是專屬的每使用者目錄，且不能是 `/`。遠端路徑既做詞法檢查，也做遠端
`realpath` 檢查；建立類操作還會拒絕已存在的符號連結目標。傳輸工具則有獨立的本機
`local_root`（預設為行程工作目錄），拒絕符號連結組件與憑證類檔名，並在傳輸後強制大小上限。

登入節點指令是一份很小的允許清單。不接受任何 shell 運算子、執行檔路徑、巢狀
shell、跟隨符號連結的選項，也不接受直接查詢 Slurm 的指令。作業檢視只透過會驗證
目前行程工作階段 `tool_session` 項目的 Slurm 方法暴露。輸出路徑由數字 job ID 與
canonical 作業目錄推導，絕不來自不可信的中介資料。Slurm 會先寫入扁平化的
`%j.stdout.log`/`%j.stderr.log` 暫存檔（它在開啟輸出前無法建立中間目錄）；
接著由 server 建立 canonical 作業目錄，並把這兩個檔案連結到其下。

## 查詢成本邊界

共享帳號安全同樣意味著*根本不把其他使用者或海量資料載入 MCP 行程*：

- **僅查詢自有作業。** `status`/`queue`/提交時的活躍作業計數只執行
  `squeue -j <tracked ids>`。沒有追蹤作業時該呼叫不發動任何 `squeue`。
  他人的作業中介資料永遠不進入本行程。
- **有界檔案讀取。** `hpc.files.read` 只回傳一個切片，上限為
  `files.max_read_slice_bytes`（預設 256 KiB）；工具描述引導 agent 先用
  `hpc.files.search`，而不是一路翻頁到 EOF。
- **有界遞迴列舉。** `hpc.files.list` 每次遠端呼叫只列舉一層，用 `head`
  截斷遠端輸出（透過 `bash -o pipefail` 偵測 SIGPIPE），並用 `depth:N`
  cursor 續讀。
- **有界搜尋。** `hpc.files.search`（單檔：先 `stat`，超限即拒；目錄樹：
  `grep -rn` + 排除目錄 + 每檔 `-m` + 全域 `head` + `timeout`）的每一項預算
  都在服務端鉗制；pattern 作為已加引號的 grep 參數傳遞，控制字元被拒絕。
- **高階工具。** `hpc.jobs.diagnose`（狀態 + 記帳 + 有界尾部 + 錯誤簽章）與
  `hpc.project.snapshot`（有界目錄樹 + git 摘要 + 已追蹤作業）把多次低階呼叫
  合併為一次，且每次查詢都有界。
- **拓撲探索。** `hpc.cluster.topo` 把一份 `sinfo` 摘要（本就在允許清單內，且限定
  `-p <allowed partitions>`，未授權分割區永遠不會進入回應）與一個**固定的、
  由服務端產生的**採集指令稿結合起來，該指令稿以 1 核作業執行在運算節點上。任何工具
  參數都不會被插值進指令稿，指令稿只讀節點本機檔案——從不查詢
  `squeue`/`sacct`/`scontrol`，因此共享帳號隔離規則在運算側同樣成立。結果快取於
  行程內，並以 JSON 檔案形式存放於 `$ROOT/.hpc-mcp/topo/`，有效期
  `topology.cache_ttl_seconds`（預設 24 h），從而避免排隊中的採集作業被逐次重複
  提交；超過 `topology.wait_seconds` 仍在等待的採集作業會回傳 `status: "pending"`
  並被重用（最終可能被取消），而不是被重複提交。
- **查詢快取。** 冪等的唯讀工具按 `tool + 正規化參數` 在
  `cache_ttl_seconds`（預設 2 s，0 停用）內去重；任何寫操作會使整個快取失效。

## 已知殘餘風險（三層）

| 層 | 強制機制 | 防護目標 |
|---|---|---|
| 1. MCP policy | 路徑沙箱、指令允許清單、Slurm 資源策略、作業歸屬、查詢預算、稽核 | 行為異常的 agent |
| 2. Slurm | 分割區允許清單、資源／並行上限、唯一 `sbatch` 入口 | 運算資源濫用 |
| 3. OS／叢集 | 獨立 Unix UID、Slurm 作業隔離 + filesystem ACL、容器沙箱，或特權遠端輔助服務 | *惡意程式碼*隔離 |

遠端主機是共享 Unix 帳號，因此擁有同等帳號權限的行程可以在 canonical 化與最終指令
之間對路徑發動競態。Server 盡力縮小這個視窗並拒絕符號連結目標，但要完全消除競態，
需要基於 `openat(2)`/`O_NOFOLLOW` 的特權遠端輔助服務，或為每個使用者分配獨立 Unix
帳號。同樣，JSON 追蹤檔是邏輯上的工作階段邊界，而非作業系統層級存取控制邊界：同等
Unix 帳號可以讀取或修改它。

關鍵在於，`hpc.slurm.submit` 賦予了 agent 以該 Unix UID 在運算節點上執行程式的
能力。Layer 1-2 無法約束隨後讀取該 UID 可讀一切的惡意程式碼。不要依賴對「明顯惡意」
的 Julia/Python 做正規表示式／策略過濾來充當 OS 層級隔離機制；當同帳號下的惡意程式碼
在威脅模型內時，請部署 Layer 3（獨立 UID、作業隔離 + ACL，或容器／沙箱）。

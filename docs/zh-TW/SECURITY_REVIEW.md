# 安全審查（2026-09-04）

其他語言：[English](../SECURITY_REVIEW.md) | [简体中文](../zh-CN/SECURITY_REVIEW.md) | [日本語](../ja/SECURITY_REVIEW.md) | [한국어](../ko/SECURITY_REVIEW.md)

## 發現與修正

| 嚴重度 | 領域 | 發現 | 修正 |
| --- | --- | --- | --- |
| Critical | 檔案傳輸 | 上傳／下載允許任意本機路徑，包括符號連結目標與憑證檔案。 | `local_root`、路徑組件檢查、憑證檔名拒絕清單、批次傳輸控制字元拒絕，以及下載後大小上限。 |
| Critical | Safe shell | 詞法上位於 root 內的符號連結可讓 `cat`/`grep` 讀到遠端沙箱之外；執行檔路徑與 follow 類選項又擴大了指令面。 | 對運算元做遠端 canonical 檢查、只允許執行檔 basename 的策略，以及拒絕 `-L/-H/-follow`。 |
| High | 作業追蹤 | 儲存的 `job_dir` 與跨工作階段項目被信任，導致可存取另一工作階段作業的 output/accounting/cancel。 | 工作階段綁定的 schema 驗證、由數字 job ID 推導路徑；canonical 中介資料目錄與加鎖的原子寫入。 |
| High | 資源耗盡 | SSH/SFTP 使用無界的 `communicate()` 緩衝，並接受超大指令／環境變數負載。 | 串流位元組上限、子行程回收、有界的 argv/environment/指令稿大小，以及經過驗證的 timeouts。 |
| High | 設定 | 注入的 `environ` 被忽略；畸形的巢狀 YAML 與負值上限會以執行期錯誤或非法策略狀態洩漏出來。 | 決定性的環境來源、帶型別的 section/limit 驗證、canonical roots，以及僅允許安全的 host-key 模式。 |
| Medium | 稽核 | 僅用正規表示式的脫敏漏掉了字典鍵中的祕密（如 `{"token": "..."}`）與控制字元。 | 遞迴的、按欄位名感知的脫敏，控制字元轉義與截斷。 |
| Medium | 並行 | 並行的 submit 呼叫可能同時通過活躍作業檢查。 | 非同步提交鎖與原子中介資料鎖目錄。 |
| Low | 可維護性 | 未使用的 helper 與 import 模糊了安全邊界。 | 刪除死程式碼，並記錄 policy/service/transport 的職責歸屬。 |

## 驗證

回歸測試涵蓋路徑穿越、符號連結逃逸、指令注入、login/compute 分離、Slurm 上限、
作業歸屬、設定與檔案服務。執行：

```bash
python -m compileall -q src
python -m pytest -q
git diff --check
```

靜態檢查在可用時還應加入 Bandit/Ruff，並在部署環境中執行 `python -m pip check`。

## 維運要求

盡量使用 `StrictHostKeyChecking=yes` 並預先填充 `known_hosts`。保持
`HPC_MCP_ROOT` 僅對目標使用者可見，並把 `HPC_MCP_LOCAL_ROOT` 設為傳輸所需的
最小本機專案目錄。新的行程工作階段無法管理較早工作階段登記的作業；這是應用層邊界。
同等 Unix 帳號仍可竄改 JSON 追蹤檔或對遠端路徑發動競態，因此敵對的同帳號部署
需要獨立 Unix 帳號或特權遠端輔助服務。

---

# 安全 + 效率審查（v0.2，2026-09）

## 範圍

本輪把 server 從「阻止 agent 亂來」升級為「幫助 agent 以最少的遠端 I/O 獲取最大
價值」，同時不削弱任何既有邊界。以下所有發現均在本輪修正，並由回歸測試涵蓋。

## 發現與修正

| 嚴重度 | 領域 | 發現 | 修正 |
| --- | --- | --- | --- |
| Critical | 共享帳號 Slurm | `_queue_states()` / `queue()` 執行全帳號 `squeue` 並在 Python 裡過濾，導致其他使用者的作業中介資料進入 MCP 行程。 | 所有歸屬檢查現在只執行 `squeue -j <tracked ids>`（按 500 分批）；沒有追蹤作業時不發動任何 `squeue`。新增 `tests/security/test_shared_account_isolation.py` 證明他人的列永遠不會進入結果，且永不發出不帶 `-j` 的查詢。 |
| High | 檔案讀取 | 工具描述鼓勵把整個日誌從 `offset=0` 翻到 EOF；每次 `read` 都是一次 stat+dd+base64 往返。 | `hpc.files.read` 現在是由 `files.max_read_slice_bytes`（預設 256 KiB）鉗制的 bounded slice，描述引導先使用 `hpc.files.search`。 |
| High | 目錄列舉 | 遞迴 `find` 會串流輸出整棵樹，Python 事後才截斷。 | `hpc.files.list` 每次遠端呼叫只列舉一層，用 `head` 截斷遠端輸出（透過 `bash -o pipefail` 偵測 SIGPIPE），以 `depth:N` cursor 續讀，並鉗制 `max_depth`（預設 3）。 |
| High | 日誌診斷 | Agent 需要拼裝 `status -> output -> accounting -> read` 序列（工具抖動）。 | 新增 `hpc.jobs.diagnose`（狀態 + 記帳 + 有界尾部 + 錯誤簽章）與 `hpc.jobs.wait_and_diagnose`；每個內部查詢都僅限自有作業且有上限。 |
| High | 搜尋 | 沒有一等公民的有界搜尋；agent 只能臨時拼 `grep` 管線。 | 新增 `hpc.files.search`：單檔先 `stat` 並拒絕超限；目錄樹用 `grep -rn` + 排除目錄 + `-m` + `head` + `timeout`；每項預算都由服務端鉗制；pattern 作為已加引號的 argv 傳遞（無注入）。 |
| Medium | 查詢去重 | 重複的相同 status/list 呼叫每次都重跑 SSH。 | `QueryCache`（TTL `cache_ttl_seconds`，預設 2 s，0 停用）按正規化 tool+args 對冪等唯讀工具去重；任何寫操作使整個快取失效。 |
| Medium | 指令成本 | 合法指令（`find`/`du`/`sort`/`git grep`）仍可能壓滿登入節點。 | `security/command_cost.py` 為每條 safe 指令分層（LOW 15s/256KiB，MEDIUM 30s/512KiB，HIGH 10s/512KiB）；在 `SafeExec` 中強制，並回傳 `cost_tier`/`timeout_seconds`。 |
| Medium | 回應洩漏 | 稽核脫敏並不保護*回傳給 agent 的回應*（例如作業 stderr 裡的 `password=`）。 | `response_redactor.py` 在 `files.read/search`、`slurm.output`、`shell.run_safe`、`diagnose`、`snapshot` 中遮蔽明顯的祕密（`password=`/`token=`/`api_key`/`Bearer`/AWS/PEM 區塊）；模式保持最小以免破壞科研日誌內容。 |
| Medium | 資訊暴露 | `hpc.info` 回傳了原始 `local_roots` 路徑（可能含使用者名稱／專案名）。 | 替換為 `workspace_available`/`transfer_enabled` 布林值；回歸測試斷言本機使用者名稱永不出現。 |
| Medium | 寫入競態 | 覆蓋他人已修改的檔案可能靜默破壞工作。 | `hpc.files.write` 接受 `expected_size`/`expected_mtime`/`expected_sha256`；任何不匹配（或檔案缺失）都 fail-closed 拒絕寫入。 |
| Low | 高階執行 | 專家與 agent 共用一個原始 argv 提交面。 | 新增 `hpc.job.run`，從固定 runtime profile（julia/python/moose/bash）建構 argv 並重用完整提交策略；`hpc.slurm.submit` 保留給專家。 |

## 新增安全回歸套件（本輪）

- `test_shared_account_isolation.py` —— 無全帳號 `squeue`；他人作業永不進入結果；
  沒有追蹤作業時不掃描；他人的 cancel/accounting/output 被拒絕。
- `test_files_search.py` —— 搜尋預算、沙箱逃逸、控制字元與注入拒絕、`-m`/`head`
  上限、上下文解析。
- `test_command_cost.py` —— 分層歸類、git 子指令細化、在 `SafeExec` 中強制的
  timeout/output 鉗制。
- `test_response_redaction.py` —— password/token/API key/Bearer/AWS/PEM 被遮蔽，
  正常日誌內容保留，遞迴有界。
- `test_info_minimal_exposure.py` —— `hpc.info` 隱藏本機 root 與 SSH 細節。
- `test_cache.py` —— 去重鍵、TTL 過期、容量上限、寫操作失效。
- `test_project_snapshot.py` —— 有界目錄樹、git 摘要、僅限自有作業。
- `test_files_service.py` 增補 —— 讀取切片預算、list 分頁/cursor、寫入並行保護。
- `test_slurm_manager.py` 增補 —— diagnose 聚合與上限、wait_and_diagnose、
  job.run 策略重用。

## 殘餘風險（未變，現以三層顯式表述）

Layer 1（MCP policy）與 Layer 2（Slurm 資源策略）讓*行為異常的 agent* 無害，
但無法隔離在共享 Unix UID 下執行於運算節點的*惡意程式碼*：`hpc.slurm.submit`
授予的就是該 UID 的權限。應對該威脅模型需要 Layer 3（獨立 Unix UID、
Slurm 作業隔離 + filesystem ACL、容器／沙箱，或特權遠端輔助服務）。
對「明顯惡意」程式做正規表示式／策略過濾被明確地不作為 OS 層級隔離機制。參見
`README.md` / `SECURITY.md` / `docs/ARCHITECTURE.md`。

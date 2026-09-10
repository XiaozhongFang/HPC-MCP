# Security Policy

HPC-MCP 的安全模型：**所有關鍵邊界由 MCP Server 的決定性程式碼強制執行**，不依賴 Skill、system prompt 或工具描述的正確性。

實作邊界和請求流程詳見 [`docs/zh-TW/ARCHITECTURE.md`](docs/zh-TW/ARCHITECTURE.md)，本輪審查記錄見 [`docs/zh-TW/SECURITY_REVIEW.md`](docs/zh-TW/SECURITY_REVIEW.md)。

其他語言：[English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md) | [日本語](SECURITY.ja.md) | [한국어](SECURITY.ko.md)

## 威脅模型

- HPC 使用**共享帳號**（一個 SSH 帳號對應多個使用者）。
- Agent（LLM）可能收到錯誤、含糊或被注入的指令。
- 目標：即使 Agent 行為異常，也不能越權。

## 強制邊界

### 1. 路徑沙箱（Path Sandbox）

- 所有遠端路徑先經詞法正規化（`posixpath.normpath`，拒絕 `..`、tilde、NUL/CR/LF、相對路徑）。
- 再經遠端 `realpath` 對**已存在路徑**或**最近已存在父目錄**做 canonical 驗證，阻斷符號連結逃逸。
- 任何一步無法確定即拒絕。
- 遠端 root 必須是專用目錄，不能設定為 `/`；本機傳輸另有 `local_root` 沙箱（預設啟動目錄），拒絕符號連結、`.ssh` 和常見私鑰檔案。

**不允許**：存取 `$HPC_MCP_ROOT` 之外的一切路徑，包括其他共享帳號使用者的目錄、`/etc`、`/tmp`、系統目錄；透過 symlink 逃逸；rename/copy 到 root 之外；刪除 root 本身。

### 2. 登入節點指令策略（Command Policy）

- 僅允許清單內的輕量指令可執行（`ls`、`cat`、`grep`、`git status/diff/log`、`module list` 等，可設定擴充）。`squeue`、`sacct`、`scontrol` 禁止從 `hpc.shell.run_safe` 呼叫，避免繞過作業歸屬檢查。
- 拒絕所有 shell 中繼字元：`;` `&&` `||` `|` `>` `>>` `<` `$( )` `` ` `` `${ }` `&` 換行等。
- 拒絕運算／編譯程式：`julia`、`python`、`make`、`cmake`、`ninja`、`mpirun`、`srun`、編譯器、`pytest`、`matlab`、容器執行環境等。
- 拒絕危險程式：`sudo`、`ssh`/`scp`/`rsync`、`curl`/`wget`、`nohup`/`setsid`/`tmux`、`kill`、`chmod`、`dd`、巢狀 shell、`xargs`、`eval` 等。
- `git` 僅限唯讀子指令（拒絕 `commit/push/pull/clone/-c/--exec-path/--git-dir`）；`module` 僅查詢；`find` 拒絕 `-exec/-delete`。
- 本機與遠端均無 `shell=True`；argv 經 `shlex.join` 重新序列化。
- 解析失敗即拒絕。
- `cat`/`grep`/`find` 等指令的路徑運算元會執行遠端 canonical 驗證；`find -L/-H/-follow`、`ls -L`、`du -L` 均拒絕。`env`/`printenv` 僅在清空後的最小環境中執行。

### 3. Slurm 資源策略

- 分割區允許清單（預設空 = 全拒）。
- 節點／CPU／記憶體／GPU／時長／並行上限，超限即拒。
- 工作目錄必須在 root 內；指令 argv 不得含控制字元。
- 作業輸出固定寫入 `$ROOT/.hpc-mcp/jobs/<id>/`，不得越界。

### 4. 作業歸屬隔離

- 僅可管理目前服務工作階段提交並登記的 job（tracked_jobs.json 中的 `tool_session` 必須匹配）；重啟後的舊工作階段作業預設不可由新實例接管。
- **查詢隔離**：`hpc.slurm.queue/status` 只對本實例追蹤的 job ID 執行 `squeue -j <ids>`；沒有追蹤作業時直接回傳空，**絕不**發出全帳號 `squeue`/`sacct`——共享帳號下其他使用者的作業中介資料不會進入本行程。
- 不得檢視／取消其他使用者或他實例的作業。
- 該登記檔是應用層隔離，不是同一 Unix UID 下的強制存取控制；敵對共享帳號必須使用獨立 UID 或特權遠端輔助服務。

### 5. 查詢成本邊界（診斷工具與預算）

- `hpc.files.read` 是 **bounded slice**：單次讀取 ≤ `min(max_bytes, files.max_read_slice_bytes)`（預設 256 KiB）；工具描述不引導 Agent 從 offset=0 讀到 EOF（大檔排查用 `hpc.files.search` 定位）。
- `hpc.files.search` 所有預算（`max_matches`/`max_context_lines`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`）服務端鉗制；單檔先 `stat` 拒絕超預算檔案；遠端 `grep -m` + `head` 截斷 + `timeout` 兜底；pattern 經 shlex 引用、拒絕控制字元，杜絕注入。
- `hpc.files.list` 遞迴列舉逐層分頁（`page_size` + `depth:N` cursor），遠端 `head` 截斷輸出，`bash -o pipefail` 辨識 SIGPIPE 精確判定截斷；絕不整樹掃描後丟棄。
- `hpc.jobs.diagnose` / `hpc.project.snapshot` 所有內部查詢受限且帶預算。
- 唯讀冪等查詢按 `tool + 正規化參數` 去重（`cache_ttl_seconds`，預設 2s，0 停用）；任何寫操作主動使快取失效。
- `hpc.info` 不回傳本機路徑根（可能含使用者名稱／專案名），僅回傳能力布林與資源上限。
- `hpc.shell.run_safe` 所有合法指令受 **command cost policy** 分層鉗制：`ls`/`head`/`pwd` 等 LOW 層（15s/256KiB），`grep`/`cat`/`git diff` 等 MEDIUM 層（30s/512KiB），`find`/`du`/`sort`/`git grep` 等 HIGH 層（10s/512KiB）。timeout 與 output 均由服務端強制，不依賴 Agent 自覺。
- `hpc.files.write` 支援 **樂觀並行保護**：`expected_size`/`expected_mtime`/`expected_sha256` 任一不匹配目前檔案狀態即拒絕覆蓋（fail-closed），防止共享帳號下覆蓋同伴剛修改的程式碼。

### 6. SSH 邊界

- 只允許連接設定的單一 host；BatchMode、StrictHostKeyChecking、連線逾時。
- **不讀取、不輸出私鑰內容**；推薦 `~/.ssh/config` 管理。
- SSH/SFTP 輸出採用串流位元組上限，逾時會 kill 並回收子行程；ControlMaster 在服務結束時關閉。
- 不提供 `hpc.ssh(command=...)` 或任何任意指令工具。
- 無連接埠轉發、無 ProxyJump、無多主機。

### 7. 憑證與日誌

- 私鑰、密碼、token 永不寫日誌；稽核日誌對敏感模式脫敏並截斷。
- 稽核欄位：timestamp、tool、args（脫敏）、decision(ALLOW/DENY)、reason、job_id、duration。
- 脫敏按欄位名遞迴處理（password/token/secret/private-key 等），並清除控制字元後截斷；未知例外不會原樣回傳給 Agent。

## 三層安全邊界與殘餘風險

| 層 | 機制 | 防護目標 |
|---|---|---|
| Layer 1: MCP policy | 路徑沙箱、指令允許清單、Slurm 資源策略、作業歸屬、查詢預算、稽核 | Agent 越權／亂來 |
| Layer 2: Slurm | 分割區允許清單、資源上限、並行上限、`sbatch` 入口唯一 | 運算資源濫用 |
| Layer 3: OS／叢集 | 獨立 Unix UID／Slurm 作業隔離 + filesystem ACL／容器沙箱／特權遠端輔助服務 | **惡意程式碼隔離**（可選） |

**殘餘風險**：共享 Unix UID 下，MCP 只能保證「Agent 不亂來」（Layer 1/2），**無法**保證提交到運算節點的惡意程式碼不存取同 UID 可讀的資料（Layer 3）。`hpc.slurm.submit` 本質是「以該 Unix UID 執行程式」。不要用 Python 正規表示式／策略去「篩掉惡意程式碼」——那會產生虛假的安全感；真正的惡意程式碼隔離必須依賴 Layer 3 的 OS 層級機制。在 README / QUICKSTART 中均需向使用者明確這一點。

## 明確不實作（v1）

任意遠端 shell、任意 SSH host、sudo、遠端連接埠轉發、job 遷移、多主機 SSH、HPC 常駐 daemon、遠端 HTTP MCP、自動帳號切換、自動憑證管理、修改 `~/.ssh/config` / `authorized_keys`。

## 失敗安全

設定不完整、SSH 異常、路徑無法解析、指令解析失敗、Slurm 參數無法解析、分割區／主機不確定——統一 **DENY**，絕不回退到無限制 shell。

## 回報安全問題

請透過倉庫 Issue 私密回報或聯絡維護者，勿在公開管道披露未修復的細節。

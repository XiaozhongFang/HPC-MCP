# MCP 工具參數參考

其他語言：[English](../TOOLS.md) | [简体中文](../zh-CN/TOOLS.md) | [日本語](../ja/TOOLS.md) | [한국어](../ko/TOOLS.md)

本文件說明 HPC-MCP 暴露給 Agent 的 **22 個工具**的用途、參數、預設值、約束與回傳要點。
它是 [README.zh-TW.md](../../README.zh-TW.md)「工具清單」的展開版：README 回答「有哪些工具」，
本文件回答「每個參數怎麼填、會被什麼規則鉗制」。

- **設定參數**（host/root/分割區允許清單/資源上限……）見 [CONFIGURATION.md](CONFIGURATION.md)。
- **命令列參數**（`--host`/`--root`/`--check`……）見 [QUICKSTART.md](QUICKSTART.md) 第 4 節。
- **Agent 行為指南**（什麼時候用哪個工具）見 [skills/hpc-development/SKILL.md](../../skills/hpc-development/SKILL.md)。

---

## 通用約定（所有工具都適用）

| 約定 | 說明 |
|---|---|
| 路徑沙箱 | 所有遠端路徑必須是**絕對路徑**且位於 `$HPC_MCP_ROOT` 之內；`..`、符號連結出界、`/etc` 等一律拒絕。本機路徑必須位於 `local_roots`（預設＝啟動時工作目錄 + `/tmp`），並拒絕 `.ssh`、私鑰和符號連結。 |
| 服務端鉗制 | 所有「預算類」參數（位元組數、筆數、深度、逾時）都是**請求值 ≤ 服務端上限**，上限見 CONFIGURATION.md。傳 0 或負數會被拒（fail-closed）。 |
| 分頁 | 遞迴列舉用 `page_size` + `cursor` 逐層推進，不會整樹掃描；讀取用 `offset` + `next_offset` 續讀。 |
| 查詢去重 | 唯讀且冪等的工具按 `工具+參數` 在 `cache_ttl_seconds`（預設 2 秒）內去重，重複呼叫不產生 SSH 往返；任何寫操作都會清空該快取。 |
| 回應脫敏 | 日誌／檔案內容中的 `password=`、`token=`、`Bearer`、AWS 憑證、PEM 私鑰區塊等會被替換為 `[REDACTED]`；科研日誌內容不受影響。 |
| 失敗即拒絕 | 設定缺失、路徑無法解析、指令解析失敗、分割區不確定、SSH 異常等一切不確定情況 **DENY**，並給出可操作的替代建議（`Reason:` + `Use:`）。 |
| annotations | `readOnly`＝不修改遠端狀態；`destructive`＝可能刪除／覆蓋；`idempotent`＝重複呼叫無額外副作用；`openWorld`＝會觸及叢集運算側（提交作業）。 |

---

## 一、環境與專案上下文

### `hpc.info`

連線與叢集能力概覽。**無參數**。

回傳：`slurm_available`、`cluster`、`working_root`、`workspace_available`、`transfer_enabled`、
`allowed_partitions`（可提交的分割區允許清單）、`max_cpus`/`max_nodes`/`max_memory_mb`/`max_gpus`/`max_time`。

> 出於安全考量**不回傳** SSH host/user/port 和本機路徑根（避免 Agent 繞過 MCP 直連）。
> 申請資源前先看這裡的上限。

### `hpc.project.snapshot` — `readOnly, idempotent`

一次呼叫建立專案上下文：淺層目錄概覽 + 關鍵原始碼／日誌檔大小 + 唯讀 git 狀態 + 本專案已追蹤作業。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | `$HPC_MCP_ROOT` 內的絕對路徑 |
| `depth` | int | — | `2` | 列舉深度，服務端再按 `files.max_recursive_depth` 鉗制 |
| `include_git` | bool | — | `true` | 是否包含唯讀 git 摘要（`git status` 等） |
| `include_jobs` | bool | — | `true` | 是否包含本專案已追蹤作業 |

> 一次 `snapshot` 代替 `list` + `read` + `git status` + `queue` 的多次往返。

---

## 二、遠端檔案

### `hpc.files.list` — `readOnly, idempotent`

列目錄。遞迴時**逐層分頁**。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目錄絕對路徑（root 內） |
| `recursive` | bool | — | `false` | 遞迴列舉（逐層推進） |
| `max_entries` | int | — | 服務端 `files.max_list_entries`（2000） | 單次回傳筆數硬上限 |
| `page_size` | int | — | 同 `max_entries` | 每頁筆數 |
| `cursor` | string | — | — | 上一頁回傳的 `next_cursor`，用於繼續遞迴列舉 |
| `max_depth` | int | — | 服務端 `files.max_recursive_depth`（3） | 遞迴深度上限 |

### `hpc.files.read` — `readOnly, idempotent`

讀取檔案的一段（**bounded slice**，單次 ≤ `files.max_read_slice_bytes`，預設 256 KiB）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 檔案絕對路徑（root 內） |
| `max_bytes` | int | — | 服務端 `files.max_read_bytes`（1 MiB） | 本次讀取位元組上限，仍受 slice 上限鉗制 |
| `offset` | int | — | `0` | 起始位元組位移 |

回傳：`size`、`bytes`、`truncated`、`end_of_file`、`next_offset`、`content`（UTF-8，非法位元組替換）。

> 排查大日誌請先用 `hpc.files.search` 定位行號，再用 `offset` 讀一小段；
> **不要**從 `offset=0` 一直讀到 EOF。

### `hpc.files.search` — `readOnly, idempotent`

帶預算的正規表示式搜尋（POSIX ERE），用於「先定位再精讀」。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 檔案或目錄絕對路徑 |
| `pattern` | string | ✅ | — | 擴充正規表示式（作為單一引號參數傳遞，控制字元被拒） |
| `max_matches` | int | — | `files.search_max_matches`（200） | 最多匹配數 |
| `context_lines` | int | — | `files.search_max_context_lines`（10） | 每個匹配的上下文行數 |
| `max_scan_bytes` | int | — | `files.search_max_scan_bytes`（64 MiB） | 單檔掃描上限 |
| `max_files` | int | — | `files.search_max_files`（1000） | 目錄樹中最多掃描檔案數 |
| `max_depth` | int | — | `files.search_max_depth`（6） | 目錄樹深度上限 |
| `timeout` | int | — | `files.search_timeout`（5 s） | 遠端搜尋逾時 |

### `hpc.files.write` — `destructive`

寫檔案，支援**樂觀並行保護**（共享帳號下防止覆蓋他人剛改的程式碼）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目標絕對路徑 |
| `content` | string | ✅ | — | 文字內容（整體上限 `files.max_write_bytes`，預設 10 MiB） |
| `append` | bool | — | `false` | 追加而非覆蓋 |
| `expected_size` | int | — | — | 期望的目前位元組數，不符則**拒絕寫入** |
| `expected_mtime` | int | — | — | 期望的目前 mtime（epoch 秒），不符則拒絕 |
| `expected_sha256` | string | — | — | 期望的目前 SHA-256（64 位十六進位），不符則拒絕 |

> 三個 `expected_*` 可任意組合；只要提供就必須全部匹配，且檔案必須存在（否則拒絕）。

### `hpc.files.mkdir` — `destructive`

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目錄路徑 |
| `parents` | bool | — | `false` | 等同 `mkdir -p`（會驗證最近存在祖先仍在 root 內） |

### `hpc.files.delete` — `destructive`

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目標路徑（不允許刪除 root 本身） |
| `recursive` | bool | — | `false` | 遞迴刪除（`rm -rf`） |

### `hpc.files.upload` / `hpc.files.download`

SFTP 傳輸，**兩端都受沙箱限制**（本機 ∈ `local_roots`，遠端 ∈ `$HPC_MCP_ROOT`）。

| 工具 | 參數 | 型別 | 必填 | 說明 |
|---|---|---|---|---|
| `hpc.files.upload` (destructive) | `local_path` | string | ✅ | 本機來源檔案路徑 |
| | `remote_path` | string | ✅ | 目標路徑（root 內） |
| `hpc.files.download` (readOnly) | `remote_path` | string | ✅ | 遠端來源檔案路徑 |
| | `local_path` | string | ✅ | 本機目標路徑（`local_roots` 內） |

> 兩條路徑都拒絕符號連結；本機側額外拒絕 `.ssh`/`.gnupg` 與私鑰檔名；傳輸後驗證大小上限。

---

## 三、登入節點查詢

### `hpc.shell.run_safe` — `readOnly`

在登入節點執行**單一允許清單指令**（`ls`/`find`/`cat`/`grep`/`head`/`tail`/`wc`/`sort`/`uniq`/`stat`/`du`/`df`/`git status|diff|log`/`module list|avail`/`sinfo`/`env` 等）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `command` | string | ✅ | — | 單一指令；**不支援**管線、`&&`、`;`、重導向、`$()`、反引號、背景 `&` |
| `cwd` | string | — | `$HPC_MCP_ROOT` | 工作目錄（root 內，realpath 驗證） |
| `timeout` | int | — | `shell.max_exec_seconds`（30 s） | 再被指令成本檔鉗制 |

約束（程式碼層級強制）：

- **不能**執行運算／編譯程式（`julia`/`python`/`make`/`cmake`/`mpirun`/`pytest`/`nvcc`……）→ 改用 `hpc.slurm.submit`。
- **不能**執行 `squeue`/`sacct`/`scontrol` → 改用帶作業歸屬檢查的 Slurm 工具。
- **不能**用路徑形式執行程序（`./prog`、`/usr/bin/julia`）、不能 `find -exec`、不能 `git -c`、不能巢狀 shell。
- 指令成本分檔：低（15 s / 256 KiB，如 `ls`/`sinfo`）、中（30 s / 512 KiB，如 `cat`/`grep`）、高（10 s / 512 KiB，如 `find`/`du`/`sort`/`git grep`）。

回傳：`exit_code`、`stdout`、`stderr`、`cost_tier`、`timeout_seconds`（逾時置 `timed_out: true`）。

---

## 四、Slurm 作業

### `hpc.slurm.submit` — `openWorld`

提交運算作業（**唯一**的運算入口，列在服務端資源策略之下）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `command` | array 或 string | ✅ | — | 程式 argv（如 `["julia","--project=.","test/runtests.jl"]`），或**單一 `.sh` 指令稿路徑**（此時讀取其 `#SBATCH` 指令作為預設值） |
| `job_name` | string | — | `"job"` | 作業名（會被正規化為 `[A-Za-z0-9_.-]`，≤ 64 字元） |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 作業工作目錄（root 內，realpath 驗證） |
| `partition` | string | — | 設定的第一個允許清單分割區 | 必須命中 `slurm.allowed_partitions` |
| `nodes` | int | — | `1` | ≤ `slurm.max_nodes` |
| `ntasks` | int | — | `1` | 任務數 |
| `cpus_per_task` | int | — | `1` | 且 `nodes × ntasks × cpus_per_task ≤ slurm.max_cpus` |
| `memory` | string | — | 無 | 如 `"16G"`、`"8000"`（MiB）、`"2T"`；≤ `slurm.max_memory_mb` |
| `time_limit` | string | — | `slurm.max_time` | `HH:MM:SS`、`D-HH:MM:SS`、`MM:SS` 或純分鐘；≤ `slurm.max_time` |
| `gpus` | int | — | `0` | ≤ `slurm.max_gpus` |
| `environment` | object | — | — | 額外環境變數（名須匹配 `[A-Za-z_][A-Za-z0-9_]*`） |

行為要點：指令稿由服務端產生（`#SBATCH` 由服務端推導），stdout/stderr 固定捕捉到
`$HPC_MCP_ROOT/.hpc-mcp/jobs/<job-id>/`；提交後立刻用 `squeue -j`/`sacct -j` 交叉確認作業歸屬，
未確認的作業不會被登記；並行活躍作業數受 `slurm.max_concurrent_jobs` 限制。

### `hpc.job.run` — `openWorld`

按**受信任執行期 profile** 簡化提交，內部仍走與 `hpc.slurm.submit` 完全相同的策略。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `runtime` | string | ✅ | — | 列舉：`julia`、`python`、`moose`、`bash`、`shell` |
| `script` | string | ✅ | — | 指令稿絕對路徑（root 內）。**moose** 時傳執行檔路徑 |
| `args` | array\<string\> | — | — | 追加 argv；**moose 必填**（`[input.i]` 等），實際指令為 `mpirun -np <ntasks> -- <script> <args…>` |
| `job_name` | string | — | runtime 名 | 作業名 |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 工作目錄 |
| `partition` | string | — | 第一個允許清單分割區 | 同 submit |
| `nodes` / `ntasks` / `cpus_per_task` / `memory` / `time_limit` / `gpus` / `environment` | — | — | 同 `hpc.slurm.submit` | 資源參數語意與上限完全一致 |

各 profile 的實際指令：`julia --project=. <script>`、`python <script>`、`bash <script>`、
`moose` → `mpirun -np <ntasks> -- <script>`。

### 作業查詢與管理（僅限本實例提交的作業）

服務端只查詢**自己提交並登記**的 job ID（`squeue -j <ids>` / `sacct -j <ids>`），
絕不掃描共享帳號的全佇列；對其他作業的查詢／取消一律拒絕。

| 工具 | 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|---|
| `hpc.slurm.status` (readOnly,idempotent) | `job_id` | string | ✅ | — | 狀態；squeue 無記錄時回退 `sacct` |
| `hpc.slurm.queue` (readOnly,idempotent) | — | — | — | — | 本實例活躍作業清單（無活躍作業時回傳空，屬正常） |
| `hpc.slurm.output` (readOnly,idempotent) | `job_id` | string | ✅ | — | 讀取作業日誌 |
| | `stream` | string | — | `"stdout"` | 列舉：`stdout`、`stderr` |
| | `tail_bytes` | int | — | `shell.max_output_bytes`（1 MiB） | 從檔案末尾取多少位元組 |
| `hpc.slurm.cancel` (destructive) | `job_id` | string | ✅ | — | 取消（只能取消本實例提交的作業） |
| `hpc.slurm.accounting` (readOnly,idempotent) | `job_id` | string | ✅ | — | `sacct` 記帳：`elapsed`、`cpu_time_raw`、`max_rss`、`state`、`exit_code`、`node_list`、`alloc_cpus` |

### `hpc.jobs.wait` — `readOnly, idempotent`

輪詢直到作業進入終止態（`COMPLETED`/`FAILED`/`CANCELLED`/`TIMEOUT`/`OUT_OF_MEMORY`/`NODE_FAIL`/`PREEMPTED`）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 作業 ID |
| `timeout_seconds` | int | — | `wait_max_seconds`（3600） | 整體等待上限，再被服務端上限鉗制 |
| `poll_interval` | int | — | `10` | 輪詢間隔秒（服務端鉗制到 2–60） |

> 逾時不會取消作業，而是回傳錯誤並提示繼續輪詢。

### `hpc.jobs.diagnose` — `readOnly`

一次呼叫完成診斷：狀態 + 記帳 + stdout/stderr 尾部 + 常見錯誤簽章掃描。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 作業 ID |
| `stdout_lines` | int | — | `80` | stdout 尾部行數（上限 200；每行按 4096 位元組估算讀取量） |
| `stderr_lines` | int | — | `80` | stderr 尾部行數（上限 200） |
| `include_accounting` | bool | — | `true` | 是否附帶 `sacct` 記帳 |
| `include_error_scan` | bool | — | `true` | 是否掃描錯誤簽章（`oom`/`segfault`/`timeout`/`gpu_error`/`mpi_error`/`missing_file`） |

> 用一次 `diagnose` 代替 `status → output → accounting → read` 的多次往返。

### `hpc.jobs.wait_and_diagnose` — `readOnly`

等待作業結束再一次性診斷（作業已結束時立即回傳）。

參數：`job_id`（必填）、`timeout_seconds`、`poll_interval`、`stdout_lines`、`stderr_lines`
（語意與預設值同 `wait` / `diagnose`）。

---

## 五、叢集拓撲與並行參數

### `hpc.cluster.topo` — `readOnly, idempotent, openWorld`

取得**運算節點的真實硬體參數**與推導出的並行最佳化建議：CPU 型號/vendor、
`sockets × cores × threads`、SIMD 指令集（AVX2/AVX-512/SVE）、NUMA 域與距離矩陣、
快取階層、節點記憶體、分割區 features/GRES，以及
`recommended_parallel_parameters`（純 MPI／混合 MPI+OpenMP／純 OpenMP 三套，含
`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、`--cpu-bind=cores`、
`-map-by numa`、`OMP_NUM_THREADS`、建議 `--mem` 及理由）。

| 參數 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `partition` | string | — | 允許清單第一個分割區 | 要探測的分割區；名字須為 `[A-Za-z0-9_.-]` 且命中 `slurm.allowed_partitions` |
| `refresh` | bool | — | `false` | 強制重新採集（忽略快取；會先取消仍在排隊的舊採集作業） |

**呼叫成本與快取**（預設值見 `topology.*` 設定）：

- 第一次呼叫（或快取過期、或 `refresh: true`）會提交**一個 1 核採集作業**
  （預設時長上限 `00:03:00`），在運算節點上讀取 `lscpu` / `/proc/cpuinfo` /
  `numactl --hardware` / `/proc/meminfo` / `/sys/.../cpu0/cache`，並與登入節點
  `sinfo` 的分割區檢視合併。
- 結果快取 `topology.cache_ttl_seconds`（預設 24 h）：行程內 + 遠端
  `$HPC_MCP_ROOT/.hpc-mcp/topo/topology_<partition>.json`。快取命中時**不會**提交作業，
  回傳裡的 `source` 標明來源（`probe-job` / `session-cache` / `remote-cache`）。
- 採集作業仍在排隊（超過 `topology.wait_seconds`，預設 300 s）時回傳
  `status: "pending"` 與 `collection_job_id`；**再次呼叫會重用該作業**，不會重複提交。

**安全約束**：採集指令稿是服務端產生的**固定內容**，Agent 的任何參數都不會寫進指令稿；
指令稿只讀節點本機硬體事實，**不會**呼叫 `squeue`/`sacct`/`scontrol`（共享帳號隔離）；
作業歸屬、分割區允許清單、並行上限照常生效。管理員可用 `topology.enabled: false` 關閉該工具。

**回傳要點**：

| 欄位 | 含義 |
|---|---|
| `cpu.model_name` / `cpu.vendor` / `cpu.architecture` | CPU 型號、廠商、架構 |
| `cpu.sockets` / `cores_per_socket` / `threads_per_core` | 拓撲三要素 |
| `cpu.physical_cores` / `logical_cpus` | 實體核數／邏輯核數（SMT 開啟時後者更大） |
| `cpu.simd.level` / `present` | 向量指令集檔位（`avx512`/`avx2`/`avx`/`arm-neon`…）與命中的具體 flag |
| `cpu.cache_kib` / `cache_source` | L1d/L1i/L2/L3（KiB，`null` 表示該級未知）與來源（`lscpu`/`sysfs`） |
| `numa.count` / `nodes` / `distances` | NUMA 域數、每域 CPU 清單與大小、距離矩陣 |
| `memory_mib.mem_total` | 節點記憶體（MiB） |
| `slurm.partition` / `node_variants` | 分割區彙總（節點數／每節點核數記憶體／features/GRES）與硬體變體（含狀態直方圖） |
| `recommended_parallel_parameters` | 三套並行參數建議 + `notes`（每條建議的理由） |
| `collected_on_node` / `collection_job_id` | 取樣節點與採集作業 ID（便於追溯） |

> 建議值是依實體核／NUMA 域推導的**啟發式**起點，不是基準測試結論；
> 真正的調校仍要跑不同 rank/執行緒組合對比。

---

## 六、典型呼叫序列

```text
1. hpc.info                      # 分割區允許清單與資源上限
2. hpc.project.snapshot          # 專案上下文（目錄/git/我的作業）
3. hpc.cluster.topo              # 並行最佳化前：CPU/SIMD/NUMA + 參數建議
4. hpc.files.search  →  hpc.files.read   # 先定位，再讀一小段
5. hpc.files.write                # 遠端編輯
6. hpc.slurm.submit 或 hpc.job.run       # 編譯/測試/運算
7. hpc.jobs.wait_and_diagnose     # 等待 + 一次診斷
8. 分析 → 回到第 5 步
```

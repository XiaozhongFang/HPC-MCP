# 設定檔參考（config YAML）

其他語言：[English](../CONFIGURATION.md) | [简体中文](../zh-CN/CONFIGURATION.md) | [日本語](../ja/CONFIGURATION.md) | [한국어](../ko/CONFIGURATION.md)

本文件列出 **YAML 設定檔支援的全部參數**：位置、型別、預設值、取值範圍、
對應的環境變數（`HPC_MCP_*`）與 CLI 選項。`config/example.yaml` 是一份可直接
複製使用的完整範例。

> **優先順序（從高到低）**：CLI 選項 > 環境變數 > 設定檔 > 內建預設值。
> 同一參數在多個位置出現時，高優先者生效。

> **鍵名規則**：設定檔使用**底線**鍵（`local_root`、`allowed_partitions`、
> `ssh_bin`）。連字號寫法（`local-root`、`ssh-bin`）會被自動辨識為別名，但不推薦。
> 未辨識的鍵不會報錯，只會印一行
> `Ignoring unknown config key 'xxx'` 警告後忽略——看到這行就代表有拼寫錯誤。

> 本文件說明**服務端設定參數**。啟動／自檢用的 CLI 參數見
> [QUICKSTART.md](QUICKSTART.md) 第 4 節；**每個 MCP 工具的參數**見 [TOOLS.md](TOOLS.md)。

---

## 頂層參數

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `host` | string | —（必填） | SSH 主機；推薦用 `~/.ssh/config` 的 `Host` 別名。環境變數 `HPC_MCP_HOST`，CLI `--host` |
| `user` | string | — | SSH 使用者（可為共享帳號）。`HPC_MCP_USER`，`--user` |
| `port` | int | `22` | SSH 連接埠（1–65535）。`HPC_MCP_PORT`，`--port` |
| `root` | string | —（必填） | 遠端**使用者專屬**根目錄，Agent 的所有遠端操作被限制在此目錄內。必須是絕對路徑，且不能是 `/`。`HPC_MCP_ROOT`，`--root` |
| `local_root` | string | 目前工作目錄 | 本機上傳／下載允許的目錄（可多個，見 `local_roots`）。不能是 `.ssh`/`.gnupg` 等憑證目錄。`HPC_MCP_LOCAL_ROOT`，`--local-root` |
| `local_roots` | list[string] | `[目前目錄, 系統暫存目錄]` | 本機允許目錄清單；與 `local_root` 二擇一，同時出現時 `local_roots` 優先。`HPC_MCP_LOCAL_ROOTS`（逗號分隔） |
| `identity_file` | string | 來自 `~/.ssh/config` | SSH 私鑰路徑（Agent 永不接觸私鑰內容）。`HPC_MCP_IDENTITY_FILE`，`--identity-file` |
| `ssh_bin` | string | 從 PATH 尋找 | ssh 執行檔：純名稱、絕對路徑、或 `@` 前綴（WSL 用）。`HPC_MCP_SSH_BIN`，`--ssh-bin` |
| `sftp_bin` | string | 從 PATH 尋找 | sftp 執行檔，形式同 `ssh_bin`。`HPC_MCP_SFTP_BIN`，`--sftp-bin` |
| `wait_max_seconds` | int | `3600` | `hpc.slurm.wait` / `wait_and_diagnose` 的最大等待秒數（≤ 7 天）。`HPC_MCP_WAIT_MAX_SECONDS` |
| `cache_ttl_seconds` | float | `2.0` | 唯讀查詢去重快取 TTL（秒）；`0` 停用。`HPC_MCP_CACHE_TTL_SECONDS` |
| `log_file` | string | 僅 stderr | 追加寫日誌的檔案路徑（`~` 會展開）。`HPC_MCP_LOG_FILE`，`--log-file` |
| `log_level` | string | `INFO` | 日誌等級：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`。`HPC_MCP_LOG_LEVEL`，`--log-level` |

> `ssh.*` 子段參數也可直接寫在頂層（如 `connect_timeout`、`strict_host_key_checking`），
> 與 `ssh:` 子段等價。

---

## `ssh:` 子段

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `host` | string | — | 同頂層 `host` |
| `port` | int | `22` | 同頂層 `port` |
| `user` | string | — | 同頂層 `user` |
| `identity_file` | string | — | 同頂層 `identity_file` |
| `connect_timeout` | int | `15` | SSH 建連逾時（秒，≤ 3600）。`HPC_MCP_CONNECT_TIMEOUT` |
| `command_timeout` | int | `30` | 單一遠端指令逾時（秒，≤ 86400）。`HPC_MCP_COMMAND_TIMEOUT` |
| `strict_host_key_checking` | string | `yes` | 主機金鑰驗證：`yes`（要求 `known_hosts` 已固定，推薦）/ `accept-new`（首次自動登記）。**拒絕 `no`**。`HPC_MCP_STRICT_HOST_KEY_CHECKING` |
| `ssh_bin` | string | PATH | 同頂層 `ssh_bin` |
| `sftp_bin` | string | PATH | 同頂層 `sftp_bin` |

---

## `slurm:` 子段

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `allowed_partitions` | list[string] | `[]`（空 = 拒絕一切提交，fail-closed） | 允許提交的分割區允許清單。名稱不能含 `/` 或控制字元。`HPC_MCP_ALLOWED_PARTITIONS`（逗號分隔） |
| `max_nodes` | int | `2` | 單一作業節點數上限（≥ 1）。`HPC_MCP_MAX_NODES` |
| `max_cpus` | int | `64` | 單一作業總 CPU 上限：`nodes × ntasks × cpus_per_task`（≥ 1）。`HPC_MCP_MAX_CPUS` |
| `max_memory_mb` | int | `262144`（256 GiB） | 單一作業記憶體上限（MiB，≥ 1）。`HPC_MCP_MAX_MEMORY_MB` |
| `max_gpus` | int | `4` | 單一作業 GPU 上限（≥ 0）。`HPC_MCP_MAX_GPUS` |
| `max_time` | string | `"24:00:00"` | 單一作業時長上限。Slurm 格式：`HH:MM:SS`、`D-HH:MM:SS`；天數可溢位（`"2-24:00:00"` = 3 天）。`HPC_MCP_MAX_TIME` |
| `max_concurrent_jobs` | int | `20` | **同時活躍的最大作業數。** 提交時檢查該實例登錄檔裡「squeue 或 sacct 均非終止態」的作業數，達到上限即拒絕新提交並提示等待。已結束（`COMPLETED`/`FAILED`/`CANCELLED`/…）的作業自動釋放名額；squeue 查詢成功但作業已不在佇列、且 sacct 也已無記錄時同樣釋放（避免 accounting 記錄過期後永久佔滿配額）；squeue 查詢失敗時保守地繼續計數（fail-closed）。`HPC_MCP_MAX_CONCURRENT_JOBS` |

> 所有 `max_*` 數字欄位都支援簡單算術運算式：`+ - * /` 與括號，如 `"4*16"`、`"128/4"`。
> 環境變數中同樣支援（如 `HPC_MCP_MAX_CPUS=4*16`）。

---

## `shell:` 子段

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `safe_commands` | list[string] | `[]`（在內建允許清單之上追加） | 追加到內建最小允許清單的允許指令 basename（不含 `/`、≤ 128 字元）。`HPC_MCP_SAFE_COMMANDS`（逗號分隔） |
| `max_exec_seconds` | int | `30` | 單一 safe 指令執行時長上限（秒，≤ 86400）。`HPC_MCP_SHELL_MAX_EXEC_SECONDS` |
| `max_output_bytes` | int | `1048576`（1 MiB） | safe 指令單次輸出上限（位元組）。`HPC_MCP_MAX_OUTPUT_BYTES` |

---

## `files:` 子段

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `max_read_bytes` | int | `1048576`（1 MiB） | `hpc.files.read` 單次讀取的**硬上限**（位元組）。`HPC_MCP_MAX_READ_BYTES` |
| `max_read_slice_bytes` | int | `262144`（256 KiB） | 單次 `hpc.files.read` 回傳上限（bounded slice），防止 Agent 一次翻頁讀完整份大日誌。`HPC_MCP_MAX_READ_SLICE_BYTES` |
| `max_write_bytes` | int | `10485760`（10 MiB） | `hpc.files.write` 單次寫入上限（位元組）。`HPC_MCP_MAX_WRITE_BYTES` |
| `max_list_entries` | int | `2000` | `hpc.files.list` 單次回傳項目上限。`HPC_MCP_MAX_LIST_ENTRIES` |
| `max_recursive_depth` | int | `3` | `hpc.files.list recursive` 遞迴深度上限（≤ 64）。`HPC_MCP_MAX_RECURSIVE_DEPTH` |
| `search_max_matches` | int | `200` | `hpc.files.search` 單一檔案最多匹配數（≤ 100000）。`HPC_MCP_SEARCH_MAX_MATCHES` |
| `search_max_context_lines` | int | `10` | 每個匹配附帶的上下文行數（≤ 1000）。`HPC_MCP_SEARCH_MAX_CONTEXT_LINES` |
| `search_max_scan_bytes` | int | `67108864`（64 MiB） | 單檔搜尋掃描上限（位元組）。`HPC_MCP_SEARCH_MAX_SCAN_BYTES` |
| `search_max_files` | int | `1000` | 一次搜尋最多掃描檔案數（≤ 1000000）。`HPC_MCP_SEARCH_MAX_FILES` |
| `search_max_depth` | int | `6` | 搜尋遞迴深度上限（≤ 64）。`HPC_MCP_SEARCH_MAX_DEPTH` |
| `search_timeout` | int | `5` | 遠端單次搜尋逾時（秒，≤ 3600）。`HPC_MCP_SEARCH_TIMEOUT` |

---

## `topology:` 子段

控制 `hpc.cluster.topo`（運算節點硬體／NUMA／SIMD 拓撲探索）。該工具第一次呼叫
（或快取過期、或 `refresh=true`）會在指定分割區提交**一個 1 核的採集作業**，
在運算節點上讀取 `lscpu`／`/proc/cpuinfo`／`numactl --hardware`／`/proc/meminfo`，
並與登入節點 `sinfo` 的分割區／節點檢視合併，回傳 CPU 型號、sockets/cores/threads、
SIMD 指令集、NUMA 域與距離、快取階層、節點記憶體，以及推導出的並行參數建議
（`ntasks_per_node`、`cpus_per_task`、`--cpu-bind`/`--hint`、`OMP_NUM_THREADS`）。

採集指令稿是**服務端固定內容**：Agent 提供的任何參數都不會寫進指令稿，指令稿也
**不會**呼叫 `squeue`/`sacct`/`scontrol`（共享帳號隔離），只讀節點本機硬體事實。

| 鍵 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否註冊 `hpc.cluster.topo`。設為 `false` 時該工具不暴露給 Agent，也就永遠不會提交採集作業。`HPC_MCP_TOPOLOGY_ENABLED` |
| `cache_ttl_seconds` | int | `86400`（24 h） | 拓撲結果的新鮮期：行程內快取 + 遠端 `$ROOT/.hpc-mcp/topo/topology_<partition>.json` 共用該 TTL，避免重複排隊。`0` = 每次呼叫都重新採集。`HPC_MCP_TOPOLOGY_CACHE_TTL_SECONDS` |
| `wait_seconds` | int | `300` | 等待採集作業結束的秒數；逾時回傳 `status: "pending"` 與 `job_id`（作業繼續排隊，下次呼叫重用它，不會重複提交）。`HPC_MCP_TOPOLOGY_WAIT_SECONDS` |
| `collect_time_limit` | string | `"00:03:00"` | 採集作業本身的 Slurm 時長上限（格式同 `slurm.max_time`）。`HPC_MCP_TOPOLOGY_COLLECT_TIME_LIMIT` |

> 採集作業同樣受 `slurm.allowed_partitions`、`max_concurrent_jobs` 與作業歸屬
> 規則約束；`allowed_partitions` 為空時該工具 fail-closed（拒絕呼叫）。
> 探到的分割區名會用於 `sinfo -p <分割區>` 與快取檔名，因此比通用 Slurm 策略更嚴：
> 只接受 `[A-Za-z0-9_.-]`。

---

## 完整範例

```yaml
# HPC-MCP 完整設定範例（所有鍵均為可選，除非標註「必填」）
host: my-hpc                      # 必填：SSH Host 別名或位址
user: shared_account
port: 22
root: /home/shared_account/alice  # 必填：遠端使用者專屬根目錄
local_root: /home/alice/proj      # 本機上傳／下載目錄
# local_roots: [ /home/alice/proj, /tmp ]
# identity_file: ~/.ssh/id_ed25519
# ssh_bin: /usr/bin/ssh
# sftp_bin: @/mnt/c/Windows/System32/OpenSSH/ssh.exe

ssh:
  connect_timeout: 15
  command_timeout: 30
  strict_host_key_checking: "yes"   # 或 "accept-new"；拒絕 "no"

slurm:
  allowed_partitions: [compute]     # 必配：預設空 = 拒絕一切提交
  max_nodes: 2
  max_cpus: 64                      # 支援運算式如 "4*16"
  max_memory_mb: 262144             # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20           # 同時活躍作業上限，預設 20

shell:
  safe_commands: []                 # 追加允許的指令 basename
  max_exec_seconds: 30
  max_output_bytes: 1048576

files:
  max_read_bytes: 1048576
  max_read_slice_bytes: 262144
  max_write_bytes: 10485760
  max_list_entries: 2000
  max_recursive_depth: 3
  search_max_matches: 200
  search_max_context_lines: 10
  search_max_scan_bytes: 67108864
  search_max_files: 1000
  search_max_depth: 6
  search_timeout: 5

topology:
  enabled: true                     # false = 不註冊 hpc.cluster.topo（永不提交採集作業）
  cache_ttl_seconds: 86400          # 拓撲新鮮期（24h）；0 = 每次都重新採集
  wait_seconds: 300                 # 採集作業排隊等待上限，逾時回傳 pending
  collect_time_limit: "00:03:00"    # 採集作業自身的 Slurm 時長上限

wait_max_seconds: 3600
cache_ttl_seconds: 2.0
log_file: ~/.local/share/hpc-mcp/hpc-mcp.log
log_level: INFO
```

# 驗證設定 + 連線性
```bash
source ~/venvs/hpc-mcp/bin/activate
hpc-mcp --config ~/.config/hpc-mcp/config.yaml --check
```

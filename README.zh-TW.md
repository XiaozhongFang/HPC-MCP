# HPC-MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP stdio server](https://img.shields.io/badge/MCP-stdio%20server-6f42c1.svg)](https://modelcontextprotocol.io)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | **繁體中文**

一個**安全優先**的 MCP Server，讓 Codex、Reasonix 等 Coding Agent 透過 SSH + Slurm 安全地操作遠端 HPC 叢集——路徑沙箱、登入節點指令允許清單、Slurm 資源上限、作業歸屬、完整 ALLOW/DENY 稽核，全部由程式碼強制。

> **第一次用？先看 → [docs/zh-TW/QUICKSTART.md](docs/zh-TW/QUICKSTART.md)**：手把手的安裝、每個參數的說明、完整的 Codex/Reasonix 設定範例，以及「啟動後卡住」「網路不通」「pip 裝成 UNKNOWN」等常見問題的排查表。

## 架構

```text
Codex / Reasonix (Agent)
        │  MCP (stdio)
        ▼
  Project Skill            ← 行為指南（非安全邊界）
        │
        ▼
  HPC MCP Server           ← 安全邊界（程式碼層級強制）
        │
        ├── Path Sandbox        （USER_ROOT 強制）
        ├── Command Policy      （login node 允許清單）
        ├── Slurm Resource Policy（分割區/資源/並行限制）
        ├── SSH Manager         （固定 argv，無本機 shell）
        ├── Job Tracker         （共享帳號下的作業歸屬）
        └── Audit Logger        （每次呼叫 ALLOW/DENY 稽核）
        │ SSH / SFTP
        ▼
  HPC Login Node  ──只允許輕量查詢──┐
        │ sbatch                     │
        ▼                            ▼
  Compute Node (Slurm)      使用者工作目錄 $HPC_MCP_ROOT
        │
   Julia / MOOSE / Python / CMake
```

設計原則：**MCP 是安全邊界；Skill 只是行為指南**。即使 Agent 提示詞錯誤、Skill 被誤解，核心權限邊界也無法被繞過。

## 安全模型

### 共享帳號隔離

HPC 常使用共享帳號（如 `/home/shared_account/`）。該 home 目錄**不等於**使用者自己的目錄。必須設定：

```
HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD       # 上傳／下載允許存取的本機目錄
```

所有遠端檔案操作都被限制在該 root 之下（含符號連結 canonical 驗證）。本機 `upload`/`download` 同樣被限制在 `local_roots`（一個或多個本機允許目錄，預設 = 啟動行程目前目錄 + 系統暫存目錄 `/tmp`），拒絕 `.ssh`、私鑰和符號連結路徑。其他使用者的目錄（`/home/shared_account/other_user`）、系統目錄（`/etc`、`/opt`）一律拒絕。

### Login Node 策略

登入節點只允許輕量、唯讀的管理指令（允許清單制）：`ls`、`find`、`cat`、`grep`、`head`、`tail`、`git status/diff/log`、`module list/avail` 等。為防止共享帳號下檢視其他使用者的作業，`squeue`、`sacct`、`scontrol` 不再透過 `hpc.shell.run_safe` 暴露，只能使用帶歸屬檢查的 Slurm 工具。

**永遠拒絕**在登入節點執行運算與編譯：`julia`、`python`、`make`、`cmake --build`、`ninja`、`mpirun`、`srun`、`pytest`、`matlab`、GPU 程式等——全部引導至 `hpc.slurm.submit`。

指令策略在**程式碼層級**拒絕：shell 中繼字元（`;`、`&&`、`||`、`|`、`>`、`<`、`$()`、反引號、`&`）、路徑形式的任意執行檔（`./program`）、`find -exec`、`git -c`、巢狀 shell、`sudo`/`ssh`/`curl` 等危險程式。解析失敗同樣拒絕（fail-closed）。

`env`/`printenv` 即使被請求，也只在清空後的最小環境中執行；不會回傳 SSH token、金鑰或叢集憑證。所有指令的路徑運算元會再次執行遠端 `realpath` 驗證，並拒絕跟隨符號連結的選項。

### Slurm 資源策略

`hpc.slurm.submit` 是唯一運算入口。Server 端強制：

- 分割區允許清單（預設空 = 拒絕一切；agent 只能選用允許清單內的分割區）
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs`（並行上限）
- 工作目錄必須位於 USER_ROOT 內
- 作業 stdout/stderr 固定捕捉到 `$ROOT/.hpc-mcp/jobs/<job-id>/`

### 作業歸屬（共享帳號）

共享帳號下 Unix 使用者無法區分不同使用者。每個 MCP 實例只管理**自己提交並登記**的作業（`$ROOT/.hpc-mcp/tracked_jobs.json`）。對其他作業的 `status/output/cancel/accounting` 一律拒絕。

### 失敗安全（fail-closed）

設定缺失、路徑無法解析、指令解析失敗、分割區不確定、SSH 異常等任何不確定情況統一 **DENY**，絕不回退到無限制 shell。

### 三層安全邊界

| 層 | 機制 | 防護目標 |
|---|---|---|
| Layer 1: MCP policy | 路徑沙箱、指令允許清單、Slurm 資源策略、作業歸屬、稽核 | Agent 越權／亂來 |
| Layer 2: Slurm | 分割區允許清單、資源上限、並行上限、`sbatch` 入口唯一 | 運算資源濫用 |
| Layer 3: OS／叢集 | 獨立 Unix UID／作業隔離／filesystem ACL／容器 | **惡意程式碼隔離**（可選） |

> **殘餘風險（必須知道）**：在共享 Unix UID 下，MCP 只能保證「Agent 不亂來」（Layer 1/2），**無法**保證提交到運算節點的惡意程式碼不存取同 UID 能存取的其他資料（Layer 3）。真正敵對的程式碼隔離需要獨立 UID、Slurm 作業隔離 + filesystem ACL，或叢集容器／沙箱。不要把 Python 側的正規表示式／策略當作對惡意程式碼的 OS 層級隔離。

### 查詢成本與去重

- `hpc.files.read` 是 **bounded slice**：單次最多 `min(max_bytes, files.max_read_slice_bytes)`（預設 256KiB）位元組，不再引導 Agent 從 offset=0 讀到 EOF。
- `hpc.files.list` 遞迴列舉**逐層分頁**（`page_size` + `next_cursor`），遠端 `head` 截斷，絕不整樹掃描後丟棄。
- `hpc.files.search` 帶硬預算（`max_matches`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`，全部服務端鉗制），用於「先定位再精讀」。
- 唯讀冪等查詢按 `tool+參數` 去重（TTL 預設 2 秒，`cache_ttl_seconds` 可調，0 停用）；任何寫操作主動使快取失效。
- `hpc.slurm.queue/status` 只查本實例追蹤的 job ID（`squeue -j <ids>`），**絕不**掃描共享帳號的全佇列。
- `hpc.shell.run_safe` 的所有指令（含允許清單內合法指令）都有 **command cost 預算**：`find`/`du`/`sort`/`git grep` 等高風險指令被鉗制在更短 timeout + 輸出上限內。
- 回傳給 Agent 的內容（日誌／檔案內容）經**最小限度 secret 脫敏**（`password=`/`token=`/`Bearer`/AWS/PEM 私鑰區塊等），與稽核日誌脫敏分開治理，不破壞科研日誌。

## 安裝

詳細安裝流程見 [docs/zh-TW/QUICKSTART.md](docs/zh-TW/QUICKSTART.md)

安裝後得到 `hpc-mcp` 指令。

## 設定

三種方式，優先順序 **CLI > 環境變數 > 設定檔 > 預設值**。

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account \
  --root /home/shared_account/alice --local-root "$PWD"

# 指定 ssh/sftp 執行檔（WSL 環境需要時）：
#   --ssh-bin  /usr/bin/ssh
#   --ssh-bin  @/usr/bin/ssh
#   --ssh-bin  @/mnt/c/Windows/System32/OpenSSH/ssh.exe
#   --sftp-bin @/usr/bin/sftp
```

### 環境變數

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML 設定檔

完整參數說明見 [`docs/zh-TW/CONFIGURATION.md`](docs/zh-TW/CONFIGURATION.md)（所有支援的鍵、
預設值、取值範圍、對應環境變數）；這裡是常用最小設定：

```yaml
host: my-hpc
user: shared_account
root: /home/shared_account/alice
local_root: /path/to/local/project

slurm:
  allowed_partitions: [compute]
  max_cpus: 64
  max_nodes: 2
  max_memory_mb: 262144      # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20    # 同時活躍作業上限（預設 20，可調）
```

```bash
hpc-mcp --config config.yaml
```

### SSH 設定（推薦）

使用 `~/.ssh/config` 管理連線細節，`HPC_MCP_HOST` 直接引用 Host 別名：

```sshconfig
Host my-hpc
    HostName hpc.example.edu
    User shared_account
    IdentityFile ~/.ssh/id_ed25519
```

Agent 永遠不會接觸私鑰內容。

**必須設定免密登入**（hpc-mcp 強制 `BatchMode=yes`，不彈密碼）。設定前先用
BatchMode 確認免密已就緒，否則所有工具呼叫都會回報 `Permission denied`：

```bash
ssh -o BatchMode=yes my-hpc "echo OK"   # 能直接回傳 OK 才代表免密可用
ssh-copy-id my-hpc                      # 若上面失敗，先設定免密（要一次密碼）
```

預設 `StrictHostKeyChecking=yes`：首次連線請先手動 `ssh my-hpc` 確認主機指紋並寫入 `known_hosts`；如確需首次自動登記，可在設定中設 `ssh.strict_host_key_checking: accept-new`。

### 連線性自檢

```bash
hpc-mcp --host my-hpc --root /home/shared_account/alice --check
```

## MCP 客戶端整合

### 推薦：一行指令自動註冊（`hpc-mcp mcp-add`）

安裝好之後，用 `mcp-add` 自動把 hpc-mcp 註冊進 Codex 和 Reasonix 的設定。
它寫入的是**可攜的啟動指令稿路徑**（`<repo>/scripts/hpc-mcp-run`），該指令稿
自動定位本機的 hpc-mcp（conda/venv/PATH），因此**不綁定**某台機器的
conda 路徑，換機器後重新 clone + 安裝即可：

```bash
# 在倉庫目錄內執行（會找到倉庫的 scripts/hpc-mcp-run）
cd ~/git_repo/HPC-MCP
hpc-mcp mcp-add --config ~/.config/hpc-mcp/192.168.10.10.yaml

# 或直接傳參
hpc-mcp mcp-add --host my-hpc --user shared_account --root /home/shared_account/alice
```

效果：
- `~/.codex/config.toml` 寫入 `[mcp_servers.hpc]`（command 指向 `scripts/hpc-mcp-run`）
- `~/.reasonix/config.toml` 寫入 hpc plugin（同樣指向啟動指令稿）
- 啟動指令稿按順序定位 hpc-mcp：`$HPC_MCP_BIN` → PATH → 常見 conda/venv 路徑；
  找不到時給出清楚提示而不是靜默失敗
- 只新增／更新 hpc 段，**不破壞**你已有的其他 MCP server / provider 設定
- 冪等：重複執行不會產生重複段

改完後**重啟 Codex / Reasonix** 即可。

### CC-Switch（MCP 設定管理器）

[CC-Switch](https://github.com/farion1231/cc-switch) 用 JSON 管理多個 MCP
server 設定並支援一鍵切換。完整的 stdio JSON 設定（`command` +
`env`）、欄位說明與常見問題見 **[`docs/zh-TW/CC_SWITCH.md`](docs/zh-TW/CC_SWITCH.md)**：

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/yourname/git_repo/HPC-MCP/scripts/hpc-mcp-run",
  "args": [],
  "env": {
    "HPC_MCP_HOST": "my-hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice",
    "HPC_MCP_LOCAL_ROOT": "/home/yourname/my-project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug"
  }
}
```

### 專案層級 `.mcp.json`（最可攜）

倉庫自帶一份 `.mcp.json` 範本（MCP 標準專案層級設定）。在其中新增一個 `hpc`
項目，command 指向 `./scripts/hpc-mcp-run`；支援專案層級 MCP 的客戶端（如從倉庫
目錄啟動的 Codex/Reasonix）會自動載入，host/root 等透過環境變數注入
（`${HPC_MCP_HOST}` 等，在 shell profile 定義）。換機器只需：
clone 倉庫 → 安裝 hpc-mcp → 定義環境變數 → 從倉庫目錄啟動客戶端。

### 手動方式（可選）

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_USER=shared_account \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  hpc-mcp
```

兩者都是 stdio argv 方式啟動，無需 shell。注意手動方式裡 `hpc-mcp` 若
不在 PATH，需換成絕對路徑（如 `/path/to/conda/envs/hpc-mcp/bin/hpc-mcp`）。

## 工具清單（22 個）

> **每個工具的參數、預設值、約束與回傳要點**見 **[docs/zh-TW/TOOLS.md](docs/zh-TW/TOOLS.md)**；
> 下表只是索引。

### 低階原語

| 工具 | 說明 | annotations |
|---|---|---|
| `hpc.info` | 連線／叢集資訊（本機路徑不暴露） | readOnly |
| `hpc.files.list` | 列目錄（bounded 分頁，recursive 逐層 + cursor） | readOnly |
| `hpc.files.read` | 讀檔案（bounded slice，單次 ≤256KiB） | readOnly |
| `hpc.files.write` | 寫檔案（支援 expected_size/mtime/hash 樂觀並行保護） | destructive |
| `hpc.files.mkdir` | 建目錄 | — |
| `hpc.files.delete` | 刪除 | destructive |
| `hpc.files.upload` | 本機上傳（SFTP） | destructive |
| `hpc.files.download` | 下載到本機（SFTP） | readOnly |
| `hpc.shell.run_safe` | 允許清單輕量指令（帶 command cost 預算） | readOnly |
| `hpc.slurm.submit` | 提交運算作業 | openWorld |
| `hpc.slurm.status` | 作業狀態 | readOnly |
| `hpc.slurm.queue` | 我的作業佇列 | readOnly |
| `hpc.slurm.output` | 作業 stdout/stderr | readOnly |
| `hpc.slurm.cancel` | 取消作業 | destructive |
| `hpc.slurm.accounting` | sacct 記帳 | readOnly |
| `hpc.jobs.wait` | 等待作業完成（有上限） | readOnly |

### 高階 Agent 工具

| 工具 | 說明 | annotations |
|---|---|---|
| `hpc.files.search` | 帶預算的正規表示式搜尋（定位日誌錯誤行） | readOnly |
| `hpc.jobs.diagnose` | 一次完成狀態+記帳+日誌尾部+錯誤簽章診斷 | readOnly |
| `hpc.jobs.wait_and_diagnose` | 等待作業結束並一次診斷 | readOnly |
| `hpc.project.snapshot` | 一次建立專案上下文（目錄概覽+git+作業） | readOnly |
| `hpc.cluster.topo` | 運算節點硬體拓撲（CPU 型號/SIMD/NUMA/cache）+ 並行參數建議 | readOnly, openWorld |
| `hpc.job.run` | runtime profile 高階提交（julia/python/moose/bash） | openWorld |

### 範例呼叫

提交 Julia 作業（分割區可從 `hpc.info` 回傳的允許清單中選擇，不指定則取設定的第一個）：

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "job_name": "demo-test",
    "working_directory": "/home/shared_account/alice/demo_benchmark",
    "partition": "compute",
    "cpus_per_task": 8,
    "time_limit": "00:30:00",
    "command": ["julia", "--project=.", "scripts/test.jl"]
  }
}
```

參數預設值來自服務端設定或 `.sh` 指令稿的 `#SBATCH` 指令（顯式參數優先）；
分割區必須命中設定允許清單，否則拒絕。也可以直接提交一個 `.sh` 作業指令稿路徑：

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "command": "/home/shared_account/alice/proj/run.sh"
  }
}
```

被拒絕時回傳可操作資訊：

```text
Operation denied.

Reason:
'julia' is a computational/build workload and is forbidden on HPC login nodes.

Use:
hpc.slurm.submit
```

## 並行最佳化參數（`hpc.cluster.topo`）

登入節點允許清單**故意**不放行 `lscpu`/`numactl`，`/proc` 也在路徑沙箱之外，
所以 CPU 型號、SIMD 指令集、NUMA 拓撲這些參數只能從運算節點取。`hpc.cluster.topo`
把這套流程做成一次呼叫：

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- **第一次呼叫**（或快取過期、或 `refresh: true`）提交**一個 1 核採集作業**，
  在運算節點上讀 `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo`，
  再與登入節點 `sinfo` 的分割區檢視合併。
- **回傳**：CPU 型號與 sockets/cores/threads、SIMD 指令集（AVX2/AVX-512/SVE）、
  NUMA 域與距離矩陣、快取階層、節點記憶體、分割區 features/GRES，以及推導出的
  並行參數建議（`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、
  `--cpu-bind=cores`、`OMP_NUM_THREADS`、`-map-by numa`、建議 `--mem`），
  並附上每一條建議的理由與「實體核 vs 邏輯核」的取捨。
- **快取**：`topology.cache_ttl_seconds`（預設 24h）內命中行程內快取或遠端
  `$ROOT/.hpc-mcp/topo/topology_<partition>.json`，後續工作階段不會為同一台機器反覆排隊。
- **排隊中**：採集作業尚未排程時回傳 `status: "pending"` + `job_id`；
  再次呼叫會重用該作業，不會重複提交。
- **安全**：採集指令稿是**服務端固定內容**（Agent 的任何參數都不進入指令稿），
  且指令稿**不查佇列**（`squeue`/`sacct`/`scontrol`），只讀節點本機硬體事實，
  符合共享帳號隔離規則；作業歸屬、分割區允許清單、並行上限照常生效。
- 管理員可用 `topology.enabled: false` 整個關閉該工具（永不提交採集作業）。

## 推薦工作流程（Agent）

1. `hpc.info` 了解環境 → 2. `hpc.project.snapshot` 一次建立專案上下文 →
3. 涉及並行／效能時 `hpc.cluster.topo` 一次拿到 CPU/SIMD/NUMA 與推薦並行參數 →
4. `hpc.files.search` 先定位（日誌錯誤行等），再 `hpc.files.read` 讀小段上下文 →
5. `hpc.files.write` 遠端編輯 → 6. 編譯／測試／運算一律 `hpc.slurm.submit` →
7. `hpc.jobs.diagnose` 一次診斷失敗作業 → 8. 分析、修改、重複。

原則：**能一次高階呼叫完成的，不要拆成多次低階呼叫**；`hpc.files.read` 是 bounded slice，不要從 offset=0 讀到 EOF；重複的唯讀查詢由服務端 TTL 快取去重。

詳見 [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md)。

## 安全測試

```bash
python -m pytest tests/ -q
```

涵蓋：路徑穿越、符號連結逃逸、指令注入、login/compute 邊界、Slurm 資源濫用、作業隔離。

完整的發現、修正和殘餘風險記錄見 [`docs/zh-TW/SECURITY_REVIEW.md`](docs/zh-TW/SECURITY_REVIEW.md)，模組邊界和請求流程見 [`docs/zh-TW/ARCHITECTURE.md`](docs/zh-TW/ARCHITECTURE.md)。

## 限制（v1 明確不做）

任意遠端 shell、任意 SSH host、sudo、連接埠轉發、多主機、遠端常駐 daemon、HTTP MCP、自動憑證管理。

## 故障排查

- **啟動回報 "No HPC host/root configured"**：三種設定方式至少提供 host 與 root。
- **工具全部 DENY "No Slurm partitions are allowed"**：設定 `slurm.allowed_partitions`（預設空，fail-closed）。
- **SSH 255 錯誤**：先用 `hpc-mcp ... --check` 驗證；確認 `~/.ssh/config` 與 BatchMode 免密可用。
- **日誌**：寫 stderr（stdout 只走 MCP 協定）；`--log-file` 可追加到檔案。

## 文件

| 文件 | 內容 |
|---|---|
| [docs/zh-TW/QUICKSTART.md](docs/zh-TW/QUICKSTART.md) | 手把手安裝 + 每個參數說明 + 排查表 |
| [docs/zh-TW/CONFIGURATION.md](docs/zh-TW/CONFIGURATION.md) | 全部 YAML 鍵、預設值、取值範圍與環境變數 |
| [docs/zh-TW/TOOLS.md](docs/zh-TW/TOOLS.md) | 22 個工具的參數／預設值／約束 |
| [docs/zh-TW/CC_SWITCH.md](docs/zh-TW/CC_SWITCH.md) | CC-Switch JSON 設定、欄位說明、常見問題 |
| [docs/zh-TW/ARCHITECTURE.md](docs/zh-TW/ARCHITECTURE.md) | 模組邊界、請求流程、隔離層次 |
| [docs/zh-TW/SECURITY_REVIEW.md](docs/zh-TW/SECURITY_REVIEW.md) | 發現、修正、驗證與殘餘風險 |
| [SECURITY.zh-TW.md](SECURITY.zh-TW.md) | 威脅模型與強制邊界 |

## 授權條款

MIT，見 [LICENSE](LICENSE)。

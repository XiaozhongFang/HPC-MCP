# 快速上手與排障（手把手）

其他語言：[English](../QUICKSTART.md) | [简体中文](../zh-CN/QUICKSTART.md) | [日本語](../ja/QUICKSTART.md) | [한국어](../ko/QUICKSTART.md)

這份文件帶你從零跑通 hpc-mcp，並解釋每個參數、常見「卡住」現象的原因。

---

## 0. 一分鐘理解它在做什麼

`hpc-mcp` 是一個 **MCP Server（stdio）**，它不是一次性指令：

- 你用 `hpc-mcp ...` 啟動它後，它會印出一行 `starting: ...` 然後**停在那裡等待輸入**。
  **這是正常的！** 它在等 MCP 客戶端（Codex / Reasonix）透過標準輸入送來請求。
  它**此刻還沒連接 SSH**，所以光看啟動日誌判斷不了 SSH 通不通。
- 真正的 SSH 連線發生在客戶端第一次呼叫工具（例如 `hpc.info`）時。
- 想**立刻驗證設定和網路是否通**，用 `--check`（見第 3 步），它會主動連一次 SSH 並印出結果。

---

## 1. 安裝

**強烈建議用獨立的虛擬環境安裝**，不要直接 `pip install --user` 塞進系統
`~/.local`——否則會和系統裡已有的套件（torch、httpcore 等）互相衝突，出現
`UNKNOWN-0.0.0` 空殼套件、`pip's dependency resolver does not take into account...`
警告。

### 方案 A（推薦）：virtualenv 隔離環境

系統缺少 `python3-venv` 時用 virtualenv（純使用者層級，無需 root）：

```bash
# 1. 安裝 virtualenv（一次性）
python3 -m pip install --user virtualenv

# 2. 建立專案專用環境（只建一次，以後一直用）
python3 -m virtualenv ~/venvs/hpc-mcp

# 3. 啟用並安裝 hpc-mcp
source ~/venvs/hpc-mcp/bin/activate
cd ~/git_repo/HPC-MCP
pip install .

# 4. 使用
hpc-mcp --version        # 應輸出 hpc-mcp 0.1.0
hpc-mcp --help
```

> 如果系統已裝 `python3-venv`，可改用標準庫：
> `python3 -m venv ~/venvs/hpc-mcp`（其餘步驟相同）。

驗證時若 `hpc-mcp` 找不到，檢查是否已啟用環境，或直接用全路徑：

```bash
~/venvs/hpc-mcp/bin/hpc-mcp --version
```

給 Codex / Reasonix 註冊時，把指令換成該環境的全路徑：

```bash
codex mcp add hpc ... -- ~/venvs/hpc-mcp/bin/hpc-mcp
```

### 方案 B：conda

如果你已經用 conda：

```bash
conda create -n hpc-mcp python=3.10
conda activate hpc-mcp
cd ~/git_repo/HPC-MCP
pip install .
```

### 方案 C：直接裝進 ~/.local（不推薦，僅臨時用）

```bash
cd ~/git_repo/HPC-MCP
python3 -m pip install --user .
```

> 若出現 `UNKNOWN-0.0.0` 或相依性衝突警告，代表 pip/setuptools 太舊或
> `~/.local` 已混亂，請改用方案 A。

### 遇到問題先看

- `pip install .` 出現 `UNKNOWN-0.0.0` → pip/setuptools 太舊，環境沒有隔離。
- 出現 `ERROR: pip's dependency resolver does not currently take into account...`
  → 系統 `~/.local` 裡有別的套件衝突（torch/httpcore 等）。**不影響 hpc-mcp 執行**，
  但代表該環境已混裝，建議換虛擬環境。

### ⚠️ 安裝後第一步：確認 SSH 免密登入已設定

**hpc-mcp 強制使用免密登入（BatchMode=yes，禁止密碼互動）**。如果沒設定免密，
server 一啟動工具呼叫就會全部失敗（`Permission denied`），而且**不會彈出密碼提示**。
所以裝完套件、連叢集之前，**務必先手動確認免密可用**：

```bash
# 關鍵：加 -o BatchMode=yes 模擬 hpc-mcp 的連線方式，
# 如果這行能直接回傳 OK（不詢問密碼），代表免密已就緒：
ssh -o BatchMode=yes -o ConnectTimeout=10 username@192.168.12.12 "echo OK"
```

- 能印出 `OK` → 免密已設定，直接繼續第 3 步。
- 回報 `Permission denied (publickey...)` → **還沒免密**，先設定：
  ```bash
  ssh-copy-id username@192.168.12.12   # 會要一次密碼，之後就不用
  ```
  然後重跑上面的 BatchMode 確認指令。
- 回報 `Connection timed out` → 網路不通，先連 VPN / 設定跳板主機（見第 2 節）。

> 用 `~/.ssh/config` 管理連線時，確認指令裡的主機換成 `Host` 別名：
> `ssh -o BatchMode=yes my-hpc "echo OK"`

---

## 2. 準備 SSH：先確保「裸 ssh 能免密登上」

hpc-mcp 底層用的是系統 OpenSSH，**一切 ssh 問題先在終端機裡重現**。先手動測：

```bash
ssh username@192.168.12.12 "echo OK && hostname"
```

三種結果：

| 結果 | 含義 | 怎麼辦 |
|---|---|---|
| 印出 `OK` + 主機名 | 免密 + 網路都通 | 直接進第 3 步 |
| 卡在 `password:` | 沒有免密 | 設定 SSH key：`ssh-copy-id username@192.168.12.12` |
| `Connection timed out` / 100% 丟包 | **網路根本不通** | 見下面「網路不通」 |

### 網路不通（最常見）

`192.168.12.12` 是**內網位址**。如果你目前不在校園網路／沒連 VPN，從外網是連不上的。

- 先確認平常怎麼上的：要不要先連 VPN？要不要走跳板主機？
- 需要 VPN：連上 VPN 再測。
- 需要跳板主機：見第 4 步的 `~/.ssh/config` 設定（ProxyJump）。

### 強烈推薦：用 `~/.ssh/config` 管理連線

把連線細節寫進 config，hpc-mcp 的 `--host` 直接引用別名，最省心：

```sshconfig
# ~/.ssh/config
Host 192.168.12.12
    HostName 192.168.12.12
    User username
    IdentityFile ~/.ssh/id_ed25519
    # 需要跳板主機時打開下面這行（把 jump-host 換成你的跳板）：
    # ProxyJump jump-host
```

設定好後終端機測 `ssh 192.168.12.12 "echo OK"` 能通，hpc-mcp 就用 `--host 192.168.12.12`。

---

## 3. 用 `--check` 驗證（關鍵！不要跳過）

`--check` 會**主動連一次 SSH** 並印出遠端資訊，設定／網路對不對立刻知道：

```bash
hpc-mcp \
  --host 192.168.12.12 \
  --user username \
  --ssh-bin /mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --sftp-bin /mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --root /home/username/alice \
  --local-root "$PWD" \
  --check
```

- 成功：印出 `Configuration OK. Remote probe succeeded:` 及 hostname / 遠端使用者 / Slurm 是否可用。
- 失敗：印出 `Connection check FAILED: ...` 及原因（逾時 / 拒絕 / 認證失敗），據此排查。

> **判斷依據**：`--check` 成功 = 網路和設定都沒問題，之後接 Codex/Reasonix 就能用。
> `--check` 都失敗 = 先去解決 SSH/網路，別急著接客戶端。

---

## 4. 參數說明

> 這一節只列**啟動／自檢相關**的 CLI 參數。完整的設定檔參數（含
> `slurm.*`、`files.*`、`topology.*` 及全部環境變數）見
> [CONFIGURATION.md](CONFIGURATION.md)；**每個 MCP 工具的參數**見
> [TOOLS.md](TOOLS.md)。

| 參數 | 作用 | 環境變數 | 是否必填 |
|---|---|---|---|
| `--host` | HPC 主機（IP 或 `~/.ssh/config` 別名） | `HPC_MCP_HOST` | **必填** |
| `--user` | SSH 登入使用者（這裡是共享帳號） | `HPC_MCP_USER` | 建議填 |
| `--root` | **遠端**沙箱根目錄，Agent 只能動這裡面的檔案 | `HPC_MCP_ROOT` | **必填** |
| `--local-root` | **本機**允許上傳／下載的目錄（預設＝啟動時所在目錄 + `/tmp`，可多個用 `local_roots`） | `HPC_MCP_LOCAL_ROOT` / `HPC_MCP_LOCAL_ROOTS` | 可選 |
| `--port` | SSH 連接埠（預設 22） | `HPC_MCP_PORT` | 可選 |
| `--identity-file` | 私鑰路徑（預設用 `~/.ssh/config`） | `HPC_MCP_IDENTITY_FILE` | 可選 |
| `--ssh-bin` | ssh 執行檔路徑，如 `/usr/bin/ssh`、`@/usr/bin/ssh`、`@/mnt/c/.../ssh.exe` | `HPC_MCP_SSH_BIN` | 可選 |
| `--sftp-bin` | sftp 執行檔路徑（形式同 `--ssh-bin`） | `HPC_MCP_SFTP_BIN` | 可選 |
| `--config` | YAML 設定檔路徑 | — | 可選 |
| `--check` | 只驗證連線性然後結束 | — | 可選 |
| `--log-file` | 日誌追加寫入的檔案（同時仍寫 stderr） | `HPC_MCP_LOG_FILE` | 可選 |
| `--log-level` | 日誌等級：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`（預設 `INFO`） | `HPC_MCP_LOG_LEVEL` | 可選 |
| `--version` | 印出版本並結束 | — | 可選 |

子命令 `hpc-mcp mcp-add`（自動寫入 Codex / Reasonix 的 MCP 設定，冪等，不破壞既有設定）：

| 參數 | 作用 | 預設 |
|---|---|---|
| `--client` | 要更新的客戶端：`codex`、`reasonix`、`all` | `all` |
| `--config` | YAML 設定路徑（其中的 host/root 等會被寫入客戶端 env） | — |
| `--host` / `--user` / `--root` | 直接傳參（不透過設定檔時使用） | — |

**關於 `--local-root`**：它限制 `hpc.files.upload`/`download` 能存取的**本機**目錄範圍，防止 Agent 讀你本機的 `.ssh` 等敏感目錄。一般設為目前專案目錄（`$PWD`）即可。**它不是必填**，不傳就預設目前目錄。

**關於 `--root`**：這是遠端叢集上**專屬於你的子目錄**（因為登入用的是共享帳號 `username`）。你填的 `/home/username/alice` 就是你在这个共享帳號下的個人空間——完全正確。

---

## 5. 用設定檔代替一長串參數（推薦）

把參數固化到 YAML，以後一行指令啟動：

```bash
mkdir -p ~/.config/hpc-mcp
cat > ~/.config/hpc-mcp/192.168.12.12.yaml <<'EOF'
host: 192.168.12.12                                  # ~/.ssh/config 裡的 Host 別名
user: username
root: /home/username/alice
local_root: /home/alice               # 本機允許目錄
ssh_bin: /mnt/c/Windows/System32/OpenSSH/ssh.exe
sftp_bin: /mnt/c/Windows/System32/OpenSSH/sftp.exe

slurm:
  allowed_partitions: [thcp1]               # 改成你叢集真實的分割區名稱！
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"

# 可選：運算節點拓撲探測（hpc.cluster.topo 用）
# topology:
#   enabled: true             # false = 不註冊該工具，永不提交採集作業
#   cache_ttl_seconds: 86400  # 拓撲快取 24h
EOF
```

啟動 / 自檢：

```bash
hpc-mcp --config ~/.config/hpc-mcp/192.168.12.12.yaml --check
```

> 注意：`allowed_partitions` 預設是**空**（= 拒絕一切提交，fail-closed）。
> 務必改成你叢集真實的分割區名稱（用 `sinfo` 在叢集上查）。

> **鍵名規則**：設定檔裡的鍵用**底線**（`ssh_bin`、`local_root`、
> `allowed_partitions`），和 `config/example.yaml` 保持一致。
> 連字號寫法（`ssh-bin`）也會被自動辨識，但不推薦。
> 拼錯的鍵不會報錯，只會印一行
> `Ignoring unknown config key 'xxx'` 警告然後被忽略——看到這行就要改設定。

> **改了原始碼／設定卻沒生效？** 如果你是 `pip install .` 安裝的，
> 虛擬環境裡是一份**複製**，改倉庫原始碼不會自動生效，必須重裝：
> ```bash
> source ~/venvs/hpc-mcp/bin/activate
> cd ~/git_repo/HPC-MCP && pip install .
> # 或裝成可編輯模式，以後改程式碼立刻生效：pip install -e .
> ```

---

## 6. 接入 Codex / Reasonix

`--check` 通過後，註冊到客戶端：

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  -- \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

註冊後客戶端會用 stdio 啟動 `hpc-mcp`，這時你在客戶端裡就能讓它「列出我的專案目錄」「提交一個 Slurm 作業」了。

---

## 7. 「卡住」現象對照表

| 你看到的現象 | 真實原因 | 處理 |
|---|---|---|
| 啟動後停在 `starting: ...` 不動 | **正常**，stdio server 在等客戶端輸入 | 不用管，去客戶端裡呼叫工具；或用 `--check` 驗證 |
| `--check` 回報 `Connection timed out` | **網路不通**（內網 IP 需 VPN） | 連 VPN / 設定跳板主機，再 `ssh` 手動測 |
| 命令列能連、用設定檔卻連不上 | 設定裡的 `ssh_bin` 沒生效（鍵名拼錯 / 裝的是舊程式碼複製），於是回落到 PATH 裡的 `ssh`（WSL 下是 Linux 版 ssh，不走 Windows 的 VPN 路由） | 看日誌裡的 `using ssh executable: ...` 是不是你設定的那個；鍵名改對（見第 5 節），並 `pip install .` 重裝 |
| `--check` 回報 `Permission denied` | 沒免密 | `ssh-copy-id` 設定 key |
| `--check` 回報 `getsockname failed: Not a socket` | SSH 控制 socket 重用異常（常見於 WSL 或舊版安裝） | 更新並重裝 hpc-mcp；新版強制使用獨立的普通 `ssh` 行程 |
| 首次連線問 `Are you sure ... yes/no?` | 主機金鑰沒固定 | 手動 `ssh` 一次輸入 yes；或設定 `strict_host_key_checking: accept-new` |
| 工具呼叫全被拒 `No Slurm partitions are allowed` | 分割區允許清單為空 | 設定 `slurm.allowed_partitions` |
| `pip install .` 得到 `UNKNOWN-0.0.0` | pip/setuptools 太舊 | 用虛擬環境重裝（見第 1 節方案 A） |
| 安裝時提示 `ERROR: pip's dependency resolver does not currently take into account...`（torch/httpcore 衝突） | 系統 `~/.local` 已混裝其他套件 | **不影響 hpc-mcp 執行**；建議改用虛擬環境隔離（第 1 節方案 A） |

---

## 8. 看日誌

- 所有日誌走 **stderr**（stdout 只留給 MCP 協定）。
- 想落盤：`--log-file ~/.local/share/hpc-mcp/hpc-mcp.log`。
- 想看每次呼叫的允許／拒絕：`--log-level DEBUG`。

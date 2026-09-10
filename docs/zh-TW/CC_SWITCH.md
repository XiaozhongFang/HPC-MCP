# CC-Switch 接入設定（MCP JSON）

其他語言：[English](../CC_SWITCH.md) | [简体中文](../zh-CN/CC_SWITCH.md) | [日本語](../ja/CC_SWITCH.md) | [한국어](../ko/CC_SWITCH.md)

[CC-Switch](https://github.com/farion1231/cc-switch) 用 **JSON** 管理多個 MCP 設定
（Claude Code / Codex / Roo Code 等），可以在多個 MCP server 設定間一鍵切換。

hpc-mcp 是 **stdio** 類型的 MCP server：CC-Switch 裡用 `type: "stdio"` +
`command` + `args` + `env` 描述，**不需要** `url`（那是 SSE/HTTP 類型才用的）。

---

## 完整設定範例

在 CC-Switch 中「新增設定」時，貼上下面的 JSON（按你自己的叢集資訊改值）：

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/alice/venvs/hpc-mcp/bin/hpc-mcp",
  "args": ["--config", "/home/alice/.config/hpc-mcp/192.168.12.12.yaml"],
  "env": {
    "HPC_MCP_HOST": "hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice/yourhome",
    "HPC_MCP_LOCAL_ROOT": "/home/alice/project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug",
    "HPC_MCP_SSH_BIN": "/mnt/c/Windows/System32/OpenSSH/ssh.exe",
    "HPC_MCP_SFTP_BIN": "/mnt/c/Windows/System32/OpenSSH/sftp.exe",
    "HPC_MCP_PORT": "22",
    "HPC_MCP_LOG_FILE": "/home/alice/.local/share/hpc-mcp/hpc-mcp.log",
    "HPC_MCP_LOG_LEVEL": "INFO"
  }
}
```

> 若你的環境裡 `hpc-mcp` 已裝在 conda/venv 且不在 PATH，可把 `command`
> 直接指向執行檔全路徑，例如：
> `"/home/alice/venvs/hpc-mcp/bin/hpc-mcp"`。
> 倉庫內的 `scripts/hpc-mcp-run` 啟動指令稿會自動定位本機安裝，最可攜。

---

## 欄位說明

| 欄位 | 是否必填 | 說明 |
|---|---|---|
| `name` | 必填 | server 名稱，CC-Switch 裡顯示的名字，可隨意（如 `hpc-mcp`） |
| `type` | 必填 | 固定 `"stdio"`（hpc-mcp 透過標準輸入輸出與客戶端通訊） |
| `command` | 必填 | 啟動指令。推薦指向 `<倉庫>/scripts/hpc-mcp-run`（自動定位安裝的 hpc-mcp），或 hpc-mcp 執行檔的絕對路徑 |
| `args` | 可選 | 附加命令列參數，如 `["--check"]` 只做連線性自檢；正常執行留 `[]` 或不寫 |
| `env` | 必填（至少 host/root） | 注入的環境變數，見下 |

### env 常用變數（與 README「設定」一節完全一致）

| 變數 | 是否必填 | 作用 |
|---|---|---|
| `HPC_MCP_HOST` | **必填** | HPC 主機（IP 或 `~/.ssh/config` 的 `Host` 別名），如 `my-hpc` |
| `HPC_MCP_ROOT` | **必填** | **遠端**沙箱根目錄，Agent 只能操作該目錄下的檔案（如 `/home/shared_account/alice`） |
| `HPC_MCP_USER` | 建議 | SSH 登入使用者（共享帳號） |
| `HPC_MCP_LOCAL_ROOT` | 可選 | **本機**允許上傳／下載的目錄（多個用逗號分隔 → `HPC_MCP_LOCAL_ROOTS`）；不填預設＝啟動時目錄 + `/tmp` |
| `HPC_MCP_ALLOWED_PARTITIONS` | 建議 | 允許的 Slurm 分割區，逗號分隔。**預設空 = 拒絕一切提交（fail-closed）** |
| `HPC_MCP_PORT` | 可選 | SSH 連接埠（預設 22） |
| `HPC_MCP_IDENTITY_FILE` | 可選 | 私鑰路徑（預設走 `~/.ssh/config`） |
| `HPC_MCP_SSH_BIN` | 可選 | ssh 執行檔，如 `/usr/bin/ssh`、`@/mnt/c/Windows/System32/OpenSSH/ssh.exe`（WSL 需要時） |
| `HPC_MCP_SFTP_BIN` | 可選 | sftp 執行檔（形式同 `HPC_MCP_SSH_BIN`） |
| `HPC_MCP_LOG_FILE` | 可選 | 日誌落盤路徑 |
| `HPC_MCP_LOG_LEVEL` | 可選 | `DEBUG`/`INFO`/`WARNING`/`ERROR`，預設 `INFO` |

> 更多參數（Slurm 資源上限、shell 允許清單、檔案預算、快取 TTL 等）屬於
> **設定檔／CLI** 範疇，CC-Switch 的 `env` 只涵蓋上面這些；完整清單見
> [`config/example.yaml`](../../config/example.yaml) 與 [`CONFIGURATION.md`](CONFIGURATION.md)，
> 手把手流程見 [`QUICKSTART.md`](QUICKSTART.md)。

---

## 使用前必做

1. **設定好免密登入**：hpc-mcp 強制 `BatchMode=yes`，不彈密碼。先手動驗證：
   ```bash
   ssh -o BatchMode=yes my-hpc "echo OK"
   ```
   能直接回傳 `OK` 才能用；否則先 `ssh-copy-id my-hpc`。
2. **確認網路通**：內網 IP（如 `192.168.x.x`）需先連 VPN 或設定 `~/.ssh/config`
   `ProxyJump` 跳板主機。
3. **建議先自檢**：`env` 裡加 `"HPC_MCP_HOST"` 等設定後，可暫時用
   `command` + `args: ["--check"]` 驗證連線性，成功再切換回正常模式。

---

## 常見問題

| 現象 | 原因與處理 |
|---|---|
| 啟動後停在 `starting: ...` 不動 | **正常**：stdio server 在等客戶端輸入，去客戶端裡呼叫工具即可 |
| 工具全部 DENY `No Slurm partitions are allowed` | `HPC_MCP_ALLOWED_PARTITIONS` 沒設或為空 → 填上真實分割區名稱（叢集上 `sinfo` 查詢） |
| 連線回報 `Permission denied` | 沒設免密 → `ssh-copy-id` |
| 回報 `Connection timed out` | 網路不通（內網需 VPN／跳板主機） |
| WSL 下連不上但命令列能連 | `HPC_MCP_SSH_BIN`/`HPC_MCP_SFTP_BIN` 需指向 Windows 的 OpenSSH（`/mnt/c/...`） |
| 首次連線問 `Are you sure ... yes/no?` | 主機金鑰未固定 → 手動 `ssh` 一次確認，或設定 `strict_host_key_checking: accept-new` |

完整排查表見 [QUICKSTART.md](QUICKSTART.md) 第 7 節。

---

## 關聯檔案

- 專案層級標準設定：[`.mcp.json`](../../.mcp.json)（MCP 標準格式，`mcpServers` 物件）
- 參數權威說明：[`config/example.yaml`](../../config/example.yaml)
- 安裝與排障：[`QUICKSTART.md`](QUICKSTART.md)

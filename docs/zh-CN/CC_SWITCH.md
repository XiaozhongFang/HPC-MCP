# CC-Switch 接入配置（MCP JSON）

其他语言：[English](../CC_SWITCH.md) | [日本語](../ja/CC_SWITCH.md) | [한국어](../ko/CC_SWITCH.md) | [繁體中文](../zh-TW/CC_SWITCH.md)

[CC-Switch](https://github.com/farion1231/cc-switch) 用 **JSON** 管理多个 MCP 配置
（Claude Code / Codex / Roo Code 等），可以在多个 MCP server 配置间一键切换。

hpc-mcp 是 **stdio** 类型的 MCP server：CC-Switch 里用 `type: "stdio"` +
`command` + `args` + `env` 描述，**不需要** `url`（那是 SSE/HTTP 类型才用的）。

---

## 完整配置示例

在 CC-Switch 中“新增配置”时，粘贴下面的 JSON（按你自己的集群信息改值）：

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

> 若你的环境里 `hpc-mcp` 已装在 conda/venv 且不在 PATH，可把 `command`
> 直接指向可执行文件全路径，例如：
> `"/home/alice/venvs/hpc-mcp/bin/hpc-mcp"`。
> 仓库内的 `scripts/hpc-mcp-run` 启动脚本会自动定位本机安装，最可移植。

---

## 字段说明

| 字段 | 是否必填 | 说明 |
|---|---|---|
| `name` | 必填 | server 名称，CC-Switch 里显示的名字，可随意（如 `hpc-mcp`） |
| `type` | 必填 | 固定 `"stdio"`（hpc-mcp 通过标准输入输出与客户端通信） |
| `command` | 必填 | 启动命令。推荐指向 `<仓库>/scripts/hpc-mcp-run`（自动定位安装的 hpc-mcp），或 hpc-mcp 可执行文件的绝对路径 |
| `args` | 可选 | 附加命令行参数，如 `["--check"]` 只做连通性自检；正常运行留 `[]` 或不写 |
| `env` | 必填（至少 host/root） | 注入的环境变量，见下 |

### env 常用变量（与 README「配置」一节完全一致）

| 变量 | 是否必填 | 作用 |
|---|---|---|
| `HPC_MCP_HOST` | **必填** | HPC 主机（IP 或 `~/.ssh/config` 的 `Host` 别名），如 `my-hpc` |
| `HPC_MCP_ROOT` | **必填** | **远程**沙箱根目录，Agent 只能操作该目录下的文件（如 `/home/shared_account/alice`） |
| `HPC_MCP_USER` | 建议 | SSH 登录用户（共享账号） |
| `HPC_MCP_LOCAL_ROOT` | 可选 | **本地**允许上传/下载的目录（多个用逗号分隔 → `HPC_MCP_LOCAL_ROOTS`）；不填默认=启动时目录 + `/tmp` |
| `HPC_MCP_ALLOWED_PARTITIONS` | 建议 | 允许的 Slurm 分区，逗号分隔。**默认空 = 拒绝一切提交（fail-closed）** |
| `HPC_MCP_PORT` | 可选 | SSH 端口（默认 22） |
| `HPC_MCP_IDENTITY_FILE` | 可选 | 私钥路径（默认走 `~/.ssh/config`） |
| `HPC_MCP_SSH_BIN` | 可选 | ssh 可执行文件，如 `/usr/bin/ssh`、`@/mnt/c/Windows/System32/OpenSSH/ssh.exe`（WSL 需要时） |
| `HPC_MCP_SFTP_BIN` | 可选 | sftp 可执行文件（形式同 `HPC_MCP_SSH_BIN`） |
| `HPC_MCP_LOG_FILE` | 可选 | 日志落盘路径 |
| `HPC_MCP_LOG_LEVEL` | 可选 | `DEBUG`/`INFO`/`WARNING`/`ERROR`，默认 `INFO` |

> 更多参数（Slurm 资源上限、shell 白名单、文件预算、缓存 TTL 等）属于
> **配置文件/CLI** 范畴，CC-Switch 的 `env` 只覆盖上面这些；完整清单见
> [`config/example.yaml`](../../config/example.yaml) 与 [`QUICKSTART.md`](QUICKSTART.md)。

---

## 使用前必做

1. **配好免密登录**：hpc-mcp 强制 `BatchMode=yes`，不弹密码。先手动验证：
   ```bash
   ssh -o BatchMode=yes my-hpc "echo OK"
   ```
   能直接返回 `OK` 才能用；否则先 `ssh-copy-id my-hpc`。
2. **确认网络通**：内网 IP（如 `192.168.x.x`）需先连 VPN 或配 `~/.ssh/config`
   `ProxyJump` 跳板机。
3. **建议先自检**：`env` 里加 `"HPC_MCP_HOST"` 等配置后，可临时用
   `command` + `args: ["--check"]` 验证连通性，成功再切换回正常模式。

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 启动后停在 `starting: ...` 不动 | **正常**：stdio server 在等客户端输入，去客户端里调用工具即可 |
| 工具全部 DENY `No Slurm partitions are allowed` | `HPC_MCP_ALLOWED_PARTITIONS` 没配或为空 → 填上真实分区名（集群上 `sinfo` 查询） |
| 连接报 `Permission denied` | 没配免密 → `ssh-copy-id` |
| 报 `Connection timed out` | 网络不通（内网需 VPN/跳板机） |
| WSL 下连不上但命令行能连 | `HPC_MCP_SSH_BIN`/`HPC_MCP_SFTP_BIN` 需指向 Windows 的 OpenSSH（`/mnt/c/...`） |
| 首次连接问 `Are you sure ... yes/no?` | 主机密钥未固定 → 手动 `ssh` 一次确认，或配置 `strict_host_key_checking: accept-new` |

完整排查表见 [QUICKSTART.md](QUICKSTART.md) 第 7 节。

---

## 关联文件

- 项目级标准配置：[`.mcp.json`](../../.mcp.json)（MCP 标准格式，`mcpServers` 对象）
- 参数权威说明：[`config/example.yaml`](../../config/example.yaml)
- 安装与排障：[`QUICKSTART.md`](QUICKSTART.md)

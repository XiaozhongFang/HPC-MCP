# 配置文件参考（config YAML）

本文档列出 **YAML 配置文件支持的全部参数**：位置、类型、默认值、取值范围、
对应的环境变量（`HPC_MCP_*`）与 CLI 选项。`config/example.yaml` 是一份可直接
复制使用的完整示例。

> **优先级（从高到低）**：CLI 选项 > 环境变量 > 配置文件 > 内置默认值。
> 同一参数在多个位置出现时，高优先级者生效。

> **键名规则**：配置文件使用**下划线**键（`local_root`、`allowed_partitions`、
> `ssh_bin`）。连字符写法（`local-root`、`ssh-bin`）会被自动识别为别名，但不推荐。
> 未识别的键不会报错，只会打一行
> `Ignoring unknown config key 'xxx'` 警告后忽略——看到这行就说明有拼写错误。

> 本文件说明**服务端配置参数**。启动/自检用的 CLI 参数见
> [QUICKSTART.md](QUICKSTART.md) 第 4 节；**每个 MCP 工具的参数**见 [TOOLS.md](TOOLS.md)。

---

## 顶层参数

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `host` | string | —（必填） | SSH 主机；推荐用 `~/.ssh/config` 的 `Host` 别名。环境变量 `HPC_MCP_HOST`，CLI `--host` |
| `user` | string | — | SSH 用户（可为共享账号）。`HPC_MCP_USER`，`--user` |
| `port` | int | `22` | SSH 端口（1–65535）。`HPC_MCP_PORT`，`--port` |
| `root` | string | —（必填） | 远程**用户专属**根目录，Agent 的所有远程操作被限制在此目录内。必须是绝对路径，且不能是 `/`。`HPC_MCP_ROOT`，`--root` |
| `local_root` | string | 当前工作目录 | 本地上传/下载允许的目录（可多个，见 `local_roots`）。不能是 `.ssh`/`.gnupg` 等凭据目录。`HPC_MCP_LOCAL_ROOT`，`--local-root` |
| `local_roots` | list[string] | `[当前目录, 系统临时目录]` | 本地允许目录列表；与 `local_root` 二选一，同时出现时 `local_roots` 优先。`HPC_MCP_LOCAL_ROOTS`（逗号分隔） |
| `identity_file` | string | 来自 `~/.ssh/config` | SSH 私钥路径（Agent 永不接触私钥内容）。`HPC_MCP_IDENTITY_FILE`，`--identity-file` |
| `ssh_bin` | string | PATH 中查找 | ssh 可执行文件：纯名字、绝对路径、或 `@` 前缀（WSL 用）。`HPC_MCP_SSH_BIN`，`--ssh-bin` |
| `sftp_bin` | string | PATH 中查找 | sftp 可执行文件，形式同 `ssh_bin`。`HPC_MCP_SFTP_BIN`，`--sftp-bin` |
| `wait_max_seconds` | int | `3600` | `hpc.slurm.wait` / `wait_and_diagnose` 的最大等待秒数（≤ 7 天）。`HPC_MCP_WAIT_MAX_SECONDS` |
| `cache_ttl_seconds` | float | `2.0` | 只读查询去重缓存 TTL（秒）；`0` 禁用。`HPC_MCP_CACHE_TTL_SECONDS` |
| `log_file` | string | 仅 stderr | 追加写日志的文件路径（`~` 会展开）。`HPC_MCP_LOG_FILE`，`--log-file` |
| `log_level` | string | `INFO` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`。`HPC_MCP_LOG_LEVEL`，`--log-level` |

> `ssh.*` 子段参数也可直接写在顶层（如 `connect_timeout`、`strict_host_key_checking`），
> 与 `ssh:` 子段等价。

---

## `ssh:` 子段

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `host` | string | — | 同顶层 `host` |
| `port` | int | `22` | 同顶层 `port` |
| `user` | string | — | 同顶层 `user` |
| `identity_file` | string | — | 同顶层 `identity_file` |
| `connect_timeout` | int | `15` | SSH 建连超时（秒，≤ 3600）。`HPC_MCP_CONNECT_TIMEOUT` |
| `command_timeout` | int | `30` | 单条远程命令超时（秒，≤ 86400）。`HPC_MCP_COMMAND_TIMEOUT` |
| `strict_host_key_checking` | string | `yes` | 主机密钥校验：`yes`（要求 `known_hosts` 已固定，推荐）/ `accept-new`（首次自动登记）。**拒绝 `no`**。`HPC_MCP_STRICT_HOST_KEY_CHECKING` |
| `ssh_bin` | string | PATH | 同顶层 `ssh_bin` |
| `sftp_bin` | string | PATH | 同顶层 `sftp_bin` |

---

## `slurm:` 子段

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `allowed_partitions` | list[string] | `[]`（空 = 拒绝一切提交，fail-closed） | 允许提交的分区白名单。名称不能含 `/` 或控制字符。`HPC_MCP_ALLOWED_PARTITIONS`（逗号分隔） |
| `max_nodes` | int | `2` | 单作业节点数上限（≥ 1）。`HPC_MCP_MAX_NODES` |
| `max_cpus` | int | `64` | 单作业总 CPU 上限：`nodes × ntasks × cpus_per_task`（≥ 1）。`HPC_MCP_MAX_CPUS` |
| `max_memory_mb` | int | `262144`（256 GiB） | 单作业内存上限（MiB，≥ 1）。`HPC_MCP_MAX_MEMORY_MB` |
| `max_gpus` | int | `4` | 单作业 GPU 上限（≥ 0）。`HPC_MCP_MAX_GPUS` |
| `max_time` | string | `"24:00:00"` | 单作业时长上限。Slurm 格式：`HH:MM:SS`、`D-HH:MM:SS`；天数可溢出（`"2-24:00:00"` = 3 天）。`HPC_MCP_MAX_TIME` |
| `max_concurrent_jobs` | int | `20` | **同时活跃的最大作业数**。提交时检查该实例注册表里「squeue 或 sacct 均非终止态」的作业数，达到上限即拒绝新提交并提示等待。已结束（`COMPLETED`/`FAILED`/`CANCELLED`/…）的作业自动释放名额；squeue 查询成功但作业已不在队列、且 sacct 也已无记录时同样释放（避免 accounting 记录过期后永久占满配额）；squeue 查询失败时保守地继续计数（fail-closed）。`HPC_MCP_MAX_CONCURRENT_JOBS` |

> 所有 `max_*` 数字字段都支持简单算术表达式：`+ - * /` 与括号，如 `"4*16"`、`"128/4"`。
> 环境变量中同样支持（如 `HPC_MCP_MAX_CPUS=4*16`）。

---

## `shell:` 子段

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `safe_commands` | list[string] | `[]`（内置白名单之上追加） | 追加到内置最小白名单的允许命令 basename（不含 `/`、≤ 128 字符）。`HPC_MCP_SAFE_COMMANDS`（逗号分隔） |
| `max_exec_seconds` | int | `30` | 单条 safe 命令执行时长上限（秒，≤ 86400）。`HPC_MCP_SHELL_MAX_EXEC_SECONDS` |
| `max_output_bytes` | int | `1048576`（1 MiB） | safe 命令单次输出上限（字节）。`HPC_MCP_MAX_OUTPUT_BYTES` |

---

## `files:` 子段

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_read_bytes` | int | `1048576`（1 MiB） | `hpc.files.read` 单次读取的**硬上限**（字节）。`HPC_MCP_MAX_READ_BYTES` |
| `max_read_slice_bytes` | int | `262144`（256 KiB） | 单次 `hpc.files.read` 返回上限（bounded slice），防止 Agent 一次翻页读完整份大日志。`HPC_MCP_MAX_READ_SLICE_BYTES` |
| `max_write_bytes` | int | `10485760`（10 MiB） | `hpc.files.write` 单次写入上限（字节）。`HPC_MCP_MAX_WRITE_BYTES` |
| `max_list_entries` | int | `2000` | `hpc.files.list` 单次返回条目上限。`HPC_MCP_MAX_LIST_ENTRIES` |
| `max_recursive_depth` | int | `3` | `hpc.files.list recursive` 递归深度上限（≤ 64）。`HPC_MCP_MAX_RECURSIVE_DEPTH` |
| `search_max_matches` | int | `200` | `hpc.files.search` 单个文件最多匹配数（≤ 100000）。`HPC_MCP_SEARCH_MAX_MATCHES` |
| `search_max_context_lines` | int | `10` | 每个匹配附带的上下文行数（≤ 1000）。`HPC_MCP_SEARCH_MAX_CONTEXT_LINES` |
| `search_max_scan_bytes` | int | `67108864`（64 MiB） | 单文件搜索扫描上限（字节）。`HPC_MCP_SEARCH_MAX_SCAN_BYTES` |
| `search_max_files` | int | `1000` | 一次搜索最多扫描文件数（≤ 1000000）。`HPC_MCP_SEARCH_MAX_FILES` |
| `search_max_depth` | int | `6` | 搜索递归深度上限（≤ 64）。`HPC_MCP_SEARCH_MAX_DEPTH` |
| `search_timeout` | int | `5` | 远端单次搜索超时（秒，≤ 3600）。`HPC_MCP_SEARCH_TIMEOUT` |

---

## `topology:` 子段

控制 `hpc.cluster.topo`（计算节点硬件/NUMA/SIMD 拓扑发现）。该工具第一次调用
（或缓存过期、或 `refresh=true`）会在指定分区提交**一个 1 核的采集作业**，
在计算节点上读取 `lscpu`/`/proc/cpuinfo`/`numactl --hardware`/`/proc/meminfo`，
并与登录节点 `sinfo` 的分区/节点视图合并，返回 CPU 型号、sockets/cores/threads、
SIMD 指令集、NUMA 域与距离、cache 层级、节点内存，以及推导出的并行参数建议
（`ntasks_per_node`、`cpus_per_task`、`--cpu-bind`/`--hint`、`OMP_NUM_THREADS`）。

采集脚本是**服务端固定内容**：Agent 提供的任何参数都不会写进脚本，脚本也
**不会**调用 `squeue`/`sacct`/`scontrol`（共享账号隔离），只读节点本地硬件事实。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否注册 `hpc.cluster.topo`。设为 `false` 时该工具不暴露给 Agent，也就永远不会提交采集作业。`HPC_MCP_TOPOLOGY_ENABLED` |
| `cache_ttl_seconds` | int | `86400`（24 h） | 拓扑结果的新鲜期：进程内缓存 + 远端 `$ROOT/.hpc-mcp/topo/topology_<partition>.json` 共用该 TTL，避免重复排队。`0` = 每次调用都重新采集。`HPC_MCP_TOPOLOGY_CACHE_TTL_SECONDS` |
| `wait_seconds` | int | `300` | 等待采集作业结束的秒数；超时返回 `status: "pending"` 与 `job_id`（作业继续排队，下次调用复用它，不会重复提交）。`HPC_MCP_TOPOLOGY_WAIT_SECONDS` |
| `collect_time_limit` | string | `"00:03:00"` | 采集作业自身的 Slurm 时长上限（格式同 `slurm.max_time`）。`HPC_MCP_TOPOLOGY_COLLECT_TIME_LIMIT` |

> 采集作业同样受 `slurm.allowed_partitions`、`max_concurrent_jobs` 与作业归属
> 规则约束；`allowed_partitions` 为空时该工具 fail-closed（拒绝调用）。
> 探到的分区名会用于 `sinfo -p <分区>` 与缓存文件名，因此比通用 Slurm 策略更严：
> 只接受 `[A-Za-z0-9_.-]`。

---

## 完整示例

```yaml
# HPC-MCP 完整配置示例（所有键均为可选，除非标注"必填"）
host: my-hpc                      # 必填：SSH Host 别名或地址
user: shared_account
port: 22
root: /home/shared_account/alice  # 必填：远程用户专属根目录
local_root: /home/alice/proj      # 本地上传/下载目录
# local_roots: [ /home/alice/proj, /tmp ]
# identity_file: ~/.ssh/id_ed25519
# ssh_bin: /usr/bin/ssh
# sftp_bin: @/mnt/c/Windows/System32/OpenSSH/ssh.exe

ssh:
  connect_timeout: 15
  command_timeout: 30
  strict_host_key_checking: "yes"   # 或 "accept-new"；拒绝 "no"

slurm:
  allowed_partitions: [compute]     # 必配：默认空 = 拒绝一切提交
  max_nodes: 2
  max_cpus: 64                      # 支持表达式如 "4*16"
  max_memory_mb: 262144             # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20           # 同时活跃作业上限，默认 20

shell:
  safe_commands: []                 # 追加允许的命令 basename
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
  enabled: true                     # false = 不注册 hpc.cluster.topo（永不提交采集作业）
  cache_ttl_seconds: 86400          # 拓扑新鲜期（24h）；0 = 每次都重新采集
  wait_seconds: 300                 # 采集作业排队等待上限，超时返回 pending
  collect_time_limit: "00:03:00"    # 采集作业自身的 Slurm 时长上限

wait_max_seconds: 3600
cache_ttl_seconds: 2.0
log_file: ~/.local/share/hpc-mcp/hpc-mcp.log
log_level: INFO
```

# 校验配置 + 连通性
```bash
source ~/venvs/hpc-mcp/bin/activate
hpc-mcp --config ~/.config/hpc-mcp/config.yaml --check
```

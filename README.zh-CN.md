# HPC-MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP stdio server](https://img.shields.io/badge/MCP-stdio%20server-6f42c1.svg)](https://modelcontextprotocol.io)

[English](README.md) | **简体中文** | [日本語](README.ja.md) | [한국어](README.ko.md) | [繁體中文](README.zh-TW.md)

一个**安全优先**的 MCP Server，让 Codex、Reasonix 等 Coding Agent 通过 SSH + Slurm 安全地操作远程 HPC 集群——路径沙箱、登录节点命令白名单、Slurm 资源上限、作业归属、完整 ALLOW/DENY 审计，全部由代码强制。

> **第一次用？先看 → [docs/zh-CN/QUICKSTART.md](docs/zh-CN/QUICKSTART.md)**：手把手的安装、每个参数的说明、完整的 Codex/Reasonix 配置示例，以及“启动后卡住”“网络不通”“pip 装成 UNKNOWN”等常见问题的排查表。

## 架构

```text
Codex / Reasonix (Agent)
        │  MCP (stdio)
        ▼
  Project Skill            ← 行为指南（非安全边界）
        │
        ▼
  HPC MCP Server           ← 安全边界（代码级强制）
        │
        ├── Path Sandbox        （USER_ROOT 强制）
        ├── Command Policy      （login node 白名单）
        ├── Slurm Resource Policy（分区/资源/并发限制）
        ├── SSH Manager         （固定 argv，无本地 shell）
        ├── Job Tracker         （共享账号下的作业归属）
        └── Audit Logger        （每次调用 ALLOW/DENY 审计）
        │ SSH / SFTP
        ▼
  HPC Login Node  ──只允许轻量查询──┐
        │ sbatch                   │
        ▼                          ▼
  Compute Node (Slurm)      用户工作目录 $HPC_MCP_ROOT
        │
   Julia / MOOSE / Python / CMake
```

设计原则：**MCP 是安全边界；Skill 只是行为指南**。即使 Agent 提示词错误、Skill 被误解，核心权限边界也无法被绕过。

## 安全模型

### 共享账号隔离

HPC 常使用共享账号（如 `/home/shared_account/`）。该 home 目录**不等于**用户自己的目录。必须配置：

```
HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD       # 上传/下载允许访问的本地目录
```

所有远程文件操作都被限制在该 root 之下（含符号链接 canonical 校验）。本地 `upload`/`download` 同样被限制在 `local_roots`（一个或多个本地允许目录，默认 = 启动进程当前目录 + 系统临时目录 `/tmp`），拒绝 `.ssh`、私钥和符号链接路径。其他用户的目录（`/home/shared_account/other_user`）、系统目录（`/etc`、`/opt`）一律拒绝。

### Login Node 策略

登录节点只允许轻量、只读的管理命令（白名单制）：`ls`、`find`、`cat`、`grep`、`head`、`tail`、`git status/diff/log`、`module list/avail` 等。为防止共享账号下查看其他使用者的作业，`squeue`、`sacct`、`scontrol` 不再通过 `hpc.shell.run_safe` 暴露，只能使用带归属检查的 Slurm 工具。

**永远拒绝**在登录节点执行计算与编译：`julia`、`python`、`make`、`cmake --build`、`ninja`、`mpirun`、`srun`、`pytest`、`matlab`、GPU 程序等——全部引导至 `hpc.slurm.submit`。

命令策略在**代码层面**拒绝：shell 元字符（`;`、`&&`、`||`、`|`、`>`、`<`、`$()`、反引号、`&`）、路径形式的任意可执行文件（`./program`）、`find -exec`、`git -c`、嵌套 shell、`sudo`/`ssh`/`curl` 等危险程序。解析失败同样拒绝（fail-closed）。

`env`/`printenv` 即使被请求，也只在清空后的最小环境中运行；不会返回 SSH token、密钥或集群凭证。所有命令的路径操作数会再次执行远程 `realpath` 校验，并拒绝跟随符号链接的选项。

### Slurm 资源策略

`hpc.slurm.submit` 是唯一计算入口。Server 端强制：

- 分区白名单（默认空 = 拒绝一切；agent 只能选用白名单内的分区）
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs`（并发上限）
- 工作目录必须位于 USER_ROOT 内
- 作业 stdout/stderr 固定捕获到 `$ROOT/.hpc-mcp/jobs/<job-id>/`

### 作业归属（共享账号）

共享账号下 Unix 用户无法区分不同使用者。每个 MCP 实例只管理**自己提交并登记**的作业（`$ROOT/.hpc-mcp/tracked_jobs.json`）。对其他作业的 `status/output/cancel/accounting` 一律拒绝。

### 失败安全（fail-closed）

配置缺失、路径无法解析、命令解析失败、分区不确定、SSH 异常等任何不确定情况统一 **DENY**，绝不回退到无限制 shell。

### 三层安全边界

| 层 | 机制 | 防护目标 |
|---|---|---|
| Layer 1: MCP policy | 路径沙箱、命令白名单、Slurm 资源策略、作业归属、审计 | Agent 越权/乱来 |
| Layer 2: Slurm | 分区白名单、资源上限、并发上限、`sbatch` 入口唯一 | 计算资源滥用 |
| Layer 3: OS/集群 | 独立 Unix UID / 作业隔离 / filesystem ACL / 容器 | **恶意代码隔离**（可选） |

> **残余风险（必须知晓）**：在共享 Unix UID 下，MCP 只能保证"Agent 不乱来"（Layer 1/2），**无法**保证提交到计算节点的恶意代码不访问同 UID 能访问的其他数据（Layer 3）。真正敌对的代码隔离需要独立 UID、Slurm 作业隔离 + filesystem ACL，或集群容器/沙箱。不要把 Python 侧的正则/策略当作对恶意代码的 OS 级隔离。

### 查询成本与去重

- `hpc.files.read` 是 **bounded slice**：单次最多 `min(max_bytes, files.max_read_slice_bytes)`（默认 256KiB）字节，不再引导 Agent 从 offset=0 读到 EOF。
- `hpc.files.list` 递归列举**逐层分页**（`page_size` + `next_cursor`），远端 `head` 截断，绝不整树扫描后丢弃。
- `hpc.files.search` 带硬预算（`max_matches`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`，全部服务端钳制），用于"先定位再精读"。
- 只读幂等查询按 `tool+参数` 去重（TTL 默认 2 秒，`cache_ttl_seconds` 可调，0 禁用）；任何写操作主动失效缓存。
- `hpc.slurm.queue/status` 只查本实例跟踪的 job ID（`squeue -j <ids>`），**绝不**扫描共享账号的全队列。
- `hpc.shell.run_safe` 的所有命令（含白名单内合法命令）都有 **command cost 预算**：`find`/`du`/`sort`/`git grep` 等高风险命令被钳制在更短 timeout + 输出上限内。
- 返回给 Agent 的内容（日志/文件内容）经**最小限度 secret 脱敏**（`password=`/`token=`/`Bearer`/AWS/PEM 私钥块等），与审计日志脱敏分开治理，不破坏科研日志。

## 安装

详细安装流程见 [docs/zh-CN/QUICKSTART.md](docs/zh-CN/QUICKSTART.md)

安装后得到 `hpc-mcp` 命令。

## 配置

三种方式，优先级 **CLI > 环境变量 > 配置文件 > 默认值**。

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account \
  --root /home/shared_account/alice --local-root "$PWD"

# 指定 ssh/sftp 可执行文件（WSL 环境需要时）：
#   --ssh-bin  /usr/bin/ssh
#   --ssh-bin  @/usr/bin/ssh
#   --ssh-bin  @/mnt/c/Windows/System32/OpenSSH/ssh.exe
#   --sftp-bin @/usr/bin/sftp
```

### 环境变量

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML 配置文件

完整参数说明见 [`docs/zh-CN/CONFIGURATION.md`](docs/zh-CN/CONFIGURATION.md)（所有支持的键、
默认值、取值范围、对应环境变量）；这里是常用最小配置：

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
  max_concurrent_jobs: 20    # 同时活跃作业上限（默认 20，可调）
```

```bash
hpc-mcp --config config.yaml
```

### SSH 配置（推荐）

使用 `~/.ssh/config` 管理连接细节，`HPC_MCP_HOST` 直接引用 Host 别名：

```sshconfig
Host my-hpc
    HostName hpc.example.edu
    User shared_account
    IdentityFile ~/.ssh/id_ed25519
```

Agent 永远不会接触私钥内容。

**必须配置免密登录**（hpc-mcp 强制 `BatchMode=yes`，不弹密码）。配置前先用
BatchMode 确认免密已就绪，否则所有工具调用都会报 `Permission denied`：

```bash
ssh -o BatchMode=yes my-hpc "echo OK"   # 能直接返回 OK 才代表免密可用
ssh-copy-id my-hpc                      # 若上面失败，先配免密（要一次密码）
```

默认 `StrictHostKeyChecking=yes`：首次连接请先手动 `ssh my-hpc` 确认主机指纹并写入 `known_hosts`；如确需首次自动登记，可在配置中设 `ssh.strict_host_key_checking: accept-new`。

### 连通性自检

```bash
hpc-mcp --host my-hpc --root /home/shared_account/alice --check
```

## MCP 客户端集成

### 推荐：一条命令自动注册（`hpc-mcp mcp-add`）

安装好之后，用 `mcp-add` 自动把 hpc-mcp 注册进 Codex 和 Reasonix 的配置。
它写入的是**可移植的启动脚本路径**（`<repo>/scripts/hpc-mcp-run`），该脚本
自动定位本机的 hpc-mcp（conda/venv/PATH），因此**不绑定**某台机器的
conda 路径，换机器后重新 clone + 安装即可：

```bash
# 在仓库目录内运行（会找到仓库的 scripts/hpc-mcp-run）
cd ~/git_repo/HPC-MCP
hpc-mcp mcp-add --config ~/.config/hpc-mcp/192.168.10.10.yaml

# 或直接传参
hpc-mcp mcp-add --host my-hpc --user shared_account --root /home/shared_account/alice
```

效果：
- `~/.codex/config.toml` 写入 `[mcp_servers.hpc]`（command 指向 `scripts/hpc-mcp-run`）
- `~/.reasonix/config.toml` 写入 hpc plugin（同样指向启动脚本）
- 启动脚本按顺序定位 hpc-mcp：`$HPC_MCP_BIN` → PATH → 常见 conda/venv 路径；
  找不到时给出清晰提示而不是静默失败
- 只新增/更新 hpc 段，**不破坏**你已有的其它 MCP server / provider 配置
- 幂等：重复运行不会产生重复段

改完后**重启 Codex / Reasonix** 即可。

### CC-Switch（MCP 配置管理器）

[CC-Switch](https://github.com/farion1231/cc-switch) 用 JSON 管理多个 MCP
server 配置并支持一键切换。完整的 stdio JSON 配置（`command` +
`env`）、字段说明与常见问题见 **[`docs/zh-CN/CC_SWITCH.md`](docs/zh-CN/CC_SWITCH.md)**：

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

### 项目级 `.mcp.json`（最可移植）

仓库自带一份 `.mcp.json` 模板（MCP 标准项目级配置）。在其中新增一个 `hpc`
条目，command 指向 `./scripts/hpc-mcp-run`；支持项目级 MCP 的客户端（如从仓库
目录启动的 Codex/Reasonix）会自动加载，host/root 等通过环境变量注入
（`${HPC_MCP_HOST}` 等，在 shell profile 定义）。换机器只需：
clone 仓库 → 安装 hpc-mcp → 定义环境变量 → 从仓库目录启动客户端。

### 手动方式（可选）

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

两者都是 stdio argv 方式启动，无需 shell。注意手动方式里 `hpc-mcp` 若
不在 PATH，需换成绝对路径（如 `/path/to/conda/envs/hpc-mcp/bin/hpc-mcp`）。

## 工具清单（22 个）

> **每个工具的参数、默认值、约束与返回要点**见 **[docs/zh-CN/TOOLS.md](docs/zh-CN/TOOLS.md)**；
> 下表只是索引。

### 低级原语

| 工具 | 说明 | annotations |
|---|---|---|
| `hpc.info` | 连接/集群信息（本地路径不暴露） | readOnly |
| `hpc.files.list` | 列目录（bounded 分页，recursive 逐层 + cursor） | readOnly |
| `hpc.files.read` | 读文件（bounded slice，单次 ≤256KiB） | readOnly |
| `hpc.files.write` | 写文件（支持 expected_size/mtime/hash 乐观并发保护） | destructive |
| `hpc.files.mkdir` | 建目录 | — |
| `hpc.files.delete` | 删除 | destructive |
| `hpc.files.upload` | 本地上传（SFTP） | destructive |
| `hpc.files.download` | 下载到本地（SFTP） | readOnly |
| `hpc.shell.run_safe` | 白名单轻量命令（带 command cost 预算） | readOnly |
| `hpc.slurm.submit` | 提交计算作业 | openWorld |
| `hpc.slurm.status` | 作业状态 | readOnly |
| `hpc.slurm.queue` | 我的作业队列 | readOnly |
| `hpc.slurm.output` | 作业 stdout/stderr | readOnly |
| `hpc.slurm.cancel` | 取消作业 | destructive |
| `hpc.slurm.accounting` | sacct 记账 | readOnly |
| `hpc.jobs.wait` | 等待作业完成（有上限） | readOnly |

### 高阶 Agent 工具

| 工具 | 说明 | annotations |
|---|---|---|
| `hpc.files.search` | 带预算的正则搜索（定位日志错误行） | readOnly |
| `hpc.jobs.diagnose` | 一次完成状态+记账+日志尾部+错误签名诊断 | readOnly |
| `hpc.jobs.wait_and_diagnose` | 等待作业结束并一次诊断 | readOnly |
| `hpc.project.snapshot` | 一次建立项目上下文（目录概览+git+作业） | readOnly |
| `hpc.cluster.topo` | 计算节点硬件拓扑（CPU 型号/SIMD/NUMA/cache）+ 并行参数建议 | readOnly, openWorld |
| `hpc.job.run` | runtime profile 高阶提交（julia/python/moose/bash） | openWorld |

### 示例调用

提交 Julia 作业（分区可从 `hpc.info` 返回的允许列表中选择，不指定则取配置的第一个）：

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

参数默认值来自服务端配置或 `.sh` 脚本的 `#SBATCH` 指令（显式参数优先）；
分区必须命中配置白名单，否则拒绝。也可以直接提交一个 `.sh` 作业脚本路径：

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "command": "/home/shared_account/alice/proj/run.sh"
  }
}
```

被拒绝时返回可操作信息：

```text
Operation denied.

Reason:
'julia' is a computational/build workload and is forbidden on HPC login nodes.

Use:
hpc.slurm.submit
```

## 并行优化参数（`hpc.cluster.topo`）

登录节点白名单**故意**不放行 `lscpu`/`numactl`，`/proc` 也在路径沙箱之外，
所以 CPU 型号、SIMD 指令集、NUMA 拓扑这些参数只能从计算节点取。`hpc.cluster.topo`
把这套流程做成一次调用：

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- **第一次调用**（或缓存过期、或 `refresh: true`）提交**一个 1 核采集作业**，
  在计算节点上读 `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo`，
  再与登录节点 `sinfo` 的分区视图合并。
- **返回**：CPU 型号与 sockets/cores/threads、SIMD 指令集（AVX2/AVX-512/SVE）、
  NUMA 域与距离矩阵、cache 层级、节点内存、分区 features/GRES，以及推导出的
  并行参数建议（`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、
  `--cpu-bind=cores`、`OMP_NUM_THREADS`、`-map-by numa`、建议 `--mem`），
  并附上每一条建议的理由与"物理核 vs 逻辑核"的取舍。
- **缓存**：`topology.cache_ttl_seconds`（默认 24h）内命中进程内缓存或远端
  `$ROOT/.hpc-mcp/topo/topology_<partition>.json`，后续会话不会为同一台机器反复排队。
- **排队中**：采集作业尚未调度时返回 `status: "pending"` + `job_id`；
  再次调用会复用该作业，不会重复提交。
- **安全**：采集脚本是**服务端固定内容**（Agent 的任何参数都不进入脚本），
  且脚本**不查队列**（`squeue`/`sacct`/`scontrol`），只读节点本地硬件事实，
  符合共享账号隔离规则；作业归属、分区白名单、并发上限照常生效。
- 管理员可用 `topology.enabled: false` 整个关闭该工具（永不提交采集作业）。

## 推荐工作流（Agent）

1. `hpc.info` 了解环境 → 2. `hpc.project.snapshot` 一次建立项目上下文 →
3. 涉及并行/性能时 `hpc.cluster.topo` 一次拿到 CPU/SIMD/NUMA 与推荐并行参数 →
4. `hpc.files.search` 先定位（日志错误行等），再 `hpc.files.read` 读小段上下文 →
5. `hpc.files.write` 远程编辑 → 6. 编译/测试/计算一律 `hpc.slurm.submit` →
7. `hpc.jobs.diagnose` 一次诊断失败作业 → 8. 分析、修改、重复。

原则：**能一次高阶调用完成的，不要拆成多次低级调用**；`hpc.files.read` 是 bounded slice，不要从 offset=0 读到 EOF；重复的只读查询由服务端 TTL 缓存去重。

服务端会明确告知的几条整理规则（避免把轮次浪费在权限报错上）：

- **临时文件只有一个去处**：测试脚本、测试日志、作业包装脚本、中间产物放 `hpc.info` 返回的 `session_tmp_dir`（`$ROOT/.hpc-mcp/tmp/<会话>/`），任务结束用 `hpc.files.delete(recursive=true)` 整目录删除。
- **`.sh` 脚本只提交、不执行**：把脚本路径作为 `command` 传给 `hpc.slurm.submit`——服务端用 `bash` 执行并读取其 `#SBATCH` 指令，因此脚本不需要可执行位，也不需要 `chmod`（登录节点本来就禁用它）。
- **作业日志由服务端捕获**在 `$ROOT/.hpc-mcp/jobs/<job-id>/`，用 `hpc.slurm.output` / `hpc.jobs.diagnose` 读取，不要在作业脚本里自己重定向输出。

详见 [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md)。

## 安全测试

```bash
python -m pytest tests/ -q
```

覆盖：路径穿越、符号链接逃逸、命令注入、login/compute 边界、Slurm 资源滥用、作业隔离。

完整的发现、修复和残余风险记录见 [`docs/zh-CN/SECURITY_REVIEW.md`](docs/zh-CN/SECURITY_REVIEW.md)，模块边界和请求流程见 [`docs/zh-CN/ARCHITECTURE.md`](docs/zh-CN/ARCHITECTURE.md)。

## 限制（v1 明确不做）

任意远程 shell、任意 SSH host、sudo、端口转发、多主机、远程常驻 daemon、HTTP MCP、自动凭证管理。

## 故障排查

- **启动报 "No HPC host/root configured"**：三种配置方式至少提供 host 与 root。
- **工具全部 DENY "No Slurm partitions are allowed"**：配置 `slurm.allowed_partitions`（默认空，fail-closed）。
- **SSH 255 错误**：先用 `hpc-mcp ... --check` 验证；确认 `~/.ssh/config` 与 BatchMode 免密可用。
- **日志与审计轨迹**：始终写 stderr（stdout 只走 MCP 协议），并默认追加到 `~/.local/share/hpc-mcp/hpc-mcp.log`，每次工具调用都会记录 ALLOW/DENY 决策。可用 `--log-file` / `HPC_MCP_LOG_FILE` / 配置文件的 `log_file` 改路径，设为 `none` 则只写 stderr。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/zh-CN/QUICKSTART.md](docs/zh-CN/QUICKSTART.md) | 手把手安装 + 每个参数说明 + 排查表 |
| [docs/zh-CN/CONFIGURATION.md](docs/zh-CN/CONFIGURATION.md) | 全部 YAML 键、默认值、取值范围与环境变量 |
| [docs/zh-CN/TOOLS.md](docs/zh-CN/TOOLS.md) | 22 个工具的参数/默认值/约束 |
| [docs/zh-CN/CC_SWITCH.md](docs/zh-CN/CC_SWITCH.md) | CC-Switch JSON 配置、字段说明、常见问题 |
| [docs/zh-CN/ARCHITECTURE.md](docs/zh-CN/ARCHITECTURE.md) | 模块边界、请求流程、隔离层次 |
| [docs/zh-CN/SECURITY_REVIEW.md](docs/zh-CN/SECURITY_REVIEW.md) | 发现、修复、验证与残余风险 |
| [SECURITY.zh-CN.md](SECURITY.zh-CN.md) | 威胁模型与强制边界 |

## 许可证

MIT，见 [LICENSE](LICENSE)。

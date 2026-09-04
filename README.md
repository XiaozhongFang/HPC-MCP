# HPC-MCP

一个**安全优先**的 MCP Server，让 VS Code 中的 Codex、Reasonix 等 Coding Agent 通过 SSH + Slurm 安全地操作远程 HPC 集群。

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
HPC_MCP_ROOT=/home/shared_account/fangxiaozhong
```

所有文件操作都被限制在该 root 之下（含符号链接 canonical 校验）。其他用户的目录（`/home/shared_account/other_user`）、系统目录（`/etc`、`/tmp`、`/opt`）一律拒绝。

### Login Node 策略

登录节点只允许轻量、只读的管理/查询命令（白名单制）：`ls`、`find`、`cat`、`grep`、`head`、`tail`、`git status/diff/log`、`module list/avail`、`squeue`、`sacct`、`scontrol show` 等。

**永远拒绝**在登录节点执行计算与编译：`julia`、`python`、`make`、`cmake --build`、`ninja`、`mpirun`、`srun`、`pytest`、`matlab`、GPU 程序等——全部引导至 `hpc.slurm.submit`。

命令策略在**代码层面**拒绝：shell 元字符（`;`、`&&`、`||`、`|`、`>`、`<`、`$()`、反引号、`&`）、路径形式的任意可执行文件（`./program`）、`find -exec`、`git -c`、嵌套 shell、`sudo`/`ssh`/`curl` 等危险程序。解析失败同样拒绝（fail-closed）。

### Slurm 资源策略

`hpc.slurm.submit` 是唯一计算入口。Server 端强制：

- 分区白名单（默认空 = 拒绝一切）
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs`（并发上限）
- 工作目录必须位于 USER_ROOT 内
- 作业 stdout/stderr 固定捕获到 `$ROOT/.hpc-mcp/jobs/<job-id>/`

### 作业归属（共享账号）

共享账号下 Unix 用户无法区分不同使用者。每个 MCP 实例只管理**自己提交并登记**的作业（`$ROOT/.hpc-mcp/tracked_jobs.json`）。对其他作业的 `status/output/cancel/accounting` 一律拒绝。

### 失败安全（fail-closed）

配置缺失、路径无法解析、命令解析失败、分区不确定、SSH 异常等任何不确定情况统一 **DENY**，绝不回退到无限制 shell。

## 安装

```bash
# pip
pip install .

# pipx
pipx install .

# uv
uv tool install .

# 或从源码运行
python -m hpc_mcp --help
```

安装后得到 `hpc-mcp` 命令。

## 配置

三种方式，优先级 **CLI > 环境变量 > 配置文件 > 默认值**。

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account --root /home/shared_account/fangxiaozhong
```

### 环境变量

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/fangxiaozhong
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML 配置文件

见 [`config/example.yaml`](config/example.yaml)：

```yaml
host: my-hpc
user: shared_account
root: /home/shared_account/fangxiaozhong

slurm:
  allowed_partitions: [compute]
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"
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

### 连通性自检

```bash
hpc-mcp --host my-hpc --root /home/shared_account/fangxiaozhong --check
```

## MCP 客户端集成

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_USER=shared_account \
  --env HPC_MCP_ROOT=/home/shared_account/fangxiaozhong \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_ROOT=/home/shared_account/fangxiaozhong \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

两者都是 stdio argv 方式启动，无需 shell。

## 工具清单（16 个）

| 工具 | 说明 | annotations |
|---|---|---|
| `hpc.info` | 连接/集群信息 | readOnly |
| `hpc.files.list` | 列目录 | readOnly |
| `hpc.files.read` | 读文件（大小受限） | readOnly |
| `hpc.files.write` | 写文件 | destructive |
| `hpc.files.mkdir` | 建目录 | — |
| `hpc.files.delete` | 删除 | destructive |
| `hpc.files.upload` | 本地上传（SFTP） | destructive |
| `hpc.files.download` | 下载到本地（SFTP） | readOnly |
| `hpc.shell.run_safe` | 白名单轻量命令 | readOnly |
| `hpc.slurm.submit` | 提交计算作业 | openWorld |
| `hpc.slurm.status` | 作业状态 | readOnly |
| `hpc.slurm.queue` | 我的作业队列 | readOnly |
| `hpc.slurm.output` | 作业 stdout/stderr | readOnly |
| `hpc.slurm.cancel` | 取消作业 | destructive |
| `hpc.slurm.accounting` | sacct 记账 | readOnly |
| `hpc.jobs.wait` | 等待作业完成（有上限） | readOnly |

### 示例调用

提交 Julia 作业：

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "job_name": "mas1998-test",
    "working_directory": "/home/shared_account/fangxiaozhong/mas1998_benchmark",
    "partition": "compute",
    "cpus_per_task": 8,
    "time_limit": "00:30:00",
    "command": ["julia", "--project=.", "scripts/test.jl"]
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

## 推荐工作流（Agent）

1. 本地阅读/修改代码 → 2. 本地轻量检查 → 3. `hpc.files.upload` 同步 →
4. `hpc.shell.run_safe` 做轻量查询 → 5. 编译/测试/计算一律 `hpc.slurm.submit` →
6. `hpc.slurm.status` 轮询 → 7. `hpc.slurm.output` 取日志 → 8. 分析、修改、重复。

详见 [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md)。

## 安全测试

```bash
python -m pytest tests/ -q
```

覆盖：路径穿越、符号链接逃逸、命令注入、login/compute 边界、Slurm 资源滥用、作业隔离。

## 限制（v1 明确不做）

任意远程 shell、任意 SSH host、sudo、端口转发、多主机、远程常驻 daemon、HTTP MCP、自动凭证管理。

## 故障排查

- **启动报 "No HPC host/root configured"**：三种配置方式至少提供 host 与 root。
- **工具全部 DENY "No Slurm partitions are allowed"**：配置 `slurm.allowed_partitions`（默认空，fail-closed）。
- **SSH 255 错误**：先用 `hpc-mcp ... --check` 验证；确认 `~/.ssh/config` 与 BatchMode 免密可用。
- **日志**：写 stderr（stdout 只走 MCP 协议）；`--log-file` 可追加到文件。

## 许可证

MIT，见 [LICENSE](LICENSE)。

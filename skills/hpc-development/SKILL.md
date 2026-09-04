---
name: hpc-development
description: 在远程 HPC 集群上安全地进行科研计算开发（Julia/MOOSE/Python/CMake）。当任务涉及 HPC 集群、Slurm 作业、远程编译/测试/模拟时使用本技能。
---

# HPC 开发工作流

你通过 **HPC MCP Server** 操作远程集群。MCP 是安全边界：越权操作会被代码级拒绝并附带可操作的替代建议——被拒绝时按提示改用正确工具，**不要尝试绕过**。

## 核心规则（违反会被拒绝）

1. **登录节点不做计算**。`julia`、`python`、`make`、`cmake --build`、`mpirun`、`pytest`、大型测试——一律走 `hpc.slurm.submit`。
2. **只碰 `$HPC_MCP_ROOT` 内的路径**。`../`、符号链接出界、其他用户目录都会被拒。用 `hpc.info` 查看 root。
3. **不要自己构造 SSH/SCP 命令**；用 `hpc.files.*` 工具传输和读写。
4. `hpc.shell.run_safe` 仅限轻量查询（`ls`、`cat`、`git status/diff/log`、`module list`、`squeue` 等），不支持管道/重定向/命令串联。

## 标准工作流

1. **本地检查**：先在本地阅读、修改代码，做本地静态/轻量检查。
2. **同步代码**：`hpc.files.upload`（或 `hpc.files.write` 写小文件）。
3. **轻量验证**：`hpc.shell.run_safe` 运行 `git diff`、`ls`、`module avail` 等。
4. **提交计算**：需要编译/测试/模拟时：

   ```json
   {
     "tool": "hpc.slurm.submit",
     "arguments": {
       "job_name": "descriptive-name",
       "working_directory": "<root 内的项目目录>",
       "partition": "<允许的分区>",
       "cpus_per_task": 8,
       "time_limit": "00:30:00",
       "command": ["julia", "--project=.", "test/runtests.jl"]
     }
   }
   ```

5. **跟踪**：`hpc.slurm.status` 轮询，或 `hpc.jobs.wait` 等待（有上限）。
6. **取日志**：`hpc.slurm.output`（stdout/stderr，尾部截取）；需要记账信息用 `hpc.slurm.accounting`。
7. **分析失败 → 修改 → 重复**。

## Julia 项目

- 测试：`command: ["julia", "--project=.", "test/runtests.jl"]`
- 脚本：`command: ["julia", "--project=.", "scripts/run.jl"]`
- 环境设置放 `environment` 字段，例如 `{"JULIA_NUM_THREADS": "8"}`。
- `instantiate`/`precompile` 也属于计算，提交到计算节点而非登录节点。

## MOOSE / MPI

- `command: ["mpirun", "-np", "16", "./moose-opt", "-i", "input.i"]`，配套 `ntasks`/`cpus_per_task`/`nodes`。
- 绝不在登录节点直接 `mpirun`。

## 被拒绝时怎么办

拒绝信息包含：原因、允许范围、替代工具。例如：

- "forbidden on HPC login nodes … Use: hpc.slurm.submit" → 改用提交作业。
- "Path escapes the configured user root" → 检查路径是否在 root 内。
- "not in the login-node command whitelist" → 换轻量查询，或走 Slurm。

**不要**试图用拼接、编码、符号链接等方式绕过——所有边界都在服务端代码强制。

## 资源意识

- 申请资源要适度：分区、CPU、内存、时长都受服务端上限约束，超限会被拒。
- 同时运行的作业有并发上限；先 `hpc.slurm.queue` 看自己的作业。
- 取消自己的作业用 `hpc.slurm.cancel`（只能取消本实例提交的）。

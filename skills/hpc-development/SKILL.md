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
4. `hpc.shell.run_safe` 仅限轻量查询（`ls`、`cat`、`git status/diff/log`、`module list` 等），不支持管道/重定向/命令串联；`squeue`/`sacct`/`scontrol` 必须通过带归属检查的 Slurm 工具查询。
5. 文件传输只能使用配置的 `local_root` 与远程 `HPC_MCP_ROOT`；不要上传 `.ssh`、私钥或符号链接。

## 标准工作流（优先远程直接操作）

**默认在远程工作，不要先把文件下载到本地再改。** 远程文件可以直接读（`hpc.files.read`）、直接写（`hpc.files.write`）、直接跑（`hpc.slurm.submit`）。只有本地有而远程没有的工具/环境、或需要本地人检查产出物时，才用 `download`。

1. **了解环境**：`hpc.info` 查看 working_root、local_roots、可用分区与资源上限（注意：SSH host/user 不暴露，不要尝试获取或绕过 MCP 直连）。
2. **查看项目**：`hpc.files.list` / `hpc.files.read` 直接读远程代码，`hpc.shell.run_safe` 做 `git diff`、`ls` 等轻量检查。
3. **远程编辑**：`hpc.files.write`（或先 `read` 再 `write` 修改片段）直接在远程改代码；小改动不需要下载。
4. **远程提交计算**：需要编译/测试/模拟时走 `hpc.slurm.submit`：

   ```json
   {
     "tool": "hpc.slurm.submit",
     "arguments": {
       "job_name": "descriptive-name",
       "command": ["julia", "--project=.", "test/runtests.jl"],
       "cpus_per_task": 8
     }
   }
   ```

   - `working_directory` 缺省 = 配置的用户根目录；`partition` 可用 `hpc.info` 查到的允许分区名指定（GPU 作业选 GPU 分区），缺省取配置的第一个；`cpus_per_task`/`nodes`/`ntasks`/`gpus`/`time_limit` 都有安全默认值，也可从所提交 `.sh` 脚本的 `#SBATCH` 指令读取。
   - 资源上限（CPU/节点/内存/GPU/时长/并发）由服务端强制，超限会被拒——先看 `hpc.info` 的限额再申请。
5. **跟踪**：`hpc.slurm.status` 轮询，或 `hpc.jobs.wait` 等待（有上限）。
6. **取日志**：`hpc.slurm.output`（stdout/stderr，尾部截取）；需要记账信息用 `hpc.slurm.accounting`。
7. **分析失败 → 远程修改（步骤 3）→ 重复**，直到通过。

### 什么时候才下载到本地

- 需要本地工具分析/可视化产出文件，且本地环境具备对应工具。
- 本地有而远程缺失的依赖，需要本地验证。
- 下载目标必须位于 `hpc.info` 返回的 `local_roots` 之一。

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

- 申请资源要适度：CPU、内存、时长、GPU 都受服务端上限约束，超限会被拒；分区只能从 `hpc.info` 显示的允许列表中选择，指定白名单外的分区会被拒绝。
- 同时运行的作业有并发上限；先 `hpc.slurm.queue` 看自己的作业。
- 取消自己的作业用 `hpc.slurm.cancel`（只能取消本实例提交的）。

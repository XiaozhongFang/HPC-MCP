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
4. `hpc.shell.run_safe` 仅限轻量查询（`ls`、`cat`、`git status/diff/log`、`module list` 等），不支持管道/重定向/命令串联；`squeue`/`sacct`/`scontrol` 必须通过带归属检查的 Slurm 工具查询。`lscpu`/`numactl` 等硬件查询也不在登录节点白名单内——要并行/性能参数请用 `hpc.cluster.topo`（它会在计算节点采集）。
5. 文件传输只能使用配置的 `local_root` 与远程 `HPC_MCP_ROOT`；不要上传 `.ssh`、私钥或符号链接。

## 标准工作流（优先远程直接操作）

**默认在远程工作，不要先把文件下载到本地再改。** 远程文件可以直接读（`hpc.files.read`）、直接写（`hpc.files.write`）、直接跑（`hpc.slurm.submit`）。只有本地有而远程没有的工具/环境、或需要本地人检查产出物时，才用 `download`。

1. **了解环境**：`hpc.info` 查看 working_root、可用分区与资源上限（注意：SSH host/user 与本地路径不暴露，不要尝试获取或绕过 MCP 直连）。
2. **建立项目上下文**：用一次 `hpc.project.snapshot` 拿到目录概览、源码/日志文件大小、只读 git 状态和本项目已跟踪作业——不要用多次 `list`+`read`+`git status`+`queue` 拼出来。
3. **先定位，再精读**：
   - 日志/输出排查先 `hpc.files.search`（带预算的正则搜索，定位行号），再用 `hpc.files.read(offset=…)` 读**一小段上下文**。
   - `hpc.files.read` 是 bounded slice：每次最多返回 `min(max_bytes, 服务端 slice 上限)` 字节。**不要**从 `offset=0` 无目的地一直读到 `end_of_file`——大文件用 search 定位后再读局部。
4. **远程编辑**：`hpc.files.write`（或先 `read` 再 `write` 修改片段）直接在远程改代码；小改动不需要下载。
5. **远程提交计算**：需要编译/测试/模拟时走 `hpc.slurm.submit`：

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
6. **跟踪**：`hpc.slurm.status` 轮询，或 `hpc.jobs.wait` 等待（有上限）。**生产级**用 `hpc.jobs.wait_and_diagnose` 一次完成等待+诊断。
7. **作业失败诊断**：直接用一次 `hpc.jobs.diagnose`（状态 + exit code + 记账 + stdout/stderr 尾部 + 常见错误签名扫描）。**不要**自己串联 `status → output → accounting → read`。
8. **重复查询会被去重**：同一只读查询在 2 秒内重复调用直接命中缓存，不产生 SSH；但不要依赖它——**没有新证据就不要重复调用**。
9. **分析失败 → 远程修改（步骤 4）→ 重复**，直到通过。

### 高阶提交（可选）

`hpc.job.run` 用 runtime profile 简化提交（julia/python/moose/bash），内部仍走全部 Slurm 策略：

```json
{
  "tool": "hpc.job.run",
  "arguments": {
    "runtime": "julia",
    "script": "scripts/run.jl",
    "args": ["--grid", "128"],
    "cpus_per_task": 8
  }
}
```

`hpc.slurm.submit` 保留给需要完整 argv 控制的专家。

### 并行/性能调优：先取拓扑，不要猜硬件

要决定 `ntasks_per_node`、`cpus_per_task`、MPI 绑定、`OMP_NUM_THREADS` 或编译期
`-march` 之前，**先用一次 `hpc.cluster.topo` 拿真实硬件参数**，不要凭集群名字或惯例猜：

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- 返回 CPU 型号、sockets × cores × threads、SIMD 指令集（AVX2/AVX-512/SVE）、
  NUMA 域与距离、cache 层级、节点内存、分区 features/GRES，以及推导好的
  `recommended_parallel_parameters`（纯 MPI / 混合 MPI+OpenMP / 纯 OpenMP 三套）。
- 第一次调用会在该分区提交一个 **1 核采集作业**（服务端固定脚本，只读节点本地硬件，
  不查队列），结果按 TTL 缓存（默认 24h），后续调用不会重复排队。
- 若返回 `status: "pending"`，说明采集作业还在排队：稍后**再次调用同一工具**即可
  （会复用那个 `job_id`，**不要**自己重新提交采集作业）。
- 只把 `recommended_parallel_parameters` 当**起点**：它是按物理核/NUMA 域推导的启发式，
  真正的调优仍要跑基准并对比（`hpc.job.run` 提交不同 rank/线程组合，用
  `hpc.jobs.wait_and_diagnose` 收结果）。

### 写保护

`hpc.files.write` 支持 `expected_size` / `expected_mtime` / `expected_sha256`：如果文件在上次读取后被其他进程修改，写入会被拒绝。这对共享账号环境尤其重要——避免覆盖同伴刚修改的代码。

### 响应脱敏

返回的日志/文件内容中如有 `password=`、`token=`、`API_KEY=`、`Bearer`、AWS 凭证、PEM 私钥块等明显 secret，会被自动脱敏为 `[REDACTED]`。科研日志内容不受影响。

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
- **搜索/读取有预算**：`hpc.files.search` 的 `max_matches`/`max_scan_bytes`/`timeout`、`hpc.files.read` 的单次 slice、`hpc.files.list` 的 `page_size`/`max_depth` 都由服务端钳制；超预算会返回 `truncated`——按提示缩小范围，不要重试更粗暴的查询。
- **只查询自己的作业**：服务端只查本实例跟踪的 job ID，`hpc.slurm.queue` 返回空是正常的（你没有活跃作业），不代表集群空闲。
- 每次远程命令都会消耗登录节点资源；能用一次高阶调用（`snapshot`/`diagnose`/`search`）完成的，不要拆成多次低级调用。

# MCP 工具参数参考

其他语言：[English](../TOOLS.md) | [日本語](../ja/TOOLS.md) | [한국어](../ko/TOOLS.md) | [繁體中文](../zh-TW/TOOLS.md)

本文件说明 HPC-MCP 暴露给 Agent 的 **22 个工具**的用途、参数、默认值、约束与返回要点。
它是 [README.zh-CN.md](../../README.zh-CN.md)「工具清单」的展开版：README 回答"有哪些工具"，
本文件回答"每个参数怎么填、会被什么规则钳制"。

- **配置参数**（host/root/分区白名单/资源上限……）见 [CONFIGURATION.md](CONFIGURATION.md)。
- **命令行参数**（`--host`/`--root`/`--check`……）见 [QUICKSTART.md](QUICKSTART.md) 第 4 节。
- **Agent 行为指南**（什么时候用哪个工具）见 [skills/hpc-development/SKILL.md](../../skills/hpc-development/SKILL.md)。

---

## 通用约定（所有工具都适用）

| 约定 | 说明 |
|---|---|
| 路径沙箱 | 所有远程路径必须是**绝对路径**且位于 `$HPC_MCP_ROOT` 之内；`..`、符号链接出界、`/etc` 等一律拒绝。本地路径必须位于 `local_roots`（默认=启动时工作目录 + `/tmp`），并拒绝 `.ssh`、私钥和符号链接。 |
| 服务端钳制 | 所有"预算类"参数（字节数、条数、深度、超时）都是**请求值 ≤ 服务端上限**，上限见 CONFIGURATION.md。传 0 或负数会被拒（fail-closed）。 |
| 分页 | 递归列举用 `page_size` + `cursor` 逐层推进，不会整树扫描；读取用 `offset` + `next_offset` 续读。 |
| 查询去重 | 只读且幂等的工具按 `工具+参数` 在 `cache_ttl_seconds`（默认 2 秒）内去重，重复调用不产生 SSH 往返；任何写操作都会清空该缓存。 |
| 响应脱敏 | 日志/文件内容中的 `password=`、`token=`、`Bearer`、AWS 凭证、PEM 私钥块等会被替换为 `[REDACTED]`；科研日志内容不受影响。 |
| 失败即拒绝 | 配置缺失、路径无法解析、命令解析失败、分区不确定、SSH 异常等一切不确定情况 **DENY**，并给出可操作的替代建议（`Reason:` + `Use:`）。 |
| annotations | `readOnly`＝不修改远程状态；`destructive`＝可能删除/覆盖；`idempotent`＝重复调用无额外副作用；`openWorld`＝会触达集群计算侧（提交作业）。 |

---

## 一、环境与项目上下文

### `hpc.info`

连接与集群能力概览。**无参数**。

返回：`slurm_available`、`cluster`、`working_root`、`tmp_dir`、`session_tmp_dir`、`workspace_available`、`transfer_enabled`、
`allowed_partitions`（可提交的分区白名单）、`max_cpus`/`max_nodes`/`max_memory_mb`/`max_gpus`/`max_time`。

> **临时文件只有一个去处**：测试脚本、测试日志、作业包装脚本、中间产物一律放 `session_tmp_dir`
> （`$HPC_MCP_ROOT/.hpc-mcp/tmp/<会话>/`），不要散落在项目目录；任务结束后用
> `hpc.files.delete(recursive=true)` 整个目录删除。交付物应归档到项目目录，不要留在临时目录里。
>
> 出于安全考虑**不返回** SSH host/user/port 和本地路径根（避免 Agent 绕过 MCP 直连）。
> 申请资源前先看这里的上限。

### `hpc.project.snapshot` — `readOnly, idempotent`

一次调用建立项目上下文：浅层目录概览 + 关键源码/日志文件大小 + 只读 git 状态 + 本项目已跟踪作业。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | `$HPC_MCP_ROOT` 内的绝对路径 |
| `depth` | int | — | `2` | 枚举深度，服务端再按 `files.max_recursive_depth` 钳制 |
| `include_git` | bool | — | `true` | 是否包含只读 git 摘要（`git status` 等） |
| `include_jobs` | bool | — | `true` | 是否包含本项目已跟踪作业 |

> 一次 `snapshot` 代替 `list` + `read` + `git status` + `queue` 的多次往返。

---

## 二、远程文件

### `hpc.files.list` — `readOnly, idempotent`

列目录。递归时**逐层分页**。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目录绝对路径（root 内） |
| `recursive` | bool | — | `false` | 递归列举（逐层推进） |
| `max_entries` | int | — | 服务端 `files.max_list_entries`（2000） | 单次返回条数硬上限 |
| `page_size` | int | — | 同 `max_entries` | 每页条数 |
| `cursor` | string | — | — | 上一页返回的 `next_cursor`，用于继续递归列举 |
| `max_depth` | int | — | 服务端 `files.max_recursive_depth`（3） | 递归深度上限 |

### `hpc.files.read` — `readOnly, idempotent`

读取文件的一段（**bounded slice**，单次 ≤ `files.max_read_slice_bytes`，默认 256 KiB）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 文件绝对路径（root 内） |
| `max_bytes` | int | — | 服务端 `files.max_read_bytes`（1 MiB） | 本次读取字节上限，仍受 slice 上限钳制 |
| `offset` | int | — | `0` | 起始字节偏移 |

返回：`size`、`bytes`、`truncated`、`end_of_file`、`next_offset`、`content`（UTF-8，非法字节替换）。

> 排查大日志请先用 `hpc.files.search` 定位行号，再用 `offset` 读一小段；
> **不要**从 `offset=0` 一直读到 EOF。

### `hpc.files.search` — `readOnly, idempotent`

带预算的正则搜索（POSIX ERE），用于"先定位再精读"。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 文件或目录绝对路径 |
| `pattern` | string | ✅ | — | 扩展正则（作为单个引号参数传递，控制字符被拒） |
| `max_matches` | int | — | `files.search_max_matches`（200） | 最多匹配数 |
| `context_lines` | int | — | `files.search_max_context_lines`（10） | 每个匹配的上下文行数 |
| `max_scan_bytes` | int | — | `files.search_max_scan_bytes`（64 MiB） | 单文件扫描上限 |
| `max_files` | int | — | `files.search_max_files`（1000） | 目录树中最多扫描文件数 |
| `max_depth` | int | — | `files.search_max_depth`（6） | 目录树深度上限 |
| `timeout` | int | — | `files.search_timeout`（5 s） | 远端搜索超时 |

### `hpc.files.write` — `destructive`

写文件，支持**乐观并发保护**（共享账号下防止覆盖他人刚改的代码）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目标绝对路径 |
| `content` | string | ✅ | — | 文本内容（整体上限 `files.max_write_bytes`，默认 10 MiB） |
| `append` | bool | — | `false` | 追加而非覆盖 |
| `expected_size` | int | — | — | 期望的当前字节数，不符则**拒绝写入** |
| `expected_mtime` | int | — | — | 期望的当前 mtime（epoch 秒），不符则拒绝 |
| `expected_sha256` | string | — | — | 期望的当前 SHA-256（64 位十六进制），不符则拒绝 |

> 三个 `expected_*` 可任意组合；只要提供就必须全部匹配，且文件必须存在（否则拒绝）。

### `hpc.files.mkdir` — `destructive`

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目录路径 |
| `parents` | bool | — | `false` | 等同 `mkdir -p`（会校验最近存在祖先仍在 root 内） |

### `hpc.files.delete` — `destructive`

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 目标路径（不允许删除 root 本身） |
| `recursive` | bool | — | `false` | 递归删除（`rm -rf`） |

### `hpc.files.upload` / `hpc.files.download`

SFTP 传输，**两端都受沙箱限制**（本地 ∈ `local_roots`，远程 ∈ `$HPC_MCP_ROOT`）。

| 工具 | 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `hpc.files.upload` (destructive) | `local_path` | string | ✅ | 本地源文件路径 |
| | `remote_path` | string | ✅ | 目标路径（root 内） |
| `hpc.files.download` (readOnly) | `remote_path` | string | ✅ | 远程源文件路径 |
| | `local_path` | string | ✅ | 本地目标路径（`local_roots` 内） |

> 两条路径都拒绝符号链接；本地侧额外拒绝 `.ssh`/`.gnupg` 与私钥文件名；传输后校验大小上限。
>
> 上传的 **`.sh` 作业脚本不需要可执行位**：直接用 `hpc.slurm.submit` 把脚本路径作为 `command` 提交
> （服务端用 `bash` 执行并读取其 `#SBATCH` 指令）。不要 `chmod`，也不要在登录节点上尝试直接执行——
> 上传结果里也会带同样的 `submit_hint` 提示。

---

## 三、登录节点查询

### `hpc.shell.run_safe` — `readOnly`

在登录节点运行**单条白名单命令**（`ls`/`find`/`cat`/`grep`/`head`/`tail`/`wc`/`sort`/`uniq`/`stat`/`du`/`df`/`git status|diff|log`/`module list|avail`/`sinfo`/`env` 等）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `command` | string | ✅ | — | 单条命令；**不支持**管道、`&&`、`;`、重定向、`$()`、反引号、后台 `&` |
| `cwd` | string | — | `$HPC_MCP_ROOT` | 工作目录（root 内，realpath 校验） |
| `timeout` | int | — | `shell.max_exec_seconds`（30 s） | 再被命令成本档钳制 |

约束（代码级强制）：

- **不能**执行计算/编译程序（`julia`/`python`/`make`/`cmake`/`mpirun`/`pytest`/`nvcc`……）→ 改用 `hpc.slurm.submit`。
- **不能**执行 `squeue`/`sacct`/`scontrol` → 改用带作业归属检查的 Slurm 工具。
- **不能**用路径形式执行程序（`./prog`、`/usr/bin/julia`）、不能 `find -exec`、不能 `git -c`、不能嵌套 shell。
- 命令成本分档：低（15 s / 256 KiB，如 `ls`/`sinfo`）、中（30 s / 512 KiB，如 `cat`/`grep`）、高（10 s / 512 KiB，如 `find`/`du`/`sort`/`git grep`）。

返回：`exit_code`、`stdout`、`stderr`、`cost_tier`、`timeout_seconds`（超时置 `timed_out: true`）。

---

## 四、Slurm 作业

### `hpc.slurm.submit` — `openWorld`

提交计算作业（**唯一**的计算入口，列在服务端资源策略之下）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `command` | array 或 string | ✅ | — | 程序 argv（如 `["julia","--project=.","test/runtests.jl"]`），或**单个 `.sh` 脚本路径**——用 `bash` 执行，**不需要可执行位**；此时读取其 `#SBATCH` 指令作为默认值 |
| `job_name` | string | — | `"job"` | 作业名（会被规范化为 `[A-Za-z0-9_.-]`，≤ 64 字符） |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 作业工作目录（root 内，realpath 校验） |
| `partition` | string | — | 配置的第一个白名单分区 | 必须命中 `slurm.allowed_partitions` |
| `nodes` | int | — | `1` | ≤ `slurm.max_nodes` |
| `ntasks` | int | — | `1` | 任务数 |
| `cpus_per_task` | int | — | `1` | 且 `nodes × ntasks × cpus_per_task ≤ slurm.max_cpus` |
| `memory` | string | — | 无 | 如 `"16G"`、`"8000"`（MiB）、`"2T"`；≤ `slurm.max_memory_mb` |
| `time_limit` | string | — | `slurm.max_time` | `HH:MM:SS`、`D-HH:MM:SS`、`MM:SS` 或纯分钟；≤ `slurm.max_time` |
| `gpus` | int | — | `0` | ≤ `slurm.max_gpus` |
| `environment` | object | — | — | 额外环境变量（名须匹配 `[A-Za-z_][A-Za-z0-9_]*`） |

行为要点：脚本由服务端生成（`#SBATCH` 由服务端推导），stdout/stderr 固定捕获到
`$HPC_MCP_ROOT/.hpc-mcp/jobs/<job-id>/`；提交后立刻用 `squeue -j`/`sacct -j` 交叉确认作业归属，
未确认的作业不会被登记；并发活跃作业数受 `slurm.max_concurrent_jobs` 限制。

单个 `.sh` 参数会以 **`bash <脚本>`** 执行，而不是把脚本当可执行文件直接调用——上传的脚本没有可执行位也能跑
（登录节点也禁用 `chmod`），因此不需要在文件权限上试错。一次性的作业脚本请放在 `session_tmp_dir`（见 `hpc.info`）。

### `hpc.job.run` — `openWorld`

按**受信任运行时 profile** 简化提交，内部仍走与 `hpc.slurm.submit` 完全相同的策略。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `runtime` | string | ✅ | — | 枚举：`julia`、`python`、`moose`、`bash`、`shell` |
| `script` | string | ✅ | — | 脚本绝对路径（root 内）。**moose** 时传可执行文件路径 |
| `args` | array\<string\> | — | — | 追加 argv；**moose 必填**（`[input.i]` 等），实际命令为 `mpirun -np <ntasks> -- <script> <args…>` |
| `job_name` | string | — | runtime 名 | 作业名 |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 工作目录 |
| `partition` | string | — | 第一个白名单分区 | 同 submit |
| `nodes` / `ntasks` / `cpus_per_task` / `memory` / `time_limit` / `gpus` / `environment` | — | — | 同 `hpc.slurm.submit` | 资源参数语义与上限完全一致 |

各 profile 的实际命令：`julia --project=. <script>`、`python <script>`、`bash <script>`、
`moose` → `mpirun -np <ntasks> -- <script>`。

### 作业查询与管理（仅限本实例提交的作业）

服务端只查询**自己提交并登记**的 job ID（`squeue -j <ids>` / `sacct -j <ids>`），
绝不扫描共享账号的全队列；对其他作业的查询/取消一律拒绝。

| 工具 | 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|---|
| `hpc.slurm.status` (readOnly,idempotent) | `job_id` | string | ✅ | — | 状态；squeue 无记录时回退 `sacct` |
| `hpc.slurm.queue` (readOnly,idempotent) | — | — | — | — | 本实例活跃作业列表（无活跃作业时返回空，属正常） |
| `hpc.slurm.output` (readOnly,idempotent) | `job_id` | string | ✅ | — | 读取作业日志 |
| | `stream` | string | — | `"stdout"` | 枚举：`stdout`、`stderr` |
| | `tail_bytes` | int | — | `shell.max_output_bytes`（1 MiB） | 从文件末尾取多少字节 |
| `hpc.slurm.cancel` (destructive) | `job_id` | string | ✅ | — | 取消（只能取消本实例提交的作业） |
| `hpc.slurm.accounting` (readOnly,idempotent) | `job_id` | string | ✅ | — | `sacct` 记账：`elapsed`、`cpu_time_raw`、`max_rss`、`state`、`exit_code`、`node_list`、`alloc_cpus` |

### `hpc.jobs.wait` — `readOnly, idempotent`

轮询直到作业进入终止态（`COMPLETED`/`FAILED`/`CANCELLED`/`TIMEOUT`/`OUT_OF_MEMORY`/`NODE_FAIL`/`PREEMPTED`）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 作业 ID |
| `timeout_seconds` | int | — | `wait_max_seconds`（3600） | 整体等待上限，再被服务端上限钳制 |
| `poll_interval` | int | — | `10` | 轮询间隔秒（服务端钳制到 2–60） |

> 超时不会取消作业，而是返回错误并提示继续轮询。

### `hpc.jobs.diagnose` — `readOnly`

一次调用完成诊断：状态 + 记账 + stdout/stderr 尾部 + 常见错误签名扫描。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 作业 ID |
| `stdout_lines` | int | — | `80` | stdout 尾部行数（上限 200；每行按 4096 字节估算读取量） |
| `stderr_lines` | int | — | `80` | stderr 尾部行数（上限 200） |
| `include_accounting` | bool | — | `true` | 是否附带 `sacct` 记账 |
| `include_error_scan` | bool | — | `true` | 是否扫描错误签名（`oom`/`segfault`/`timeout`/`gpu_error`/`mpi_error`/`missing_file`） |

> 用一次 `diagnose` 代替 `status → output → accounting → read` 的多次往返。

### `hpc.jobs.wait_and_diagnose` — `readOnly`

等待作业结束再一次性诊断（作业已结束时立即返回）。

参数：`job_id`（必填）、`timeout_seconds`、`poll_interval`、`stdout_lines`、`stderr_lines`
（语义与默认值同 `wait` / `diagnose`）。

---

## 五、集群拓扑与并行参数

### `hpc.cluster.topo` — `readOnly, idempotent, openWorld`

获取**计算节点的真实硬件参数**与推导出的并行优化建议：CPU 型号/vendor、
`sockets × cores × threads`、SIMD 指令集（AVX2/AVX-512/SVE）、NUMA 域与距离矩阵、
cache 层级、节点内存、分区 features/GRES，以及
`recommended_parallel_parameters`（纯 MPI / 混合 MPI+OpenMP / 纯 OpenMP 三套，含
`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、`--cpu-bind=cores`、
`-map-by numa`、`OMP_NUM_THREADS`、建议 `--mem` 及理由）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `partition` | string | — | 白名单第一个分区 | 要探测的分区；名字须为 `[A-Za-z0-9_.-]` 且命中 `slurm.allowed_partitions` |
| `refresh` | bool | — | `false` | 强制重新采集（忽略缓存；会先取消仍在排队的旧采集作业） |

**调用成本与缓存**（默认值见 `topology.*` 配置）：

- 第一次调用（或缓存过期、或 `refresh: true`）会提交**一个 1 核采集作业**
  （默认时长上限 `00:03:00`），在计算节点上读取 `lscpu` / `/proc/cpuinfo` /
  `numactl --hardware` / `/proc/meminfo` / `/sys/.../cpu0/cache`，并与登录节点
  `sinfo` 的分区视图合并。
- 结果缓存 `topology.cache_ttl_seconds`（默认 24 h）：进程内 + 远端
  `$HPC_MCP_ROOT/.hpc-mcp/topo/topology_<partition>.json`。缓存命中时**不会**提交作业，
  返回里的 `source` 标明来源（`probe-job` / `session-cache` / `remote-cache`）。
- 采集作业仍在排队（超过 `topology.wait_seconds`，默认 300 s）时返回
  `status: "pending"` 与 `collection_job_id`；**再次调用会复用该作业**，不会重复提交。

**安全约束**：采集脚本是服务端生成的**固定内容**，Agent 的任何参数都不会写进脚本；
脚本只读节点本地硬件事实，**不会**调用 `squeue`/`sacct`/`scontrol`（共享账号隔离）；
作业归属、分区白名单、并发上限照常生效。管理员可用 `topology.enabled: false` 关闭该工具。

**返回要点**：

| 字段 | 含义 |
|---|---|
| `cpu.model_name` / `cpu.vendor` / `cpu.architecture` | CPU 型号、厂商、架构 |
| `cpu.sockets` / `cores_per_socket` / `threads_per_core` | 拓扑三要素 |
| `cpu.physical_cores` / `logical_cpus` | 物理核数 / 逻辑核数（SMT 开启时后者更大） |
| `cpu.simd.level` / `present` | 向量指令集档位（`avx512`/`avx2`/`avx`/`arm-neon`…）与命中的具体 flag |
| `cpu.cache_kib` / `cache_source` | L1d/L1i/L2/L3（KiB，`null` 表示该级未知）与来源（`lscpu`/`sysfs`） |
| `numa.count` / `nodes` / `distances` | NUMA 域数、每域 CPU 列表与大小、距离矩阵 |
| `memory_mib.mem_total` | 节点内存（MiB） |
| `slurm.partition` / `node_variants` | 分区汇总（节点数/每节点核数内存/features/GRES）与硬件变体（含状态直方图） |
| `recommended_parallel_parameters` | 三套并行参数建议 + `notes`（每条建议的理由） |
| `collected_on_node` / `collection_job_id` | 采样节点与采集作业 ID（便于追溯） |

> 建议值是按物理核/NUMA 域推导的**启发式**起点，不是基准测试结论；
> 真正的调优仍要跑不同 rank/线程组合对比。

---

## 六、典型调用序列

```text
1. hpc.info                      # 分区白名单与资源上限
2. hpc.project.snapshot          # 项目上下文（目录/git/我的作业）
3. hpc.cluster.topo              # 并行优化前：CPU/SIMD/NUMA + 参数建议
4. hpc.files.search  →  hpc.files.read   # 先定位，再读一小段
5. hpc.files.write                # 远程编辑
6. hpc.slurm.submit 或 hpc.job.run       # 编译/测试/计算
7. hpc.jobs.wait_and_diagnose     # 等待 + 一次诊断
8. 分析 → 回到第 5 步
```

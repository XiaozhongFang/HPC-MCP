# HPC-MCP 架构

其他语言：[English](../ARCHITECTURE.md) | [日本語](../ja/ARCHITECTURE.md) | [한국어](../ko/ARCHITECTURE.md) | [繁體中文](../zh-TW/ARCHITECTURE.md)

HPC-MCP 是一个 stdio MCP server。MCP 协议是唯一写入 stdout 的数据；
诊断与审计记录同时写 stderr **和**配置的日志文件（默认
`~/.local/share/hpc-mcp/hpc-mcp.log`），因此每次 ALLOW/DENY 决策在会话结束后仍可复查。

```text
MCP client
   |
   v
server.py（分发、运行时参数形状校验、审计、生命周期）
   |
   +--> tools/registry.py（对外工具 schema 与适配器）
   |       |
   |       +--> security/*（纯策略决策）
   |       +--> filesystem / shell / slurm services
   |       +--> cluster/topology.py（计算节点采集作业 + sinfo，带缓存）
   |
   +--> ssh/manager.py 与 ssh/sftp.py（固定 argv，进程有界）
               |
               v
          单一已配置的 HPC host
```

## 请求流程

1. `server.on_call_tool` 解析工具名，并拒绝非对象、缺失或未知的参数。
2. registry 适配器校验标量类型，只转发通过类型检查的值。
3. 任何会改动远端状态的命令执行前，先由服务层施加策略：
   `path_policy` 对远程路径做 canonical 化，`command_policy` 对登录节点命令分类，
   `slurm_policy` 校验资源。
4. 服务层要求 SSH transport 执行固定 argv 或可信的、已加引号的内部包装脚本。
   transport 输出按字节上限流式截断；无论成功、失败还是超时，子进程都会被回收。
5. 结果以文本内容返回。每次调用都产生一条带递归凭证脱敏的 ALLOW/DENY 审计记录。

## 隔离边界

远程 `root` 是专属的每用户目录，且不能是 `/`。远程路径既做词法校验，也做远程
`realpath` 校验；创建类操作还会拒绝已存在的符号链接目标。传输工具则有独立的本地
`local_root`（默认为进程工作目录），拒绝符号链接组件与凭证类文件名，并在传输后强制大小上限。

登录节点命令是一份很小的白名单。不接受任何 shell 操作符、可执行文件路径、嵌套
shell、跟随符号链接的选项，也不接受直接查询 Slurm 的命令。作业查看只通过会校验
当前进程会话 `tool_session` 条目的 Slurm 方法暴露。输出路径由数字 job ID 与
canonical 作业目录推导，绝不来自不可信元数据。Slurm 会先写入扁平的
`%j.stdout.log`/`%j.stderr.log` 暂存文件（它在打开输出前无法创建中间目录）；
随后由 server 创建 canonical 作业目录，并把这两个文件链接到其下。

## 查询成本边界

共享账号安全同样意味着*根本不把其他用户或海量数据加载进 MCP 进程*：

- **仅查询自有作业。** `status`/`queue`/提交时的活跃作业计数只运行
  `squeue -j <tracked ids>`。没有跟踪作业时该调用不发起任何 `squeue`。
  他人的作业元数据永远不进入本进程。
- **有界文件读取。** `hpc.files.read` 只返回一个切片，上限为
  `files.max_read_slice_bytes`（默认 256 KiB）；工具描述引导 agent 先用
  `hpc.files.search`，而不是一路翻页到 EOF。
- **有界递归列举。** `hpc.files.list` 每次远程调用只枚举一层，用 `head`
  截断远端输出（通过 `bash -o pipefail` 检测 SIGPIPE），并用 `depth:N`
  cursor 续读。
- **有界搜索。** `hpc.files.search`（单文件：先 `stat`，超限即拒；目录树：
  `grep -rn` + 排除目录 + 每文件 `-m` + 全局 `head` + `timeout`）的每一项预算
  都在服务端钳制；pattern 作为已加引号的 grep 参数传递，控制字符被拒绝。
- **高阶工具。** `hpc.jobs.diagnose`（状态 + 记账 + 有界尾部 + 错误签名）与
  `hpc.project.snapshot`（有界目录树 + git 摘要 + 已跟踪作业）把多次低级调用
  合并为一次，且每次查询都有界。
- **拓扑发现。** `hpc.cluster.topo` 把一份 `sinfo` 摘要（本就在白名单内，且限定
  `-p <allowed partitions>`，未授权分区永远不会进入响应）与一个**固定的、
  服务端生成的**采集脚本结合起来，该脚本作为 1 核作业运行在计算节点上。任何工具
  参数都不会被插值进脚本，脚本只读节点本地文件——从不查询
  `squeue`/`sacct`/`scontrol`，因此共享账号隔离规则在计算侧同样成立。结果缓存于
  进程内，并以 JSON 文件形式存放于 `$ROOT/.hpc-mcp/topo/`，有效期
  `topology.cache_ttl_seconds`（默认 24 h），从而避免排队中的采集作业被逐次重复
  提交；超过 `topology.wait_seconds` 仍在等待的采集作业会返回 `status: "pending"`
  并被复用（最终可能被取消），而不是被重复提交。
- **查询缓存。** 幂等的只读工具按 `tool + 规范化参数` 在
  `cache_ttl_seconds`（默认 2 s，0 禁用）内去重；任何写操作会使整个缓存失效。
- **临时空间。** agent 的一次性文件（测试脚本、测试日志、作业包装脚本、中间产物）
  统一放在 `$ROOT/.hpc-mcp/tmp/<会话>/`（每个 MCP 实例一个目录），由 `hpc.info`
  以 `session_tmp_dir` 返回，任务结束后 agent 用一次递归 `hpc.files.delete` 删除
  ——临时文件不会堆积在项目目录里。
- **作业脚本。** 传给 `hpc.slurm.submit` 的单个 `.sh` 路径会渲染为 `bash <脚本>`；
  sbatch 只读取该文件，因此上传的脚本不需要可执行位，`chmod`（登录节点禁用）
  也不再出现在工作流里。

## 已知残余风险（三层）

| 层 | 强制机制 | 防护目标 |
|---|---|---|
| 1. MCP policy | 路径沙箱、命令白名单、Slurm 资源策略、作业归属、查询预算、审计 | 行为异常的 agent |
| 2. Slurm | 分区白名单、资源/并发上限、唯一 `sbatch` 入口 | 计算资源滥用 |
| 3. OS/集群 | 独立 Unix UID、Slurm 作业隔离 + filesystem ACL、容器沙箱，或特权远端辅助服务 | *恶意代码*隔离 |

远程主机是共享 Unix 账号，因此拥有同等账号权限的进程可以在 canonical 化与最终命令
之间对路径发起竞态。Server 尽力缩小这个窗口并拒绝符号链接目标，但要完全消除竞态，
需要基于 `openat(2)`/`O_NOFOLLOW` 的特权远端辅助服务，或为每个用户分配独立 Unix
账号。同样，JSON 跟踪文件是逻辑上的会话边界，而非操作系统级访问控制边界：同等
Unix 账号可以读取或修改它。

关键在于，`hpc.slurm.submit` 赋予了 agent 以该 Unix UID 在计算节点上执行程序的
能力。Layer 1-2 无法约束随后读取该 UID 可读一切的恶意代码。不要依赖对"明显恶意"
的 Julia/Python 做正则/策略过滤来充当 OS 级隔离机制；当同账号下的恶意代码在威胁
模型内时，请部署 Layer 3（独立 UID、作业隔离 + ACL，或容器/沙箱）。

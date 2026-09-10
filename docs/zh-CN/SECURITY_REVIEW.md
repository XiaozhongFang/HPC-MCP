# 安全审查（2026-09-04）

其他语言：[English](../SECURITY_REVIEW.md) | [日本語](../ja/SECURITY_REVIEW.md) | [한국어](../ko/SECURITY_REVIEW.md) | [繁體中文](../zh-TW/SECURITY_REVIEW.md)

## 发现与修复

| 严重度 | 领域 | 发现 | 修复 |
| --- | --- | --- | --- |
| Critical | 文件传输 | 上传/下载允许任意本地路径，包括符号链接目标与凭证文件。 | `local_root`、路径组件校验、凭证文件名拒绝清单、批量传输控制字符拒绝，以及下载后大小上限。 |
| Critical | Safe shell | 词法上位于 root 内的符号链接可让 `cat`/`grep` 读到远程沙箱之外；可执行文件路径与 follow 类选项又扩大了命令面。 | 对操作数做远程 canonical 校验、只允许可执行文件 basename 的策略，以及拒绝 `-L/-H/-follow`。 |
| High | 作业跟踪 | 存储的 `job_dir` 与跨会话条目被信任，导致可访问另一会话作业的 output/accounting/cancel。 | 会话绑定的 schema 校验、由数字 job ID 推导路径；canonical 元数据目录与加锁的原子写入。 |
| High | 资源耗尽 | SSH/SFTP 使用无界的 `communicate()` 缓冲，并接受超大命令/环境变量负载。 | 流式字节上限、子进程回收、有界的 argv/environment/脚本大小，以及经过校验的 timeouts。 |
| High | 配置 | 注入的 `environ` 被忽略；畸形的嵌套 YAML 与负值上限会以运行时错误或非法策略状态泄漏出来。 | 确定性的环境来源、带类型的 section/limit 校验、canonical roots，以及仅允许安全的 host-key 模式。 |
| Medium | 审计 | 仅用正则的脱敏漏掉了字典键中的秘密（如 `{"token": "..."}`）与控制字符。 | 递归的、按字段名感知的脱敏，控制字符转义与截断。 |
| Medium | 并发 | 并发的 submit 调用可能同时通过活跃作业检查。 | 异步提交锁与原子元数据锁目录。 |
| Low | 可维护性 | 未使用的 helper 与 import 模糊了安全边界。 | 删除死代码，并记录 policy/service/transport 的职责归属。 |

## 验证

回归测试覆盖路径穿越、符号链接逃逸、命令注入、login/compute 分离、Slurm 上限、
作业归属、配置与文件服务。运行：

```bash
python -m compileall -q src
python -m pytest -q
git diff --check
```

静态检查在可用时还应加入 Bandit/Ruff，并在部署环境中运行 `python -m pip check`。

## 运维要求

尽量使用 `StrictHostKeyChecking=yes` 并预先填充 `known_hosts`。保持
`HPC_MCP_ROOT` 仅对目标用户可见，并把 `HPC_MCP_LOCAL_ROOT` 设为传输所需的
最小本地项目目录。新的进程会话无法管理更早会话登记的作业；这是应用层边界。
同等 Unix 账号仍可篡改 JSON 跟踪文件或对远程路径发起竞态，因此敌对的同账号部署
需要独立 Unix 账号或特权远端辅助服务。

---

# 安全 + 效率审查（v0.2，2026-09）

## 范围

本轮把 server 从"阻止 agent 乱来"升级为"帮助 agent 以最少的远程 I/O 获取最大
价值"，同时不削弱任何既有边界。以下所有发现均在本轮修复，并由回归测试覆盖。

## 发现与修复

| 严重度 | 领域 | 发现 | 修复 |
| --- | --- | --- | --- |
| Critical | 共享账号 Slurm | `_queue_states()` / `queue()` 运行全账号 `squeue` 并在 Python 里过滤，导致其他用户的作业元数据进入 MCP 进程。 | 所有归属检查现在只运行 `squeue -j <tracked ids>`（按 500 分批）；没有跟踪作业时不发起任何 `squeue`。新增 `tests/security/test_shared_account_isolation.py` 证明他人的行永远不会进入结果，且永不发出不带 `-j` 的查询。 |
| High | 文件读取 | 工具描述鼓励把整个日志从 `offset=0` 翻到 EOF；每次 `read` 都是一次 stat+dd+base64 往返。 | `hpc.files.read` 现在是由 `files.max_read_slice_bytes`（默认 256 KiB）钳制的 bounded slice，描述引导先使用 `hpc.files.search`。 |
| High | 目录列举 | 递归 `find` 会流式输出整棵树，Python 事后才截断。 | `hpc.files.list` 每次远程调用只枚举一层，用 `head` 截断远端输出（通过 `bash -o pipefail` 检测 SIGPIPE），以 `depth:N` cursor 续读，并钳制 `max_depth`（默认 3）。 |
| High | 日志诊断 | Agent 需要拼装 `status -> output -> accounting -> read` 序列（工具抖动）。 | 新增 `hpc.jobs.diagnose`（状态 + 记账 + 有界尾部 + 错误签名）与 `hpc.jobs.wait_and_diagnose`；每个内部查询都仅限自有作业且有上限。 |
| High | 搜索 | 没有一等公民的有界搜索；agent 只能临时拼 `grep` 管道。 | 新增 `hpc.files.search`：单文件先 `stat` 并拒绝超限；目录树用 `grep -rn` + 排除目录 + `-m` + `head` + `timeout`；每项预算都由服务端钳制；pattern 作为已加引号的 argv 传递（无注入）。 |
| Medium | 查询去重 | 重复的相同 status/list 调用每次都重跑 SSH。 | `QueryCache`（TTL `cache_ttl_seconds`，默认 2 s，0 禁用）按规范化 tool+args 对幂等只读工具去重；任何写操作使整个缓存失效。 |
| Medium | 命令成本 | 合法命令（`find`/`du`/`sort`/`git grep`）仍可能压满登录节点。 | `security/command_cost.py` 为每条 safe 命令分层（LOW 15s/256KiB，MEDIUM 30s/512KiB，HIGH 10s/512KiB）；在 `SafeExec` 中强制，并返回 `cost_tier`/`timeout_seconds`。 |
| Medium | 响应泄漏 | 审计脱敏并不保护*返回给 agent 的响应*（例如作业 stderr 里的 `password=`）。 | `response_redactor.py` 在 `files.read/search`、`slurm.output`、`shell.run_safe`、`diagnose`、`snapshot` 中遮蔽明显的秘密（`password=`/`token=`/`api_key`/`Bearer`/AWS/PEM 块）；模式保持最小以免破坏科研日志内容。 |
| Medium | 信息暴露 | `hpc.info` 返回了原始 `local_roots` 路径（可能含用户名/项目名）。 | 替换为 `workspace_available`/`transfer_enabled` 布尔值；回归测试断言本地用户名永不出现。 |
| Medium | 写竞态 | 覆盖他人已修改的文件可能静默破坏工作。 | `hpc.files.write` 接受 `expected_size`/`expected_mtime`/`expected_sha256`；任何不匹配（或文件缺失）都 fail-closed 拒绝写入。 |
| Low | 高阶执行 | 专家与 agent 共用一个原始 argv 提交面。 | 新增 `hpc.job.run`，从固定 runtime profile（julia/python/moose/bash）构建 argv 并复用完整提交策略；`hpc.slurm.submit` 保留给专家。 |

## 新增安全回归套件（本轮）

- `test_shared_account_isolation.py` —— 无全账号 `squeue`；他人作业永不进入结果；
  没有跟踪作业时不扫描；他人的 cancel/accounting/output 被拒绝。
- `test_files_search.py` —— 搜索预算、沙箱逃逸、控制字符与注入拒绝、`-m`/`head`
  上限、上下文解析。
- `test_command_cost.py` —— 分层归类、git 子命令细化、在 `SafeExec` 中强制的
  timeout/output 钳制。
- `test_response_redaction.py` —— password/token/API key/Bearer/AWS/PEM 被遮蔽，
  正常日志内容保留，递归有界。
- `test_info_minimal_exposure.py` —— `hpc.info` 隐藏本地 root 与 SSH 细节。
- `test_cache.py` —— 去重键、TTL 过期、容量上限、写操作失效。
- `test_project_snapshot.py` —— 有界目录树、git 摘要、仅限自有作业。
- `test_files_service.py` 增补 —— 读取切片预算、list 分页/cursor、写并发保护。
- `test_slurm_manager.py` 增补 —— diagnose 聚合与上限、wait_and_diagnose、
  job.run 策略复用。

## 残余风险（未变，现以三层显式表述）

Layer 1（MCP policy）与 Layer 2（Slurm 资源策略）让*行为异常的 agent* 无害，
但无法隔离在共享 Unix UID 下运行于计算节点的*恶意代码*：`hpc.slurm.submit`
授予的就是该 UID 的权限。应对该威胁模型需要 Layer 3（独立 Unix UID、
Slurm 作业隔离 + filesystem ACL、容器/沙箱，或特权远端辅助服务）。
对"明显恶意"程序做正则/策略过滤被明确地不作为 OS 级隔离机制。参见
`README.md` / `SECURITY.md` / `docs/ARCHITECTURE.md`。

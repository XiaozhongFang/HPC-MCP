# Security Policy

HPC-MCP 的安全模型：**所有关键边界由 MCP Server 的确定性代码强制执行**，不依赖 Skill、system prompt 或工具描述的正确性。

实现边界和请求流详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，本轮审查记录见 [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md)。

## 威胁模型

- HPC 使用**共享账号**（一个 SSH 账号对应多个使用者）。
- Agent（LLM）可能收到错误、含糊或被注入的指令。
- 目标：即使 Agent 行为异常，也不能越权。

## 强制边界

### 1. 路径沙箱（Path Sandbox）

- 所有远程路径先经词法规范化（`posixpath.normpath`，拒绝 `..`、tilde、NUL/CR/LF、相对路径）。
- 再经远程 `realpath` 对**已存在路径**或**最近已存在父目录**做 canonical 校验，阻断符号链接逃逸。
- 任何一步无法确定即拒绝。
- 远程 root 必须是专用目录，不能配置为 `/`；本地传输另有 `local_root` 沙箱（默认启动目录），拒绝符号链接、`.ssh` 和常见私钥文件。

**不允许**：访问 `$HPC_MCP_ROOT` 之外的一切路径，包括其他共享账号使用者的目录、`/etc`、`/tmp`、系统目录；通过 symlink 逃逸；rename/copy 到 root 之外；删除 root 本身。

### 2. 登录节点命令策略（Command Policy）

- 仅白名单轻量命令可执行（`ls`、`cat`、`grep`、`git status/diff/log`、`module list` 等，可配置扩展）。`squeue`、`sacct`、`scontrol` 禁止从 `hpc.shell.run_safe` 调用，避免绕过作业归属检查。
- 拒绝所有 shell 元字符：`;` `&&` `||` `|` `>` `>>` `<` `$( )` `` ` `` `${ }` `&` 换行等。
- 拒绝计算/编译程序：`julia`、`python`、`make`、`cmake`、`ninja`、`mpirun`、`srun`、编译器、`pytest`、`matlab`、容器运行时等。
- 拒绝危险程序：`sudo`、`ssh`/`scp`/`rsync`、`curl`/`wget`、`nohup`/`setsid`/`tmux`、`kill`、`chmod`、`dd`、嵌套 shell、`xargs`、`eval` 等。
- `git` 仅限只读子命令（拒绝 `commit/push/pull/clone/-c/--exec-path/--git-dir`）；`module` 仅查询；`find` 拒绝 `-exec/-delete`。
- 本地与远端均无 `shell=True`；argv 经 `shlex.join` 重新序列化。
- 解析失败即拒绝。
- `cat`/`grep`/`find` 等命令的路径操作数会执行远程 canonical 校验；`find -L/-H/-follow`、`ls -L`、`du -L` 均拒绝。`env`/`printenv` 仅在清空后的最小环境中运行。

### 3. Slurm 资源策略

- 分区白名单（默认空 = 全拒）。
- 节点/CPU/内存/GPU/时长/并发上限，超限即拒。
- 工作目录必须在 root 内；命令 argv 不得含控制字符。
- 作业输出固定写入 `$ROOT/.hpc-mcp/jobs/<id>/`，不得越界。

### 4. 作业归属隔离

- 仅可管理当前服务会话提交并登记的 job（tracked_jobs.json 中的 `tool_session` 必须匹配）；重启后的旧会话作业默认不可由新实例接管。
- 不得查看/取消其他使用者或他实例的作业。
- 该登记文件是应用层隔离，不是同一 Unix UID 下的强制访问控制；敌对共享账号必须使用独立 UID 或特权远端辅助服务。

### 5. SSH 边界

- 只允许连接配置的单一 host；BatchMode、StrictHostKeyChecking、连接超时。
- **不读取、不输出私钥内容**；推荐 `~/.ssh/config` 管理。
- SSH/SFTP 输出采用流式字节上限，超时会 kill 并回收子进程；ControlMaster 在服务退出时关闭。
- 不提供 `hpc.ssh(command=...)` 或任何任意命令工具。
- 无端口转发、无 ProxyJump、无多主机。

### 6. 凭证与日志

- 私钥、密码、token 永不写日志；审计日志对敏感模式脱敏并截断。
- 审计字段：timestamp、tool、args（脱敏）、decision(ALLOW/DENY)、reason、job_id、duration。
- 脱敏按字段名递归处理（password/token/secret/private-key 等），并清除控制字符后截断；未知异常不会原样返回给 Agent。

## 明确不实现（v1）

任意远程 shell、任意 SSH host、sudo、远程端口转发、job 迁移、多主机 SSH、HPC 常驻 daemon、远程 HTTP MCP、自动账号切换、自动凭证管理、修改 `~/.ssh/config` / `authorized_keys`。

## 失败安全

配置不完整、SSH 异常、路径无法解析、命令解析失败、Slurm 参数无法解析、分区/主机不确定——统一 **DENY**，绝不回退到无限制 shell。

## 报告安全问题

请通过仓库 Issue 私密报告或联系维护者，勿在公开渠道披露未修复的细节。

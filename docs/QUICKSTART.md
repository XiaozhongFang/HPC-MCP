# 快速上手与排障（手把手）

这份文档带你从零跑通 hpc-mcp，并解释每个参数、常见“卡住”现象的原因。

---

## 0. 一分钟理解它在干什么

`hpc-mcp` 是一个 **MCP Server（stdio）**，它不是一次性命令：

- 你用 `hpc-mcp ...` 启动它后，它会打印一行 `starting: ...` 然后**停在那里等待输入**。
  **这是正常的！** 它在等 MCP 客户端（Codex / Reasonix）通过标准输入发来请求。
  它**此刻还没有连接 SSH**，所以光看启动日志判断不了 SSH 通不通。
- 真正的 SSH 连接发生在客户端第一次调用工具（比如 `hpc.info`）时。
- 想**立刻验证配置和网络是否通**，用 `--check`（见第 3 步），它会主动连一次 SSH 并打印结果。

---

## 1. 安装

```bash
cd ~/git_repo/HPC-MCP

# 推荐：先升级 pip（旧 pip + 旧 setuptools 会打出 UNKNOWN-0.0.0 空壳包）
python3 -m pip install --user -U pip

# 安装本包
python3 -m pip install --user .
```

验证：

```bash
export PATH="$HOME/.local/bin:$PATH"   # 建议写进 ~/.bashrc
hpc-mcp --version                       # 应输出 hpc-mcp 0.1.0
hpc-mcp --help                          # 应列出所有参数
```

> 如果 `pip install .` 输出里出现 `UNKNOWN-0.0.0`，说明 pip/setuptools 太旧，
> 先 `python3 -m pip install --user -U pip setuptools` 再重装即可。

---

## 2. 准备 SSH：先确保“裸 ssh 能免密登上”

hpc-mcp 底层用的是系统 OpenSSH，**一切 ssh 问题先在终端里复现**。先手动测：

```bash
ssh nudt_liujie05@192.168.10.10 "echo OK && hostname"
```

三种结果：

| 结果 | 含义 | 怎么办 |
|---|---|---|
| 打印 `OK` + 主机名 | 免密 + 网络都通 | 直接进第 3 步 |
| 卡在 `password:` | 没有免密 | 配 SSH key：`ssh-copy-id nudt_liujie05@192.168.10.10` |
| `Connection timed out` / 100% 丢包 | **网络根本不通** | 见下面“网络不通” |

### 网络不通（最常见）

`192.168.10.10` 是**内网地址**。如果你当前不在校园网/没连 VPN，从外网是连不上的。

- 先确认平时怎么上的：要不要先连 VPN？要不要走跳板机？
- 需要 VPN：连上 VPN 再测。
- 需要跳板机：见第 4 步的 `~/.ssh/config` 配置（ProxyJump）。

### 强烈推荐：用 `~/.ssh/config` 管理连接

把连接细节写进 config，hpc-mcp 的 `--host` 直接引用别名，最省心：

```sshconfig
# ~/.ssh/config
Host tianhe
    HostName 192.168.10.10
    User nudt_liujie05
    IdentityFile ~/.ssh/id_ed25519
    # 需要跳板机时打开下面这行（把 jump-host 换成你的跳板）：
    # ProxyJump jump-host
```

配好后终端测 `ssh tianhe "echo OK"` 能通，hpc-mcp 就用 `--host tianhe`。

---

## 3. 用 `--check` 验证（关键！不要跳过）

`--check` 会**主动连一次 SSH** 并打印远端信息，配置/网络对不对立刻知道：

```bash
hpc-mcp \
  --host 192.168.10.10 \
  --user nudt_liujie05 \
  --root /thfs1/home/nudt_liujie05/fangxiaozhong \
  --local-root "$PWD" \
  --check
```

- 成功：打印 `Configuration OK. Remote probe succeeded:` 及 hostname / 远程用户 / Slurm 是否可用。
- 失败：打印 `Connection check FAILED: ...` 及原因（超时 / 拒绝 / 认证失败），据此排查。

> **判断依据**：`--check` 成功 = 网络和配置都没问题，之后接 Codex/Reasonix 就能用。
> `--check` 都失败 = 先去解决 SSH/网络，别急着接客户端。

---

## 4. 参数逐个说明（对应你那条命令）

你运行的：

```bash
hpc-mcp --host 192.168.10.10 --user nudt_liujie05 \
        --root /thfs1/home/nudt_liujie05/fangxiaozhong \
        --local-root "/home/fangxiaozhong"
```

| 参数 | 作用 | 环境变量 | 是否必填 |
|---|---|---|---|
| `--host` | HPC 主机（IP 或 `~/.ssh/config` 别名） | `HPC_MCP_HOST` | **必填** |
| `--user` | SSH 登录用户（这里是共享账号） | `HPC_MCP_USER` | 建议填 |
| `--root` | **远程**沙箱根目录，Agent 只能动这里面的文件 | `HPC_MCP_ROOT` | **必填** |
| `--local-root` | **本地**允许上传/下载的目录（默认=启动时所在目录） | `HPC_MCP_LOCAL_ROOT` | 可选 |
| `--port` | SSH 端口（默认 22） | `HPC_MCP_PORT` | 可选 |
| `--identity-file` | 私钥路径（默认用 `~/.ssh/config`） | `HPC_MCP_IDENTITY_FILE` | 可选 |
| `--config` | YAML 配置文件路径 | — | 可选 |
| `--check` | 只验证连通性然后退出 | — | 可选 |

**关于 `--local-root`**：它限制 `hpc.files.upload`/`download` 能访问的**本地**目录范围，防止 Agent 读你本地的 `.ssh` 等敏感目录。一般设为当前项目目录（`$PWD`）即可。**它不是必填**，不传就默认当前目录。

**关于 `--root`**：这是远程集群上**专属于你的子目录**（因为登录用的是共享账号 `nudt_liujie05`）。你填的 `/thfs1/home/nudt_liujie05/fangxiaozhong` 就是你在这个共享账号下的个人空间——完全正确。

---

## 5. 用配置文件代替一长串参数（推荐）

把参数固化到 YAML，以后一条命令启动：

```bash
mkdir -p ~/.config/hpc-mcp
cat > ~/.config/hpc-mcp/tianhe.yaml <<'EOF'
host: tianhe                                  # ~/.ssh/config 里的 Host 别名
user: nudt_liujie05
root: /thfs1/home/nudt_liujie05/fangxiaozhong
local_root: /home/fangxiaozhong               # 本地允许目录

slurm:
  allowed_partitions: [compute]               # 改成你集群真实分区名！
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"
EOF
```

启动 / 自检：

```bash
hpc-mcp --config ~/.config/hpc-mcp/tianhe.yaml --check
```

> 注意：`allowed_partitions` 默认是**空**（= 拒绝一切提交，fail-closed）。
> 务必改成你集群真实的分区名（用 `sinfo` 在集群上查）。

---

## 6. 接入 Codex / Reasonix

`--check` 通过后，注册到客户端：

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=tianhe \
  --env HPC_MCP_USER=nudt_liujie05 \
  --env HPC_MCP_ROOT=/thfs1/home/nudt_liujie05/fangxiaozhong \
  --env HPC_MCP_LOCAL_ROOT=/home/fangxiaozhong \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=tianhe \
  --env HPC_MCP_ROOT=/thfs1/home/nudt_liujie05/fangxiaozhong \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

注册后客户端会用 stdio 启动 `hpc-mcp`，这时你在客户端里就能让它「列出我的项目目录」「提交一个 Slurm 作业」了。

---

## 7. “卡住”现象对照表

| 你看到的现象 | 真实原因 | 处理 |
|---|---|---|
| 启动后停在 `starting: ...` 不动 | **正常**，stdio server 在等客户端输入 | 不用管，去客户端里调用工具；或用 `--check` 验证 |
| `--check` 报 `Connection timed out` | **网络不通**（内网 IP 需 VPN） | 连 VPN / 配跳板机，再 `ssh` 手动测 |
| `--check` 报 `Permission denied` | 没免密 | `ssh-copy-id` 配 key |
| 首次连接问 `Are you sure ... yes/no?` | 主机密钥没固定 | 手动 `ssh` 一次输入 yes；或配置 `strict_host_key_checking: accept-new` |
| 工具调用全被拒 `No Slurm partitions are allowed` | 分区白名单为空 | 配置 `slurm.allowed_partitions` |
| `pip install .` 得到 `UNKNOWN-0.0.0` | pip/setuptools 太旧 | `python3 -m pip install --user -U pip` 后重装 |

---

## 8. 看日志

- 所有日志走 **stderr**（stdout 只留给 MCP 协议）。
- 想落盘：`--log-file ~/.local/share/hpc-mcp/hpc-mcp.log`。
- 想看每次调用的允许/拒绝：`--log-level DEBUG`。

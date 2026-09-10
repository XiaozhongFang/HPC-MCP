# Quick Start & Troubleshooting (step by step)

This document walks you from zero to a working hpc-mcp, and explains every flag plus the usual "it hangs" symptoms.

> Other languages: [简体中文](zh-CN/QUICKSTART.md) | [日本語](ja/QUICKSTART.md) | [한국어](ko/QUICKSTART.md) | [繁體中文](zh-TW/QUICKSTART.md)

---

## 0. What it is, in one minute

`hpc-mcp` is an **MCP server (stdio)** — it is not a one-shot command:

- After you launch it with `hpc-mcp ...`, it prints a `starting: ...` line and then **just sits there waiting for input**.
  **That is normal!** It is waiting for an MCP client (Codex / Reasonix) to send requests over stdin.
  It has **not connected over SSH yet**, so the startup log tells you nothing about SSH reachability.
- The real SSH connection happens the first time the client calls a tool (e.g. `hpc.info`).
- To **verify the config and the network right now**, use `--check` (step 3): it actively connects once and prints the result.

---

## 1. Install

**Strongly prefer a dedicated virtual environment** — do not dump it into the system `~/.local` with `pip install --user`, or it will fight with packages already installed there (torch, httpcore, ...) and you will see `UNKNOWN-0.0.0` ghost packages and `pip's dependency resolver does not take into account...` warnings.

### Option A (recommended): an isolated virtualenv

If the system lacks `python3-venv`, use virtualenv (user-level, no root needed):

```bash
# 1. Install virtualenv (once)
python3 -m pip install --user virtualenv

# 2. Create the project environment (once; reuse it afterwards)
python3 -m virtualenv ~/venvs/hpc-mcp

# 3. Activate it and install hpc-mcp
source ~/venvs/hpc-mcp/bin/activate
cd ~/git_repo/HPC-MCP
pip install .

# 4. Use it
hpc-mcp --version        # should print hpc-mcp 0.1.0
hpc-mcp --help
```

> If `python3-venv` is available, the stdlib route works too:
> `python3 -m venv ~/venvs/hpc-mcp` (the rest is identical).

If `hpc-mcp` is not found afterwards, check that the environment is activated, or call it by full path:

```bash
~/venvs/hpc-mcp/bin/hpc-mcp --version
```

When registering the MCP server with Codex / Reasonix, use that full path:

```bash
codex mcp add hpc ... -- ~/venvs/hpc-mcp/bin/hpc-mcp
```

### Option B: conda

If you already use conda:

```bash
conda create -n hpc-mcp python=3.10
conda activate hpc-mcp
cd ~/git_repo/HPC-MCP
pip install .
```

### Option C: straight into ~/.local (not recommended, temporary only)

```bash
cd ~/git_repo/HPC-MCP
python3 -m pip install --user .
```

> If you get `UNKNOWN-0.0.0` or dependency-conflict warnings, your pip/setuptools is too old or your `~/.local` is already a mess — use Option A instead.

### If something goes wrong

- `pip install .` yields `UNKNOWN-0.0.0` → pip/setuptools too old, environment not isolated.
- `ERROR: pip's dependency resolver does not currently take into account...`
  → other packages in `~/.local` conflict (torch/httpcore etc.). **This does not break hpc-mcp**, but it does mean that environment is mixed; switch to a virtualenv.

### ⚠️ First thing after installing: make sure passwordless SSH works

**hpc-mcp forces passwordless login (`BatchMode=yes`, no password prompts).** Without it, every tool call fails with `Permission denied` as soon as the server starts — and **no password prompt will ever appear**. So after installing and before wiring up the cluster, **verify key auth manually**:

```bash
# Key point: -o BatchMode=yes mimics how hpc-mcp connects.
# If this returns OK without asking for a password, key auth is ready:
ssh -o BatchMode=yes -o ConnectTimeout=10 username@192.168.12.12 "echo OK"
```

- Prints `OK` → key auth is configured, continue with step 3.
- `Permission denied (publickey...)` → **no key auth yet**, set it up first:
  ```bash
  ssh-copy-id username@192.168.12.12   # asks for the password once, never again
  ```
  then re-run the BatchMode check above.
- `Connection timed out` → the network is unreachable; connect the VPN / configure the jump host first (step 2).

> If you manage connections through `~/.ssh/config`, use the `Host` alias in the command instead:
> `ssh -o BatchMode=yes my-hpc "echo OK"`

---

## 2. Prepare SSH: make plain `ssh` work passwordless first

hpc-mcp uses the system OpenSSH underneath, so **reproduce every ssh problem in a terminal first**. Test manually:

```bash
ssh username@192.168.12.12 "echo OK && hostname"
```

Three possible outcomes:

| Result | Meaning | What to do |
|---|---|---|
| Prints `OK` + hostname | key auth and network both fine | continue with step 3 |
| Hangs at `password:` | no key auth | configure a key: `ssh-copy-id username@192.168.12.12` |
| `Connection timed out` / 100% packet loss | **network unreachable** | see "Network unreachable" below |

### Network unreachable (the most common case)

`192.168.12.12` is a **private-network address**. If you are not on the campus network or VPN, you simply cannot reach it from the outside.

- First ask yourself how you normally reach it: does it need a VPN first? a jump host?
- VPN required: connect, then test again.
- Jump host required: see the `~/.ssh/config` example with `ProxyJump` in step 4.

### Strongly recommended: manage connections with `~/.ssh/config`

Put the connection details in the config and let `--host` reference the alias — least friction:

```sshconfig
# ~/.ssh/config
Host 192.168.12.12
    HostName 192.168.12.12
    User username
    IdentityFile ~/.ssh/id_ed25519
    # Uncomment the next line when you need a jump host (replace jump-host):
    # ProxyJump jump-host
```

Once `ssh 192.168.12.12 "echo OK"` works in a terminal, use `--host 192.168.12.12` with hpc-mcp.

---

## 3. Verify with `--check` (important — do not skip)

`--check` **actively connects over SSH once** and prints remote information, so you immediately know whether config and network are right:

```bash
hpc-mcp \
  --host 192.168.12.12 \
  --user username \
  --ssh-bin /mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --sftp-bin /mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --root /home/username/alice \
  --local-root "$PWD" \
  --check
```

- Success: prints `Configuration OK. Remote probe succeeded:` plus hostname / remote user / whether Slurm is available.
- Failure: prints `Connection check FAILED: ...` with the cause (timeout / refused / auth failure) to troubleshoot from.

> **How to read it**: `--check` succeeding means network and config are both fine, and you can wire up Codex/Reasonix next.
> `--check` failing means fix SSH/network first — do not bother with the client yet.

---

## 4. Flags

> This section only covers **startup/self-check** CLI flags. The full config-file keys
> (`slurm.*`, `files.*`, `topology.*` and all environment variables) are in
> [CONFIGURATION.md](CONFIGURATION.md); **per-tool arguments** are in
> [TOOLS.md](TOOLS.md).

| Flag | Purpose | Environment variable | Required |
|---|---|---|---|
| `--host` | HPC host (IP or a `~/.ssh/config` alias) | `HPC_MCP_HOST` | **yes** |
| `--user` | SSH login user (the shared account here) | `HPC_MCP_USER` | recommended |
| `--root` | **remote** sandbox root; the agent may only touch files below it | `HPC_MCP_ROOT` | **yes** |
| `--local-root` | **local** directories allowed for upload/download (default = working directory + `/tmp`; multiple via `local_roots`) | `HPC_MCP_LOCAL_ROOT` / `HPC_MCP_LOCAL_ROOTS` | optional |
| `--port` | SSH port (default 22) | `HPC_MCP_PORT` | optional |
| `--identity-file` | private-key path (defaults to `~/.ssh/config`) | `HPC_MCP_IDENTITY_FILE` | optional |
| `--ssh-bin` | ssh executable path, e.g. `/usr/bin/ssh`, `@/usr/bin/ssh`, `@/mnt/c/.../ssh.exe` | `HPC_MCP_SSH_BIN` | optional |
| `--sftp-bin` | sftp executable path (same forms as `--ssh-bin`) | `HPC_MCP_SFTP_BIN` | optional |
| `--config` | YAML config file path | — | optional |
| `--check` | verify connectivity only, then exit | — | optional |
| `--log-file` | append logs to this file (stderr is still written) | `HPC_MCP_LOG_FILE` | optional |
| `--log-level` | log level: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (default `INFO`) | `HPC_MCP_LOG_LEVEL` | optional |
| `--version` | print the version and exit | — | optional |

Subcommand `hpc-mcp mcp-add` (writes the Codex / Reasonix MCP config; idempotent, never damages existing config):

| Flag | Purpose | Default |
|---|---|---|
| `--client` | which client(s) to update: `codex`, `reasonix`, `all` | `all` |
| `--config` | YAML config path (its host/root etc. get written into the client env) | — |
| `--host` / `--user` / `--root` | pass values directly (when not using a config file) | — |

**About `--local-root`**: it bounds the **local** directories `hpc.files.upload`/`download` may touch, so the agent cannot read sensitive local paths such as `.ssh`. Setting it to the current project directory (`$PWD`) is usually enough. It is **not required**; the default is the current directory.

**About `--root`**: this is the **subdirectory dedicated to you** on the remote cluster (since the login uses the shared account `username`). `/home/username/alice` is your personal space under that shared account — exactly right.

---

## 5. Use a config file instead of a long flag list (recommended)

Freeze the flags into YAML and start with a single command afterwards:

```bash
mkdir -p ~/.config/hpc-mcp
cat > ~/.config/hpc-mcp/192.168.12.12.yaml <<'EOF'
host: 192.168.12.12                                  # Host alias from ~/.ssh/config
user: username
root: /home/username/alice
local_root: /home/alice               # allowed local directory
ssh_bin: /mnt/c/Windows/System32/OpenSSH/ssh.exe
sftp_bin: /mnt/c/Windows/System32/OpenSSH/sftp.exe

slurm:
  allowed_partitions: [thcp1]               # replace with your real partition name!
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"

# Optional: compute-node topology discovery (used by hpc.cluster.topo)
# topology:
#   enabled: true             # false = do not register the tool, never submit a probe job
#   cache_ttl_seconds: 86400  # topology cache for 24h
EOF
```

Start / self-check:

```bash
hpc-mcp --config ~/.config/hpc-mcp/192.168.12.12.yaml --check
```

> Note: `allowed_partitions` defaults to **empty** (= deny every submission, fail-closed).
> Always set it to your cluster's real partition names (look them up with `sinfo` on the cluster).

> **Key naming**: config-file keys use **underscores** (`ssh_bin`, `local_root`,
> `allowed_partitions`), matching `config/example.yaml`. Hyphenated forms
> (`ssh-bin`) are recognised as aliases but not recommended.
> A misspelled key does not error out; it only logs
> `Ignoring unknown config key 'xxx'` and is ignored — if you see that line, fix the config.

> **Changed the source/config but nothing happened?** If you installed with
> `pip install .`, the virtualenv holds a **copy**; editing the repo source has no
> effect until you reinstall:
> ```bash
> source ~/venvs/hpc-mcp/bin/activate
> cd ~/git_repo/HPC-MCP && pip install .
> # or install editable so future edits apply immediately: pip install -e .
> ```

---

## 6. Hook up Codex / Reasonix

Once `--check` passes, register the server with your client:

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  -- \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

After registering, the client starts `hpc-mcp` over stdio, and you can ask it to "list my project directory" or "submit a Slurm job".

---

## 7. "It hangs" symptom table

| Symptom | Real cause | Fix |
|---|---|---|
| Stops at `starting: ...` and does nothing | **normal** — a stdio server waiting for client input | nothing to do; call tools from the client, or verify with `--check` |
| `--check` reports `Connection timed out` | **network unreachable** (private IP needs VPN) | connect the VPN / configure the jump host, then test `ssh` manually |
| Works from the CLI but not with the config file | `ssh_bin` in the config never took effect (typo'd key / stale installed copy), so it fell back to `ssh` on PATH (the Linux ssh under WSL does not use the Windows VPN route) | check the `using ssh executable: ...` line in the log; fix the key name (section 5) and reinstall with `pip install .` |
| `--check` reports `Permission denied` | no key auth | configure a key with `ssh-copy-id` |
| `--check` reports `getsockname failed: Not a socket` | broken SSH control-socket reuse (common in WSL or with old installs) | update and reinstall hpc-mcp; current versions always use a standalone plain `ssh` process |
| First connection asks `Are you sure ... yes/no?` | host key not pinned | connect manually once and answer yes; or set `strict_host_key_checking: accept-new` |
| Every tool denied with `No Slurm partitions are allowed` | partition allow-list is empty | configure `slurm.allowed_partitions` |
| `pip install .` produces `UNKNOWN-0.0.0` | pip/setuptools too old | reinstall inside a virtualenv (section 1, Option A) |
| `ERROR: pip's dependency resolver does not currently take into account...` (torch/httpcore conflicts) | system `~/.local` has other packages mixed in | **does not break hpc-mcp**; switch to an isolated virtualenv (section 1, Option A) |

---

## 8. Reading the logs

- All logs go to **stderr** (stdout is reserved for the MCP protocol).
- To persist them: `--log-file ~/.local/share/hpc-mcp/hpc-mcp.log`.
- To see the allow/deny decision of every call: `--log-level DEBUG`.

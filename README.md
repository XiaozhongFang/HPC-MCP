# HPC-MCP

[![Tests](https://github.com/XiaozhongFang/HPC-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/XiaozhongFang/HPC-MCP/actions/workflows/tests.yml)

MCP server for AI agents to interact with HPC clusters, Slurm jobs,
SSH, files, and scientific computing workflows.

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP stdio server](https://img.shields.io/badge/MCP-stdio%20server-6f42c1.svg)](https://modelcontextprotocol.io)

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [繁體中文](README.zh-TW.md)

A **security-first MCP server** that lets coding agents (Codex, Reasonix, and any other MCP client) safely operate a remote HPC cluster over **SSH + Slurm** — path sandbox, login-node command allow-list, Slurm resource ceilings, job ownership, and a full ALLOW/DENY audit trail, all enforced in code.

> **New here? Start with → [docs/QUICKSTART.md](docs/QUICKSTART.md)**: step-by-step install, every flag explained, complete Codex/Reasonix configuration examples, and a troubleshooting table for "it hangs on startup", "network unreachable", "pip installed it as UNKNOWN", and similar problems.

## Architecture

```text
Codex / Reasonix (Agent)
        │  MCP (stdio)
        ▼
  Project Skill            ← behaviour guidance (not a security boundary)
        │
        ▼
  HPC MCP Server           ← the security boundary (enforced in code)
        │
        ├── Path Sandbox        (USER_ROOT enforced)
        ├── Command Policy      (login-node allow-list)
        ├── Slurm Resource Policy (partitions / resources / concurrency)
        ├── SSH Manager         (fixed argv, no local shell)
        ├── Job Tracker         (job ownership under a shared account)
        └── Audit Logger        (ALLOW/DENY record for every call)
        │ SSH / SFTP
        ▼
  HPC Login Node  ──lightweight queries only──┐
        │ sbatch                               │
        ▼                                      ▼
  Compute Node (Slurm)              User work dir $HPC_MCP_ROOT
        │
   Julia / MOOSE / Python / CMake
```

Design principle: **the MCP server is the security boundary; the skill is only behavioural guidance.** Even if the agent's prompt is wrong or the skill is misread, the core permission boundary cannot be bypassed.

## Security model

### Shared-account isolation

HPC clusters often use a shared account (e.g. `/home/shared_account/`). That home directory is **not** the user's own directory. You must configure:

```
HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD       # local directories allowed for upload/download
```

Every remote file operation is confined below that root (including canonical symlink checks). Local `upload`/`download` are likewise confined to `local_roots` (one or more allowed local directories; defaults to the process working directory plus the system temp dir `/tmp`), and reject `.ssh`, private keys, and symlinked paths. Other users' directories (`/home/shared_account/other_user`) and system directories (`/etc`, `/opt`) are always denied.

### Login-node policy

Login nodes accept only lightweight, read-only administrative commands (allow-list): `ls`, `find`, `cat`, `grep`, `head`, `tail`, `git status/diff/log`, `module list/avail`, and similar. To prevent looking at other users' jobs under a shared account, `squeue`, `sacct`, and `scontrol` are **not** exposed through `hpc.shell.run_safe`; they are reachable only via the Slurm tools that enforce ownership checks.

**Always denied** on login nodes: compute and build workloads such as `julia`, `python`, `make`, `cmake --build`, `ninja`, `mpirun`, `srun`, `pytest`, `matlab`, GPU programs — all of them are redirected to `hpc.slurm.submit`.

The command policy rejects, **at the code level**: shell metacharacters (`;`, `&&`, `||`, `|`, `>`, `<`, `$()`, backticks, `&`), path-form executables (`./program`), `find -exec`, `git -c`, nested shells, and dangerous programs such as `sudo`/`ssh`/`curl`. A parse failure is denied too (fail-closed).

Even when requested, `env`/`printenv` run in a scrubbed minimal environment; SSH tokens, keys, and cluster credentials are never returned. Path operands of every command are re-validated with a remote `realpath`, and symlink-following options are rejected.

### Slurm resource policy

`hpc.slurm.submit` is the only compute entry point. The server enforces:

- partition allow-list (empty by default = deny everything; the agent may only pick a partition on the list)
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs` (active-job ceiling)
- the working directory must live inside USER_ROOT
- job stdout/stderr are always captured under `$ROOT/.hpc-mcp/jobs/<job-id>/`

### Job ownership (shared accounts)

Under a shared Unix account the OS cannot tell users apart. Each MCP instance manages only the jobs **it submitted and registered itself** (`$ROOT/.hpc-mcp/tracked_jobs.json`). `status`/`output`/`cancel`/`accounting` for any other job are always denied.

### Fail-closed

Missing configuration, unresolvable paths, unparsable commands, an uncertain partition, SSH errors — every uncertain condition ends in **DENY**, never in a fallback to an unrestricted shell.

### Three layers of defence

| Layer | Mechanism | Protects against |
|---|---|---|
| Layer 1: MCP policy | path sandbox, command allow-list, Slurm resource policy, job ownership, audit | a misbehaving agent |
| Layer 2: Slurm | partition allow-list, resource ceilings, concurrency ceiling, single `sbatch` entry | compute-resource abuse |
| Layer 3: OS/cluster | separate Unix UID / job isolation / filesystem ACL / containers | **hostile code isolation** (optional) |

> **Residual risk (must be understood)**: under a shared Unix UID, the MCP layer can only guarantee that *the agent behaves* (Layer 1/2). It **cannot** stop malicious code submitted to a compute node from touching everything else that UID can read (Layer 3). True hostile-code isolation requires an independent UID, Slurm job isolation plus filesystem ACLs, or cluster containers/sandboxes. Do not mistake the Python-side regex/policy checks for OS-level isolation of malicious code.

### Query cost and deduplication

- `hpc.files.read` is a **bounded slice**: at most `min(max_bytes, files.max_read_slice_bytes)` (default 256 KiB) per call, instead of steering the agent to page from `offset=0` to EOF.
- `hpc.files.list` pages **one depth layer at a time** (`page_size` + `next_cursor`), truncates remote output with `head`, and never scans the whole tree just to discard it.
- `hpc.files.search` has hard budgets (`max_matches`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`, all clamped server-side) for "locate first, read precisely second".
- Idempotent read-only queries are deduplicated per `tool + args` for a TTL (2 s by default, `cache_ttl_seconds`, 0 disables); any write invalidates the cache.
- `hpc.slurm.queue/status` query only the job IDs tracked by this instance (`squeue -j <ids>`), and **never** scan the shared account's whole queue.
- Every `hpc.shell.run_safe` command — including allow-listed ones — has a **command cost budget**: high-risk commands such as `find`/`du`/`sort`/`git grep` are clamped to a shorter timeout and an output cap.
- Content returned to the agent (logs, file contents) passes through **minimal secret redaction** (`password=`/`token=`/`Bearer`/AWS/PEM private-key blocks), governed separately from audit-log redaction so scientific logs stay intact.

## Install

Full install walkthrough: [docs/QUICKSTART.md](docs/QUICKSTART.md)

After installing you get the `hpc-mcp` command.

## Configuration

Three sources, priority **CLI > environment > config file > defaults**.

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account \
  --root /home/shared_account/alice --local-root "$PWD"

# Point at a specific ssh/sftp executable (needed in some WSL setups):
#   --ssh-bin  /usr/bin/ssh
#   --ssh-bin  @/usr/bin/ssh
#   --ssh-bin  @/mnt/c/Windows/System32/OpenSSH/ssh.exe
#   --sftp-bin @/usr/bin/sftp
```

### Environment variables

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML config file

Every supported key, its default, its range, and the matching environment variable are documented in [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md). A minimal config:

```yaml
host: my-hpc
user: shared_account
root: /home/shared_account/alice
local_root: /path/to/local/project

slurm:
  allowed_partitions: [compute]
  max_cpus: 64
  max_nodes: 2
  max_memory_mb: 262144      # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20    # active-job ceiling (default 20, adjustable)
```

```bash
hpc-mcp --config config.yaml
```

### SSH configuration (recommended)

Manage connection details in `~/.ssh/config` and let `HPC_MCP_HOST` reference the Host alias:

```sshconfig
Host my-hpc
    HostName hpc.example.edu
    User shared_account
    IdentityFile ~/.ssh/id_ed25519
```

The agent never touches private-key material.

**Passwordless login is required** (hpc-mcp forces `BatchMode=yes` and never prompts for a password). Verify it with BatchMode before configuring anything, otherwise every tool call fails with `Permission denied`:

```bash
ssh -o BatchMode=yes my-hpc "echo OK"   # must print OK without prompting
ssh-copy-id my-hpc                      # if the above fails, set up key auth first
```

`StrictHostKeyChecking=yes` is the default: connect manually with `ssh my-hpc` once to verify the host fingerprint and write it to `known_hosts`. If you really need first-connect auto-registration, set `ssh.strict_host_key_checking: accept-new` in the config.

### Connectivity self-check

```bash
hpc-mcp --host my-hpc --root /home/shared_account/alice --check
```

## MCP client integration

### Recommended: one-command registration (`hpc-mcp mcp-add`)

Once installed, `mcp-add` registers hpc-mcp in the Codex and Reasonix configs for you. It writes a **portable launcher path** (`<repo>/scripts/hpc-mcp-run`); that script locates the local hpc-mcp install (conda/venv/PATH) by itself, so the config is **not** tied to one machine's conda path — clone + install again on another machine and you are done:

```bash
# Run inside the repository (it finds the repo's scripts/hpc-mcp-run)
cd ~/git_repo/HPC-MCP
hpc-mcp mcp-add --config ~/.config/hpc-mcp/192.168.10.10.yaml

# Or pass arguments directly
hpc-mcp mcp-add --host my-hpc --user shared_account --root /home/shared_account/alice
```

What it does:

- writes `[mcp_servers.hpc]` into `~/.codex/config.toml` (command points at `scripts/hpc-mcp-run`)
- writes the hpc plugin into `~/.reasonix/config.toml` (same launcher script)
- the launcher resolves hpc-mcp in order: `$HPC_MCP_BIN` → PATH → common conda/venv paths, and prints a clear message instead of failing silently
- only adds/updates the hpc section — your other MCP servers and providers are left untouched
- idempotent: running it twice does not create duplicate sections

Restart Codex / Reasonix afterwards.

### CC-Switch (MCP config manager)

[CC-Switch](https://github.com/farion1231/cc-switch) manages multiple MCP server configs as JSON and switches between them with one click. The complete stdio JSON config (`command` + `env`), field reference, and FAQ live in **[`docs/CC_SWITCH.md`](docs/CC_SWITCH.md)**:

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/yourname/git_repo/HPC-MCP/scripts/hpc-mcp-run",
  "args": [],
  "env": {
    "HPC_MCP_HOST": "my-hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice",
    "HPC_MCP_LOCAL_ROOT": "/home/yourname/my-project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug"
  }
}
```

### Project-level `.mcp.json` (most portable)

The repository ships a `.mcp.json` template (standard project-level MCP config). Add an `hpc` entry whose command points at `./scripts/hpc-mcp-run`; MCP clients that support project-level config (such as Codex/Reasonix started from the repository directory) load it automatically. host/root and friends come from environment variables (`${HPC_MCP_HOST}` etc., defined in your shell profile). Moving to another machine is then: clone the repo → install hpc-mcp → define the environment variables → start the client from the repository directory.

### Manual setup (optional)

#### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_USER=shared_account \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

#### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  hpc-mcp
```

Both start a stdio argv process, no shell involved. Note that in the manual form, if `hpc-mcp` is not on PATH you must use an absolute path (e.g. `/path/to/conda/envs/hpc-mcp/bin/hpc-mcp`).

## Tools (22)

> Per-tool arguments, defaults, constraints, and return values are documented in **[docs/TOOLS.md](docs/TOOLS.md)**; the tables below are just an index.

### Low-level primitives

| Tool | Purpose | annotations |
|---|---|---|
| `hpc.info` | connection/cluster info (local paths never exposed) | readOnly |
| `hpc.files.list` | list a directory (bounded paging; recursive = layer by layer with cursor) | readOnly |
| `hpc.files.read` | read a file (bounded slice, ≤256 KiB per call) | readOnly |
| `hpc.files.write` | write a file (supports `expected_size`/`mtime`/`hash` optimistic-concurrency guards) | destructive |
| `hpc.files.mkdir` | create a directory | — |
| `hpc.files.delete` | delete | destructive |
| `hpc.files.upload` | upload from local (SFTP) | destructive |
| `hpc.files.download` | download to local (SFTP) | readOnly |
| `hpc.shell.run_safe` | allow-listed lightweight commands (with a command cost budget) | readOnly |
| `hpc.slurm.submit` | submit a compute job | openWorld |
| `hpc.slurm.status` | job status | readOnly |
| `hpc.slurm.queue` | my job queue | readOnly |
| `hpc.slurm.output` | job stdout/stderr | readOnly |
| `hpc.slurm.cancel` | cancel a job | destructive |
| `hpc.slurm.accounting` | sacct accounting | readOnly |
| `hpc.jobs.wait` | wait for a job to finish (bounded) | readOnly |

### High-level agent tools

| Tool | Purpose | annotations |
|---|---|---|
| `hpc.files.search` | budgeted regex search (locate error lines in logs) | readOnly |
| `hpc.jobs.diagnose` | state + accounting + log tails + error signatures in one call | readOnly |
| `hpc.jobs.wait_and_diagnose` | wait for a job to end, then diagnose once | readOnly |
| `hpc.project.snapshot` | build project context in one call (tree + git + jobs) | readOnly |
| `hpc.cluster.topo` | compute-node hardware topology (CPU model / SIMD / NUMA / cache) + parallel-parameter advice | readOnly, openWorld |
| `hpc.job.run` | high-level submit via runtime profiles (julia/python/moose/bash) | openWorld |

### Example call

Submit a Julia job (pick a partition from the allowed list returned by `hpc.info`; omit it to use the first configured one):

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "job_name": "demo-test",
    "working_directory": "/home/shared_account/alice/demo_benchmark",
    "partition": "compute",
    "cpus_per_task": 8,
    "time_limit": "00:30:00",
    "command": ["julia", "--project=.", "scripts/test.jl"]
  }
}
```

Argument defaults come from the server config or the script's `#SBATCH` directives (explicit arguments win); the partition must hit the configured allow-list or the call is denied. You can also submit a `.sh` job-script path directly:

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "command": "/home/shared_account/alice/proj/run.sh"
  }
}
```

A denied call returns something actionable:

```text
Operation denied.

Reason:
'julia' is a computational/build workload and is forbidden on HPC login nodes.

Use:
hpc.slurm.submit
```

## Parallel-tuning parameters (`hpc.cluster.topo`)

The login-node allow-list **deliberately** excludes `lscpu`/`numactl`, and `/proc` sits outside the path sandbox — so CPU model, SIMD instruction sets, and NUMA topology can only be read on a compute node. `hpc.cluster.topo` turns that whole dance into a single call:

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- **First call** (or expired cache, or `refresh: true`) submits a **one-CPU probe job** that reads `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo` on a compute node, then merges that with the login node's `sinfo` partition view.
- **Returns**: CPU model with sockets/cores/threads, SIMD instruction sets (AVX2/AVX-512/SVE), NUMA domains and distance matrix, cache levels, node memory, partition features/GRES, plus derived parallel-parameter advice (`ntasks_per_node`, `cpus_per_task`, `--hint=nomultithread`, `--cpu-bind=cores`, `OMP_NUM_THREADS`, `-map-by numa`, suggested `--mem`) with a rationale for each recommendation and the physical-vs-logical-core trade-off.
- **Caching**: within `topology.cache_ttl_seconds` (default 24 h) results come from the in-process cache or the remote `$ROOT/.hpc-mcp/topo/topology_<partition>.json`, so later sessions never queue the same probe again.
- **Queued**: while the probe job waits in the queue the call returns `status: "pending"` + `job_id`; calling again reuses that job instead of submitting another.
- **Safety**: the probe script has **fixed server-side content** (no agent argument is ever interpolated into it) and it **never queries the queue** (`squeue`/`sacct`/`scontrol`) — it only reads node-local hardware facts, so the shared-account isolation rule holds. Job ownership, partition allow-list, and the concurrency ceiling all still apply.
- Admins can disable the whole tool with `topology.enabled: false` (no probe job will ever be submitted).

## Recommended workflow (agent)

1. `hpc.info` to learn the environment → 2. `hpc.project.snapshot` to build project context in one call →
3. `hpc.cluster.topo` once when parallelism/performance matters, to get CPU/SIMD/NUMA plus recommended parallel parameters →
4. `hpc.files.search` to locate things first (e.g. error lines in a log), then `hpc.files.read` for a small slice of context →
5. `hpc.files.write` to edit remotely → 6. compile/test/compute only through `hpc.slurm.submit` →
7. `hpc.jobs.diagnose` to diagnose a failed job in one call → 8. analyse, fix, repeat.

Principle: **prefer one high-level call over several low-level ones**; `hpc.files.read` is a bounded slice — do not read from `offset=0` to EOF; repeated read-only queries are deduplicated by the server-side TTL cache.

Housekeeping rules the server states explicitly (so no turn is wasted on permission errors):

- **Scratch files have one home**: test scripts, test logs, job wrappers and intermediate artifacts go into `session_tmp_dir` from `hpc.info` (`$ROOT/.hpc-mcp/tmp/<session>/`), and the whole directory is removed with `hpc.files.delete(recursive=true)` when the task is done.
- **`.sh` job scripts are submitted, never executed**: pass the script path as `command` to `hpc.slurm.submit` — the server runs it with `bash` and reads its `#SBATCH` directives, so no executable bit is needed and `chmod` is never required (it is denied on login nodes anyway).
- **Job logs are captured by the server** under `$ROOT/.hpc-mcp/jobs/<job-id>/`; read them with `hpc.slurm.output` / `hpc.jobs.diagnose` instead of redirecting output inside the job script.

See [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md) for details.

## Security tests

```bash
python -m pytest tests/ -q
```

Covering: path traversal, symlink escape, command injection, login/compute boundary, Slurm resource abuse, and job isolation.

The full list of findings, fixes, and residual risk is in [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md); module boundaries and the request flow are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Scope limits (explicitly out of v1)

Arbitrary remote shell, arbitrary SSH hosts, sudo, port forwarding, multi-host setups, remote long-running daemons, HTTP MCP, and automatic credential management.

## Troubleshooting

- **"No HPC host/root configured" on startup**: provide at least host and root through one of the three configuration sources.
- **Every tool DENIED with "No Slurm partitions are allowed"**: configure `slurm.allowed_partitions` (empty by default, fail-closed).
- **SSH 255 errors**: verify with `hpc-mcp ... --check` first; then confirm `~/.ssh/config` and passwordless BatchMode work.
- **Logs and audit trail**: always written to stderr (stdout carries the MCP protocol only), and appended by default to `~/.local/share/hpc-mcp/hpc-mcp.log` — every tool call is recorded with its ALLOW/DENY decision. Override the path with `--log-file` / `HPC_MCP_LOG_FILE` / `log_file` in the config, or set `none` to keep the audit trail on stderr only.

## Documentation

| Document | Contents |
|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | hand-holding install + every flag + troubleshooting table |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every YAML key, default, range, and environment variable |
| [docs/TOOLS.md](docs/TOOLS.md) | arguments/defaults/constraints for all 22 tools |
| [docs/CC_SWITCH.md](docs/CC_SWITCH.md) | CC-Switch JSON config, field reference, FAQ |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | module boundaries, request flow, isolation layers |
| [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) | findings, fixes, verification, residual risk |
| [SECURITY.md](SECURITY.md) | threat model and enforced boundaries |

## License

MIT, see [LICENSE](LICENSE).

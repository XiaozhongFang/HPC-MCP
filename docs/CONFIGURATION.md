# Configuration reference (config YAML)

This document lists **every parameter the YAML config file supports**: location, type, default, range, the matching environment variable (`HPC_MCP_*`), and the CLI flag. `config/example.yaml` is a complete, copy-paste-ready example.

> **Priority (high → low)**: CLI flags > environment variables > config file > built-in defaults.
> When a parameter appears in several places, the higher-priority one wins.

> **Key naming**: the config file uses **underscore** keys (`local_root`, `allowed_partitions`, `ssh_bin`). Hyphenated forms (`local-root`, `ssh-bin`) are recognised as aliases but not recommended. Unknown keys do not raise an error — they log `Ignoring unknown config key 'xxx'` and are ignored, so that line means a typo.

> This file documents **server configuration parameters**. Startup/self-check CLI flags are in [QUICKSTART.md](QUICKSTART.md) section 4; **per-tool arguments** are in [TOOLS.md](TOOLS.md).

> Other languages: [简体中文](zh-CN/CONFIGURATION.md) | [日本語](ja/CONFIGURATION.md) | [한국어](ko/CONFIGURATION.md) | [繁體中文](zh-TW/CONFIGURATION.md)

---

## Top-level parameters

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | — (required) | SSH host; a `Host` alias from `~/.ssh/config` is recommended. Env `HPC_MCP_HOST`, CLI `--host` |
| `user` | string | — | SSH user (may be a shared account). `HPC_MCP_USER`, `--user` |
| `port` | int | `22` | SSH port (1–65535). `HPC_MCP_PORT`, `--port` |
| `root` | string | — (required) | Remote **user-owned** root directory; all remote agent operations are confined below it. Must be an absolute path and must not be `/`. `HPC_MCP_ROOT`, `--root` |
| `job_owner_id` | string | unset (random per process) | Optional 8–64 character owner namespace (`A-Z`, `a-z`, `0-9`, `.`, `_`, `-`). Set this only for one deliberately persistent MCP installation so tracked Slurm jobs remain manageable after a server restart. Independent clients must use different values; leaving it unset preserves per-process shared-account isolation. `HPC_MCP_JOB_OWNER_ID` |
| `local_root` | string | current working directory | Local directory allowed for upload/download (several via `local_roots`). Cannot be a credential directory such as `.ssh`/`.gnupg`. `HPC_MCP_LOCAL_ROOT`, `--local-root` |
| `local_roots` | list[string] | `[cwd, system temp dir]` | List of allowed local directories; alternative to `local_root`, and it wins when both are present. `HPC_MCP_LOCAL_ROOTS` (comma-separated) |
| `identity_file` | string | from `~/.ssh/config` | SSH private-key path (the agent never touches key material). `HPC_MCP_IDENTITY_FILE`, `--identity-file` |
| `ssh_bin` | string | looked up on PATH | ssh executable: bare name, absolute path, or `@`-prefixed (for WSL). `HPC_MCP_SSH_BIN`, `--ssh-bin` |
| `sftp_bin` | string | looked up on PATH | sftp executable, same forms as `ssh_bin`. `HPC_MCP_SFTP_BIN`, `--sftp-bin` |
| `wait_max_seconds` | int | `3600` | Maximum wait in seconds for `hpc.slurm.wait` / `wait_and_diagnose` (≤ 7 days). `HPC_MCP_WAIT_MAX_SECONDS` |
| `cache_ttl_seconds` | float | `2.0` | TTL of the read-only query dedup cache (seconds); `0` disables it. `HPC_MCP_CACHE_TTL_SECONDS` |
| `log_file` | string | `~/.local/share/hpc-mcp/hpc-mcp.log` | File that the append-only log + ALLOW/DENY audit trail is appended to (`~` is expanded, parent directories are created). Set `none`/`off`/`""` to keep the audit trail on stderr only. `HPC_MCP_LOG_FILE`, `--log-file` |
| `log_level` | string | `INFO` | Log level: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. `HPC_MCP_LOG_LEVEL`, `--log-level` |

> `ssh.*` sub-section parameters may also be written at the top level (e.g. `connect_timeout`, `strict_host_key_checking`), which is equivalent to the `ssh:` sub-section.

---

## `ssh:` sub-section

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | — | Same as top-level `host` |
| `port` | int | `22` | Same as top-level `port` |
| `user` | string | — | Same as top-level `user` |
| `identity_file` | string | — | Same as top-level `identity_file` |
| `connect_timeout` | int | `15` | SSH connect timeout (seconds, ≤ 3600). `HPC_MCP_CONNECT_TIMEOUT` |
| `command_timeout` | int | `30` | Timeout for a single remote command (seconds, ≤ 86400). `HPC_MCP_COMMAND_TIMEOUT` |
| `strict_host_key_checking` | string | `yes` | Host-key verification: `yes` (requires the key to be pinned in `known_hosts`, recommended) / `accept-new` (auto-register on first connect). **`no` is rejected.** `HPC_MCP_STRICT_HOST_KEY_CHECKING` |
| `ssh_bin` | string | PATH | Same as top-level `ssh_bin` |
| `sftp_bin` | string | PATH | Same as top-level `sftp_bin` |

---

## `slurm:` sub-section

| Key | Type | Default | Description |
|---|---|---|---|
| `allowed_partitions` | list[string] | `[]` (empty = deny every submission, fail-closed) | Allow-list of partitions that may be submitted to. Names may not contain `/` or control characters. `HPC_MCP_ALLOWED_PARTITIONS` (comma-separated) |
| `max_nodes` | int | `2` | Node ceiling per job (≥ 1). `HPC_MCP_MAX_NODES` |
| `max_cpus` | int | `64` | Total CPU ceiling per job: `nodes × ntasks × cpus_per_task` (≥ 1). `HPC_MCP_MAX_CPUS` |
| `max_memory_mb` | int | `262144` (256 GiB) | Memory ceiling per job (MiB, ≥ 1). `HPC_MCP_MAX_MEMORY_MB` |
| `max_gpus` | int | `4` | GPU ceiling per job (≥ 0). `HPC_MCP_MAX_GPUS` |
| `max_time` | string | `"24:00:00"` | Time ceiling per job. Slurm format: `HH:MM:SS`, `D-HH:MM:SS`; days may overflow (`"2-24:00:00"` = 3 days). `HPC_MCP_MAX_TIME` |
| `max_concurrent_jobs` | int | `20` | **Maximum number of simultaneously active jobs.** On submit, the server counts jobs in this instance's registry that are non-terminal in *both* `squeue` and `sacct`; reaching the ceiling denies the new submission and asks the caller to wait. Finished jobs (`COMPLETED`/`FAILED`/`CANCELLED`/…) release their slot automatically; when the `squeue` query succeeds but the job is no longer queued and `sacct` has no record either, the slot is released too (so an expired accounting record cannot pin the quota forever); if the `squeue` query fails, counting continues conservatively (fail-closed). `HPC_MCP_MAX_CONCURRENT_JOBS` |

> All numeric `max_*` fields accept simple arithmetic expressions: `+ - * /` and parentheses, e.g. `"4*16"`, `"128/4"`.
> The same works in environment variables (e.g. `HPC_MCP_MAX_CPUS=4*16`).

---

## `shell:` sub-section

| Key | Type | Default | Description |
|---|---|---|---|
| `safe_commands` | list[string] | `[]` (appended on top of the built-in allow-list) | Command basenames appended to the built-in minimal allow-list (no `/`, ≤ 128 characters). `HPC_MCP_SAFE_COMMANDS` (comma-separated) |
| `max_exec_seconds` | int | `30` | Time ceiling for a single safe command (seconds, ≤ 86400). `HPC_MCP_SHELL_MAX_EXEC_SECONDS` |
| `max_output_bytes` | int | `1048576` (1 MiB) | Output ceiling for a single safe command (bytes). `HPC_MCP_MAX_OUTPUT_BYTES` |

---

## `files:` sub-section

| Key | Type | Default | Description |
|---|---|---|---|
| `max_read_bytes` | int | `1048576` (1 MiB) | **Hard ceiling** for a single `hpc.files.read` (bytes). `HPC_MCP_MAX_READ_BYTES` |
| `max_read_slice_bytes` | int | `262144` (256 KiB) | Return ceiling for a single `hpc.files.read` (bounded slice), so the agent cannot page through a huge log in one call. `HPC_MCP_MAX_READ_SLICE_BYTES` |
| `max_write_bytes` | int | `10485760` (10 MiB) | Ceiling for a single `hpc.files.write` (bytes). `HPC_MCP_MAX_WRITE_BYTES` |
| `max_list_entries` | int | `2000` | Entries returned by a single `hpc.files.list`. `HPC_MCP_MAX_LIST_ENTRIES` |
| `max_recursive_depth` | int | `3` | Depth ceiling for `hpc.files.list recursive` (≤ 64). `HPC_MCP_MAX_RECURSIVE_DEPTH` |
| `search_max_matches` | int | `200` | Maximum matches per file for `hpc.files.search` (≤ 100000). `HPC_MCP_SEARCH_MAX_MATCHES` |
| `search_max_context_lines` | int | `10` | Context lines attached to each match (≤ 1000). `HPC_MCP_SEARCH_MAX_CONTEXT_LINES` |
| `search_max_scan_bytes` | int | `67108864` (64 MiB) | Scan ceiling per file (bytes). `HPC_MCP_SEARCH_MAX_SCAN_BYTES` |
| `search_max_files` | int | `1000` | Maximum files scanned in one search (≤ 1000000). `HPC_MCP_SEARCH_MAX_FILES` |
| `search_max_depth` | int | `6` | Depth ceiling for search recursion (≤ 64). `HPC_MCP_SEARCH_MAX_DEPTH` |
| `search_timeout` | int | `5` | Timeout for a single remote search (seconds, ≤ 3600). `HPC_MCP_SEARCH_TIMEOUT` |

---

## `topology:` sub-section

Controls `hpc.cluster.topo` (compute-node hardware / NUMA / SIMD topology discovery). The first call
(or an expired cache, or `refresh=true`) submits **a 1-CPU probe job** on the requested partition,
reads `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo` on the compute node, merges
that with the login node's `sinfo` partition/node view, and returns the CPU model, sockets/cores/threads,
SIMD instruction sets, NUMA domains and distances, cache levels, node memory, plus derived parallel-
parameter advice (`ntasks_per_node`, `cpus_per_task`, `--cpu-bind`/`--hint`, `OMP_NUM_THREADS`).

The probe script is **fixed server-side content**: no agent-supplied argument is ever written into it, and it
**never** calls `squeue`/`sacct`/`scontrol` (shared-account isolation) — it only reads node-local hardware facts.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Whether to register `hpc.cluster.topo`. When `false` the tool is not exposed to the agent at all, so a probe job can never be submitted. `HPC_MCP_TOPOLOGY_ENABLED` |
| `cache_ttl_seconds` | int | `86400` (24 h) | Freshness of topology results: the in-process cache and the remote `$ROOT/.hpc-mcp/topo/topology_<partition>.json` share this TTL, avoiding repeated queueing. `0` = re-collect on every call. `HPC_MCP_TOPOLOGY_CACHE_TTL_SECONDS` |
| `wait_seconds` | int | `300` | Seconds to wait for the probe job to finish; on timeout it returns `status: "pending"` and a `job_id` (the job stays queued and later calls reuse it instead of submitting another). `HPC_MCP_TOPOLOGY_WAIT_SECONDS` |
| `collect_time_limit` | string | `"00:03:00"` | Slurm time ceiling of the probe job itself (same format as `slurm.max_time`). `HPC_MCP_TOPOLOGY_COLLECT_TIME_LIMIT` |

> The probe job is subject to `slurm.allowed_partitions`, `max_concurrent_jobs`, and the usual
> job-ownership rules; with an empty `allowed_partitions` the tool is fail-closed (calls denied).
> The probed partition name is used in `sinfo -p <partition>` and in the cache filename, so it is
> stricter than the generic Slurm policy: only `[A-Za-z0-9_.-]` is accepted.

---

## Complete example

```yaml
# Complete HPC-MCP configuration example (every key is optional unless marked "required")
host: my-hpc                      # required: SSH Host alias or address
user: shared_account
port: 22
root: /home/shared_account/alice  # required: remote user-owned root directory
local_root: /home/alice/proj      # local upload/download directory
# local_roots: [ /home/alice/proj, /tmp ]
# identity_file: ~/.ssh/id_ed25519
# ssh_bin: /usr/bin/ssh
# sftp_bin: @/mnt/c/Windows/System32/OpenSSH/ssh.exe

ssh:
  connect_timeout: 15
  command_timeout: 30
  strict_host_key_checking: "yes"   # or "accept-new"; "no" is rejected

slurm:
  allowed_partitions: [compute]     # must be configured: empty default = deny everything
  max_nodes: 2
  max_cpus: 64                      # expressions like "4*16" work
  max_memory_mb: 262144             # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20           # active-job ceiling, default 20

shell:
  safe_commands: []                 # additional allowed command basenames
  max_exec_seconds: 30
  max_output_bytes: 1048576

files:
  max_read_bytes: 1048576
  max_read_slice_bytes: 262144
  max_write_bytes: 10485760
  max_list_entries: 2000
  max_recursive_depth: 3
  search_max_matches: 200
  search_max_context_lines: 10
  search_max_scan_bytes: 67108864
  search_max_files: 1000
  search_max_depth: 6
  search_timeout: 5

topology:
  enabled: true                     # false = do not register hpc.cluster.topo (never submit a probe job)
  cache_ttl_seconds: 86400          # topology freshness (24h); 0 = re-collect every time
  wait_seconds: 300                 # probe-job queue wait ceiling; times out to "pending"
  collect_time_limit: "00:03:00"    # Slurm time ceiling of the probe job itself

wait_max_seconds: 3600
cache_ttl_seconds: 2.0
# Append-only log + ALLOW/DENY audit trail (default path when unset).
# Set to `none` to keep the audit trail on stderr only.
log_file: ~/.local/share/hpc-mcp/hpc-mcp.log
log_level: INFO
```

# Validate the config + connectivity
```bash
source ~/venvs/hpc-mcp/bin/activate
hpc-mcp --config ~/.config/hpc-mcp/config.yaml --check
```

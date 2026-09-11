# MCP tool reference

This document describes the purpose, arguments, defaults, constraints, and return values of the **22 tools** HPC-MCP exposes to agents. It expands the "Tools" table in [README.md](../README.md): the README answers *which tools exist*, this file answers *how each argument is filled in and what clamps it*.

- **Configuration parameters** (host/root/partition allow-list/resource ceilings…) are in [CONFIGURATION.md](CONFIGURATION.md).
- **Command-line flags** (`--host`/`--root`/`--check`…) are in [QUICKSTART.md](QUICKSTART.md) section 4.
- **Agent behaviour guidance** (when to use which tool) is in [skills/hpc-development/SKILL.md](../skills/hpc-development/SKILL.md).

> Other languages: [简体中文](zh-CN/TOOLS.md) | [日本語](ja/TOOLS.md) | [한국어](ko/TOOLS.md) | [繁體中文](zh-TW/TOOLS.md)

---

## Common conventions (apply to every tool)

| Convention | Description |
|---|---|
| Path sandbox | Every remote path must be **absolute** and inside `$HPC_MCP_ROOT`; `..`, symlinks escaping the root, `/etc` etc. are always denied. Local paths must be inside `local_roots` (default = process working directory + `/tmp`), and `.ssh`, private keys, and symlinks are rejected. |
| Server-side clamping | Every "budget" argument (bytes, entries, depth, timeout) is **requested value ≤ server ceiling**; ceilings are in CONFIGURATION.md. Passing 0 or a negative number is denied (fail-closed). |
| Paging | Recursive listing advances one layer at a time with `page_size` + `cursor` and never scans the whole tree; reads continue with `offset` + `next_offset`. |
| Query dedup | Read-only, idempotent tools dedupe on `tool + args` for `cache_ttl_seconds` (default 2 s), so repeats cost no SSH round trip; any write clears the cache. |
| Response redaction | `password=`, `token=`, `Bearer`, AWS credentials, PEM private-key blocks, etc. in logs/file contents are replaced with `[REDACTED]`; scientific log content is preserved. |
| Fail-closed | Missing configuration, unresolvable paths, unparsable commands, an uncertain partition, SSH errors — every uncertain condition is **DENY**, with an actionable alternative (`Reason:` + `Use:`). |
| annotations | `readOnly` = does not modify remote state; `destructive` = may delete/overwrite; `idempotent` = repeated calls have no extra side effects; `openWorld` = reaches the compute side of the cluster (submits jobs). |

---

## 1. Environment and project context

### `hpc.info`

Connection and cluster capability overview. **No arguments.**

Returns: `slurm_available`, `cluster`, `working_root`, `tmp_dir`, `session_tmp_dir`, `workspace_available`, `transfer_enabled`,
`allowed_partitions` (the partition allow-list), `max_cpus`/`max_nodes`/`max_memory_mb`/`max_gpus`/`max_time`.

> **Scratch files have one home**: put test scripts, test logs, job wrappers and intermediate artifacts in `session_tmp_dir`
> (`$HPC_MCP_ROOT/.hpc-mcp/tmp/<session>/`) instead of scattering them through the project tree, and remove the whole directory with
> `hpc.files.delete(recursive=true)` when the task is done. Deliverables belong in the project, not in the scratch directory.
>
> For security it does **not** return the SSH host/user/port or the local path roots (so the agent cannot bypass the MCP layer and connect directly).
> Check the ceilings here before requesting resources.

### `hpc.project.snapshot` — `readOnly, idempotent`

Builds project context in one call: a shallow directory overview + sizes of key source/log files + read-only git status + the jobs tracked for this project.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Absolute path inside `$HPC_MCP_ROOT` |
| `depth` | int | — | `2` | Enumeration depth, clamped server-side by `files.max_recursive_depth` |
| `include_git` | bool | — | `true` | Include a read-only git summary (`git status` etc.) |
| `include_jobs` | bool | — | `true` | Include the jobs tracked for this project |

> One `snapshot` replaces the `list` + `read` + `git status` + `queue` round trips.

---

## 2. Remote files

### `hpc.files.list` — `readOnly, idempotent`

List a directory. Recursive listing is **paged layer by layer**.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Absolute directory path (inside root) |
| `recursive` | bool | — | `false` | Recursive listing (advanced layer by layer) |
| `max_entries` | int | — | server `files.max_list_entries` (2000) | Hard ceiling on entries returned in one call |
| `page_size` | int | — | same as `max_entries` | Entries per page |
| `cursor` | string | — | — | The `next_cursor` from the previous page, to continue a recursive listing |
| `max_depth` | int | — | server `files.max_recursive_depth` (3) | Recursion depth ceiling |

### `hpc.files.read` — `readOnly, idempotent`

Read one slice of a file (**bounded slice**, ≤ `files.max_read_slice_bytes` per call, default 256 KiB).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Absolute file path (inside root) |
| `max_bytes` | int | — | server `files.max_read_bytes` (1 MiB) | Byte ceiling for this read, still clamped by the slice ceiling |
| `offset` | int | — | `0` | Starting byte offset |

Returns: `size`, `bytes`, `truncated`, `end_of_file`, `next_offset`, `content` (UTF-8, invalid bytes replaced).

> To investigate a large log, locate the line with `hpc.files.search` first and then read a small slice at that `offset`; **do not** read from `offset=0` to EOF.

### `hpc.files.search` — `readOnly, idempotent`

Budgeted regex search (POSIX ERE) for "locate first, read precisely second".

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Absolute file or directory path |
| `pattern` | string | ✅ | — | Extended regex (passed as a single quoted argument; control characters rejected) |
| `max_matches` | int | — | `files.search_max_matches` (200) | Maximum matches |
| `context_lines` | int | — | `files.search_max_context_lines` (10) | Context lines per match |
| `max_scan_bytes` | int | — | `files.search_max_scan_bytes` (64 MiB) | Per-file scan ceiling |
| `max_files` | int | — | `files.search_max_files` (1000) | Maximum files scanned in the tree |
| `max_depth` | int | — | `files.search_max_depth` (6) | Tree depth ceiling |
| `timeout` | int | — | `files.search_timeout` (5 s) | Remote search timeout |

### `hpc.files.write` — `destructive`

Write a file, with **optimistic concurrency protection** (so a shared account cannot silently overwrite code someone else just changed).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Absolute target path |
| `content` | string | ✅ | — | Text content (overall ceiling `files.max_write_bytes`, default 10 MiB) |
| `append` | bool | — | `false` | Append instead of overwrite |
| `expected_size` | int | — | — | Expected current byte size; a mismatch **denies the write** |
| `expected_mtime` | int | — | — | Expected current mtime (epoch seconds); a mismatch denies the write |
| `expected_sha256` | string | — | — | Expected current SHA-256 (64 hex digits); a mismatch denies the write |

> The three `expected_*` guards can be combined freely; when any is supplied, all must match and the file must exist (otherwise the write is denied).

### `hpc.files.mkdir` — `destructive`

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Directory path |
| `parents` | bool | — | `false` | Same as `mkdir -p` (verifies the nearest existing ancestor is still inside root) |

### `hpc.files.delete` — `destructive`

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | ✅ | — | Target path (deleting the root itself is not allowed) |
| `recursive` | bool | — | `false` | Recursive delete (`rm -rf`) |

### `hpc.files.upload` / `hpc.files.download`

SFTP transfer, **sandboxed on both ends** (local ∈ `local_roots`, remote ∈ `$HPC_MCP_ROOT`).

| Tool | Argument | Type | Required | Description |
|---|---|---|---|---|
| `hpc.files.upload` (destructive) | `local_path` | string | ✅ | Local source file path |
| | `remote_path` | string | ✅ | Target path (inside root) |
| `hpc.files.download` (readOnly) | `remote_path` | string | ✅ | Remote source file path |
| | `local_path` | string | ✅ | Local target path (inside `local_roots`) |

> Both paths reject symlinks; the local side additionally rejects `.ssh`/`.gnupg` and private-key filenames; sizes are validated after transfer.
>
> An uploaded **`.sh` job script needs no executable bit**: submit it with `hpc.slurm.submit` and the script path as `command`
> (the server runs it with `bash` and reads its `#SBATCH` directives). Do not `chmod` it and do not try to execute it on the login node —
> the upload result carries the same reminder in `submit_hint`.

---

## 3. Login-node queries

### `hpc.shell.run_safe` — `readOnly`

Run **one allow-listed command** on the login node (`ls`/`find`/`cat`/`grep`/`head`/`tail`/`wc`/`sort`/`uniq`/`stat`/`du`/`df`/`git status|diff|log`/`module list|avail`/`sinfo`/`env` and similar).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | string | ✅ | — | A single command; pipes, `&&`, `;`, redirection, `$()`, backticks, and background `&` are **not supported** |
| `cwd` | string | — | `$HPC_MCP_ROOT` | Working directory (inside root, realpath-verified) |
| `timeout` | int | — | `shell.max_exec_seconds` (30 s) | Further clamped by the command cost tier |

Constraints (enforced in code):

- Compute/build programs are **not** allowed (`julia`/`python`/`make`/`cmake`/`mpirun`/`pytest`/`nvcc`…) → use `hpc.slurm.submit`.
- `squeue`/`sacct`/`scontrol` are **not** allowed → use the Slurm tools that enforce ownership checks.
- No path-form executables (`./prog`, `/usr/bin/julia`), no `find -exec`, no `git -c`, no nested shells.
- Command cost tiers: LOW (15 s / 256 KiB, e.g. `ls`/`sinfo`), MEDIUM (30 s / 512 KiB, e.g. `cat`/`grep`), HIGH (10 s / 512 KiB, e.g. `find`/`du`/`sort`/`git grep`).

Returns: `exit_code`, `stdout`, `stderr`, `cost_tier`, `timeout_seconds` (a timeout sets `timed_out: true`).

---

## 4. Slurm jobs

### `hpc.slurm.submit` — `openWorld`

Submit a compute job (the **only** compute entry point, subject to the server-side resource policy).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | array or string | ✅ | — | Program argv (e.g. `["julia","--project=.","test/runtests.jl"]`), or a **single `.sh` script path** — run with `bash`, so **no executable bit is required**; its `#SBATCH` directives then supply the defaults |
| `job_name` | string | — | `"job"` | Job name (normalised to `[A-Za-z0-9_.-]`, ≤ 64 characters) |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | Job working directory (inside root, realpath-verified) |
| `partition` | string | — | first allow-listed partition in the config | Must hit `slurm.allowed_partitions` |
| `nodes` | int | — | `1` | ≤ `slurm.max_nodes` |
| `ntasks` | int | — | `1` | Number of tasks |
| `cpus_per_task` | int | — | `1` | And `nodes × ntasks × cpus_per_task ≤ slurm.max_cpus` |
| `memory` | string | — | none | e.g. `"16G"`, `"8000"` (MiB), `"2T"`; ≤ `slurm.max_memory_mb` |
| `time_limit` | string | — | `slurm.max_time` | `HH:MM:SS`, `D-HH:MM:SS`, `MM:SS`, or plain minutes; ≤ `slurm.max_time` |
| `gpus` | int | — | `0` | ≤ `slurm.max_gpus` |
| `environment` | object | — | — | Extra environment variables (names must match `[A-Za-z_][A-Za-z0-9_]*`) |

Behaviour: the script is generated server-side (`#SBATCH` directives are derived by the server), stdout/stderr are always captured under `$HPC_MCP_ROOT/.hpc-mcp/jobs/<job-id>/`; right after submission the job's ownership is cross-checked with `squeue -j`/`sacct -j`, and an unconfirmed job is not registered; the number of concurrently active jobs is bounded by `slurm.max_concurrent_jobs`.

A single `.sh` argument is executed as **`bash <script>`**, never as a bare executable path — an uploaded script works without the executable bit (and `chmod` is denied on login nodes), so never make the agent chase file permissions. Keep throwaway job scripts in `session_tmp_dir` (see `hpc.info`).

### `hpc.job.run` — `openWorld`

Simplified submission through a **trusted runtime profile**; internally it goes through exactly the same policy as `hpc.slurm.submit`.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `runtime` | string | ✅ | — | Enum: `julia`, `python`, `moose`, `bash`, `shell` |
| `script` | string | ✅ | — | Absolute script path (inside root). For **moose**, pass the executable path |
| `args` | array\<string\> | — | — | Extra argv; **required for moose** (`[input.i]` etc.); the actual command becomes `mpirun -np <ntasks> -- <script> <args…>` |
| `job_name` | string | — | runtime name | Job name |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | Working directory |
| `partition` | string | — | first allow-listed partition | Same as submit |
| `nodes` / `ntasks` / `cpus_per_task` / `memory` / `time_limit` / `gpus` / `environment` | — | — | same as `hpc.slurm.submit` | Identical semantics and ceilings as submit |

Actual commands per profile: `julia --project=. <script>`, `python <script>`, `bash <script>`, `moose` → `mpirun -np <ntasks> -- <script>`.

### Job queries and management (only jobs submitted by this instance)

The server only queries **the job IDs it submitted and registered itself** (`squeue -j <ids>` / `sacct -j <ids>`) and never scans the shared account's whole queue; querying or cancelling any other job is denied.

| Tool | Argument | Type | Required | Default | Description |
|---|---|---|---|---|---|
| `hpc.slurm.status` (readOnly,idempotent) | `job_id` | string | ✅ | — | Status; falls back to `sacct` when `squeue` has no record |
| `hpc.slurm.queue` (readOnly,idempotent) | — | — | — | — | This instance's active jobs (an empty list when nothing is active is normal) |
| `hpc.slurm.output` (readOnly,idempotent) | `job_id` | string | ✅ | — | Read job logs |
| | `stream` | string | — | `"stdout"` | Enum: `stdout`, `stderr` |
| | `tail_bytes` | int | — | `shell.max_output_bytes` (1 MiB) | How many bytes to take from the end of the file |
| `hpc.slurm.cancel` (destructive) | `job_id` | string | ✅ | — | Cancel (only jobs submitted by this instance) |
| `hpc.slurm.accounting` (readOnly,idempotent) | `job_id` | string | ✅ | — | `sacct` accounting: `elapsed`, `cpu_time_raw`, `max_rss`, `state`, `exit_code`, `node_list`, `alloc_cpus` |

### `hpc.jobs.wait` — `readOnly, idempotent`

Poll until the job reaches a terminal state (`COMPLETED`/`FAILED`/`CANCELLED`/`TIMEOUT`/`OUT_OF_MEMORY`/`NODE_FAIL`/`PREEMPTED`).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | Job ID |
| `timeout_seconds` | int | — | `wait_max_seconds` (3600) | Overall wait ceiling, further clamped by the server ceiling |
| `poll_interval` | int | — | `10` | Poll interval in seconds (clamped to 2–60 server-side) |

> A timeout does not cancel the job; it returns an error and suggests polling again.

### `hpc.jobs.diagnose` — `readOnly`

Complete diagnosis in one call: state + accounting + stdout/stderr tails + a scan for common error signatures.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | Job ID |
| `stdout_lines` | int | — | `80` | stdout tail lines (ceiling 200; each line is budgeted at 4096 bytes) |
| `stderr_lines` | int | — | `80` | stderr tail lines (ceiling 200) |
| `include_accounting` | bool | — | `true` | Include `sacct` accounting |
| `include_error_scan` | bool | — | `true` | Scan error signatures (`oom`/`segfault`/`timeout`/`gpu_error`/`mpi_error`/`missing_file`) |

> One `diagnose` replaces the `status → output → accounting → read` round trips.

### `hpc.jobs.wait_and_diagnose` — `readOnly`

Wait for the job to end, then diagnose once (returns immediately if the job already ended).

Arguments: `job_id` (required), `timeout_seconds`, `poll_interval`, `stdout_lines`, `stderr_lines` (semantics and defaults as in `wait` / `diagnose`).

---

## 5. Cluster topology and parallel parameters

### `hpc.cluster.topo` — `readOnly, idempotent, openWorld`

Get the **real hardware parameters of a compute node** plus derived parallel-tuning advice: CPU model/vendor,
`sockets × cores × threads`, SIMD instruction sets (AVX2/AVX-512/SVE), NUMA domains and distance matrix,
cache levels, node memory, partition features/GRES, and
`recommended_parallel_parameters` (three sets: pure MPI / hybrid MPI+OpenMP / pure OpenMP, including
`ntasks_per_node`, `cpus_per_task`, `--hint=nomultithread`, `--cpu-bind=cores`,
`-map-by numa`, `OMP_NUM_THREADS`, suggested `--mem`, and the rationale).

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `partition` | string | — | first allow-listed partition | Partition to probe; the name must be `[A-Za-z0-9_.-]` and hit `slurm.allowed_partitions` |
| `refresh` | bool | — | `false` | Force re-collection (ignores caches; first cancels any still-queued old probe job) |

**Call cost and caching** (defaults from the `topology.*` config):

- The first call (or an expired cache, or `refresh: true`) submits **a 1-CPU probe job**
  (default time ceiling `00:03:00`) that reads `lscpu` / `/proc/cpuinfo` /
  `numactl --hardware` / `/proc/meminfo` / `/sys/.../cpu0/cache` on a compute node and merges
  that with the login node's `sinfo` partition view.
- Results are cached for `topology.cache_ttl_seconds` (default 24 h): in-process plus the remote
  `$HPC_MCP_ROOT/.hpc-mcp/topo/topology_<partition>.json`. A cache hit **does not** submit a job;
  the returned `source` says where it came from (`probe-job` / `session-cache` / `remote-cache`).
- While the probe job is still queued (beyond `topology.wait_seconds`, default 300 s) the call returns
  `status: "pending"` and a `collection_job_id`; **calling again reuses that job** instead of submitting another.

**Safety constraints**: the probe script is **fixed, server-generated content** — no agent argument is ever written into it; the script only reads node-local hardware facts and **never** calls `squeue`/`sacct`/`scontrol` (shared-account isolation); job ownership, the partition allow-list, and the concurrency ceiling all still apply. Admins can disable the tool with `topology.enabled: false`.

**Return highlights**:

| Field | Meaning |
|---|---|
| `cpu.model_name` / `cpu.vendor` / `cpu.architecture` | CPU model, vendor, architecture |
| `cpu.sockets` / `cores_per_socket` / `threads_per_core` | The three topology numbers |
| `cpu.physical_cores` / `logical_cpus` | Physical cores / logical CPUs (larger when SMT is on) |
| `cpu.simd.level` / `present` | Vector instruction-set tier (`avx512`/`avx2`/`avx`/`arm-neon`…) and the flags found |
| `cpu.cache_kib` / `cache_source` | L1d/L1i/L2/L3 (KiB; `null` when a level is unknown) and the source (`lscpu`/`sysfs`) |
| `numa.count` / `nodes` / `distances` | NUMA domain count, per-domain CPU lists and sizes, distance matrix |
| `memory_mib.mem_total` | Node memory (MiB) |
| `slurm.partition` / `node_variants` | Partition summary (nodes / cores and memory per node / features / GRES) and hardware variants (with a state histogram) |
| `recommended_parallel_parameters` | Three parallel-parameter sets + `notes` (the rationale for each recommendation) |
| `collected_on_node` / `collection_job_id` | Node that produced the sample and the probe-job ID (for traceability) |

> The recommendations are **heuristic** starting points derived from physical cores/NUMA domains, not benchmark conclusions; real tuning still requires comparing different rank/thread combinations.

---

## 6. Typical call sequence

```text
1. hpc.info                      # partition allow-list and resource ceilings
2. hpc.project.snapshot          # project context (tree/git/my jobs)
3. hpc.cluster.topo              # before parallel tuning: CPU/SIMD/NUMA + parameter advice
4. hpc.files.search  →  hpc.files.read   # locate first, then read a small slice
5. hpc.files.write                # remote edit
6. hpc.slurm.submit or hpc.job.run       # build/test/compute
7. hpc.jobs.wait_and_diagnose     # wait + one-shot diagnosis
8. analyse → back to step 5
```

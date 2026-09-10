# HPC-MCP Architecture

HPC-MCP is a stdio MCP server. The MCP protocol is the only data written to
stdout; diagnostics and audit records go to stderr or the configured log file.

```text
MCP client
   |
   v
server.py (dispatch, runtime argument shape, audit, lifecycle)
   |
   +--> tools/registry.py (public tool schemas and adapters)
   |       |
   |       +--> security/* (pure policy decisions)
   |       +--> filesystem / shell / slurm services
   |       +--> cluster/topology.py (compute-node probe job + sinfo, cached)
   |
   +--> ssh/manager.py and ssh/sftp.py (fixed argv, bounded processes)
               |
               v
           one configured HPC host
```

## Request flow

1. `server.on_call_tool` resolves the tool name and rejects non-object,
   missing, or unknown arguments.
2. The registry adapter validates scalar types and forwards only typed values.
3. A service applies its policy before any mutating remote command:
   `path_policy` canonicalizes remote paths, `command_policy` classifies login
   commands, and `slurm_policy` validates resources.
4. The service asks the SSH transport to execute a fixed argv or a trusted,
   quoted internal wrapper. Transport output is streamed into a byte cap and
   every child process is reaped on success, failure, or timeout.
5. Results are returned as text content. Every call receives an ALLOW/DENY
   audit record with recursive credential redaction.

## Isolation boundaries

The remote `root` is a dedicated per-user directory and may not be `/`. Remote
paths are checked lexically and through remote `realpath`; create operations
also reject an existing symlink destination. Transfer tools have an independent
local `local_root` (the process working directory by default), reject symlink
components and credential filenames, and enforce post-transfer size limits.

Login-node commands are a small allow-list. No shell operators, executable
paths, nested shells, symlink-following flags, or direct Slurm query commands
are accepted. Job inspection is exposed only by Slurm methods that validate the
current process session's `tool_session` entry. Output paths are derived from a
numeric job ID and the canonical jobs directory, never from untrusted metadata.
Slurm writes to a flat `%j.stdout.log`/`%j.stderr.log` staging file because it
cannot create an intermediate directory before opening output; the server then
creates the canonical job directory and links those files below it.

## Query cost boundaries

Shared-account safety also means *not loading other users' or huge amounts of
data into the MCP process at all*:

- **Owned-only Slurm queries.** `status`/`queue`/submit's active-job count run
  `squeue -j <tracked ids>` only. With no tracked jobs the call returns without
  any `squeue` invocation. Foreign job metadata never enters the process.
- **Bounded file reads.** `hpc.files.read` returns one slice capped by
  `files.max_read_slice_bytes` (default 256 KiB); tool descriptions steer the
  agent to `hpc.files.search` first instead of paging to EOF.
- **Bounded recursive listing.** `hpc.files.list` enumerates one depth layer
  per remote call, cuts remote output with `head` (SIGPIPE detection via
  `bash -o pipefail`), and resumes with a `depth:N` cursor.
- **Bounded search.** `hpc.files.search` (single file: stat + refuse oversized;
  tree: `grep -rn` + excluded dirs + per-file `-m` + global `head` + `timeout`)
  has every budget clamped server-side; the pattern travels as a quoted grep
  argument and control characters are rejected.
- **High-level tools.** `hpc.jobs.diagnose` (state + accounting + bounded tails
  + error signatures) and `hpc.project.snapshot` (bounded tree + git summary +
  tracked jobs) collapse several low-level calls into one, each query bounded.
- **Topology discovery.** `hpc.cluster.topo` combines a `sinfo` summary (already
  allow-listed, scoped to `-p <allowed partitions>` so unauthorized partitions
  never reach the response) with a **fixed, server-generated** probe script run
  as a one-CPU job on a compute node. No tool argument is interpolated into that
  script, and the script reads node-local files only -- it never queries
  `squeue`/`sacct`/`scontrol`, so the shared-account isolation rule holds on the
  compute side too. Results are cached in-process and as a JSON file under
  `$ROOT/.hpc-mcp/topo/` for `topology.cache_ttl_seconds` (default 24 h), which
  keeps a queued probe job from being re-submitted per call; a probe still
  waiting after `topology.wait_seconds` answers `status: "pending"` and is
  reused (or, eventually, cancelled) rather than duplicated.
- **Query cache.** Idempotent read-only tools dedupe on `tool + normalized
  args` for `cache_ttl_seconds` (default 2 s, 0 disables); any mutating tool
  invalidates the whole cache.

## Deliberate residual risk (three layers)

| Layer | Enforcement | Protects against |
|---|---|---|
| 1. MCP policy | path sandbox, command allow-list, Slurm resource policy, job ownership, query budgets, audit | a misbehaving agent |
| 2. Slurm | partition allow-list, resource/concurrency ceilings, single `sbatch` entry | compute-resource abuse |
| 3. OS/cluster | separate Unix UID, Slurm job isolation + filesystem ACL, container sandbox, or a privileged remote helper | *hostile code* isolation |

The remote host is a shared Unix account, so a process with equivalent account
permissions can race a path between canonicalization and the final command.
The server minimizes this window and refuses symlink targets, but complete race
freedom requires a privileged remote helper using `openat(2)`/`O_NOFOLLOW` or a
separate Unix account per user. The JSON tracking file is likewise a logical
session boundary, not an operating-system access-control boundary: an
equivalent Unix account can read or modify it.

Critically, `hpc.slurm.submit` grants the agent the ability to execute programs
as that Unix UID on compute nodes. Layers 1-2 cannot contain hostile code that
then reads everything the UID can read. Do not rely on regex/policy filtering of
"obviously malicious" Julia/Python as an OS-level isolation mechanism; deploy
Layer 3 (independent UID, job isolation + ACLs, or a container/sandbox) when
hostile same-account code is in scope.

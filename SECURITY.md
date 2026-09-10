# Security Policy

HPC-MCP's security model: **every critical boundary is enforced by deterministic code in the MCP server** — never by the skill, the system prompt, or the correctness of tool descriptions.

Implementation boundaries and the request flow are described in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); this round's review record is in [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md).

Other languages: [简体中文](SECURITY.zh-CN.md) | [日本語](SECURITY.ja.md) | [한국어](SECURITY.ko.md) | [繁體中文](SECURITY.zh-TW.md)

## Threat model

- HPC clusters use a **shared account** (one SSH account, many users).
- The agent (an LLM) may receive wrong, ambiguous, or injected instructions.
- Goal: even if the agent misbehaves, it cannot exceed its authority.

## Enforced boundaries

### 1. Path sandbox

- Every remote path is first normalised lexically (`posixpath.normpath`; `..`, tilde, NUL/CR/LF, and relative paths are rejected).
- It is then canonicalised with a remote `realpath` on the **existing path** or the **nearest existing parent**, blocking symlink escapes.
- If any step is uncertain, the operation is denied.
- The remote root must be a dedicated directory and cannot be configured as `/`; local transfers have their own `local_root` sandbox (default: the startup directory) that rejects symlinks, `.ssh`, and common private-key filenames.

**Not allowed**: any path outside `$HPC_MCP_ROOT` — including other shared-account users' directories, `/etc`, `/tmp`, and system directories; escaping through symlinks; rename/copy out of the root; deleting the root itself.

### 2. Login-node command policy

- Only allow-listed lightweight commands may run (`ls`, `cat`, `grep`, `git status/diff/log`, `module list`, …; extensible via configuration). `squeue`, `sacct`, and `scontrol` cannot be called through `hpc.shell.run_safe`, to prevent bypassing the job-ownership checks.
- All shell metacharacters are rejected: `;` `&&` `||` `|` `>` `>>` `<` `$( )` `` ` `` `${ }` `&`, newlines, etc.
- Compute/build programs are rejected: `julia`, `python`, `make`, `cmake`, `ninja`, `mpirun`, `srun`, compilers, `pytest`, `matlab`, container runtimes, etc.
- Dangerous programs are rejected: `sudo`, `ssh`/`scp`/`rsync`, `curl`/`wget`, `nohup`/`setsid`/`tmux`, `kill`, `chmod`, `dd`, nested shells, `xargs`, `eval`, etc.
- `git` is limited to read-only subcommands (`commit/push/pull/clone/-c/--exec-path/--git-dir` rejected); `module` is query-only; `find` rejects `-exec`/`-delete`.
- No `shell=True` locally or remotely; argv is re-serialised through `shlex.join`.
- A parse failure means denial.
- Path operands of commands such as `cat`/`grep`/`find` go through a remote canonical check; `find -L/-H/-follow`, `ls -L`, and `du -L` are rejected. `env`/`printenv` run only in a scrubbed minimal environment.

### 3. Slurm resource policy

- Partition allow-list (empty by default = deny everything).
- Node/CPU/memory/GPU/time/concurrency ceilings; exceeding any of them denies the submission.
- The working directory must be inside the root; command argv may not contain control characters.
- Job output is always written under `$ROOT/.hpc-mcp/jobs/<id>/` and cannot escape.

### 4. Job-ownership isolation

- Only jobs submitted and registered by the current service session can be managed (the `tool_session` entry in `tracked_jobs.json` must match); after a restart, a new instance does not adopt jobs from an older session by default.
- **Query isolation**: `hpc.slurm.queue/status` run `squeue -j <ids>` only for jobs tracked by this instance; with no tracked jobs they return empty immediately and **never** issue a whole-account `squeue`/`sacct` — other users' job metadata under a shared account never enters this process.
- Other users' or other instances' jobs may not be inspected or cancelled.
- That registry file is application-level isolation, not mandatory access control under the same Unix UID; hostile shared-account deployments require an independent UID or a privileged remote helper.

### 5. Query cost boundaries (diagnostics and budgets)

- `hpc.files.read` is a **bounded slice**: at most `min(max_bytes, files.max_read_slice_bytes)` per call (default 256 KiB); the tool description does not steer the agent from `offset=0` to EOF (investigate large files with `hpc.files.search`).
- Every `hpc.files.search` budget (`max_matches`/`max_context_lines`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`) is clamped server-side; single files are `stat`-ed first and oversized ones refused; remotely `grep -m` plus `head` truncation plus a `timeout` backstop apply; the pattern is shlex-quoted and control characters are rejected, ruling out injection.
- `hpc.files.list` pages recursive listings layer by layer (`page_size` + a `depth:N` cursor), truncates remote output with `head`, and uses `bash -o pipefail` to detect SIGPIPE and determine truncation precisely; it never scans the whole tree just to discard it.
- All internal queries of `hpc.jobs.diagnose` / `hpc.project.snapshot` are bounded and budgeted.
- Idempotent read-only queries dedupe on `tool + normalised args` (`cache_ttl_seconds`, default 2 s, 0 disables); any write invalidates the cache.
- `hpc.info` does not return local path roots (they may embed usernames/project names) — only capability booleans and resource ceilings.
- Every legal `hpc.shell.run_safe` command is tiered by the **command cost policy**: LOW for `ls`/`head`/`pwd` (15 s/256 KiB), MEDIUM for `grep`/`cat`/`git diff` (30 s/512 KiB), HIGH for `find`/`du`/`sort`/`git grep` (10 s/512 KiB). Timeouts and output caps are enforced server-side, never left to the agent's self-restraint.
- `hpc.files.write` supports **optimistic concurrency protection**: if any of `expected_size`/`expected_mtime`/`expected_sha256` does not match the file's current state, the overwrite is denied (fail-closed), preventing a shared account from clobbering code a colleague just changed.

### 6. SSH boundary

- Only the single configured host may be connected to; BatchMode, StrictHostKeyChecking, and connect timeouts apply.
- Private-key contents are **never read or printed**; managing keys through `~/.ssh/config` is recommended.
- SSH/SFTP output is streamed under byte caps; on timeout the child process is killed and reaped; ControlMaster sockets are closed when the service exits.
- There is no `hpc.ssh(command=...)` tool and no arbitrary-command tool of any kind.
- No port forwarding, no ProxyJump, no multi-host.

### 7. Credentials and logs

- Private keys, passwords, and tokens are never written to logs; the audit log redacts sensitive patterns and truncates values.
- Audit fields: timestamp, tool, args (redacted), decision (ALLOW/DENY), reason, job_id, duration.
- Redaction works recursively on field names (password/token/secret/private-key, …), strips control characters, and truncates; unexpected exceptions are never returned verbatim to the agent.

## Three layers of defence and residual risk

| Layer | Mechanism | Protects against |
|---|---|---|
| Layer 1: MCP policy | path sandbox, command allow-list, Slurm resource policy, job ownership, query budgets, audit | a misbehaving agent |
| Layer 2: Slurm | partition allow-list, resource ceilings, concurrency ceiling, single `sbatch` entry | compute-resource abuse |
| Layer 3: OS/cluster | independent Unix UID / Slurm job isolation + filesystem ACL / container sandbox / privileged remote helper | **hostile code isolation** (optional) |

**Residual risk**: under a shared Unix UID, MCP can only guarantee that *the agent behaves* (Layer 1/2). It **cannot** guarantee that malicious code submitted to a compute node will not access data readable by that UID (Layer 3). `hpc.slurm.submit` is essentially "execute a program as that Unix UID". Do not try to "filter out malicious code" with Python regex/policy checks — that creates a false sense of security; genuine hostile-code isolation must rely on Layer 3 OS-level mechanisms. This must be made explicit to users in the README / QUICKSTART.

## Explicitly not implemented (v1)

Arbitrary remote shell, arbitrary SSH host, sudo, remote port forwarding, job migration, multi-host SSH, a resident HPC daemon, remote HTTP MCP, automatic account switching, automatic credential management, and modifying `~/.ssh/config` / `authorized_keys`.

## Fail-closed

Incomplete configuration, SSH errors, unresolvable paths, unparsable commands, unparsable Slurm parameters, an uncertain partition or host — all of them end in **DENY**, never in a fallback to an unrestricted shell.

## Reporting a security issue

Report privately through a repository issue or contact the maintainers; do not disclose unfixed details in public channels.

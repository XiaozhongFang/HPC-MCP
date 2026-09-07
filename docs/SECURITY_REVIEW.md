# Security Review (2026-09-04)

## Findings and fixes

| Severity | Area | Finding | Remediation |
| --- | --- | --- | --- |
| Critical | File transfer | Arbitrary local paths allowed upload/download, including symlink targets and credential files. | `local_root`, component checks, credential-name deny-list, batch control-byte rejection, and post-download cap. |
| Critical | Safe shell | Lexically in-root symlinks could make `cat`/`grep` read outside the remote sandbox; executable paths and follow flags widened the command surface. | Remote canonical operand checks, executable-basename-only policy, and `-L/-H/-follow` denial. |
| High | Job tracking | Stored `job_dir` and cross-session entries were trusted, allowing output/accounting/cancel access to another session's job. | Session-bound schema validation and paths derived from numeric job IDs; canonical metadata directories and atomic locked writes. |
| High | Resource exhaustion | SSH/SFTP used unbounded `communicate()` buffering and accepted oversized command/environment payloads. | Streaming byte caps, process reaping, bounded argv/environment/script sizes, and validated timeouts. |
| High | Configuration | Injected `environ` was ignored; malformed nested YAML and negative limits escaped as runtime errors or invalid policy state. | Deterministic environment source, typed section/limit validation, canonical roots, and secure host-key modes only. |
| Medium | Audit | Regex-only sanitization missed secrets in dictionary keys such as `{"token": "..."}` and control characters. | Recursive key-aware redaction, control-character escaping, and truncation. |
| Medium | Concurrency | Concurrent submit calls could pass the active-job check simultaneously. | Async submission lock and atomic metadata lock directory. |
| Low | Maintainability | Unused helpers and imports obscured the security boundary. | Removed dead helpers and documented policy/service/transport ownership. |

## Verification

The regression suite covers traversal, symlink escapes, command injection, login/
compute separation, Slurm limits, job ownership, configuration, and file
services. Run:

```bash
python -m compileall -q src
python -m pytest -q
git diff --check
```

Static checks should additionally include Bandit/Ruff when available and
`python -m pip check` in the deployment environment.

## Operational requirements

Use `StrictHostKeyChecking=yes` with a pre-populated `known_hosts` file where
possible. Keep `HPC_MCP_ROOT` private to the intended user and set
`HPC_MCP_LOCAL_ROOT` to the smallest local project directory needed for
transfers. A new process session cannot manage jobs registered by an older
session; this is an application-level boundary. An equivalent Unix account can
still tamper with the JSON tracking file or race remote paths, so hostile
same-account deployments require separate Unix accounts or a privileged remote
helper.

---

# Security + Efficiency Review (v0.2, 2026-09)

## Scope

This round upgrades the server from "prevent agent misbehavior" to "help the
agent get the most value with the least remote I/O", without weakening any
existing boundary.  All findings below were fixed in this round and are
covered by regression tests.

## Findings and fixes

| Severity | Area | Finding | Remediation |
| --- | --- | --- | --- |
| Critical | Shared-account Slurm | `_queue_states()` / `queue()` ran whole-account `squeue` and filtered in Python, so other users' job metadata entered the MCP process. | All ownership checks now run `squeue -j <tracked ids>` only (batched at 500); with no tracked jobs the call returns without any `squeue`. New `tests/security/test_shared_account_isolation.py` proves foreign rows never reach results and no `-j`-less query is ever issued. |
| High | File read | Tool description encouraged paging a whole log from `offset=0` to EOF; each `read` was a stat+dd+base64 round trip. | `hpc.files.read` is now a bounded slice capped by `files.max_read_slice_bytes` (default 256 KiB) and the description steers to `hpc.files.search` first. |
| High | File listing | Recursive `find` streamed the whole tree and Python truncated after the fact. | `hpc.files.list` enumerates one depth layer per remote call, cuts remote output with `head` (SIGPIPE detection via `bash -o pipefail`), resumes with a `depth:N` cursor, and clamps `max_depth` (default 3). |
| High | Log diagnosis | Agents assembled `status -> output -> accounting -> read` sequences (tool thrashing). | New `hpc.jobs.diagnose` (state + accounting + bounded tails + error signatures) and `hpc.jobs.wait_and_diagnose`; every internal query is owned-only and capped. |
| High | Search | No first-class bounded search; agents improvised `grep` pipelines. | New `hpc.files.search`: single-file stat + refuse oversized; tree `grep -rn` + excluded dirs + `-m` + `head` + `timeout`; every budget server-clamped; pattern travels as a quoted argv (no injection). |
| Medium | Query dedup | Repeated identical status/list calls re-ran SSH each time. | `QueryCache` (TTL `cache_ttl_seconds`, default 2 s, 0 disables) dedupes idempotent read-only tools on normalized tool+args; any mutation invalidates the whole cache. |
| Medium | Command cost | Legal commands (`find`/`du`/`sort`/`git grep`) could peg the login node. | `security/command_cost.py` tiers every safe command (LOW 15s/256KiB, MEDIUM 30s/512KiB, HIGH 10s/512KiB); enforced in `SafeExec`, returns `cost_tier`/`timeout_seconds`. |
| Medium | Response leakage | Audit redaction did not protect the *response* to the agent (e.g. `password=` in job stderr). | `response_redactor.py` masks obvious secrets (`password=`/`token=`/`api_key`/`Bearer`/AWS/PEM blocks) in `files.read/search`, `slurm.output`, `shell.run_safe`, `diagnose`, `snapshot`; minimal patterns preserve scientific log content. |
| Medium | Info exposure | `hpc.info` returned raw `local_roots` paths (may embed usernames/project names). | Replaced with `workspace_available`/`transfer_enabled` booleans; regression test asserts the local username never appears. |
| Medium | Write races | Overwrite of a file another process changed could silently clobber work. | `hpc.files.write` accepts `expected_size`/`expected_mtime`/`expected_sha256`; any mismatch (or missing file) denies the write fail-closed. |
| Low | High-level execution | Experts and agents shared one raw-argv submit surface. | New `hpc.job.run` builds argv from fixed runtime profiles (julia/python/moose/bash) and reuses the full submit policy; `hpc.slurm.submit` stays for experts. |

## New security regression suite (this round)

- `test_shared_account_isolation.py` -- no whole-account `squeue`; foreign jobs
  never reach results; no-scan when nothing is tracked; foreign cancel/
  accounting/output denied.
- `test_files_search.py` -- search budgets, sandbox escape, control-char and
  injection rejection, `-m`/`head` caps, context parsing.
- `test_command_cost.py` -- tier classification, git-subcommand refinement,
  timeout/output clamping enforced in `SafeExec`.
- `test_response_redaction.py` -- password/token/API key/Bearer/AWS/PEM masked,
  normal log content preserved, bounded recursion.
- `test_info_minimal_exposure.py` -- `hpc.info` hides local roots and SSH
  details.
- `test_cache.py` -- dedup key, TTL expiry, bounded size, mutation
  invalidation.
- `test_project_snapshot.py` -- bounded tree, git summary, owned-jobs-only.
- `test_files_service.py` additions -- read slice budget, list paging/cursor,
  write concurrency protection.
- `test_slurm_manager.py` additions -- diagnose aggregation and caps,
  wait_and_diagnose, job.run policy reuse.

## Residual risk (unchanged, now explicit in three layers)

Layers 1 (MCP policy) and 2 (Slurm resource policy) make a *misbehaving
agent* harmless, but they cannot isolate *hostile code* running under a
shared Unix UID on compute nodes: `hpc.slurm.submit` grants that UID's
permissions.  Layer 3 (independent Unix UID, Slurm job isolation + filesystem
ACL, container/sandbox, or a privileged remote helper) is required for that
threat model.  Regex/policy filtering of "obviously malicious" programs is
explicitly not treated as an OS-level isolation mechanism.  See
`README.md` / `SECURITY.md` / `docs/ARCHITECTURE.md`.

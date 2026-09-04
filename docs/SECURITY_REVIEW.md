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

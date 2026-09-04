# HPC-MCP Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Slurm HPC MCP server against path, command, local-file, configuration, job-ownership, logging, and resource-exhaustion vulnerabilities while removing dead code and documenting the enforced architecture.

**Architecture:** Keep policy decisions in small pure modules, make transport helpers fail closed, and pass validated/canonical values between services. Remote metadata uses one canonical jobs-root policy; local transfers use an explicit local sandbox; tool dispatch validates runtime values before invoking service methods. The server owns transport cleanup and emits structured redacted audit records.

**Tech Stack:** Python 3.10+, asyncio, OpenSSH CLI/SFTP, PyYAML, MCP low-level server, pytest/pytest-asyncio.

---

### Task 1: Configuration validation and canonical roots (complete)

**Files:**
- Modify: `src/hpc_mcp/config.py`
- Modify: `src/hpc_mcp/__main__.py`
- Modify: `config/example.yaml`
- Test: `tests/unit/test_config.py`

- [ ] Add deterministic environment injection (`build_config(..., environ=...)`) and use it for every `HPC_MCP_*` lookup; keep process environment as the default only when `environ` is omitted.
- [ ] Normalize the remote root with POSIX `normpath`, reject `/`, control characters, and non-string values, and validate nested YAML sections before accessing `.get`.
- [ ] Validate all numeric limits as positive integers (or non-negative for GPU/count fields), validate time limits through `slurm_policy.parse_time_limit`, cap command/output/file limits, reject `strict_host_key_checking=no`, and validate host/user/partition/command names for control characters.
- [ ] Add `local_root` to `Config` with CLI/env/YAML precedence and default it to the resolved process working directory; normalize it and reject a local root that is a file.
- [ ] Add regression tests for injected environments, malformed nested mappings, negative/zero limits, insecure host-key mode, root normalization, and local-root precedence.
- [ ] Run `python -m pytest tests/unit/test_config.py -q`.

### Task 2: Canonical path and local-transfer policy (complete)

**Files:**
- Modify: `src/hpc_mcp/security/path_policy.py`
- Modify: `src/hpc_mcp/filesystem/service.py`
- Modify: `src/hpc_mcp/filesystem/transfer.py`
- Modify: `src/hpc_mcp/ssh/sftp.py`
- Modify: `src/hpc_mcp/config.py`
- Test: `tests/security/test_path_traversal.py`
- Test: `tests/security/test_symlink_escape.py`
- Test: `tests/unit/test_files_service.py`
- Create: `tests/security/test_local_transfer.py`

- [ ] Make `is_within` normalize both operands and require absolute paths; add a `validate_local_path` helper that rejects NUL/CR/LF, escapes from `local_root`, symlinks in every existing component, and sensitive SSH credential filenames.
- [ ] Make create operations verify both the canonical parent and the final target: reject an existing symlink/non-regular target for writes, and re-check the target immediately before write/rename/delete operations.
- [ ] Validate file read offsets/caps and list/output caps as non-negative bounded integers; reject negative offsets and avoid `value or default` behavior for explicit zero.
- [ ] Replace direct private-method use in transfers with public canonicalization methods; enforce local upload/download roots, no local symlink targets, configured transfer ceilings, and post-download size checks.
- [ ] Reject control characters in SFTP batch paths and escape batch commands with a dedicated helper; terminate timed-out SFTP processes and reap them.
- [ ] Add regression tests for symlink-to-outside writes/uploads, newline SFTP injection, local `~/.ssh` reads/overwrites, negative offsets/caps, and normalized sibling paths.
- [ ] Run the path/filesystem/security test groups.

### Task 3: Login command policy and safe execution (complete)

**Files:**
- Modify: `src/hpc_mcp/security/command_policy.py`
- Modify: `src/hpc_mcp/shell/safe_exec.py`
- Modify: `src/hpc_mcp/config.py`
- Modify: `skills/hpc-development/SKILL.md`
- Test: `tests/security/test_command_injection.py`
- Test: `tests/security/test_login_compute_boundary.py`
- Create: `tests/security/test_safe_exec_boundary.py`

- [ ] Reject all executable tokens containing `/`, all ASCII control characters, and command-policy extensions that contain separators or forbidden basenames.
- [ ] Deny symlink-following flags (`find -L/-H/-follow`, `du -L`, `ls -L`) and constrain `env`/`printenv` to a sanitized allowlist so credentials cannot be returned.
- [ ] Remove or gate direct `squeue`, `sacct`, and `scontrol` access from `hpc.shell.run_safe`; tracked Slurm tools remain the only job-inspection path.
- [ ] Resolve every absolute path operand remotely before execution, including `--flag=/path` operands, and fail closed when `realpath` is unavailable or outside the root.
- [ ] Execute safe commands through a single fixed wrapper that does not permit user-controlled shell fragments; preserve timeout/output caps and sanitize returned stderr.
- [ ] Add tests for relative executable paths, symlink operands, follow flags, environment leaks, Slurm ownership bypasses, malformed control bytes, and timeout bounds.
- [ ] Run command/security tests.

### Task 4: SSH transport hardening and lifecycle (complete)

**Files:**
- Modify: `src/hpc_mcp/ssh/manager.py`
- Modify: `src/hpc_mcp/server.py`
- Modify: `src/hpc_mcp/__main__.py`
- Test: `tests/unit/test_ssh_manager.py`
- Create: `tests/security/test_transport_limits.py`

- [ ] Remove the unused SSH lock or use it to serialize control-socket setup/teardown; validate timeout/output arguments before process creation.
- [ ] Replace unbounded `communicate()` buffering with capped asynchronous stream readers that stop collecting after the configured byte limit while still draining/reaping the process.
- [ ] Ensure timeout paths kill and await the SSH process; map transport failures to non-secret errors.
- [ ] Close `SshManager` in `run_server` and `--check` `finally` blocks.
- [ ] Add tests for output caps, timeout cleanup, strict host-key argv, and lifecycle close behavior.
- [ ] Run transport tests and the full suite.

### Task 5: Slurm submission, metadata integrity, and concurrency (complete)

**Files:**
- Modify: `src/hpc_mcp/slurm/jobs.py`
- Modify: `src/hpc_mcp/slurm/manager.py`
- Modify: `src/hpc_mcp/security/slurm_policy.py`
- Modify: `src/hpc_mcp/tools/registry.py`
- Test: `tests/unit/test_slurm_manager.py`
- Test: `tests/security/test_slurm_policy.py`
- Create: `tests/security/test_job_tracker_integrity.py`

- [ ] Validate job IDs, job names, partitions, command argv length/count, environment count/size, and all resource fields before any remote query or script generation.
- [ ] Serialize submission/concurrency checks with an async lock and normalize terminal Slurm states when counting active jobs.
- [ ] Make job metadata entries schema-validated, session-owned, and constrained to the expected jobs directory; never trust a stored `job_dir` for output/accounting paths.
- [ ] Canonicalize `.hpc-mcp` and job directories before creation, reject symlinked metadata paths, and write tracking data atomically with a lock/temp-file/rename sequence inside the sandbox.
- [ ] Generate Slurm directives from validated values only and preserve shell-safe argv quoting for the compute command.
- [ ] Validate `tail_bytes`, wait limits, and poll intervals explicitly; ensure output reads canonical expected paths.
- [ ] Add tests for forged tracking entries, cross-session ownership, metadata symlinks, concurrent submits, oversized scripts, invalid resource values, and negative output/wait values.
- [ ] Run Slurm/security tests.

### Task 6: Tool dispatch, annotations, and audit redaction (complete)

**Files:**
- Modify: `src/hpc_mcp/tools/registry.py`
- Modify: `src/hpc_mcp/server.py`
- Modify: `src/hpc_mcp/logging.py`
- Modify: `src/hpc_mcp/errors.py`
- Test: `tests/unit/test_files_service.py`
- Create: `tests/unit/test_server_dispatch.py`
- Create: `tests/security/test_logging_redaction.py`

- [ ] Add runtime argument validation for JSON tool calls (types, required values, unknown keys, ranges) before handler invocation; return a stable policy error instead of raw `TypeError`/`ValueError`.
- [ ] Mark mkdir/rename and other mutating tools with accurate destructive/idempotent annotations.
- [ ] Replace regex-only logging sanitization with recursive key-aware redaction for password/token/secret/private-key fields and control-character stripping, then truncate.
- [ ] Ensure unknown-tool names and unexpected exception messages cannot inject log lines or expose credentials.
- [ ] Remove dead helpers (`_deep_get`, `_suggest`, `EXECUTABLE_RE`, unused `stat_exists`, unused imports) and document the resulting module boundaries.
- [ ] Add dispatch and redaction tests, then run the full suite.

### Task 7: Documentation, static review, and release checks (complete)

**Files:**
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `config/example.yaml`
- Create: `docs/SECURITY_REVIEW.md`
- Create: `docs/ARCHITECTURE.md`

- [ ] Document remote and local sandbox roots, ownership/session semantics, denied login commands, sanitized environment behavior, transfer limits, host-key requirements, and fail-closed behavior.
- [ ] Record the security findings, severity, affected files, fixes, residual TOCTOU limitations, and operational guidance in `docs/SECURITY_REVIEW.md`.
- [ ] Describe policy/transport/service/registry boundaries and lifecycle in `docs/ARCHITECTURE.md` with a request-flow diagram.
- [ ] Run `python -m compileall -q src`, `python -m pytest -q`, and available static scans (`python -m pip check`, plus Bandit/Ruff when installed); inspect `git diff --check`.
- [ ] Verify no shell=True, unsafe YAML loader, unbounded subprocess output, private-key logging, or unresolved TODO/dead helper remains.

---

Self-review: the plan covers the requested security audit, vulnerability fixes, dead/invalid code cleanup, documentation refinement, and architecture optimization. It keeps Slurm compute submission available while narrowing login and transfer surfaces, and every behavior change has a named regression-test location.

## Execution Status

All tasks in this plan are implemented in the current working tree. The final
verification command passed with 297 tests, compilation, and `git diff --check`.

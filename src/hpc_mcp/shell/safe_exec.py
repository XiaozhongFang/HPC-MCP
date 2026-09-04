"""Restricted execution of whitelisted login-node commands.

The command passes :mod:`hpc_mcp.security.command_policy` first; the
resulting argv runs without any shell (locally *and* remotely we build one
quoted argv).  ``cwd`` must be inside the user root, and the executable
runs with a hard timeout and output cap.
"""

from __future__ import annotations

import shlex
import posixpath

from ..config import Config
from ..errors import CommandPolicyError
from ..security.command_policy import check_command
from ..security.path_policy import is_within, validate_path
from ..ssh.manager import SshManager


class SafeExec:
    def __init__(self, cfg: Config, ssh: SshManager) -> None:
        self._cfg = cfg
        self._ssh = ssh

    async def run(self, command: str, cwd: str | None = None, *, timeout: int | None = None) -> dict:
        cfg = self._cfg
        argv = check_command(command, cfg.shell.safe_commands)
        if not argv:
            raise CommandPolicyError("Empty command", requested=command)

        if cwd is not None:
            # cwd must stay inside the sandbox; realpath-verify to stop
            # symlink escapes of the working directory.
            from ..filesystem.service import FileService

            fs = FileService(cfg, self._ssh)
            real_cwd = await fs._resolve_existing(cwd)
            if not is_within(real_cwd, cfg.root):
                raise CommandPolicyError(
                    "Working directory escapes the configured user root",
                    requested=cwd,
                    scope=cfg.root,
                )
        else:
            real_cwd = cfg.root

        base = posixpath.basename(argv[0])
        if base in {"squeue", "sacct", "scontrol"}:
            raise CommandPolicyError(
                "Direct Slurm queries are disabled in shell.run_safe to preserve shared-account job isolation",
                requested=command,
                use_instead="hpc.slurm.queue/status/accounting",
            )

        # Path operands must stay inside the sandbox too.  Lexical checks do
        # not catch an in-root symlink pointing at /etc, so every likely path
        # operand is canonicalized on the remote host before execution.
        for token in argv[1:]:
            candidate = token
            if token.startswith("-"):
                _, eq, val = token.partition("=")
                if not eq or not val:
                    continue
                candidate = val
            if candidate.startswith("/") or "/" in candidate or base in {
                "cat", "head", "tail", "stat", "ls", "du", "df", "wc", "sort", "uniq", "find", "grep"
            }:
                if not candidate.startswith("/"):
                    candidate = posixpath.join(real_cwd, candidate)
                await self._check_operand(candidate, command)

        if timeout is None:
            timeout = cfg.shell.max_exec_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise CommandPolicyError("timeout must be a positive integer", requested=str(timeout))
        timeout = min(timeout, cfg.shell.max_exec_seconds)
        max_out = cfg.shell.max_output_bytes

        # Do not inherit account-level variables such as LD_PRELOAD,
        # BASH_ENV, GIT_EXTERNAL_DIFF, or credential tokens.  Git config is
        # also forced away from global/system files and pagers/hooks.
        env_prefix = [
            "env", "-i", "PATH=/usr/bin:/bin", "HOME=/nonexistent",
            "LANG=C", "LC_ALL=C", "GIT_CONFIG_NOSYSTEM=1",
            "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null",
            "GIT_TERMINAL_PROMPT=0", "GIT_PAGER=cat", "GIT_EXTERNAL_DIFF=",
        ]
        quoted = " ".join(shlex.quote(a) for a in [*env_prefix, *argv])
        remote = f"cd {shlex.quote(real_cwd)} && timeout {int(timeout)} {quoted}"
        res = await self._ssh.run_raw(
            remote, timeout=timeout + 10, max_output=max_out, check=False
        )
        result = {
            "command": command,
            "cwd": real_cwd,
            "exit_code": res.exit_code,
            "stdout": res.stdout_text,
            "stderr": res.stderr_text,
        }
        if res.exit_code == 124:
            result["timed_out"] = True
            result["stderr"] += f"\n[killed after {timeout}s timeout]"
        return result

    async def _check_operand(self, path_token: str, command: str) -> None:
        try:
            lexical = validate_path(path_token, self._cfg.root)
            real = await self._ssh.realpath(lexical)
            if real is None:
                raise ValueError("remote realpath unavailable")
            validate_path(real, self._cfg.root)
        except Exception as exc:
            raise CommandPolicyError(
                f"Path operand {path_token!r} escapes the configured user root",
                requested=command,
                scope=self._cfg.root,
            ) from exc

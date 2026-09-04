"""Restricted execution of whitelisted login-node commands.

The command passes :mod:`hpc_mcp.security.command_policy` first; the
resulting argv runs without any shell (locally *and* remotely we build one
quoted argv).  ``cwd`` must be inside the user root, and the executable
runs with a hard timeout and output cap.
"""

from __future__ import annotations

from ..config import Config
from ..errors import CommandPolicyError, RemoteCommandError
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

        timeout = min(timeout or cfg.shell.max_exec_seconds, cfg.shell.max_exec_seconds)
        max_out = cfg.shell.max_output_bytes

        import shlex

        quoted = " ".join(shlex.quote(a) for a in argv)
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

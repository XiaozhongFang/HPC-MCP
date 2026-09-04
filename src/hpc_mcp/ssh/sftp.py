"""SFTP-based file transfer (used by upload/download tools).

Transfers run through the OpenSSH ``sftp`` client in batch mode with a
fixed argv (no local shell).  All remote paths must already have passed the
path sandbox before reaching this layer -- this module performs no policy
checks itself.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from ..config import Config
from ..errors import RemoteCommandError, SshError
from ..logging import get_logger

_MAX_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024  # hard ceiling: 2 GiB


class SftpClient:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._log = get_logger()
        self._bin = shutil.which("sftp")
        if self._bin is None:
            raise SshError("OpenSSH 'sftp' client not found in PATH")

    def _base_argv(self) -> list[str]:
        cfg = self._cfg.ssh
        argv = [
            self._bin or "sftp",
            "-b", "-",  # batch mode from stdin
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={cfg.connect_timeout}",
            "-o", f"StrictHostKeyChecking={cfg.strict_host_key_checking}",
            "-o", "NumberOfPasswordPrompts=0",
        ]
        if cfg.port and cfg.port != 22:
            argv += ["-P", str(cfg.port)]
        if cfg.identity_file:
            argv += ["-i", cfg.identity_file, "-o", "IdentitiesOnly=yes"]
        dest = cfg.host or ""
        if cfg.user:
            dest = f"{cfg.user}@{cfg.host}"
        argv += ["--", dest]
        return argv

    async def _run_batch(self, commands: list[str], timeout: int) -> None:
        batch = "\n".join(commands) + "\n"
        argv = self._base_argv()
        self._log.debug("sftp batch: %s", "; ".join(commands))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SshError(f"Failed to launch sftp: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(batch.encode()), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise SshError(f"SFTP transfer timed out after {timeout}s") from exc
        if proc.returncode not in (0, None):
            raise RemoteCommandError(
                f"SFTP batch failed (exit {proc.returncode}): {stderr.decode(errors='replace')[:500]}",
                exit_code=proc.returncode,
            )
        err_text = stderr.decode(errors="replace")
        if "Couldn't" in err_text or "No such file" in err_text or "Permission denied" in err_text:
            raise RemoteCommandError(f"SFTP error: {err_text.strip()[:500]}")

    async def upload(self, local_path: str, remote_path: str, *, timeout: int | None = None) -> int:
        """Upload a local file to an already-sandbox-validated remote path."""
        size = Path(local_path).stat().st_size
        if size > _MAX_TRANSFER_BYTES:
            raise RemoteCommandError(
                f"Local file is {size} bytes, exceeding the transfer ceiling of {_MAX_TRANSFER_BYTES}"
            )
        timeout = timeout or max(120, size // (5 * 1024 * 1024) + 60)
        # sftp batch commands: quote remote path against batch-line parsing
        await self._run_batch([f'put "{_batch_escape(local_path)}" "{_batch_escape(remote_path)}"'], timeout)
        return size

    async def download(self, remote_path: str, local_path: str, *, timeout: int | None = None) -> int:
        timeout = timeout or 600
        await self._run_batch([f'get "{_batch_escape(remote_path)}" "{_batch_escape(local_path)}"'], timeout)
        return Path(local_path).stat().st_size


def _batch_escape(path: str) -> str:
    """Escape a path for an sftp batch line (inside double quotes)."""
    return path.replace("\\", "\\\\").replace('"', '\\"')

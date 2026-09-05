"""SFTP-based file transfer (used by upload/download tools).

Transfers run through the OpenSSH ``sftp`` client in batch mode with a
fixed argv (no local shell).  All remote paths must already have passed the
path sandbox before reaching this layer -- this module performs no policy
checks itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import Config
from ..errors import RemoteCommandError, SshError
from ..logging import get_logger
from .manager import _resolve_bin

_MAX_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024  # hard ceiling: 2 GiB


class SftpClient:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._log = get_logger()
        self._bin = _resolve_bin(cfg.ssh.sftp_bin, "sftp")

    def _base_argv(self) -> list[str]:
        cfg = self._cfg.ssh
        argv = [
            self._bin,
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
        if not isinstance(timeout, int) or timeout <= 0:
            raise SshError("SFTP timeout must be a positive integer")
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
        async def collect(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await stream.read(64 * 1024)
                if not chunk:
                    return b"".join(chunks)[:1024 * 1024]
                if total < 1024 * 1024:
                    take = min(len(chunk), 1024 * 1024 - total)
                    chunks.append(chunk[:take])
                    total += take
        stdout_task = asyncio.create_task(collect(proc.stdout))
        stderr_task = asyncio.create_task(collect(proc.stderr))
        try:
            if proc.stdin is not None:
                proc.stdin.write(batch.encode())
                await proc.stdin.drain()
                proc.stdin.close()
        except (BrokenPipeError, ConnectionError) as exc:
            proc.kill()
            await proc.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise SshError("SFTP process closed its input unexpectedly") from exc
        try:
            await asyncio.wait_for(asyncio.gather(stdout_task, stderr_task, proc.wait()), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise SshError(f"SFTP transfer timed out after {timeout}s") from exc
        stdout = stdout_task.result()
        stderr = stderr_task.result()
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
        timeout = max(120, size // (5 * 1024 * 1024) + 60) if timeout is None else timeout
        # sftp batch commands: quote remote path against batch-line parsing
        await self._run_batch([f'put "{_batch_escape(local_path)}" "{_batch_escape(remote_path)}"'], timeout)
        return size

    async def download(self, remote_path: str, local_path: str, *, timeout: int | None = None) -> int:
        timeout = 600 if timeout is None else timeout
        await self._run_batch([f'get "{_batch_escape(remote_path)}" "{_batch_escape(local_path)}"'], timeout)
        return Path(local_path).stat().st_size


def _batch_escape(path: str) -> str:
    """Escape a path for an sftp batch line (inside double quotes)."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        raise RemoteCommandError("SFTP paths may not contain control characters")
    return path.replace("\\", "\\\\").replace('"', '\\"')

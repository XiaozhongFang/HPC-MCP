"""Upload/download between local machine and the sandboxed remote root."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..errors import PolicyDenied
from ..security import limits
from ..ssh.sftp import SftpClient
from .service import FileService


class TransferService:
    def __init__(self, cfg: Config, sftp: SftpClient, files: FileService) -> None:
        self._cfg = cfg
        self._sftp = sftp
        self._files = files

    async def upload(self, local_path: str, remote_path: str) -> dict:
        lp = Path(local_path).expanduser()
        if not lp.is_file():
            raise PolicyDenied(f"Local path is not a regular file: {local_path}")
        limits.check_write_size(lp.stat().st_size, self._cfg.files.max_write_bytes)
        real_remote = await self._files._resolve_for_create(remote_path)
        nbytes = await self._sftp.upload(str(lp), real_remote)
        return {"local_path": str(lp), "remote_path": real_remote, "bytes": nbytes}

    async def download(self, remote_path: str, local_path: str) -> dict:
        real_remote = await self._files._resolve_existing(remote_path)
        st = await self._files.stat(real_remote)
        if st["type"] != "regular file":
            raise PolicyDenied(f"Remote path is not a regular file: {real_remote}")
        limits.check_read_size(st["size"], self._cfg.files.max_read_bytes * 10)
        lp = Path(local_path).expanduser()
        nbytes = await self._sftp.download(real_remote, str(lp))
        return {"remote_path": real_remote, "local_path": str(lp), "bytes": nbytes}

"""SSH transport manager.

Runs remote commands via the OpenSSH CLI with a strict, fixed argv
(``shell=False`` semantics -- we never go through a local shell).  Only the
single configured host may be contacted; there is no API for arbitrary
hosts, tunnels or ProxyJump.

Design notes:

* The SSH connection is *only* used as a transport.  Every command executed
  through :meth:`SshManager.run` is produced by a trusted internal caller
  (filesystem service, Slurm manager, safe-exec) that has already passed
  the relevant policy layer.  The remote argv is re-serialized with
  :func:`shlex.join` so the remote shell parses it exactly as one argv.
* ControlMaster connection sharing is used when available to keep latency
  low; the control socket lives in a private temp dir and is cleaned up on
  exit.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import tempfile
from dataclasses import dataclass

from ..config import Config
from ..errors import RemoteCommandError, SshError
from ..logging import get_logger


@dataclass
class RemoteResult:
    stdout: bytes
    stderr: bytes
    exit_code: int

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class SshManager:
    """Executes commands on the single configured HPC host."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._log = get_logger()
        self._control_dir: tempfile.TemporaryDirectory[str] | None = None
        self._control_path: str | None = None
        self._ssh_bin = shutil.which("ssh")
        if self._ssh_bin is None:
            raise SshError("OpenSSH client ('ssh') not found in PATH")
        self._lock = asyncio.Lock()

    # -- connection setup ---------------------------------------------------

    def _base_argv(self) -> list[str]:
        cfg = self._cfg.ssh
        argv = [
            self._ssh_bin or "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={cfg.connect_timeout}",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "NumberOfPasswordPrompts=0",
        ]
        # Connection sharing (speeds up bursts of short commands)
        if self._control_path is None:
            self._control_dir = tempfile.TemporaryDirectory(prefix="hpc-mcp-ssh-")
            self._control_path = f"{self._control_dir.name}/ctl"
        argv += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self._control_path}",
            "-o", "ControlPersist=60",
        ]
        if cfg.port and cfg.port != 22:
            argv += ["-p", str(cfg.port)]
        if cfg.identity_file:
            argv += ["-i", cfg.identity_file, "-o", "IdentitiesOnly=yes"]
        destination = cfg.host or ""
        if cfg.user:
            destination = f"{cfg.user}@{cfg.host}"
        argv += ["--", destination]
        return argv

    async def close(self) -> None:
        """Shut down the shared control connection, if any."""
        if self._control_path:
            argv = self._base_argv()[:1] + [
                "-O", "exit", "-o", f"ControlPath={self._control_path}",
            ]
            destination = self._cfg.ssh.host or ""
            if self._cfg.ssh.user:
                destination = f"{self._cfg.ssh.user}@{self._cfg.ssh.host}"
            argv += ["--", destination]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (OSError, asyncio.TimeoutError):
                pass
        if self._control_dir:
            self._control_dir.cleanup()
            self._control_dir = None
            self._control_path = None

    # -- command execution ---------------------------------------------------

    async def run(
        self,
        argv: list[str],
        *,
        timeout: int | None = None,
        max_output: int = 4 * 1024 * 1024,
        check: bool = True,
    ) -> RemoteResult:
        """Run ``argv`` on the remote host with no local shell.

        The argv is re-serialized with :func:`shlex.join` so the remote side
        parses exactly one command with fixed arguments.
        """
        if not argv:
            raise SshError("Empty remote argv")
        remote_cmd = shlex.join([str(a) for a in argv])
        return await self.run_raw(remote_cmd, timeout=timeout, max_output=max_output, check=check)

    async def run_raw(
        self,
        remote_cmd: str,
        *,
        timeout: int | None = None,
        max_output: int = 4 * 1024 * 1024,
        check: bool = True,
        stdin_text: str | None = None,
    ) -> RemoteResult:
        """Run a pre-serialized remote command string.

        Only trusted internal callers may use this; the string must have
        been built with shlex quoting on every untrusted component.
        """
        timeout = timeout or self._cfg.ssh.command_timeout
        full_argv = self._base_argv() + [remote_cmd]
        self._log.debug("ssh exec: %s", remote_cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_argv,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
            )
        except OSError as exc:
            raise SshError(f"Failed to launch ssh: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_text.encode() if stdin_text is not None else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001 - best effort kill
                pass
            raise SshError(f"Remote command timed out after {timeout}s: {remote_cmd[:200]}") from exc

        if len(stdout) > max_output:
            stdout = stdout[:max_output] + b"\n...[output truncated]"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + b"\n...[output truncated]"

        result = RemoteResult(stdout=stdout, stderr=stderr, exit_code=proc.returncode or 0)

        if proc.returncode == 255:
            # ssh-level failure (connection/auth/host-key)
            raise SshError(
                f"SSH to {self._cfg.ssh.host} failed: {result.stderr_text.strip()[:400]}"
            )
        if check and result.exit_code != 0:
            raise RemoteCommandError(
                f"Remote command failed (exit {result.exit_code}): {remote_cmd[:200]}\n"
                f"{result.stderr_text.strip()[:800]}",
                exit_code=result.exit_code,
            )
        return result

    # -- helpers -------------------------------------------------------------

    async def probe(self) -> dict[str, str]:
        """Connectivity + environment probe used by --check and hpc.info."""
        res = await self.run(
            ["sh", "-c", "echo HOSTNAME=$(hostname); echo USER=$(whoami); "
             "echo SLURM=$(command -v sbatch || echo none); "
             "echo CLUSTER=$(scontrol show config 2>/dev/null | grep -m1 ClusterName || echo unknown)"],
            timeout=self._cfg.ssh.connect_timeout + 10,
            check=True,
        )
        info: dict[str, str] = {}
        for line in res.stdout_text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip()
        return {
            "hostname": info.get("HOSTNAME", "unknown"),
            "remote_user": info.get("USER", "unknown"),
            "slurm_available": str(info.get("SLURM", "none") != "none"),
            "cluster": info.get("CLUSTER", "unknown").split("=")[-1].strip(),
        }

    async def realpath(self, path: str) -> str | None:
        """Resolve a remote path with realpath(1).  Returns None if missing."""
        res = await self.run(["realpath", "-m", "--", path], check=False)
        if res.exit_code != 0:
            return None
        out = res.stdout_text.strip()
        return out or None

    async def stat_exists(self, path: str) -> bool:
        res = await self.run(["test", "-e", path], check=False)
        return res.exit_code == 0

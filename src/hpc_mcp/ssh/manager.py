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
            "-o", f"StrictHostKeyChecking={cfg.strict_host_key_checking}",
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
        async with self._lock:
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
        if any("\x00" in str(a) or "\n" in str(a) or "\r" in str(a) for a in argv):
            raise SshError("Remote argv contains forbidden control characters")
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
        if not isinstance(remote_cmd, str) or not remote_cmd or "\x00" in remote_cmd:
            raise SshError("Remote command is empty or contains NUL")
        if len(remote_cmd) > 1024 * 1024:
            raise SshError("Remote command exceeds the transport size limit")
        timeout = self._cfg.ssh.command_timeout if timeout is None else timeout
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise SshError("Remote command timeout must be a positive integer")
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 0:
            raise SshError("Remote output limit must be a non-negative integer")
        if max_output > 64 * 1024 * 1024:
            raise SshError("Remote output limit exceeds the transport hard ceiling")
        if stdin_text is not None and (not isinstance(stdin_text, str) or len(stdin_text.encode()) > 8 * 1024 * 1024):
            raise SshError("Remote stdin exceeds the transport size limit")
        async with self._lock:
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

        async def collect(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            chunks: list[bytes] = []
            total = 0
            truncated = False
            while True:
                chunk = await stream.read(64 * 1024)
                if not chunk:
                    break
                if total < max_output:
                    take = min(len(chunk), max_output - total)
                    chunks.append(chunk[:take])
                    total += take
                    if take < len(chunk):
                        truncated = True
                else:
                    truncated = True
            data = b"".join(chunks)
            if truncated and max_output:
                marker = b"\n...[output truncated]"
                return (data + marker)[:max_output]
            return data[:max_output]

        stdout_task = asyncio.create_task(collect(proc.stdout))
        stderr_task = asyncio.create_task(collect(proc.stderr))
        try:
            if stdin_text is not None and proc.stdin is not None:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
        except (BrokenPipeError, ConnectionError) as exc:
            proc.kill()
            await proc.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise SshError("SSH process closed its input unexpectedly") from exc
        try:
            await asyncio.wait_for(asyncio.gather(stdout_task, stderr_task, proc.wait()), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001 - best effort kill
                pass
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise SshError(f"Remote command timed out after {timeout}s: {remote_cmd[:200]}") from exc

        stdout = stdout_task.result()
        stderr = stderr_task.result()

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

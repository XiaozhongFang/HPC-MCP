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
* Each command uses a regular OpenSSH process.  ControlMaster multiplexing is
  intentionally not forced: some supported environments (notably WSL and
  SSH wrappers) expose a non-socket ControlPath and fail with opaque errors
  such as ``getsockname failed: Not a socket`` even though plain ``ssh`` works.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
from dataclasses import dataclass

from ..config import Config
from ..errors import RemoteCommandError, SshError
from ..logging import get_logger


def _resolve_bin(configured: str | None, name: str) -> str:
    """Resolve the ssh/sftp executable path.

    ``configured`` may be:
      - None          -> search PATH for ``name``
      - "name"        -> search PATH
      - "/abs/path"   -> use exactly
      - "@/abs/path"  -> WSL '@' prefix, strip and use exactly
    Raises SshError when a configured path is unusable or PATH lookup fails.
    """
    if configured:
        value = configured.strip()
        if value.startswith("@"):
            value = value[1:]
        if "/" in value or "\\" in value:
            # explicit path
            if not value or value.startswith("~") or "\x00" in value:
                raise SshError(f"Invalid {name} executable path: {configured!r}")
            return value
        # bare name: let PATH decide, but validate it resolves
        found = shutil.which(value)
        if found is None:
            raise SshError(f"OpenSSH client ('{value}') not found in PATH")
        return found
    found = shutil.which(name)
    if found is None:
        raise SshError(f"OpenSSH client ('{name}') not found in PATH")
    return found


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
        self._ssh_bin = _resolve_bin(cfg.ssh.ssh_bin, "ssh")
        self._lock = asyncio.Lock()

    @property
    def ssh_bin(self) -> str:
        """Resolved path of the ssh executable actually used."""
        return self._ssh_bin

    # -- connection setup ---------------------------------------------------

    def _base_argv(self) -> list[str]:
        cfg = self._cfg.ssh
        argv = [
            self._ssh_bin,
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={cfg.connect_timeout}",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
            "-o", f"StrictHostKeyChecking={cfg.strict_host_key_checking}",
            "-o", "NumberOfPasswordPrompts=0",
            # Do not inherit a broken global ControlPath from the user's SSH
            # config; every invocation must behave like a standalone ssh call.
            "-o", "ControlMaster=no",
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
        """Keep the transport lifecycle API for server shutdown compatibility."""
        return None

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
            raise SshError(
                f"连接 HPC 登录节点超时（{timeout}s 内无响应）。\n\n"
                "原因：很可能是网络不通——目标主机不可达（内网地址需连 VPN / 配跳板机），"
                "或防火墙拦截。\n\n"
                "请联系管理员或检查本地网络环境（host 等信息不会透露给代理）。"
            ) from exc

        stdout = stdout_task.result()
        stderr = stderr_task.result()

        result = RemoteResult(stdout=stdout, stderr=stderr, exit_code=proc.returncode or 0)

        if proc.returncode == 255:
            # ssh-level failure (connection/auth/host-key) -> 翻译成可操作的诊断
            raise SshError(self._diagnose_ssh_failure(result.stderr_text))
        if check and result.exit_code != 0:
            raise RemoteCommandError(
                f"Remote command failed (exit {result.exit_code}): {remote_cmd[:200]}\n"
                f"{result.stderr_text.strip()[:800]}",
                exit_code=result.exit_code,
            )
        return result

    # -- helpers -------------------------------------------------------------

    def _diagnose_ssh_failure(self, stderr: str) -> str:
        """Translate raw OpenSSH stderr into an actionable, plain-language message.

        The message must NOT reveal the SSH host/user/port: the agent could use
        those to bypass the MCP and log in directly.  Only generic guidance is
        returned.
        """
        raw = stderr.strip()[:400]
        low = raw.lower()
        # scrub host-like tokens (IPs, hostnames, user@host) from the raw
        # stderr so nothing reachable is echoed back to the agent
        import re as _re
        raw = _re.sub(r"\d{1,3}(?:\.\d{1,3}){3}", "[ip]", raw)
        raw = _re.sub(r"[\w.-]+@[\w.-]+", "[user@host]", raw)
        raw = _re.sub(r"connect to host [\w.-]+", "connect to host [host]", raw)

        header = "无法连接到 HPC 登录节点。"
        if "connection timed out" in low or "no route to host" in low or "timeout" in low:
            return (
                f"{header}\n\n原因：网络不通——主机不可达或连接超时。\n\n"
                "请检查：\n"
                "  1. 是否在校园网内 / 已连接 VPN？\n"
                "  2. 是否需要跳板机（在 ~/.ssh/config 里配置 ProxyJump）？\n"
                "  3. 手动 ssh 登录是否正常？\n\n"
                f"原始错误（已脱敏）：{raw}"
            )
        if "connection refused" in low:
            return (
                f"{header}\n\n原因：登录节点的 SSH 服务拒绝连接（端口不对或服务未运行）。\n\n"
                "请检查：\n"
                "  1. 配置的 SSH 端口是否正确。\n"
                "  2. 是否需要走跳板机。\n\n"
                f"原始错误（已脱敏）：{raw}"
            )
        if "permission denied" in low:
            return (
                f"{header}\n\n原因：认证失败（没有可用的免密登录）。\n\n"
                "请配置 SSH 公钥免密（用 ~/.ssh/config 管理 IdentityFile），"
                "或由管理员配置免密。\n\n"
                f"原始错误（已脱敏）：{raw}"
            )
        if "host key verification failed" in low:
            return (
                f"{header}\n\n原因：主机密钥未固定（StrictHostKeyChecking=yes 拒绝首次未知主机）。\n\n"
                "请先手动 ssh 登录一次确认指纹并写入 known_hosts，"
                "或由管理员配置 strict_host_key_checking: accept-new（仅信任环境）。\n\n"
                f"原始错误（已脱敏）：{raw}"
            )
        if "could not resolve hostname" in low or "name or service not known" in low:
            return (
                f"{header}\n\n原因：无法解析登录节点主机名。\n\n"
                "请在 ~/.ssh/config 里用 Host 别名映射到正确地址，或由管理员修正配置。\n\n"
                f"原始错误（已脱敏）：{raw}"
            )
        return f"{header}\n\n原始错误（已脱敏）：{raw}"

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
            # hostname / remote_user are deliberately omitted: they leak
            # cluster topology and the shared SSH account to the agent.
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

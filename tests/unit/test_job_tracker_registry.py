"""Registry-write tests for the job tracker.

``JobTracker._write_register`` guards the shared-account ownership registry
with a remote shell script (lock directory, temp file, atomic rename).  These
tests execute that very script through a local ``sh`` so its lock semantics --
stale-lock reclaim, bounded retry, symlink refusal, temp-file reuse -- are
exercised for real instead of being asserted against a hand-written mock.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from hpc_mcp.config import Config, SlurmConfig, SshConfig
from hpc_mcp.errors import RemoteCommandError
from hpc_mcp.slurm.jobs import JobTracker, _RegistryConflict
from hpc_mcp.ssh.manager import RemoteResult

REGISTER = "tracked_jobs.json"


class LocalShell:
    """Transport double that runs the tracker's commands on this machine.

    Mirrors :class:`SshManager` in the one way that matters here: a non-zero
    exit is raised as :class:`RemoteCommandError` when ``check`` is set, and
    returned to the caller otherwise.
    """

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.stdins: list[str | None] = []

    @staticmethod
    def _checked(result: RemoteResult, cmd: str, check: bool) -> RemoteResult:
        if check and result.exit_code != 0:
            raise RemoteCommandError(
                f"Remote command failed (exit {result.exit_code}): {cmd[:200]}\n"
                f"{result.stderr_text.strip()[:800]}",
                exit_code=result.exit_code,
            )
        return result

    async def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        cmd = " ".join(str(a) for a in argv)
        proc = await asyncio.create_subprocess_exec(
            *[str(a) for a in argv],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return self._checked(RemoteResult(stdout=out, stderr=err, exit_code=proc.returncode or 0), cmd, check)

    async def run_raw(
        self,
        cmd,
        *,
        stdin_text=None,
        timeout=None,
        max_output=4 * 1024 * 1024,
        check=True,
    ):
        self.commands.append(cmd)
        self.stdins.append(stdin_text)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate((stdin_text or "").encode() or None)
        result = RemoteResult(stdout=out, stderr=err, exit_code=proc.returncode or 0)
        return self._checked(result, cmd, check)


@dataclass
class Harness:
    tracker: JobTracker
    ssh: LocalShell
    root: Path

    @property
    def metadata(self) -> Path:
        return self.root / ".hpc-mcp"

    @property
    def register_path(self) -> Path:
        return self.metadata / REGISTER

    @property
    def lock_path(self) -> Path:
        return self.metadata / f"{REGISTER}.lock"

    async def register(self, job_id: str = "1") -> None:
        await self.tracker.register(
            job_id=job_id,
            job_name="t",
            project_root=str(self.root),
            job_dir=f"{self.root}/.hpc-mcp/jobs/{job_id}",
        )

    def registered_ids(self) -> list[str]:
        return sorted(json.loads(self.register_path.read_text()))

    def residue(self) -> list[str]:
        return sorted(p.name for p in self.metadata.iterdir() if p.name not in {REGISTER, "jobs"})

    def age_lock(self, seconds: int) -> None:
        stale = time.time() - seconds
        os.utime(self.lock_path, (stale, stale))


def make_harness(root: Path) -> Harness:
    """Build one tracker instance; several may share a root (one per MCP server)."""
    cfg = Config(
        root=str(root),
        ssh=SshConfig(host="h", user="u"),
        slurm=SlurmConfig(allowed_partitions=["compute"]),
    )
    ssh = LocalShell()
    return Harness(tracker=JobTracker(cfg, ssh), ssh=ssh, root=root)


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    # ``realpath`` is what ensure_ready() compares against, so the sandbox root
    # must be the resolved path.
    return make_harness(Path(os.path.realpath(str(tmp_path))))


@pytest.mark.asyncio
class TestRegistryWrite:
    async def test_register_writes_atomically_without_residue(self, harness: Harness):
        await harness.register()
        assert harness.registered_ids() == ["1"]
        assert harness.residue() == []

    async def test_second_register_keeps_both_entries(self, harness: Harness):
        await harness.register("1")
        await harness.register("2")
        assert harness.registered_ids() == ["1", "2"]
        assert harness.residue() == []

    async def test_large_registry_payload_uses_stdin_not_ssh_command_line(
        self, harness: Harness
    ):
        """Windows OpenSSH cannot launch a remote command above ~32 KiB."""
        await harness.tracker.ensure_ready()
        data = {
            str(job_id): {
                "job_id": str(job_id),
                "job_name": "large-registry-entry",
                "project_root": str(harness.root),
                "job_dir": f"{harness.root}/.hpc-mcp/jobs/{job_id}",
                "created_at": "2026-09-10T00:00:00Z",
                "tool_session": "session-" + "x" * 32,
            }
            for job_id in range(256)
        }
        payload = json.dumps(data, indent=2, sort_keys=True)
        assert len(payload.encode()) > 32_767

        await harness.tracker._write_register(data, expected="absent")

        assert len(harness.ssh.commands[-1].encode()) < 32_767
        assert harness.ssh.stdins[-1] == payload
        assert harness.registered_ids() == sorted(data)
        assert harness.residue() == []

    async def test_stale_lock_is_reclaimed(self, harness: Harness):
        """A lock left by a killed/hung session must not block forever."""
        await harness.tracker.ensure_ready()
        harness.lock_path.mkdir()
        harness.age_lock(3600)
        await harness.register()
        assert not harness.lock_path.exists()
        assert harness.registered_ids() == ["1"]

    async def test_live_lock_is_not_stolen_and_failure_is_actionable(self, harness: Harness):
        """A lock held by a live writer is waited for, never removed."""
        # Keep the bounded retry short: the production 6x1s window is verified
        # by the retry parameters themselves, not by sleeping here.
        harness.tracker._LOCK_ATTEMPTS = 2
        harness.tracker._LOCK_RETRY_SLEEP_SECONDS = 0
        await harness.tracker.ensure_ready()
        harness.lock_path.mkdir()
        with pytest.raises(RemoteCommandError) as excinfo:
            await harness.register()
        message = excinfo.value.user_message
        assert str(harness.lock_path) in message
        assert "rmdir" in message
        # The remote diagnostic is relayed, not the command template: the
        # template is what made this failure look like a mystery lock file.
        assert "lock is busy" in message
        assert harness.lock_path.is_dir()
        assert not harness.register_path.exists()

    async def test_stale_but_non_empty_lock_is_never_reclaimed(self, harness: Harness):
        harness.tracker._LOCK_ATTEMPTS = 2
        harness.tracker._LOCK_RETRY_SLEEP_SECONDS = 0
        await harness.tracker.ensure_ready()
        harness.lock_path.mkdir()
        (harness.lock_path / "planted").write_text("x")
        harness.age_lock(3600)
        with pytest.raises(RemoteCommandError):
            await harness.register()
        assert (harness.lock_path / "planted").exists()

    async def test_leftover_temp_file_does_not_block_later_writes(self, harness: Harness):
        """A temp file from an interrupted write must not be fatal."""
        await harness.tracker.ensure_ready()
        # The pre-fix name was stable for the whole life of the process.
        (harness.metadata / f"{REGISTER}.tmp.{harness.tracker.session_id}").write_text("{}")
        await harness.register("1")
        await harness.register("2")
        assert harness.registered_ids() == ["1", "2"]

    async def test_symlinked_register_is_refused(self, harness: Harness):
        """A planted symlink must never turn register() into a file overwrite."""
        await harness.tracker.ensure_ready()
        victim = harness.root / "victim.txt"
        victim.write_text('{"keep": true}')
        harness.register_path.symlink_to(victim)
        with pytest.raises(RemoteCommandError):
            await harness.register()
        assert victim.read_text() == '{"keep": true}'
        assert harness.register_path.is_symlink()

    async def test_symlinked_lock_is_refused(self, harness: Harness):
        """A symlinked lock path is refused instead of being unlinked through."""
        harness.tracker._LOCK_RETRY_SLEEP_SECONDS = 0
        await harness.tracker.ensure_ready()
        harness.lock_path.symlink_to(harness.root / "nowhere")
        with pytest.raises(RemoteCommandError) as excinfo:
            await harness.register()
        assert "symlink" in excinfo.value.user_message
        assert harness.lock_path.is_symlink()

    async def test_unreadable_registry_is_never_overwritten(self, harness: Harness):
        """A registry path that cannot be hashed is refused, not replaced."""
        await harness.tracker.ensure_ready()
        harness.register_path.mkdir()  # a directory where the registry file goes
        with pytest.raises(RemoteCommandError) as excinfo:
            await harness.register()
        assert "could not be read" in excinfo.value.user_message
        assert harness.register_path.is_dir()

    async def test_stale_snapshot_is_refused_instead_of_clobbering(self, harness: Harness):
        """A payload merged from an outdated snapshot must never commit."""
        await harness.register("1")
        _data, stale_token = await harness.tracker._read_register_snapshot()
        await harness.register("2")  # another instance writes in the meantime
        with pytest.raises(_RegistryConflict):
            await harness.tracker._write_register({"9": {"job_id": "9"}}, expected=stale_token)
        assert harness.registered_ids() == ["1", "2"]
        assert harness.residue() == []

    async def test_concurrent_registrations_from_several_instances_all_survive(
        self, tmp_path: Path
    ):
        """Simultaneous submissions from different MCP instances must all survive.

        One instance per client process, all sharing one account: a plain
        read-merge-write lets the last writer win and silently drops the other
        instance's job, which can then never be managed again.
        """
        root = Path(os.path.realpath(str(tmp_path)))
        instances = [make_harness(root) for _ in range(4)]
        await asyncio.gather(*(inst.register(str(i)) for i, inst in enumerate(instances)))
        data = json.loads(instances[0].register_path.read_text())
        assert sorted(data) == ["0", "1", "2", "3"]
        for job_id, inst in enumerate(instances):
            assert data[str(job_id)]["tool_session"] == inst.tracker.session_id
        assert instances[0].residue() == []

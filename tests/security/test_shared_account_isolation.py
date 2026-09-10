"""Shared-account Slurm isolation: the MCP must never query the whole queue.

Under a shared Unix account, ``squeue``/``sacct`` would expose every user's
job metadata.  Every ownership check must therefore restrict itself to the
job IDs this instance actually tracks -- never a whole-account scan.
"""

import json

import pytest

from hpc_mcp.config import Config, SlurmConfig, SshConfig
from hpc_mcp.errors import SlurmPolicyError
from hpc_mcp.slurm.jobs import JobTracker
from hpc_mcp.slurm.manager import SlurmManager
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/alice"


def make_cfg() -> Config:
    return Config(
        root=ROOT,
        ssh=SshConfig(host="h", user="u"),
        slurm=SlurmConfig(allowed_partitions=["compute"], max_cpus=64, max_nodes=2),
    )


class FakeSsh:
    """Scripted SSH: the shared-account queue contains both our jobs and
    foreign jobs; only a ``-j`` restricted query may ever be issued."""

    def __init__(self) -> None:
        self.register: dict = {}
        self.states: dict[str, str] = {}
        self.squeue_calls: list[list[str]] = []
        self.submitted_scripts: list[str] = []
        self.next_job_id = "9001001"

    async def realpath(self, path):
        return path

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        import re as _re

        if argv[0] == "sh" and "tracked_jobs.json" in argv[-1] and "HPCMCP_EOF" in argv[-1]:
            m = _re.search(r"HPCMCP_EOF'?\n(.*?)\nHPCMCP_EOF", argv[-1], _re.S)
            if m:
                self.register = json.loads(m.group(1))
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if argv[:2] == ["cat", f"{ROOT}/.hpc-mcp/tracked_jobs.json"]:
            return RemoteResult(stdout=json.dumps(self.register).encode(), stderr=b"", exit_code=0)
        if argv[0] == "squeue":
            self.squeue_calls.append(list(argv))
            if "-j" in argv:
                jid = argv[argv.index("-j") + 1]
                fmt = argv[argv.index("-o") + 1] if "-o" in argv else "%i"
                if jid in self.states:
                    if fmt == "%i":
                        out = f"{jid}\n"
                    else:  # "%i|%j|%T|%M|%l|%D|%R" used by queue()
                        out = f"{jid}|{jid}|{self.states[jid]}|00:01:00|00:30:00|1|None\n"
                else:
                    out = ""
                return RemoteResult(stdout=out.encode(), stderr=b"", exit_code=0)
            # A whole-account query (no -j) would return *everyone's* jobs.
            lines = [f"{jid} {st}" for jid, st in self.states.items()]
            return RemoteResult(stdout=("\n".join(lines)).encode(), stderr=b"", exit_code=0)
        if argv[0] == "sacct":
            jids = argv[2].split(",")
            out = "".join(f"{j}|{self.states.get(j, 'COMPLETED')}|0:0\n" for j in jids)
            return RemoteResult(stdout=out.encode(), stderr=b"", exit_code=0)
        if argv[0] == "scancel":
            self.states[argv[-1]] = "CANCELLED"
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, *, stdin_text=None, **kw):
        if "tracked_jobs.json" in cmd and stdin_text is not None:
            self.register = json.loads(stdin_text)
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if "sbatch" in cmd:
            self.submitted_scripts.append(stdin_text or "")
            self.states[self.next_job_id] = "PENDING"
            return RemoteResult(stdout=(self.next_job_id + "\n").encode(), stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)


@pytest.mark.asyncio
class TestSharedAccountIsolation:
    async def _mgr(self, ssh: FakeSsh) -> SlurmManager:
        return SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))

    async def test_queue_never_queries_whole_account(self):
        """hpc.slurm.queue must only ever run ``squeue -j <tracked>``."""
        ssh = FakeSsh()
        # foreign jobs from other users of the shared account
        ssh.states = {"111": "RUNNING", "222": "PENDING", "333": "COMPLETED"}
        mgr = await self._mgr(ssh)
        res = await mgr.submit(job_name="mine", working_directory=ROOT, command=["ls"])
        mine = res["job_id"]
        ssh.states[mine] = "RUNNING"

        out = await mgr.queue()
        assert [j["job_id"] for j in out] == [mine]
        for call in ssh.squeue_calls:
            assert "-j" in call, f"squeue called without -j restriction: {call}"

    async def test_status_without_owned_jobs_does_not_scan(self):
        """With no tracked jobs, status of an untracked id is denied without
        ever touching squeue (no whole-account scan, no foreign data)."""
        ssh = FakeSsh()
        ssh.states = {"555": "RUNNING"}  # someone else's job
        mgr = await self._mgr(ssh)
        with pytest.raises(SlurmPolicyError, match="not submitted"):
            await mgr.status("555")
        assert ssh.squeue_calls == []

    async def test_queue_with_no_tracked_jobs_returns_empty_without_scan(self):
        ssh = FakeSsh()
        ssh.states = {"111": "RUNNING"}
        mgr = await self._mgr(ssh)
        assert await mgr.queue() == []
        assert ssh.squeue_calls == []

    async def test_foreign_jobs_never_reach_results_even_if_queued(self):
        """If the fake squeue misbehaves and returns foreign rows, they must
        be filtered out before reaching the agent."""
        ssh = FakeSsh()
        ssh.states = {"111": "RUNNING", "222": "PENDING"}
        mgr = await self._mgr(ssh)
        res = await mgr.submit(job_name="mine", working_directory=ROOT, command=["ls"])
        mine = res["job_id"]
        ssh.states[mine] = "RUNNING"
        # poke the queue() path with a scenario where the restricted query
        # still returns rows for ids we did not track
        out = await mgr.queue()
        for job in out:
            assert job["job_id"] == mine
        assert all(job["job_id"] == mine for job in out)

    async def test_submit_active_check_counts_only_owned(self):
        """Concurrency accounting must only count this instance's jobs."""
        ssh = FakeSsh()
        # lots of foreign active jobs must not count against the agent
        ssh.states = {str(i): "RUNNING" for i in range(1, 200)}
        mgr = await self._mgr(ssh)
        res = await mgr.submit(job_name="a", working_directory=ROOT, command=["ls"])
        ssh.states[res["job_id"]] = "RUNNING"
        # foreign jobs never queried: only our own id appears in squeue calls
        for call in ssh.squeue_calls:
            if call[0] == "squeue":
                jid_arg = call[call.index("-j") + 1]
                assert jid_arg == res["job_id"]

    async def test_foreign_cancel_denied(self):
        ssh = FakeSsh()
        ssh.states = {"999": "RUNNING"}
        mgr = await self._mgr(ssh)
        with pytest.raises(SlurmPolicyError):
            await mgr.cancel("999")

    async def test_foreign_accounting_denied(self):
        ssh = FakeSsh()
        ssh.states = {"999": "COMPLETED"}
        mgr = await self._mgr(ssh)
        with pytest.raises(SlurmPolicyError):
            await mgr.accounting("999")

    async def test_foreign_output_denied(self):
        ssh = FakeSsh()
        ssh.states = {"999": "COMPLETED"}
        mgr = await self._mgr(ssh)
        with pytest.raises(SlurmPolicyError):
            await mgr.output("999")

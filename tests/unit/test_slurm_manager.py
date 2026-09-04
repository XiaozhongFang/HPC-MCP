"""SlurmManager tests with fake SSH (scripted sbatch/sacct/squeue)."""

import pytest

from hpc_mcp.config import Config, SlurmConfig, SshConfig
from hpc_mcp.errors import RemoteCommandError, SlurmPolicyError
from hpc_mcp.slurm.jobs import JobTracker
from hpc_mcp.slurm.manager import SlurmManager
from hpc_mcp.ssh.manager import RemoteResult

ROOT = "/home/shared_account/fangxiaozhong"


def make_cfg() -> Config:
    return Config(
        root=ROOT,
        ssh=SshConfig(host="h", user="u"),
        slurm=SlurmConfig(allowed_partitions=["compute"], max_cpus=64, max_nodes=2),
    )


class FakeSsh:
    def __init__(self):
        self.register: dict = {}
        self.states: dict[str, str] = {}
        self.submitted_scripts: list[str] = []
        self.next_job_id = "424242"
        self.outputs: dict[str, str] = {}

    async def realpath(self, path):
        return path

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        import json, re as _re
        if argv[0] == "sh" and "tracked_jobs.json" in argv[-1] and "HPCMCP_EOF" in argv[-1]:
            m = _re.search(r"HPCMCP_EOF'?\n(.*?)\nHPCMCP_EOF", argv[-1], _re.S)
            if m:
                self.register = json.loads(m.group(1))
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if argv[:2] == ["cat", f"{ROOT}/.hpc-mcp/tracked_jobs.json"]:
            return RemoteResult(stdout=json.dumps(self.register).encode(), stderr=b"", exit_code=0)
        if argv[0] == "squeue":
            if "-j" in argv:
                jid = argv[argv.index("-j") + 1]
                out = f"{jid}\n" if jid in self.states else ""
                return RemoteResult(stdout=out.encode(), stderr=b"", exit_code=0)
            lines = [f"{jid} {st}" for jid, st in self.states.items()]
            return RemoteResult(stdout=("\n".join(lines)).encode(), stderr=b"", exit_code=0)
        if argv[0] == "sacct":
            jid = argv[2]
            st = self.states.get(jid, "COMPLETED")
            return RemoteResult(stdout=f"{jid}|{st}|0:0\n".encode(), stderr=b"", exit_code=0)
        if argv[0] == "scancel":
            self.states[argv[-1]] = "CANCELLED"
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if argv[0] == "tail":
            path = argv[-1]
            data = self.outputs.get(path, "")
            code = 0 if path in self.outputs else 1
            return RemoteResult(stdout=data.encode(), stderr=b"", exit_code=code)
        if argv[0] == "stat" and "%s" in argv[2]:
            return RemoteResult(stdout=b"10", stderr=b"", exit_code=0)
        if argv[0] == "stat":
            return RemoteResult(stdout=b"directory|10|755|u|g|1", stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, *, stdin_text=None, **kw):
        if "sbatch" in cmd:
            self.submitted_scripts.append(stdin_text or "")
            self.states[self.next_job_id] = "PENDING"
            return RemoteResult(stdout=(self.next_job_id + "\n").encode(), stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)


@pytest.mark.asyncio
class TestSubmit:
    async def test_submit_registers_and_returns(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        res = await mgr.submit(
            job_name="t", working_directory=ROOT + "/proj",
            command=["julia", "--project=.", "t.jl"], cpus_per_task=4,
        )
        assert res["job_id"] == "424242"
        assert "#SBATCH --cpus-per-task=4" in ssh.submitted_scripts[0]
        assert "julia --project=. t.jl" in ssh.submitted_scripts[0]
        assert f"#SBATCH --output={ROOT}/.hpc-mcp/jobs/%j.stdout.log" in ssh.submitted_scripts[0]
        assert ssh.register["424242"]["job_name"] == "t"

    async def test_submit_escape_cwd_denied(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        from hpc_mcp.errors import HpcMcpError
        with pytest.raises(HpcMcpError):  # PathSandboxError or SlurmPolicyError
            await mgr.submit(job_name="t", working_directory="/etc", command=["ls"])

    async def test_submit_bad_partition_denied(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(SlurmPolicyError):
            await mgr.submit(
                job_name="t", working_directory=ROOT, command=["ls"], partition="gpu"
            )

    async def test_submit_huge_cpus_denied(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(SlurmPolicyError, match="CPUs"):
            await mgr.submit(
                job_name="t", working_directory=ROOT, command=["ls"], cpus_per_task=1024
            )

    async def test_submit_bad_env_name_denied(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(SlurmPolicyError):
            await mgr.submit(
                job_name="t", working_directory=ROOT, command=["ls"],
                environment={"BAD;NAME": "x"},
            )


@pytest.mark.asyncio
class TestOwnership:
    def _mgr(self, ssh):
        cfg = make_cfg()
        return SlurmManager(cfg, ssh, JobTracker(cfg, ssh))

    async def test_status_untracked_denied(self):
        ssh = FakeSsh()
        with pytest.raises(SlurmPolicyError, match="not submitted"):
            await self._mgr(ssh).status("111")

    async def test_cross_session_entry_denied(self):
        ssh = FakeSsh()
        cfg = make_cfg()
        tracker = JobTracker(cfg, ssh)
        ssh.register["111"] = {
            "job_id": "111", "job_name": "old", "project_root": ROOT,
            "job_dir": f"{ROOT}/.hpc-mcp/jobs/111", "tool_session": "another-session",
        }
        with pytest.raises(SlurmPolicyError, match="not submitted"):
            await SlurmManager(cfg, ssh, tracker).status("111")

    async def test_cancel_untracked_denied(self):
        ssh = FakeSsh()
        with pytest.raises(SlurmPolicyError):
            await self._mgr(ssh).cancel("111")

    async def test_status_tracked(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        out = await mgr.status(res["job_id"])
        assert out["state"] == "PENDING"

    async def test_cancel_tracked(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        out = await mgr.cancel(res["job_id"])
        assert out["cancelled"] is True

    async def test_output_tracked(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{res['job_id']}/stdout.log"] = "hello output"
        out = await mgr.output(res["job_id"])
        assert out["content"] == "hello output"

    async def test_wait_completes(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        ssh.states[res["job_id"]] = "COMPLETED"
        out = await mgr.wait(res["job_id"], timeout_seconds=5, poll_interval=2)
        assert out["state"] == "COMPLETED"

"""SlurmManager tests with fake SSH (scripted sbatch/sacct/squeue)."""

import pytest

from hpc_mcp.config import Config, SlurmConfig, SshConfig
from hpc_mcp.errors import RemoteCommandError, SlurmPolicyError
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
    def __init__(self):
        self.register: dict = {}
        self.states: dict[str, str] = {}
        self.submitted_scripts: list[str] = []
        self.next_job_id = "424242"
        self.outputs: dict[str, str] = {}
        self.scripts: dict[str, str] = {}
        self.squeue_fails = False  # simulate a squeue probe that could not run
        # When set, the registry write fails with this result (lock contention,
        # stale lock, unwritable metadata dir, ...).
        self.register_write_error: RemoteResult | None = None
        self.scancel_fails = False  # simulate a rollback that could not run
        self.sacct_omits: set[str] = set()  # ids accounting has no record for

    async def realpath(self, path):
        return path

    async def run(self, argv, *, timeout=None, max_output=4 * 1024 * 1024, check=True):
        import json, re as _re
        if argv[0] == "sh" and "tracked_jobs.json" in argv[-1] and "HPCMCP_EOF" in argv[-1]:
            if self.register_write_error is not None:
                return self.register_write_error
            m = _re.search(r"HPCMCP_EOF'?\n(.*?)\nHPCMCP_EOF", argv[-1], _re.S)
            if m:
                self.register = json.loads(m.group(1))
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if argv[:2] == ["cat", f"{ROOT}/.hpc-mcp/tracked_jobs.json"]:
            return RemoteResult(stdout=json.dumps(self.register).encode(), stderr=b"", exit_code=0)
        if argv[0] == "squeue":
            if self.squeue_fails:
                return RemoteResult(stdout=b"", stderr=b"slurm_load_jobs error", exit_code=1)
            if "-j" in argv:
                jid = argv[argv.index("-j") + 1]
                out = f"{jid}\n" if jid in self.states else ""
                return RemoteResult(stdout=out.encode(), stderr=b"", exit_code=0)
            lines = [f"{jid} {st}" for jid, st in self.states.items()]
            return RemoteResult(stdout=("\n".join(lines)).encode(), stderr=b"", exit_code=0)
        if argv[0] == "sacct":
            jids = argv[2].split(",")
            out = "".join(
                f"{j}|{self.states.get(j, 'COMPLETED')}|0:0\n"
                for j in jids
                if j not in self.sacct_omits
            )
            return RemoteResult(stdout=out.encode(), stderr=b"", exit_code=0)
        if argv[0] == "scancel":
            if self.scancel_fails:
                return RemoteResult(stdout=b"", stderr=b"scancel: error", exit_code=1)
            self.states[argv[-1]] = "CANCELLED"
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if argv[0] == "cat" and "--" in argv:
            path = argv[-1]
            data = self.scripts.get(path, "")
            code = 0 if path in self.scripts else 1
            return RemoteResult(stdout=data.encode(), stderr=b"", exit_code=code)
        if argv[0] == "tail":
            path = argv[-1]
            data = self.outputs.get(path, "")
            cap = int(argv[argv.index("-c") + 1]) if "-c" in argv else None
            if cap is not None:
                data = data[-cap:]
            code = 0 if path in self.outputs else 1
            return RemoteResult(stdout=data.encode(), stderr=b"", exit_code=code)
        if argv[0] == "stat" and "%s" in argv[2]:
            return RemoteResult(stdout=b"10", stderr=b"", exit_code=0)
        if argv[0] == "stat":
            return RemoteResult(stdout=b"directory|10|755|u|g|1", stderr=b"", exit_code=0)
        return RemoteResult(stdout=b"", stderr=b"", exit_code=0)

    async def run_raw(self, cmd, *, stdin_text=None, **kw):
        import json
        if "tracked_jobs.json" in cmd and stdin_text is not None:
            if self.register_write_error is not None:
                return self.register_write_error
            self.register = json.loads(stdin_text)
            return RemoteResult(stdout=b"", stderr=b"", exit_code=0)
        if "sbatch" in cmd:
            self.submitted_scripts.append(stdin_text or "")
            self.states[self.next_job_id] = "PENDING"
            jid = self.next_job_id
            self.next_job_id = str(int(self.next_job_id) + 1)
            return RemoteResult(stdout=(jid + "\n").encode(), stderr=b"", exit_code=0)
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

    async def test_registry_failure_rolls_back_job_and_says_so(self):
        """A job that cannot be registered is cancelled -- and the error says so.

        The submission has already reached Slurm at this point, so the error
        must report the rollback instead of leaving the agent hunting for a job
        that is already gone.
        """
        ssh = FakeSsh()
        ssh.register_write_error = RemoteResult(
            stdout=b"",
            stderr=b"tracked-jobs lock is busy: /x/.hpc-mcp/tracked_jobs.json.lock",
            exit_code=1,
        )
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(RemoteCommandError) as excinfo:
            await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        assert ssh.states["424242"] == "CANCELLED"
        message = excinfo.value.user_message
        assert "424242" in message
        assert "cancelled" in message
        assert "tracked_jobs.json.lock" in message

    async def test_registry_failure_reports_failed_rollback(self):
        """If even the rollback fails, the queue state is reported honestly."""
        ssh = FakeSsh()
        ssh.register_write_error = RemoteResult(stdout=b"", stderr=b"boom", exit_code=1)
        ssh.scancel_fails = True
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(RemoteCommandError) as excinfo:
            await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        message = excinfo.value.user_message
        assert "may still be queued" in message
        assert "not manageable" in message
        assert "boom" in message  # the original failure is not masked by scancel

    async def test_submit_defaults_working_dir_to_root(self):
        """When working_directory is omitted the user root is used."""
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        res = await mgr.submit(
            job_name="t", working_directory=ROOT,
            command=["julia", "t.jl"],
        )
        assert res["working_directory"] == ROOT
        assert f"#SBATCH --chdir={ROOT}" in ssh.submitted_scripts[0]

    async def test_submit_reads_sbatch_defaults_from_script(self):
        """#SBATCH directives in a user .sh script become parameter defaults."""
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        script = f"{ROOT}/proj/run.sh"
        ssh.scripts[script] = (
            "#!/bin/bash\n"
            "#SBATCH --partition=compute\n"
            "#SBATCH --nodes=2\n"
            "#SBATCH --cpus-per-task=16\n"
            "#SBATCH --time=02:00:00\n"
            "#SBATCH --mem=32G\n"
            "#SBATCH --gres=gpu:2\n"
            "echo running\n"
        )
        res = await mgr.submit(
            job_name="t", working_directory=ROOT + "/proj",
            command=script,
        )
        rendered = ssh.submitted_scripts[0]
        assert "#SBATCH --partition=compute" in rendered
        assert "#SBATCH --nodes=2" in rendered
        assert "#SBATCH --cpus-per-task=16" in rendered
        assert "#SBATCH --time=02:00:00" in rendered
        assert "#SBATCH --mem=32768M" in rendered
        assert "#SBATCH --gres=gpu:2" in rendered
        assert "/proj/run.sh" in rendered

    async def test_submit_explicit_args_override_script(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        script = f"{ROOT}/proj/run.sh"
        ssh.scripts[script] = (
            "#!/bin/bash\n#SBATCH --cpus-per-task=2\n#SBATCH --time=00:10:00\n"
        )
        await mgr.submit(
            job_name="t", working_directory=ROOT + "/proj",
            command=script, cpus_per_task=8, time_limit="01:00:00",
        )
        rendered = ssh.submitted_scripts[0]
        assert "#SBATCH --cpus-per-task=8" in rendered
        assert "#SBATCH --time=01:00:00" in rendered

    async def test_submit_script_bad_partition_denied(self):
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        script = f"{ROOT}/proj/run.sh"
        ssh.scripts[script] = "#!/bin/bash\n#SBATCH --partition=gpu-long\n"
        with pytest.raises(SlurmPolicyError):
            await mgr.submit(job_name="t", working_directory=ROOT + "/proj", command=script)

    async def test_submit_returns_partition(self):
        """The effective partition is returned so the agent knows where it ran."""
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        res = await mgr.submit(
            job_name="t", working_directory=ROOT,
            command=["julia", "t.jl"],
        )
        assert res["partition"] == "compute"  # first allowed partition default

    async def test_submit_explicit_partition(self):
        """Agent may request a partition from the allow-list."""
        ssh = FakeSsh()
        cfg = make_cfg()
        cfg.slurm.allowed_partitions = ["compute", "gpu"]
        mgr = SlurmManager(cfg, ssh, JobTracker(cfg, ssh))
        res = await mgr.submit(
            job_name="t", working_directory=ROOT,
            command=["julia", "t.jl"], partition="gpu",
        )
        assert res["partition"] == "gpu"
        assert "#SBATCH --partition=gpu" in ssh.submitted_scripts[0]

    async def test_submit_disallowed_partition_denied(self):
        """A partition outside the allow-list is rejected without leaking names."""
        ssh = FakeSsh()
        mgr = SlurmManager(make_cfg(), ssh, JobTracker(make_cfg(), ssh))
        with pytest.raises(SlurmPolicyError) as excinfo:
            await mgr.submit(
                job_name="t", working_directory=ROOT,
                command=["julia", "t.jl"], partition="gpu-long",
            )
        msg = str(excinfo.value)
        assert "gpu-long" not in msg  # the requested name is redacted

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
class TestJobRun:
    def _mgr(self, ssh):
        cfg = make_cfg()
        return SlurmManager(cfg, ssh, JobTracker(cfg, ssh))

    async def test_run_julia_builds_argv(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.run(runtime="julia", script="scripts/run.jl", args=["--grid", "128"], cpus_per_task=8)
        assert res["job_id"] == "424242"
        rendered = ssh.submitted_scripts[0]
        assert "julia --project=. scripts/run.jl --grid 128" in rendered
        assert "#SBATCH --cpus-per-task=8" in rendered
        # still tracked like a normal submit
        assert ssh.register["424242"]["job_name"] == "julia"

    async def test_run_moose_builds_mpirun(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        await mgr.run(runtime="moose", script="/path/moose-opt", args=["input.i"], ntasks=16)
        rendered = ssh.submitted_scripts[0]
        assert "mpirun -np 16 -- /path/moose-opt input.i" in rendered

    async def test_run_unknown_runtime_denied(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        with pytest.raises(SlurmPolicyError, match="Unknown runtime"):
            await mgr.run(runtime="matlab", script="x.m")

    async def test_run_still_enforces_slurm_policy(self):
        """hpc.job.run must not bypass the resource policy."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        with pytest.raises(SlurmPolicyError, match="CPUs"):
            await mgr.run(runtime="julia", script="a.jl", cpus_per_task=1024)

    async def test_run_bad_partition_denied(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        with pytest.raises(SlurmPolicyError):
            await mgr.run(runtime="julia", script="a.jl", partition="gpu-long")

    async def test_run_bad_args_denied(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        with pytest.raises(SlurmPolicyError):
            await mgr.run(runtime="julia", script="a.jl", args=["a\nb"])


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


@pytest.mark.asyncio
class TestConcurrencyCounting:
    """count_active must not count finished jobs that left the squeue."""

    def _mgr(self, ssh):
        cfg = make_cfg()
        cfg.slurm.max_concurrent_jobs = 2
        return SlurmManager(cfg, ssh, JobTracker(cfg, ssh))

    async def test_finished_job_not_counted_active(self):
        """squeue no longer returns the job, but sacct says COMPLETED."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        await mgr.submit(job_name="old", working_directory=ROOT, command=["ls"])
        # job finished: it disappears from squeue, sacct keeps the terminal state
        ssh.states.clear()
        states, queue_ok = await mgr._queue_states()
        active = await mgr._tracker.count_active(states, queue_ok=queue_ok)
        assert queue_ok is True
        assert active == 0

    async def test_ghost_entry_released_when_both_probes_agree(self):
        """Neither squeue nor sacct knows the job: it must not hold a slot."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="ghost", working_directory=ROOT, command=["ls"])
        ssh.states.clear()  # left the queue
        ssh.sacct_omits = {res["job_id"]}  # accounting record already purged
        states, queue_ok = await mgr._queue_states()
        active = await mgr._tracker.count_active(states, queue_ok=queue_ok)
        assert queue_ok is True
        assert active == 0
        # the ownership record survives, only the quota slot is freed
        assert res["job_id"] in {e["job_id"] for e in await mgr._tracker.list_mine()}

    async def test_failed_squeue_probe_stays_fail_closed(self):
        """An unanswered squeue proves nothing: the slot stays taken."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="unknown", working_directory=ROOT, command=["ls"])
        ssh.states.clear()
        ssh.sacct_omits = {res["job_id"]}
        ssh.squeue_fails = True
        states, queue_ok = await mgr._queue_states()
        active = await mgr._tracker.count_active(states, queue_ok=queue_ok)
        assert queue_ok is False
        assert active == 1

    async def test_unanswered_squeue_denies_submit_instead_of_guessing(self):
        """With the queue unreadable, the limit is enforced conservatively."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        r1 = await mgr.submit(job_name="a", working_directory=ROOT, command=["ls"])
        r2 = await mgr.submit(job_name="b", working_directory=ROOT, command=["ls"])
        ssh.states.clear()
        ssh.sacct_omits = {r1["job_id"], r2["job_id"]}
        ssh.squeue_fails = True
        with pytest.raises(SlurmPolicyError, match="Too many active jobs"):
            await mgr.submit(job_name="c", working_directory=ROOT, command=["ls"])

    async def test_full_quota_denies_new_submit_with_guidance(self):
        """20/20 with no queue must not happen: finished jobs free the quota."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        # two RUNNING jobs fill the quota of 2
        r1 = await mgr.submit(job_name="a", working_directory=ROOT, command=["ls"])
        r2 = await mgr.submit(job_name="b", working_directory=ROOT, command=["ls"])
        ssh.states[r1["job_id"]] = "RUNNING"
        ssh.states[r2["job_id"]] = "RUNNING"
        with pytest.raises(SlurmPolicyError, match="Too many active jobs"):
            await mgr.submit(job_name="c", working_directory=ROOT, command=["ls"])
        # one finishes and leaves the queue; the tracking entry still exists,
        # but sacct (default COMPLETED for unknown ids) frees the quota
        ssh.states.pop(r1["job_id"])
        res = await mgr.submit(job_name="d", working_directory=ROOT, command=["ls"])
        assert res["job_id"]


@pytest.mark.asyncio
class TestDiagnose:
    def _mgr(self, ssh):
        cfg = make_cfg()
        return SlurmManager(cfg, ssh, JobTracker(cfg, ssh))

    async def test_diagnose_foreign_job_denied(self):
        ssh = FakeSsh()
        with pytest.raises(SlurmPolicyError, match="not submitted"):
            await self._mgr(ssh).diagnose("999")

    async def test_diagnose_aggregates(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        jid = res["job_id"]
        ssh.states[jid] = "FAILED"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stdout.log"] = "some output\n"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stderr.log"] = "ERROR: cannot allocate memory\nkilled process 123\n"
        out = await mgr.diagnose(jid, stdout_lines=5, stderr_lines=5)
        assert out["job"]["state"] == "FAILED"
        assert out["accounting"] is not None
        assert "some output" in out["stdout_tail"]["content"]
        assert "cannot allocate memory" in out["stderr_tail"]["content"]
        assert out["diagnostics"]["oom"] is True
        assert out["diagnostics"]["segfault"] is False

    async def test_diagnose_lines_capped(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        jid = res["job_id"]
        ssh.states[jid] = "FAILED"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stdout.log"] = "x" * 5 * 1024 * 1024
        out = await mgr.diagnose(jid, stdout_lines=10 ** 9, stderr_lines=10 ** 9)
        # both tails are bounded regardless of the absurd requested line count
        assert out["stdout_tail"]["bytes"] <= 200 * 4096 + 4096
        assert out["stderr_tail"]["bytes"] <= 200 * 4096 + 4096

    async def test_diagnose_scan_negative_ok(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        jid = res["job_id"]
        ssh.states[jid] = "COMPLETED"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stdout.log"] = "all good\n"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stderr.log"] = ""
        out = await mgr.diagnose(jid)
        assert all(out["diagnostics"][k] is False for k in ("oom", "segfault", "timeout", "gpu_error", "mpi_error", "missing_file"))

    async def test_wait_and_diagnose_foreign_denied(self):
        ssh = FakeSsh()
        with pytest.raises(SlurmPolicyError, match="not submitted"):
            await self._mgr(ssh).wait_and_diagnose("999", timeout_seconds=5, poll_interval=2)

    async def test_wait_and_diagnose_aggregates(self):
        """wait_and_diagnose = wait until terminal + full diagnosis in one call."""
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        jid = res["job_id"]
        ssh.states[jid] = "FAILED"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stdout.log"] = "partial\n"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stderr.log"] = "Segmentation fault\n"
        out = await mgr.wait_and_diagnose(jid, timeout_seconds=5, poll_interval=2)
        assert out["waited_seconds"] == 0.0  # already terminal
        assert out["job"]["state"] == "FAILED"
        assert out["diagnostics"]["segfault"] is True
        assert "partial" in out["stdout_tail"]["content"]

    async def test_wait_and_diagnose_waits_then_diagnoses(self):
        ssh = FakeSsh()
        mgr = self._mgr(ssh)
        res = await mgr.submit(job_name="t", working_directory=ROOT, command=["ls"])
        jid = res["job_id"]
        ssh.states[jid] = "COMPLETED"  # real FakeSsh state once we stop spying

        calls = {"n": 0}

        async def status_spy(job_id, **_):
            # first status call sees PENDING (wait loop continues), then the
            # real status() is used so diagnose's internal queries work
            calls["n"] += 1
            if calls["n"] == 1:
                return {"job_id": job_id, "state": "PENDING", "source": "squeue"}
            return await original_status(job_id)

        original_status = mgr.status
        mgr.status = status_spy  # type: ignore[method-assign]
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stdout.log"] = "done\n"
        ssh.outputs[f"{ROOT}/.hpc-mcp/jobs/{jid}/stderr.log"] = ""
        out = await mgr.wait_and_diagnose(jid, timeout_seconds=5, poll_interval=1)
        assert calls["n"] >= 2  # wait loop polled at least once
        assert out["job"]["state"] == "COMPLETED"
        assert "done" in out["stdout_tail"]["content"]

"""Security boundaries of hpc.cluster.topo.

The tool is the only one that (a) submits a job and (b) parses output from a
compute node, so its invariants deserve explicit tests:

* the probe script is fixed server-side content -- no tool argument (partition,
  refresh) is ever interpolated into it;
* the probe script never queries the queue (squeue/sacct/scontrol), so no other
  user's job metadata can reach the MCP process under a shared account;
* only partitions on the server allow-list are queried or returned, and a
  partition name that is not a plain identifier is rejected before any remote
  command runs (fail-closed);
* everything the tool writes stays inside the configured user root.
"""

import pytest

from hpc_mcp.cluster.topology import (
    PROBE_SCRIPT,
    TopologyService,
)
from hpc_mcp.config import Config, SlurmConfig, TopologyConfig
from hpc_mcp.errors import SlurmPolicyError

ROOT = "/thfs1/home/nudt_liujie05/alice"
ALLOWED = ["thcp1"]

PROBE_STDOUT = """::hpc-mcp:meta
SLURMD_NODENAME=cn0007
SLURM_CPUS_ON_NODE=64
::hpc-mcp:lscpu
Architecture:          x86_64
CPU(s):                64
Thread(s) per core:    2
Core(s) per socket:    16
Socket(s):             2
Model name:            Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz
NUMA node(s):          2
NUMA node0 CPU(s):     0-15,32-47
NUMA node1 CPU(s):     16-31,48-63
Flags:                 sse2 avx avx2 fma avx512f
::hpc-mcp:cpuinfo
model name      : Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz
flags           : sse2 avx avx2 fma avx512f
::hpc-mcp:numactl
unavailable numactl
::hpc-mcp:meminfo
MemTotal:       256000000 kB
::hpc-mcp:os
Linux
x86_64
5.14.0-1
::hpc-mcp:end
"""

# The account's queue also holds a partition the agent is NOT allowed to use.
SINFO_PARTITIONS = """thcp1::up::5000::64::127000::(null)::(null)::infinite::idle
secret_gpu*::up::8::128::1024000::(null)::gpu:a100:8::1-00:00:00::idle
"""

SINFO_NODES = """64::127000::(null)::(null)::idle
"""


class FakeFiles:
    def __init__(self):
        self.written: dict[str, str] = {}
        self.dirs: list[str] = []

    async def mkdir(self, path, *, parents=False):
        self.dirs.append(path)
        return {"path": path, "created": True}

    async def write_file(self, path, content, **kwargs):
        text = content if isinstance(content, str) else content.decode()
        self.written[path] = text
        return {"path": path, "bytes": len(text)}

    async def read_file(self, path, *, max_bytes=None, offset=0):
        from hpc_mcp.errors import RemoteCommandError

        if path not in self.written:
            raise RemoteCommandError("No such file", exit_code=1)
        data = self.written[path]
        return {"path": path, "content": data, "size": len(data), "truncated": False, "end_of_file": True}


class FakeSafeExec:
    def __init__(self):
        self.commands: list[str] = []

    async def run(self, command, cwd=None, *, timeout=None):
        self.commands.append(command)
        if command.startswith("sinfo -h -N"):
            return {"command": command, "exit_code": 0, "stdout": SINFO_NODES, "stderr": ""}
        return {"command": command, "exit_code": 0, "stdout": SINFO_PARTITIONS, "stderr": ""}


class FakeSlurm:
    def __init__(self):
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []

    async def submit(self, **kwargs):
        job_id = str(2000 + len(self.submitted))
        self.submitted.append({"job_id": job_id, **kwargs})
        return {"job_id": job_id, "job_dir": f"{ROOT}/.hpc-mcp/jobs/{job_id}"}

    async def wait(self, job_id, *, timeout_seconds=None, poll_interval=10):
        return {"job_id": job_id, "state": "COMPLETED"}

    async def status(self, job_id):
        return {"job_id": job_id, "state": "COMPLETED"}

    async def output(self, job_id, *, stream="stdout", tail_bytes=None):
        return {"content": PROBE_STDOUT if stream == "stdout" else "", "available": True}

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        return {"job_id": job_id, "cancelled": True}


def make_service(**topology_kwargs):
    cfg = Config(
        root=ROOT,
        local_roots=["/tmp"],
        slurm=SlurmConfig(allowed_partitions=list(ALLOWED), max_cpus=512, max_nodes=8),
        topology=TopologyConfig(**topology_kwargs),
    )
    files = FakeFiles()
    safe_exec = FakeSafeExec()
    slurm = FakeSlurm()
    return TopologyService(cfg, files, safe_exec, slurm), files, safe_exec, slurm


@pytest.mark.asyncio
async def test_probe_script_is_fixed_content():
    svc, files, _safe_exec, _slurm = make_service()
    await svc.probe(partition="thcp1")
    script = files.written[f"{ROOT}/.hpc-mcp/topo/collect_topo.sh"]
    assert script == PROBE_SCRIPT
    # no tool argument may appear in the script body
    assert "thcp1" not in script
    assert "refresh" not in script


@pytest.mark.asyncio
async def test_probe_script_never_queries_the_shared_queue():
    svc, files, _safe_exec, _slurm = make_service()
    await svc.probe()
    script = files.written[f"{ROOT}/.hpc-mcp/topo/collect_topo.sh"]
    for forbidden in ("squeue", "sacct", "scontrol", "sbatch", "scancel"):
        assert forbidden not in script


@pytest.mark.asyncio
async def test_probe_only_queries_allowlisted_partitions():
    svc, _files, safe_exec, slurm = make_service()
    result = await svc.probe()
    # sinfo was scoped to the allow-list, never to the whole cluster
    node_query = next(cmd for cmd in safe_exec.commands if cmd.startswith("sinfo -h -N"))
    assert "-p thcp1" in node_query
    assert "secret_gpu" not in node_query
    # and the unauthorized partition is filtered out of the response
    assert result["slurm"]["partition"]["partition"] == "thcp1"
    assert "secret_gpu" not in str(result)
    assert slurm.submitted[0]["partition"] == "thcp1"


@pytest.mark.asyncio
async def test_probe_rejects_non_identifier_partition_names():
    svc, _files, safe_exec, slurm = make_service()
    for bad in ('thcp1"; rm -rf /', "thcp1 gpu", "thcp1;id", "../thcp1", "thcp1|cat", "*"):
        with pytest.raises(SlurmPolicyError):
            await svc.probe(partition=bad)
    assert slurm.submitted == []
    assert safe_exec.commands == []


@pytest.mark.asyncio
async def test_probe_rejects_partition_not_in_allow_list():
    svc, _files, safe_exec, slurm = make_service()
    with pytest.raises(SlurmPolicyError):
        await svc.probe(partition="secret_gpu")
    assert slurm.submitted == []
    assert safe_exec.commands == []


@pytest.mark.asyncio
async def test_probe_fails_closed_when_no_partition_is_allowed():
    cfg = Config(root=ROOT, local_roots=["/tmp"], slurm=SlurmConfig(allowed_partitions=[]))
    svc = TopologyService(cfg, FakeFiles(), FakeSafeExec(), FakeSlurm())
    with pytest.raises(SlurmPolicyError):
        await svc.probe()


@pytest.mark.asyncio
async def test_every_artifact_stays_inside_the_user_root():
    svc, files, _safe_exec, slurm = make_service()
    await svc.probe()
    for path in list(files.written) + files.dirs:
        assert path.startswith(f"{ROOT}/.hpc-mcp/topo")
    assert slurm.submitted[0]["working_directory"] == f"{ROOT}/.hpc-mcp/topo"
    assert slurm.submitted[0]["command"] == ["bash", f"{ROOT}/.hpc-mcp/topo/collect_topo.sh"]


@pytest.mark.asyncio
async def test_disabled_topology_tool_is_not_registered():
    from hpc_mcp.config import Config as Cfg
    from hpc_mcp.filesystem.service import FileService
    from hpc_mcp.filesystem.transfer import TransferService
    from hpc_mcp.shell.safe_exec import SafeExec
    from hpc_mcp.slurm.jobs import JobTracker
    from hpc_mcp.slurm.manager import SlurmManager
    from hpc_mcp.ssh.manager import SshManager
    from hpc_mcp.ssh.sftp import SftpClient
    from hpc_mcp.tools.registry import build_tools

    cfg = Cfg(
        root=ROOT,
        local_roots=["/tmp"],
        slurm=SlurmConfig(allowed_partitions=list(ALLOWED)),
        topology=TopologyConfig(enabled=False),
    )
    ssh = SshManager(cfg)
    files = FileService(cfg, ssh)
    transfer = TransferService(cfg, SftpClient(cfg), files)
    slurm = SlurmManager(cfg, ssh, JobTracker(cfg, ssh))
    tools = build_tools(cfg, ssh, files, transfer, SafeExec(cfg, ssh), slurm)
    assert "hpc.cluster.topo" not in {tool.name for tool in tools}

    cfg_on = Cfg(
        root=ROOT,
        local_roots=["/tmp"],
        slurm=SlurmConfig(allowed_partitions=list(ALLOWED)),
        topology=TopologyConfig(enabled=True),
    )
    tools_on = build_tools(
        cfg_on,
        ssh,
        files,
        transfer,
        SafeExec(cfg_on, ssh),
        SlurmManager(cfg_on, ssh, JobTracker(cfg_on, ssh)),
    )
    assert "hpc.cluster.topo" in {tool.name for tool in tools_on}

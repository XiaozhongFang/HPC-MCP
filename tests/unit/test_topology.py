"""hpc.cluster.topo tests: parsers, recommendation heuristics, cache/pending.

All remote interaction is faked (files, safe-exec and Slurm), so these tests
never touch a cluster: they pin the parsing of real lscpu/numactl/sinfo
output and the "one probe job, then cache" contract.
"""

import json

import pytest

from hpc_mcp.cluster.topology import (
    PROBE_SCRIPT,
    SECTION_PREFIX,
    TopologyService,
    count_cpu_list,
    parse_cpuinfo,
    parse_lscpu,
    parse_meminfo,
    parse_numactl,
    parse_sinfo_nodes,
    parse_sinfo_partitions,
    parse_size_to_kib,
    parse_sysfs_cache,
    recommend_parallel,
    split_sections,
    summarize_simd,
)
from hpc_mcp.config import Config, SlurmConfig, TopologyConfig
from hpc_mcp.errors import RemoteCommandError, SlurmPolicyError

ROOT = "/home/shared_account/alice"

LSCPU_X86 = """Architecture:          x86_64
CPU op-mode(s):        32-bit, 64-bit
Byte Order:            Little Endian
CPU(s):                64
On-line CPU(s) list:   0-63
Thread(s) per core:    2
Core(s) per socket:    16
Socket(s):             2
NUMA node(s):          2
Vendor ID:             GenuineIntel
CPU family:            6
Model:                 85
Model name:            Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz
Stepping:              7
CPU MHz:               2494.140
CPU max MHz:           3900.0000
CPU min MHz:           1000.0000
BogoMIPS:              4988.37
Virtualization:        VT-x
L1d cache:             32K
L1i cache:             32K
L2 cache:              1024K
L3 cache:              28160K
NUMA node0 CPU(s):     0-15,32-47
NUMA node1 CPU(s):     16-31,48-63
Flags:                 fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc avx avx2 fma avx512f avx512dq avx512cd avx512bw avx512vl
"""

# util-linux >= 2.37 collapses the cache block into "Caches (sum of all)".
LSCPU_X86_MODERN = """Architecture:            x86_64
CPU(s):                  128
On-line CPU(s) list:     0-127
Thread(s) per core:      2
Core(s) per socket:      32
Socket(s):               2
NUMA node(s):            2
Model name:              Intel(R) Xeon(R) Platinum 8358 CPU @ 2.60GHz
Caches (sum of all):
  L1d:                   3 MiB (64 instances)
  L1i:                   3 MiB (64 instances)
  L2:                    80 MiB (64 instances)
  L3:                    96 MiB (2 instances)
NUMA node0 CPU(s):       0-31,64-95
NUMA node1 CPU(s):       32-63,96-127
Flags:                   fpu vme de pse sse2 avx avx2 fma avx512f avx512cd
"""

LSCPU_ARM = """Architecture:            aarch64
Byte Order:              Little Endian
CPU(s):                  128
On-line CPU(s) list:     0-127
Thread(s) per core:      1
Core(s) per socket:      64
Socket(s):               2
NUMA node(s):            1
Model name:              Kunpeng-920
NUMA node0 CPU(s):       0-127
Features:                fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat
"""

CPUINFO_X86 = """processor       : 0
vendor_id       : GenuineIntel
cpu family      : 6
model           : 85
model name      : Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz
stepping        : 7
cpu MHz         : 2494.140
cache size      : 28160 KB
physical id     : 0
siblings        : 32
core id         : 0
cpu cores       : 16
flags           : fpu vme de pse sse2 avx avx2 fma avx512f avx512bw

processor       : 1
vendor_id       : GenuineIntel
cpu family      : 6
model           : 85
model name      : Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz
siblings        : 32
core id         : 1
cpu cores       : 16
flags           : fpu vme de pse sse2 avx avx2 fma avx512f avx512bw
"""

NUMACTL = """available: 2 nodes (0-1)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47
node 0 size: 128000 MB
node 0 free: 120000 MB
node 1 cpus: 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63
node 1 size: 128000 MB
node 1 free: 118000 MB
node distances:
node     0    1
  0:   10   21
  1:   21   10
"""

MEMINFO = """MemTotal:       256000000 kB
MemFree:        200000000 kB
MemAvailable:   240000000 kB
SwapTotal:              0 kB
Hugepagesize:       2048 kB
HugePages_Total:       0
"""

# /sys/devices/system/cpu/cpu0/cache dump (level type size shared_cpu_list).
SYSCACHE_X86 = """1 Data 32K 0
1 Instruction 32K 0
2 Unified 1024K 0
3 Unified 28160K 0-15
"""

# Phytium FT2000+: small per-core caches, no L3 visible to cpu0.
SYSCACHE_ARM = """1 Data 64K 0
1 Instruction 64K 0
2 Unified 512K 0-1
"""

SINFO_PARTITIONS = """thcp1::up::5000::64::127000::(null)::(null)::infinite::idle
gpu*::up::32::128::1024000::avx512::gpu:a100:8::1-00:00:00::mixed
"""

SINFO_NODES = """64::127000::(null)::(null)::idle
64::127000::(null)::(null)::idle
64::256000::(null)::gpu:a100:4::mixed
"""


def probe_stdout(*, lscpu=LSCPU_X86, numactl=NUMACTL) -> str:
    return "\n".join(
        [
            f"{SECTION_PREFIX}meta",
            "SLURMD_NODENAME=cn0042",
            "SLURM_JOB_NODELIST=cn0042",
            "SLURM_CPUS_ON_NODE=64",
            "SLURM_MEM_PER_NODE=127000",
            f"{SECTION_PREFIX}lscpu",
            lscpu,
            f"{SECTION_PREFIX}cpuinfo",
            CPUINFO_X86,
            f"{SECTION_PREFIX}numactl",
            numactl,
            f"{SECTION_PREFIX}meminfo",
            MEMINFO,
            f"{SECTION_PREFIX}syscache",
            SYSCACHE_X86,
            f"{SECTION_PREFIX}os",
            "Linux",
            "x86_64",
            "5.14.0-1",
            f"{SECTION_PREFIX}end",
        ]
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeFiles:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}
        self.dirs: list[str] = []

    async def mkdir(self, path, *, parents=False):
        self.dirs.append(path)
        return {"path": path, "created": True}

    async def write_file(self, path, content, **kwargs):
        text = content if isinstance(content, str) else content.decode()
        self.written[path] = text
        return {"path": path, "bytes": len(text), "created": path not in self.written}

    async def read_file(self, path, *, max_bytes=None, offset=0):
        if path not in self.written:
            raise RemoteCommandError("No such file", exit_code=1)
        data = self.written[path]
        return {
            "path": path,
            "size": len(data),
            "offset": 0,
            "bytes": len(data),
            "truncated": False,
            "end_of_file": True,
            "content": data,
        }


class FakeSafeExec:
    def __init__(self, *, partitions=SINFO_PARTITIONS, nodes=SINFO_NODES, fail=False) -> None:
        self.partitions = partitions
        self.nodes = nodes
        self.fail = fail
        self.commands: list[str] = []

    async def run(self, command, cwd=None, *, timeout=None):
        self.commands.append(command)
        if self.fail:
            return {"command": command, "exit_code": 1, "stdout": "", "stderr": "sinfo: error"}
        if command.startswith("sinfo -h -N"):
            return {"command": command, "exit_code": 0, "stdout": self.nodes, "stderr": ""}
        return {"command": command, "exit_code": 0, "stdout": self.partitions, "stderr": ""}


class FakeSlurm:
    def __init__(self, *, behavior="complete", stdout=None) -> None:
        self.behavior = behavior
        self.stdout = stdout if stdout is not None else probe_stdout()
        self.submitted: list[dict] = []
        self.states: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.wait_calls = 0

    async def submit(self, **kwargs):
        job_id = str(1000 + len(self.submitted))
        self.submitted.append({"job_id": job_id, **kwargs})
        self.states[job_id] = "COMPLETED" if self.behavior == "complete" else "PENDING"
        return {
            "job_id": job_id,
            "job_name": kwargs.get("job_name"),
            "partition": kwargs.get("partition"),
            "working_directory": kwargs.get("working_directory"),
            "job_dir": f"{ROOT}/.hpc-mcp/jobs/{job_id}",
        }

    async def wait(self, job_id, *, timeout_seconds=None, poll_interval=10):
        self.wait_calls += 1
        if self.behavior == "complete":
            return {"job_id": job_id, "state": "COMPLETED"}
        raise RemoteCommandError(f"Job {job_id} did not reach a terminal state within {timeout_seconds}s")

    async def status(self, job_id):
        return {"job_id": job_id, "state": self.states.get(job_id, "UNKNOWN")}

    async def output(self, job_id, *, stream="stdout", tail_bytes=None):
        return {
            "job_id": job_id,
            "stream": stream,
            "path": f"{ROOT}/.hpc-mcp/jobs/{job_id}/{stream}.log",
            "content": self.stdout if stream == "stdout" else "",
            "available": True,
        }

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.states[job_id] = "CANCELLED"
        return {"job_id": job_id, "cancelled": True}


def make_config(**topology_kwargs) -> Config:
    return Config(
        root=ROOT,
        local_roots=["/tmp"],
        slurm=SlurmConfig(allowed_partitions=["thcp1"], max_cpus=512, max_nodes=8),
        topology=TopologyConfig(**topology_kwargs),
    )


def make_service(**topology_kwargs):
    cfg = make_config(**topology_kwargs)
    files = FakeFiles()
    safe_exec = FakeSafeExec()
    slurm = FakeSlurm()
    svc = TopologyService(cfg, files, safe_exec, slurm)
    return svc, files, safe_exec, slurm


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_size_parsing():
    assert parse_size_to_kib("32K") == 32
    assert parse_size_to_kib("28160K") == 28160
    assert parse_size_to_kib("1.5 MiB (32 instances)") == 1536
    assert parse_size_to_kib("96 MiB (2 instances)") == 96 * 1024
    assert parse_size_to_kib("") is None


def test_count_cpu_list():
    assert count_cpu_list("0-15,32-47") == 32
    assert count_cpu_list("0") == 1
    assert count_cpu_list("") is None


def test_parse_lscpu_x86():
    cpu = parse_lscpu(LSCPU_X86)
    assert cpu["model_name"] == "Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz"
    assert cpu["vendor"] == "GenuineIntel"
    assert cpu["architecture"] == "x86_64"
    assert cpu["sockets"] == 2
    assert cpu["cores_per_socket"] == 16
    assert cpu["threads_per_core"] == 2
    assert cpu["physical_cores"] == 32
    assert cpu["logical_cpus"] == 64
    assert cpu["numa_nodes"] == 2
    assert cpu["numa_cpu_lists"] == {"0": "0-15,32-47", "1": "16-31,48-63"}
    assert cpu["cache_kib"]["l3"] == 28160
    assert "avx512f" in cpu["flags"]


def test_parse_lscpu_modern_cache_block():
    cpu = parse_lscpu(LSCPU_X86_MODERN)
    assert cpu["model_name"] == "Intel(R) Xeon(R) Platinum 8358 CPU @ 2.60GHz"
    assert cpu["cache_kib"]["l3"] == 96 * 1024
    assert cpu["cache_kib"]["l1d"] == 3 * 1024
    assert cpu["physical_cores"] == 64
    assert cpu["logical_cpus"] == 128


def test_parse_lscpu_arm_reports_sve():
    cpu = parse_lscpu(LSCPU_ARM)
    assert cpu["architecture"] == "aarch64"
    assert cpu["sockets"] == 2
    assert summarize_simd(cpu["flags"])["level"] == "arm-sve"


def test_parse_lscpu_survives_unavailable_marker():
    assert parse_lscpu("unavailable lscpu") == {}
    assert parse_lscpu("") == {}


def test_parse_cpuinfo_uses_first_block_only():
    info = parse_cpuinfo(CPUINFO_X86)
    assert info["model_name"] == "Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz"
    assert info["siblings"] == 32
    assert info["cpu_cores"] == 16
    assert "avx512f" in info["flags"]


def test_parse_numactl():
    numa = parse_numactl(NUMACTL)
    assert numa["count"] == 2
    assert numa["nodes"][0]["cpus_count"] == 32
    assert numa["nodes"][1]["size_mb"] == 128000
    assert numa["distances"] == [[10, 21], [21, 10]]


def test_parse_numactl_absent():
    assert parse_numactl(f"{SECTION_PREFIX}unavailable numactl") == {}


def test_parse_meminfo_to_mib():
    mem = parse_meminfo(MEMINFO)
    assert mem["memtotal"] == 250000
    assert mem["memavailable"] == 240000 * 1000 // 1024


def test_parse_sysfs_cache():
    assert parse_sysfs_cache(SYSCACHE_X86) == {"l1d": 32, "l1i": 32, "l2": 1024, "l3": 28160}
    assert parse_sysfs_cache(SYSCACHE_ARM) == {"l1d": 64, "l1i": 64, "l2": 512}
    assert parse_sysfs_cache("") == {}
    assert parse_sysfs_cache("garbage line without fields") == {}
    assert parse_sysfs_cache("unavailable syscache") == {}


def test_summarize_simd_levels():
    assert summarize_simd("sse sse2 avx avx2 fma")["level"] == "avx2"
    assert summarize_simd("avx avx2 fma avx512f")["level"] == "avx512"
    assert summarize_simd("sse4_2 avx")["level"] == "avx"
    assert summarize_simd("asimd sve")["level"] == "arm-sve"
    assert summarize_simd("fp asimd aes")["level"] == "arm-neon"
    assert summarize_simd(None)["level"] is None
    present = summarize_simd("avx2 fma avx512f")["present"]
    assert present == {"avx2": True, "fma": True, "avx512f": True}


def test_parse_sinfo_partitions_handles_default_marker_and_null():
    rows = parse_sinfo_partitions(SINFO_PARTITIONS)
    assert [row["partition"] for row in rows] == ["thcp1", "gpu"]
    assert rows[1]["default"] is True
    assert rows[0]["nodes"] == 5000
    assert rows[0]["features"] is None
    assert rows[1]["features"] == "avx512"
    assert rows[1]["gres"] == "gpu:a100:8"


def test_parse_sinfo_nodes_collapses_variants():
    variants, rows = parse_sinfo_nodes(SINFO_NODES)
    assert rows == 3
    assert len(variants) == 2
    assert variants[0]["nodes"] == 2
    assert variants[0]["cpus"] == 64
    assert variants[0]["states"] == {"idle": 2}
    assert variants[1]["gres"] == "gpu:a100:4"


def test_parse_sinfo_nodes_state_is_not_a_hardware_variant():
    """Allocation state changes constantly and must not fake heterogeneity."""
    text = "\n".join(
        [
            "64::127000::(null)::(null)::idle",
            "64::127000::(null)::(null)::alloc",
            "64::127000::(null)::(null)::drain",
        ]
    )
    variants, rows = parse_sinfo_nodes(text)
    assert rows == 3
    assert len(variants) == 1
    assert variants[0]["nodes"] == 3
    assert variants[0]["states"] == {"alloc": 1, "drain": 1, "idle": 1}


def test_split_sections_ignores_leading_text():
    sections = split_sections("noise\n" + probe_stdout())
    assert sections["meta"].startswith("SLURMD_NODENAME=cn0042")
    assert "Model name" in sections["lscpu"]
    assert sections["os"].splitlines() == ["Linux", "x86_64", "5.14.0-1"]


# ---------------------------------------------------------------------------
# Recommendation heuristics
# ---------------------------------------------------------------------------

def test_recommend_parallel_hybrid_two_numa_domains():
    cpu = parse_lscpu(LSCPU_X86)
    cpu["simd"] = summarize_simd(cpu["flags"])
    numa = parse_numactl(NUMACTL)
    rec = recommend_parallel(
        cpu,
        numa,
        parse_meminfo(MEMINFO),
        sinfo_partition={"cpus_per_node": 64, "memory_per_node_mb": 127000},
        slurm_max_cpus=512,
        slurm_max_nodes=8,
        slurm_max_time="4-24:00:00",
    )
    assert rec["available"] is True
    assert rec["basis"]["physical_cores_per_node"] == 32
    assert rec["pure_mpi"]["ntasks_per_node"] == 32
    assert "--hint=nomultithread" in rec["pure_mpi"]["slurm_flags"]
    assert rec["hybrid_mpi_omp"]["ranks_per_node"] == 2
    assert rec["hybrid_mpi_omp"]["cpus_per_task"] == 16
    assert rec["hybrid_mpi_omp"]["env"]["OMP_NUM_THREADS"] == "16"
    assert rec["server_limits"]["nodes_reaching_max_cpus"] == 16
    assert any("NUMA" in note for note in rec["notes"])


def test_recommend_parallel_without_topology_data():
    rec = recommend_parallel(
        {},
        {},
        {},
        sinfo_partition=None,
        slurm_max_cpus=64,
        slurm_max_nodes=1,
        slurm_max_time="01:00:00",
    )
    assert rec["available"] is False
    assert rec["notes"]


def test_recommend_parallel_uses_sinfo_when_lscpu_missing():
    rec = recommend_parallel(
        {},
        {},
        {},
        sinfo_partition={"cpus_per_node": 64, "memory_per_node_mb": 127000},
        slurm_max_cpus=512,
        slurm_max_nodes=8,
        slurm_max_time="1-00:00:00",
    )
    assert rec["available"] is True
    assert rec["pure_mpi"]["ntasks_per_node"] == 64
    assert any("lscpu" in note for note in rec["notes"])


def test_recommend_parallel_warns_when_cpu_quota_binds_first():
    rec = recommend_parallel(
        {"physical_cores": 64, "logical_cpus": 64, "threads_per_core": 1, "sockets": 2, "cores_per_socket": 32},
        {"count": 1},
        {"memtotal": 256000},
        sinfo_partition={"cpus_per_node": 64, "memory_per_node_mb": 256000},
        slurm_max_cpus=128,
        slurm_max_nodes=16,
        slurm_max_time="1-00:00:00",
    )
    assert rec["server_limits"]["nodes_reaching_max_cpus"] == 2
    assert any("max_cpus" in note for note in rec["notes"])


# ---------------------------------------------------------------------------
# Service: probe, cache, pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_submits_one_job_then_serves_cache():
    svc, files, safe_exec, slurm = make_service()

    first = await svc.probe()
    assert first["status"] == "ok"
    assert first["source"] == "probe-job"
    assert first["partition"] == "thcp1"
    assert first["collected_on_node"] == "cn0042"
    assert first["cpu"]["model_name"].startswith("Intel(R) Xeon(R) Gold 6248")
    assert first["cpu"]["simd"]["level"] == "avx512"
    assert first["numa"]["distances"] == [[10, 21], [21, 10]]
    assert first["memory_mib"]["mem_total"] == 250000
    assert first["cpu"]["cache_source"] == "lscpu"
    assert first["recommended_parallel_parameters"]["pure_mpi"]["ntasks_per_node"] == 32
    assert first["slurm"]["partition"]["nodes"] == 5000
    assert len(slurm.submitted) == 1
    assert slurm.submitted[0]["partition"] == "thcp1"
    assert slurm.submitted[0]["nodes"] == 1
    assert slurm.submitted[0]["ntasks"] == 1
    assert slurm.submitted[0]["cpus_per_task"] == 1
    assert slurm.submitted[0]["time_limit"] == "00:03:00"
    # the probe script was written inside the sandbox and persists the result
    assert files.written[f"{ROOT}/.hpc-mcp/topo/collect_topo.sh"] == PROBE_SCRIPT
    assert f"{ROOT}/.hpc-mcp/topo/topology_thcp1.json" in files.written
    # sinfo ran with the partition allow-list only
    assert all("sinfo" in cmd for cmd in safe_exec.commands)
    assert any("-p thcp1" in cmd for cmd in safe_exec.commands)

    second = await svc.probe()
    assert second["source"] == "session-cache"
    assert isinstance(second["cache_age_seconds"], float)
    assert len(slurm.submitted) == 1  # no second job

    # a fresh service instance (new session) reuses the remote JSON cache
    svc2 = TopologyService(svc._cfg, files, safe_exec, FakeSlurm())
    third = await svc2.probe()
    assert third["source"] == "remote-cache"
    assert third["cpu"]["model_name"] == first["cpu"]["model_name"]


@pytest.mark.asyncio
async def test_probe_refresh_forces_a_new_job():
    svc, files, _safe_exec, slurm = make_service()
    await svc.probe()
    again = await svc.probe(refresh=True)
    assert again["source"] == "probe-job"
    assert len(slurm.submitted) == 2


@pytest.mark.asyncio
async def test_expired_remote_cache_triggers_new_probe():
    svc, files, _safe_exec, slurm = make_service()
    await svc.probe()
    cache_path = f"{ROOT}/.hpc-mcp/topo/topology_thcp1.json"
    payload = json.loads(files.written[cache_path])
    payload["collected_at_epoch"] = payload["collected_at_epoch"] - (48 * 3600)
    files.written[cache_path] = json.dumps(payload)

    fresh = TopologyService(svc._cfg, files, FakeSafeExec(), FakeSlurm())
    result = await fresh.probe()
    assert result["source"] == "probe-job"
    assert len(fresh._slurm.submitted) == 1


@pytest.mark.asyncio
async def test_cache_ttl_zero_disables_caching():
    svc, _files, _safe_exec, slurm = make_service(cache_ttl_seconds=0)
    await svc.probe()
    await svc.probe()
    assert len(slurm.submitted) == 2


@pytest.mark.asyncio
async def test_pending_probe_is_reused_not_resubmitted():
    svc, _files, _safe_exec, slurm = make_service()
    slurm.behavior = "pending"

    first = await svc.probe()
    assert first["status"] == "pending"
    assert first["collection_job_id"] == "1000"
    assert len(slurm.submitted) == 1

    # still queued -> same job, still no second submission
    again = await svc.probe()
    assert again["status"] == "pending"
    assert again["collection_job_id"] == "1000"
    assert len(slurm.submitted) == 1

    # the job finished in the meantime -> collected and cached
    slurm.states["1000"] = "COMPLETED"
    done = await svc.probe()
    assert done["status"] == "ok"
    assert done["source"] == "probe-job"
    assert len(slurm.submitted) == 1


@pytest.mark.asyncio
async def test_failed_probe_job_reports_state_and_stderr():
    svc, _files, _safe_exec, slurm = make_service()
    slurm.behavior = "complete"
    slurm.states["1000"] = "FAILED"
    slurm.stdout = ""

    async def failed_wait(job_id, *, timeout_seconds=None, poll_interval=10):
        return {"job_id": job_id, "state": "FAILED"}

    slurm.wait = failed_wait  # type: ignore[assignment]
    with pytest.raises(RemoteCommandError) as excinfo:
        await svc.probe()
    assert "FAILED" in str(excinfo.value)


@pytest.mark.asyncio
async def test_probe_rejects_partition_outside_allow_list():
    svc, _files, safe_exec, slurm = make_service()
    with pytest.raises(SlurmPolicyError):
        await svc.probe(partition="gpu")
    with pytest.raises(SlurmPolicyError):
        await svc.probe(partition="thcp1; rm -rf /")
    assert slurm.submitted == []
    assert safe_exec.commands == []


@pytest.mark.asyncio
async def test_probe_fails_closed_without_allowed_partitions():
    cfg = Config(root=ROOT, local_roots=["/tmp"], slurm=SlurmConfig(allowed_partitions=[]))
    svc = TopologyService(cfg, FakeFiles(), FakeSafeExec(), FakeSlurm())
    with pytest.raises(SlurmPolicyError):
        await svc.probe()


@pytest.mark.asyncio
async def test_probe_rejects_non_identifier_default_partition():
    """A hand-edited config must not smuggle anything into `sinfo -p`."""
    cfg = Config(root=ROOT, local_roots=["/tmp"], slurm=SlurmConfig(allowed_partitions=["bad partition"]))
    files, safe_exec, slurm = FakeFiles(), FakeSafeExec(), FakeSlurm()
    svc = TopologyService(cfg, files, safe_exec, slurm)
    with pytest.raises(SlurmPolicyError):
        await svc.probe()
    assert slurm.submitted == []
    assert safe_exec.commands == []


@pytest.mark.asyncio
async def test_sinfo_query_ignores_non_identifier_partition_names():
    cfg = Config(
        root=ROOT,
        local_roots=["/tmp"],
        slurm=SlurmConfig(allowed_partitions=["thcp1", "thcp1;id"]),
    )
    files, safe_exec, slurm = FakeFiles(), FakeSafeExec(), FakeSlurm()
    svc = TopologyService(cfg, files, safe_exec, slurm)
    result = await svc.probe()
    assert result["status"] == "ok"
    assert all(";" not in cmd for cmd in safe_exec.commands)
    node_query = next(cmd for cmd in safe_exec.commands if cmd.startswith("sinfo -h -N"))
    assert node_query.endswith('-p thcp1 -o "%c::%m::%f::%G::%t"')
    assert result["slurm"]["error"]


@pytest.mark.asyncio
async def test_probe_still_works_when_sinfo_fails():
    svc, _files, _safe_exec, slurm = make_service()
    svc._safe_exec = FakeSafeExec(fail=True)
    result = await svc.probe()
    assert result["status"] == "ok"
    assert result["slurm"]["available"] is False
    assert any("sinfo" in note for note in result["notes"])
    assert len(slurm.submitted) == 1


@pytest.mark.asyncio
async def test_probe_falls_back_to_cpuinfo_and_lscpu_less_nodes():
    svc, _files, _safe_exec, _slurm = make_service()
    svc._slurm.stdout = "\n".join(
        [
            f"{SECTION_PREFIX}meta",
            "SLURMD_NODENAME=cn0001",
            f"{SECTION_PREFIX}lscpu",
            "unavailable lscpu",
            f"{SECTION_PREFIX}cpuinfo",
            CPUINFO_X86,
            f"{SECTION_PREFIX}numactl",
            "unavailable numactl",
            f"{SECTION_PREFIX}meminfo",
            MEMINFO,
            f"{SECTION_PREFIX}os",
            "Linux",
            "x86_64",
            "5.14.0-1",
            f"{SECTION_PREFIX}end",
        ]
    )
    result = await svc.probe()
    assert result["cpu"]["model_name"].startswith("Intel(R) Xeon(R) Gold 6248")
    assert result["memory_mib"]["mem_total"] == 250000
    assert any("lscpu" in note for note in result["notes"])


@pytest.mark.asyncio
async def test_probe_falls_back_to_sysfs_for_caches():
    """ARM nodes (e.g. Phytium FT2000+) print no cache lines in lscpu."""
    svc, _files, _safe_exec, slurm = make_service()
    slurm.stdout = "\n".join(
        [
            f"{SECTION_PREFIX}meta",
            "SLURMD_NODENAME=cn63",
            f"{SECTION_PREFIX}lscpu",
            LSCPU_ARM,
            f"{SECTION_PREFIX}cpuinfo",
            "",
            f"{SECTION_PREFIX}numactl",
            "unavailable numactl",
            f"{SECTION_PREFIX}meminfo",
            MEMINFO,
            f"{SECTION_PREFIX}syscache",
            SYSCACHE_ARM,
            f"{SECTION_PREFIX}os",
            "Linux",
            "aarch64",
            "5.4.0-65-cn+",
            f"{SECTION_PREFIX}end",
        ]
    )
    result = await svc.probe()
    assert result["cpu"]["cache_source"] == "sysfs"
    assert result["cpu"]["cache_kib"]["l1d"] == 64
    assert result["cpu"]["cache_kib"]["l2"] == 512
    assert result["cpu"]["cache_kib"]["l3"] is None


@pytest.mark.asyncio
async def test_probe_notes_missing_cache_data():
    """No cache information anywhere must be stated, not silently dropped."""
    svc, _files, _safe_exec, slurm = make_service()
    slurm.stdout = "\n".join(
        [
            f"{SECTION_PREFIX}meta",
            "SLURMD_NODENAME=cn63",
            f"{SECTION_PREFIX}lscpu",
            LSCPU_ARM,
            f"{SECTION_PREFIX}cpuinfo",
            "",
            f"{SECTION_PREFIX}numactl",
            "unavailable numactl",
            f"{SECTION_PREFIX}meminfo",
            MEMINFO,
            f"{SECTION_PREFIX}syscache",
            "unavailable syscache",
            f"{SECTION_PREFIX}os",
            "Linux",
            "aarch64",
            "5.4.0-65-cn+",
            f"{SECTION_PREFIX}end",
        ]
    )
    result = await svc.probe()
    assert result["cpu"]["cache_source"] is None
    assert any("cache" in note for note in result["notes"])

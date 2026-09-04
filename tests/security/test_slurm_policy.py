"""Slurm resource policy tests -- abuse must be denied."""

import pytest

from hpc_mcp.config import SlurmConfig
from hpc_mcp.errors import SlurmPolicyError
from hpc_mcp.security.slurm_policy import (
    format_time_limit,
    parse_memory_mb,
    parse_time_limit,
    validate_job_request,
)

CFG = SlurmConfig(
    allowed_partitions=["compute", "debug"],
    max_nodes=2,
    max_cpus=64,
    max_memory_mb=256 * 1024,
    max_gpus=4,
    max_time="24:00:00",
    max_concurrent_jobs=5,
)


def ok_request(**over):
    base = dict(
        partition="compute",
        nodes=1,
        ntasks=1,
        cpus_per_task=8,
        memory="16G",
        time_limit="00:30:00",
        gpus=0,
    )
    base.update(over)
    return validate_job_request(CFG, **base)


class TestHappyPath:
    def test_valid_request(self) -> None:
        eff = ok_request()
        assert eff["partition"] == "compute"
        assert eff["memory_mb"] == 16 * 1024
        assert eff["time_limit"] == "00:30:00"

    def test_single_allowed_partition_default(self) -> None:
        cfg = SlurmConfig(allowed_partitions=["compute"])
        eff = validate_job_request(
            cfg, partition=None, nodes=1, ntasks=1, cpus_per_task=1, memory=None, time_limit=None, gpus=0
        )
        assert eff["partition"] == "compute"
        assert parse_time_limit(eff["time_limit"]) == parse_time_limit(cfg.max_time)


class TestPartitionAbuse:
    def test_forbidden_partition(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(partition="gpu-long")

    def test_no_partitions_fail_closed(self) -> None:
        cfg = SlurmConfig(allowed_partitions=[])
        with pytest.raises(SlurmPolicyError):
            validate_job_request(
                cfg, partition="compute", nodes=1, ntasks=1, cpus_per_task=1,
                memory=None, time_limit=None, gpus=0,
            )

    def test_ambiguous_partition_requires_choice(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(partition=None)


class TestResourceAbuse:
    def test_huge_cpus(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(cpus_per_task=1024)

    def test_total_cpus_overflow(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(nodes=2, ntasks=4, cpus_per_task=16)  # 128 > 64

    def test_too_many_nodes(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(nodes=10)

    def test_huge_memory(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(memory="4096G")

    def test_too_long_time(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(time_limit="7-00:00:00")

    def test_too_many_gpus(self) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(gpus=16)

    def test_concurrency_limit(self) -> None:
        with pytest.raises(SlurmPolicyError):
            validate_job_request(
                CFG, partition="compute", nodes=1, ntasks=1, cpus_per_task=1,
                memory=None, time_limit=None, gpus=0, active_jobs=5,
            )

    @pytest.mark.parametrize("bad", [0, -1, "x"])
    def test_invalid_nodes(self, bad) -> None:
        with pytest.raises(SlurmPolicyError):
            ok_request(nodes=bad)


class TestTimeParsing:
    @pytest.mark.parametrize(
        "text,seconds",
        [
            ("00:30:00", 1800),
            ("24:00:00", 86400),
            ("1-00:00:00", 86400),
            ("2-12:30:00", 2 * 86400 + 12 * 3600 + 1800),
            ("30", 1800),  # bare minutes
        ],
    )
    def test_parse_ok(self, text: str, seconds: int) -> None:
        assert parse_time_limit(text) == seconds

    @pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "-5", "999:999:9999x"])
    def test_parse_bad(self, bad: str) -> None:
        with pytest.raises(SlurmPolicyError):
            parse_time_limit(bad)

    def test_roundtrip(self) -> None:
        assert format_time_limit(86400) == "1-00:00:00"
        assert format_time_limit(1800) == "00:30:00"


class TestMemoryParsing:
    @pytest.mark.parametrize(
        "text,mb",
        [("8000", 8000), ("8000M", 8000), ("16G", 16384), ("1T", 1048576), (4096, 4096)],
    )
    def test_parse_ok(self, text, mb) -> None:
        assert parse_memory_mb(text) == mb

    @pytest.mark.parametrize("bad", ["", "lots", "-1G", 0])
    def test_parse_bad(self, bad) -> None:
        with pytest.raises(SlurmPolicyError):
            parse_memory_mb(bad)

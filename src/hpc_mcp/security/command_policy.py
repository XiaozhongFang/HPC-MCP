"""Login-node command policy.

The login node is only for lightweight management/query work.  This module
implements a **classifier**:

    raw command line
        --> tokenize (shlex, POSIX mode)
        --> split into segments on shell operators (; && || |)
        --> classify every executable token
        --> ALLOW / DENY

Key design points:

* No ``shell=True`` anywhere in the server; commands run as argv lists.
  This module therefore receives the *raw* command string and must detect
  and reject every shell construct that could smuggle a second program in:
  ``;``, ``&&``, ``||``, pipes, redirections, command substitution
  ``$( )`` / backticks, background ``&``, subshells, ``xargs``-style
  indirection, etc.
* Classification is per *executable token* -- ``echo ok && julia main.jl``
  is rejected because the second segment's executable is ``julia``.
* Computational programs (julia, python, make, cmake, mpirun, ...) are
  unconditionally denied on the login node and steered toward
  ``hpc.slurm.submit``.
* Any parse failure or unknown construct => DENY (fail-closed).
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from enum import Enum

from ..errors import CommandPolicyError

# ---------------------------------------------------------------------------
# Command classes
# ---------------------------------------------------------------------------

#: Programs that must run on a compute node via Slurm -- never on the login
#: node.  Matching is by basename of the executable.
COMPUTE_PROGRAMS: frozenset[str] = frozenset(
    {
        # interpreters / runtimes typically used for computation
        "julia", "python", "python2", "python3", "ipython", "R", "Rscript",
        "matlab", "octave", "perl", "ruby", "node", "java",
        # MPI / parallel launchers
        "mpirun", "mpiexec", "mpiexec.hydra", "orterun", "srun",
        # build systems & compilers (long compiles are compute work)
        "make", "gmake", "cmake", "ninja", "meson", "bazel", "buck",
        "gcc", "g++", "cc", "c++", "icc", "icpc", "ifort", "gfortran",
        "clang", "clang++", "nvcc", "mpicc", "mpicxx", "mpif90",
        "cargo", "rustc", "go",
        # HPC apps & test runners
        "moose", "moose-opt", "moose-dbg", "pytest", "ctest",
        # misc heavy tools
        "docker", "singularity", "apptainer", "podman",
    }
)

#: Programs that are outright forbidden (privilege escalation, dangerous
#: system manipulation, session persistence, remote access).
FORBIDDEN_PROGRAMS: frozenset[str] = frozenset(
    {
        "sudo", "su", "doas",
        "ssh", "scp", "sftp", "rsync", "rsh", "rlogin", "telnet", "nc", "ncat", "netcat", "socat",
        "nohup", "setsid", "screen", "tmux", "at", "batch", "crontab",
        "kill", "pkill", "killall",
        "dd", "mkfs", "mount", "umount", "fdisk", "parted",
        "chmod", "chown", "chgrp",
        "useradd", "userdel", "usermod", "passwd", "visudo",
        "systemctl", "service", "init",
        "iptables", "ip", "ifconfig",
        "eval", "exec",  # shell builtins used for indirection
        "curl", "wget",  # arbitrary network egress
        "xargs", "parallel",  # execute arbitrary things from stdin
        "env",  # handled specially: allowed only as bare query
        "bash", "sh", "zsh", "ksh", "csh", "tcsh", "dash", "fish",  # nested shells
        "vim", "vi", "nano", "emacs",  # interactive editors can spawn shells
        "less", "more", "man",  # pagers can spawn shells
    }
)

#: Programs allowed as bare queries even though they appear in FORBIDDEN
#: (handled by special-case logic).
_BARE_QUERY_OK = {"env": frozenset({""})}

#: Shell metacharacters that are never permitted anywhere in the command.
_FORBIDDEN_METACHARS = (";", "&", "|", ">", "<", "`", "$(", "${", "(", ")", "\n", "\r")


class Verdict(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass
class Classification:
    verdict: Verdict
    reason: str | None = None
    use_instead: str | None = None
    argv: list[str] | None = None  # tokenized argv when ALLOW


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def _basename(exe: str) -> str:
    return posixpath.basename(exe)


def _looks_like_path(token: str) -> bool:
    return token.startswith("/") or token.startswith("./") or token.startswith("../")


def classify(command: str, safe_commands: list[str]) -> Classification:
    """Classify a raw command line for the login node.

    ``safe_commands`` is the configured allow-list of executable basenames.
    Returns a :class:`Classification`; never raises.
    """
    if not isinstance(command, str) or not command.strip():
        return Classification(Verdict.DENY, reason="Empty command")

    # 1. Reject shell metacharacters outright.  Since we execute via argv
    #    (no shell), none of these are needed, and each one is a classic
    #    injection / chaining vector.
    for meta in _FORBIDDEN_METACHARS:
        if meta in command:
            return Classification(
                Verdict.DENY,
                reason=(
                    f"Shell metacharacter {meta!r} is not allowed. "
                    "Commands run without a shell; chaining, redirection, pipes, "
                    "substitution and background execution are forbidden on the login node."
                ),
            )

    # 2. Tokenize.  A parse failure is a denial.
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return Classification(Verdict.DENY, reason=f"Could not parse command: {exc}")
    if not argv:
        return Classification(Verdict.DENY, reason="Empty command")

    exe = argv[0]
    base = _basename(exe)

    # 3. Executables addressed by path (e.g. ./prog, /usr/bin/julia) are
    #    treated as unknown programs: forbidden unless the *basename* is on
    #    the allow-list -- this stops "copy julia to ./safe_name" style
    #    renames only when basename differs, while still blocking direct
    #    program execution like ./simulation.
    if _looks_like_path(exe) and base not in safe_commands:
        return Classification(
            Verdict.DENY,
            reason=(
                f"Executing programs by path ({exe!r}) is not allowed on the login node. "
                "Only whitelisted query commands may run here."
            ),
            use_instead="hpc.slurm.submit",
        )

    # 4. Compute programs are denied and steered to Slurm.
    if base in COMPUTE_PROGRAMS:
        return Classification(
            Verdict.DENY,
            reason=(
                f"{base!r} is a computational/build workload and is forbidden on HPC login nodes. "
                "Submit it to a compute node instead."
            ),
            use_instead="hpc.slurm.submit",
        )

    # 5. Explicitly forbidden programs.
    if base in FORBIDDEN_PROGRAMS:
        allowed_bare = _BARE_QUERY_OK.get(base)
        if allowed_bare is not None and len(argv) == 1 and base in safe_commands:
            return Classification(Verdict.ALLOW, argv=argv)
        return Classification(
            Verdict.DENY,
            reason=f"{base!r} is forbidden on the login node by policy",
        )

    # 6. Allow-list check.
    if base not in safe_commands:
        return Classification(
            Verdict.DENY,
            reason=(
                f"{base!r} is not in the login-node command whitelist. "
                "Only lightweight, read-only management commands are allowed."
            ),
        )

    # 7. Per-command argument vetting.
    arg_error = _vet_arguments(base, argv)
    if arg_error:
        return Classification(Verdict.DENY, reason=arg_error, use_instead=_suggest(base))

    return Classification(Verdict.ALLOW, argv=argv)


def _suggest(base: str) -> str | None:
    return None


def _vet_arguments(base: str, argv: list[str]) -> str | None:
    """Extra per-command argument checks.  Returns a denial reason or None."""
    args = argv[1:]

    if base == "git":
        # Read-only git only.  Deny subcommands that modify state or can
        # execute arbitrary commands (git config core.sshCommand, aliases,
        # git -c, hooks triggering via commit etc.).
        if not args:
            return None
        readonly_sub = {
            "status", "diff", "log", "rev-parse", "show", "ls-files",
            "ls-tree", "blame", "describe", "shortlog",
            "grep", "cat-file", "name-only",
        }
        # Subcommands allowed only without mutating flags:
        flag_gated = {
            "branch": {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy", "--edit-description", "--unset-upstream", "--set-upstream-to", "-u"},
            "tag": {"-d", "-a", "-m", "-s", "-f", "--delete"},
            "remote": {"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update"},
            "stash": {"drop", "pop", "apply", "clear", "push", "save"},
        }
        mutating_sub = {
            "add", "commit", "push", "pull", "fetch", "clone", "checkout",
            "merge", "rebase", "reset", "rm", "mv", "init", "apply", "am",
            "cherry-pick", "revert", "clean", "submodule", "gc", "prune",
        }
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub in mutating_sub:
            return f"'git {sub}' mutates repository state and is not allowed from the agent"
        if sub is not None and sub not in readonly_sub and sub not in flag_gated:
            return f"git subcommand {sub!r} is not on the read-only allow-list"
        if sub in flag_gated:
            rest = args[args.index(sub) + 1:]
            for a in rest:
                if a in flag_gated[sub]:
                    return f"'git {sub} {a}' mutates repository state and is not allowed"
        # git -c foo=bar and --exec-path can execute arbitrary commands
        for a in args:
            if a == "-c" or a.startswith("-c") or a.startswith("--exec-path"):
                return "git -c/--exec-path is not allowed (can execute arbitrary commands)"
            if a.startswith("--git-dir") or a.startswith("--work-tree"):
                return "git --git-dir/--work-tree overrides are not allowed"
        return None

    if base == "module":
        if not args:
            return None
        allowed = {"list", "avail", "show", "whatis", "spider", "keyword", "help"}
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub is not None and sub not in allowed:
            return f"'module {sub}' changes the login-node environment and is not allowed"
        return None

    if base == "scontrol":
        if not args:
            return None
        # only 'show <entity>' style queries
        if args[0] != "show":
            return f"'scontrol {args[0]}' mutates Slurm state; only 'scontrol show' is allowed"
        return None

    if base == "find":
        # find ... -exec is arbitrary command execution
        for a in args:
            if a in ("-exec", "-execdir", "-ok", "-okdir", "-delete"):
                return f"find {a} is not allowed (arbitrary command execution / deletion)"
        return None

    if base in {"head", "tail", "cat", "grep", "du", "stat", "ls", "wc", "sort", "uniq", "echo"}:
        return None

    if base in {"squeue", "sacct", "sinfo"}:
        return None

    if base in {"pwd", "which", "hostname", "date", "uname", "id", "whoami", "printenv", "df"}:
        return None

    return None


#: Matches obvious executable-looking tokens in shell-free strings (used by
#: tests and documentation).
EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9_+.-]+$")


def check_command(command: str, safe_commands: list[str]) -> list[str]:
    """Classify and either return argv or raise :class:`CommandPolicyError`."""
    result = classify(command, safe_commands)
    if result.verdict is Verdict.DENY:
        raise CommandPolicyError(
            result.reason or "Command denied by policy",
            requested=command,
            use_instead=result.use_instead,
        )
    return result.argv or []

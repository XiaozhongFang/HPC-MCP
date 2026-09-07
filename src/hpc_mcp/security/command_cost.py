"""Command cost policy: legal commands still have a cost budget.

The login-node allow-list stops *dangerous* commands; this module stops
*expensive* ones.  ``find`` on a huge tree, ``sort``/``du``/``grep`` over a
multi-GB file, or ``git grep`` across a big history are all legal but can peg
a shared login node.  Every command class gets a remote-side ``timeout`` and
an output cap; the cost tier is chosen from the command basename (and its
first subcommand where relevant).

Enforcement happens in :class:`hpc_mcp.shell.safe_exec.SafeExec` -- the same
code path that already runs the allow-list -- so no tool can bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostTier:
    name: str
    #: Remote-side timeout in seconds (also the hard exec timeout floor).
    timeout: int
    #: Max output bytes returned to the agent.
    max_output: int
    #: Max bytes a scan-style command may read before we advise narrowing.
    scan_hint: int | None = None


#: Tiers keyed by executable basename.  Unknown/whitelisted commands fall back
#: to the MEDIUM tier (conservative middle ground) rather than unlimited.
LOW = CostTier("low", timeout=15, max_output=256 * 1024)
MEDIUM = CostTier("medium", timeout=30, max_output=512 * 1024)
HIGH = CostTier("high", timeout=10, max_output=512 * 1024, scan_hint=64 * 1024 * 1024)

_CMD_TIERS: dict[str, CostTier] = {
    # trivial queries
    "pwd": LOW,
    "hostname": LOW,
    "date": LOW,
    "uname": LOW,
    "id": LOW,
    "whoami": LOW,
    "echo": LOW,
    "which": LOW,
    "df": LOW,
    "env": LOW,
    "printenv": LOW,
    # cheap reads / listings
    "ls": LOW,
    "head": LOW,
    "tail": LOW,
    "stat": LOW,
    "wc": LOW,
    "cat": MEDIUM,
    "uniq": MEDIUM,
    # medium scans
    "grep": MEDIUM,
    "git": MEDIUM,
    "module": LOW,
    "sinfo": LOW,
    # potentially expensive scans / sorts
    "find": HIGH,
    "du": HIGH,
    "sort": HIGH,
    "scontrol": MEDIUM,
}

_DEFAULT_TIER = MEDIUM


def tier_for(argv: list[str]) -> CostTier:
    """Pick the cost tier for a classified argv list."""
    if not argv:
        return _DEFAULT_TIER
    base = argv[0]
    # git subcommand refines the tier (git status is cheap, git grep is not)
    if base == "git" and len(argv) > 1:
        sub = argv[1]
        if sub in {"grep", "log", "blame", "shortlog"}:
            return HIGH
        if sub in {"status", "rev-parse", "ls-files", "ls-tree", "name-only", "show", "cat-file", "describe"}:
            return LOW
        return MEDIUM
    return _CMD_TIERS.get(base, _DEFAULT_TIER)


def clamp_timeout(tier: CostTier, requested: int | None, max_exec_seconds: int) -> int:
    """Effective remote timeout: never above the tier or the server cap."""
    if requested is None:
        return min(tier.timeout, max_exec_seconds)
    return min(requested, tier.timeout, max_exec_seconds)


def clamp_output(tier: CostTier, server_max_output: int) -> int:
    """Effective output cap: never above the tier or the server cap."""
    return min(tier.max_output, server_max_output)

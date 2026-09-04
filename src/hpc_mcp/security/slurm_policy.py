"""Slurm resource policy.

Every ``hpc.slurm.submit`` request is validated against the configured
limits *before* a job script is ever generated.  Requests exceeding any
limit are denied with an actionable message.  Parsing failures deny
(fail-closed).
"""

from __future__ import annotations

import re

from ..config import SlurmConfig
from ..errors import SlurmPolicyError

_TIME_RE = re.compile(r"^(?:(?P<days>\d+)-)?(?P<body>\d+(?::\d+){1,2})$|^(?P<mins_only>\d+)$")


def parse_time_limit(value: str) -> int:
    """Parse a Slurm time limit into seconds.

    Accepts ``HH:MM:SS``, ``D-HH:MM:SS``, ``MM:SS``, ``D-HH:MM`` and bare
    minutes.  Raises :class:`SlurmPolicyError` on unparsable input.
    """
    if not isinstance(value, str) or not value.strip():
        raise SlurmPolicyError("Time limit must be a non-empty string", requested=repr(value))
    v = value.strip()
    m = _TIME_RE.match(v)
    if not m:
        raise SlurmPolicyError(
            "Could not parse Slurm time limit",
            requested=v,
            use_instead="Formats like HH:MM:SS, D-HH:MM:SS or bare minutes",
        )
    if m.group("mins_only") is not None:
        return int(m.group("mins_only")) * 60
    days = int(m.group("days") or 0)
    fields = [int(part) for part in m.group("body").split(":")]
    if days and len(fields) == 2:
        hours, minutes = fields
        seconds = 0
    elif len(fields) == 2:
        hours = 0
        minutes, seconds = fields
    elif len(fields) == 3:
        hours, minutes, seconds = fields
    else:  # defensive: regex already restricts this
        raise SlurmPolicyError("Could not parse Slurm time limit", requested=v)
    if minutes > 59 or seconds > 59 or hours > 24 or (hours == 24 and (minutes or seconds or days)):
        raise SlurmPolicyError("Time limit has invalid hour/minute/second fields", requested=v)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_time_limit(seconds: int) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_memory_mb(value: str | int) -> int:
    """Parse a memory spec into MiB.  Accepts int MiB, or strings like
    ``8000``, ``8000M``, ``64G``, ``2T`` (Slurm suffixes)."""
    if isinstance(value, bool):
        raise SlurmPolicyError("Memory must be a positive integer or Slurm size string", requested=repr(value))
    if isinstance(value, int):
        if value <= 0:
            raise SlurmPolicyError("Memory must be positive", requested=repr(value))
        return value
    v = str(value).strip().upper()
    m = re.match(r"^(\d+)\s*([KMGT]?)B?$", v)
    if not m:
        raise SlurmPolicyError("Could not parse memory specification", requested=str(value))
    num = int(m.group(1))
    unit = m.group(2)
    mult = {"": 1, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[unit]
    mb = int(num * mult) if unit != "K" else max(1, int(num / 1024))
    if mb <= 0:
        raise SlurmPolicyError("Memory must be positive", requested=str(value))
    return mb


def validate_job_request(
    cfg: SlurmConfig,
    *,
    partition: str | None,
    nodes: int,
    ntasks: int,
    cpus_per_task: int,
    memory: str | int | None,
    time_limit: str | None,
    gpus: int,
    active_jobs: int = 0,
) -> dict:
    """Validate a submit request against policy.  Returns the effective,
    clamped-or-defaulted parameters on success; raises on violation.
    """
    if not isinstance(cfg.max_concurrent_jobs, int) or cfg.max_concurrent_jobs < 1:
        raise SlurmPolicyError("Invalid server max_concurrent_jobs configuration")
    for name in ("max_nodes", "max_cpus", "max_memory_mb", "max_gpus"):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "max_gpus" else 1):
            raise SlurmPolicyError(f"Invalid server {name} configuration")
    if not isinstance(active_jobs, int) or active_jobs < 0:
        raise SlurmPolicyError("active_jobs must be a non-negative integer", requested=repr(active_jobs))

    # Partition -------------------------------------------------------------
    if not cfg.allowed_partitions:
        raise SlurmPolicyError(
            "No Slurm partitions are allowed by the server configuration. "
            "Ask the administrator to set slurm.allowed_partitions (fail-closed default)."
        )
    effective_partition = partition or (cfg.allowed_partitions[0] if len(cfg.allowed_partitions) == 1 else None)
    if effective_partition is None:
        raise SlurmPolicyError(
            "A partition must be specified",
            scope="allowed partitions: " + ", ".join(cfg.allowed_partitions),
        )
    if not isinstance(effective_partition, str) or not effective_partition or len(effective_partition) > 128 or any(ch in effective_partition for ch in "\x00\r\n"):
        raise SlurmPolicyError("Partition must be a simple name", requested=repr(effective_partition))
    if effective_partition not in cfg.allowed_partitions:
        raise SlurmPolicyError(
            f"Partition {effective_partition!r} is not allowed",
            requested=effective_partition,
            scope="allowed partitions: " + ", ".join(cfg.allowed_partitions),
        )

    # Concurrency -----------------------------------------------------------
    if active_jobs >= cfg.max_concurrent_jobs:
        raise SlurmPolicyError(
            f"Too many active jobs ({active_jobs}); the configured limit is {cfg.max_concurrent_jobs}. "
            "Wait for jobs to finish or cancel some."
        )

    # Nodes -----------------------------------------------------------------
    if not isinstance(nodes, int) or nodes < 1:
        raise SlurmPolicyError("nodes must be a positive integer", requested=repr(nodes))
    if nodes > cfg.max_nodes:
        raise SlurmPolicyError(
            f"Requested nodes ({nodes}) exceed the configured limit ({cfg.max_nodes})",
            requested=str(nodes),
        )

    # Tasks / CPUs ----------------------------------------------------------
    if not isinstance(ntasks, int) or ntasks < 1:
        raise SlurmPolicyError("ntasks must be a positive integer", requested=repr(ntasks))
    if not isinstance(cpus_per_task, int) or cpus_per_task < 1:
        raise SlurmPolicyError("cpus_per_task must be a positive integer", requested=repr(cpus_per_task))
    total_cpus = nodes * ntasks * cpus_per_task
    if cpus_per_task > cfg.max_cpus or total_cpus > cfg.max_cpus:
        raise SlurmPolicyError(
            f"Requested CPUs (total {total_cpus}) exceed the configured limit ({cfg.max_cpus})",
            requested=f"nodes={nodes} ntasks={ntasks} cpus_per_task={cpus_per_task}",
        )

    # GPUs ------------------------------------------------------------------
    if not isinstance(gpus, int) or gpus < 0:
        raise SlurmPolicyError("gpus must be a non-negative integer", requested=repr(gpus))
    if gpus > cfg.max_gpus:
        raise SlurmPolicyError(
            f"Requested GPUs ({gpus}) exceed the configured limit ({cfg.max_gpus})",
            requested=str(gpus),
        )

    # Memory ----------------------------------------------------------------
    effective_memory_mb: int | None = None
    if memory is not None:
        effective_memory_mb = parse_memory_mb(memory)
        if effective_memory_mb > cfg.max_memory_mb:
            raise SlurmPolicyError(
                f"Requested memory ({effective_memory_mb} MiB) exceeds the configured limit "
                f"({cfg.max_memory_mb} MiB)",
                requested=str(memory),
            )

    # Time ------------------------------------------------------------------
    effective_time = time_limit or cfg.max_time
    seconds = parse_time_limit(effective_time)
    max_seconds = parse_time_limit(cfg.max_time)
    if seconds <= 0:
        raise SlurmPolicyError("Time limit must be positive", requested=effective_time)
    if seconds > max_seconds:
        raise SlurmPolicyError(
            f"Requested time limit ({effective_time}) exceeds the configured limit ({cfg.max_time})",
            requested=effective_time,
        )
    effective_time = format_time_limit(seconds)

    return {
        "partition": effective_partition,
        "nodes": nodes,
        "ntasks": ntasks,
        "cpus_per_task": cpus_per_task,
        "memory_mb": effective_memory_mb,
        "time_limit": effective_time,
        "gpus": gpus,
    }

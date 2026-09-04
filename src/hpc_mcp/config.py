"""Configuration loading for hpc-mcp.

Priority (highest first):

    CLI arguments > environment variables > config file > defaults

Defaults are intentionally minimal-privilege (fail-closed): no allowed
Slurm partitions, a minimal login-node command whitelist and conservative
resource limits unless the user explicitly configures more.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

ENV_PREFIX = "HPC_MCP_"

# ---------------------------------------------------------------------------
# Default login-node safe command whitelist (conservative, read-only tools
# plus VCS queries and Slurm queries).  Users may extend it via config.
# ---------------------------------------------------------------------------
DEFAULT_SAFE_COMMANDS: tuple[str, ...] = (
    "pwd",
    "ls",
    "find",
    "stat",
    "du",
    "df",
    "head",
    "tail",
    "cat",
    "grep",
    "wc",
    "sort",
    "uniq",
    "echo",
    "which",
    "hostname",
    "date",
    "uname",
    "id",
    "whoami",
    "env",
    "printenv",
    "git",
    "module",
    "squeue",
    "sacct",
    "sinfo",
    "scontrol",
)

#: Conservative fallback values -- never widen these automatically.
DEFAULT_MAX_READ_BYTES = 1 * 1024 * 1024  # 1 MiB
DEFAULT_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_MAX_LIST_ENTRIES = 2000
DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_COMMAND_TIMEOUT = 30
DEFAULT_SLURM_MAX_NODES = 2
DEFAULT_SLURM_MAX_CPUS = 64
DEFAULT_SLURM_MAX_MEMORY_MB = 256 * 1024  # 256 GiB in MiB
DEFAULT_SLURM_MAX_GPUS = 4
DEFAULT_SLURM_MAX_TIME = "24:00:00"
DEFAULT_MAX_CONCURRENT_JOBS = 20
DEFAULT_WAIT_MAX_SECONDS = 3600
DEFAULT_MAX_OUTPUT_BYTES = 1 * 1024 * 1024


@dataclass
class SshConfig:
    host: str | None = None
    port: int = 22
    user: str | None = None
    identity_file: str | None = None
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT


@dataclass
class SlurmConfig:
    allowed_partitions: list[str] = field(default_factory=list)
    max_nodes: int = DEFAULT_SLURM_MAX_NODES
    max_cpus: int = DEFAULT_SLURM_MAX_CPUS
    max_memory_mb: int = DEFAULT_SLURM_MAX_MEMORY_MB
    max_gpus: int = DEFAULT_SLURM_MAX_GPUS
    max_time: str = DEFAULT_SLURM_MAX_TIME
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS


@dataclass
class ShellConfig:
    safe_commands: list[str] = field(default_factory=lambda: list(DEFAULT_SAFE_COMMANDS))
    max_exec_seconds: int = DEFAULT_COMMAND_TIMEOUT
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


@dataclass
class FilesConfig:
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    max_list_entries: int = DEFAULT_MAX_LIST_ENTRIES


@dataclass
class Config:
    """Top-level server configuration."""

    root: str  # remote user root (mandatory, fail-closed if missing)
    ssh: SshConfig = field(default_factory=SshConfig)
    slurm: SlurmConfig = field(default_factory=SlurmConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    wait_max_seconds: int = DEFAULT_WAIT_MAX_SECONDS
    log_file: str | None = None
    log_level: str = "INFO"

    @property
    def jobs_dir(self) -> str:
        """Remote directory for job metadata and captured output."""
        return f"{self.root.rstrip('/')}/.hpc-mcp/jobs"


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def _deep_get(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _env(name: str) -> str | None:
    v = os.environ.get(ENV_PREFIX + name)
    return v if v is not None and v != "" else None


def _env_list(name: str) -> list[str] | None:
    v = _env(name)
    if v is None:
        return None
    return [p.strip() for p in v.split(",") if p.strip()]


def _env_int(name: str) -> int | None:
    v = _env(name)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {ENV_PREFIX}{name} must be an integer, got {v!r}") from exc


def _coalesce(*values: Any, default: Any = None) -> Any:
    for v in values:
        if v is not None:
            return v
    return default


def load_config_file(path: str | None) -> dict[str, Any]:
    """Load a YAML (or JSON) config file.  Missing file => empty dict."""
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config file {p}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {p} must contain a mapping at the top level")
    return data


def build_config(cli_args: Any | None = None, environ: dict[str, str] | None = None) -> Config:
    """Build a :class:`Config` from CLI args, env vars and a config file.

    ``cli_args`` is expected to be an ``argparse.Namespace``-like object
    produced by :mod:`hpc_mcp.__main__` (or ``None`` in tests).
    """
    cli = vars(cli_args) if cli_args is not None else {}
    cli = {k: v for k, v in cli.items() if v is not None}

    file_data = load_config_file(cli.get("config"))

    root = _coalesce(cli.get("root"), _env("ROOT"), file_data.get("root"))
    if root is None:
        raise ConfigError(
            "No HPC user root configured.\n\n"
            "Set it via one of:\n"
            "  --root /home/<account>/<you>\n"
            f"  {ENV_PREFIX}ROOT=/home/<account>/<you>\n"
            "  config file: root: /home/<account>/<you>"
        )
    if not root.startswith("/"):
        raise ConfigError(f"Root must be an absolute remote path, got {root!r}")
    if root != "/" and root.endswith("/"):
        root = root.rstrip("/")

    # -- SSH ---------------------------------------------------------------
    ssh_file = file_data.get("ssh") or {}
    identity = _coalesce(
        cli.get("identity_file"),
        _env("IDENTITY_FILE"),
        ssh_file.get("identity_file"),
        file_data.get("identity_file"),
    )
    if identity:
        identity = str(Path(identity).expanduser())
    ssh = SshConfig(
        host=_coalesce(cli.get("host"), _env("HOST"), ssh_file.get("host"), file_data.get("host")),
        port=_coalesce(cli.get("port"), _env_int("PORT"), ssh_file.get("port"), default=22),
        user=_coalesce(cli.get("user"), _env("USER"), ssh_file.get("user"), file_data.get("user")),
        identity_file=identity,
        connect_timeout=_coalesce(
            _env_int("CONNECT_TIMEOUT"), ssh_file.get("connect_timeout"), default=DEFAULT_CONNECT_TIMEOUT
        ),
        command_timeout=_coalesce(
            _env_int("COMMAND_TIMEOUT"), ssh_file.get("command_timeout"), default=DEFAULT_COMMAND_TIMEOUT
        ),
    )
    if ssh.host is None:
        raise ConfigError(
            "No HPC host configured.\n\n"
            "Set it via --host, " + ENV_PREFIX + "HOST, or 'host' in the config file.\n"
            "A Host alias from ~/.ssh/config is recommended."
        )
    if not isinstance(ssh.port, int) or not (1 <= ssh.port <= 65535):
        raise ConfigError(f"Invalid SSH port: {ssh.port!r}")

    # -- Slurm -------------------------------------------------------------
    slurm_file = file_data.get("slurm") or {}
    allowed_partitions = _coalesce(
        _env_list("ALLOWED_PARTITIONS"),
        slurm_file.get("allowed_partitions"),
        default=[],
    )
    slurm = SlurmConfig(
        allowed_partitions=list(allowed_partitions),
        max_nodes=_coalesce(_env_int("MAX_NODES"), slurm_file.get("max_nodes"), default=DEFAULT_SLURM_MAX_NODES),
        max_cpus=_coalesce(_env_int("MAX_CPUS"), slurm_file.get("max_cpus"), default=DEFAULT_SLURM_MAX_CPUS),
        max_memory_mb=_coalesce(
            _env_int("MAX_MEMORY_MB"), slurm_file.get("max_memory_mb"), default=DEFAULT_SLURM_MAX_MEMORY_MB
        ),
        max_gpus=_coalesce(_env_int("MAX_GPUS"), slurm_file.get("max_gpus"), default=DEFAULT_SLURM_MAX_GPUS),
        max_time=_coalesce(_env("MAX_TIME"), slurm_file.get("max_time"), default=DEFAULT_SLURM_MAX_TIME),
        max_concurrent_jobs=_coalesce(
            _env_int("MAX_CONCURRENT_JOBS"),
            slurm_file.get("max_concurrent_jobs"),
            default=DEFAULT_MAX_CONCURRENT_JOBS,
        ),
    )

    # -- Shell -------------------------------------------------------------
    shell_file = file_data.get("shell") or {}
    extra_safe = shell_file.get("safe_commands") or []
    if not isinstance(extra_safe, list):
        raise ConfigError("shell.safe_commands must be a list")
    env_safe = _env_list("SAFE_COMMANDS") or []
    safe = list(dict.fromkeys([*DEFAULT_SAFE_COMMANDS, *extra_safe, *env_safe]))
    shell_cfg = ShellConfig(
        safe_commands=safe,
        max_exec_seconds=_coalesce(
            _env_int("SHELL_MAX_EXEC_SECONDS"), shell_file.get("max_exec_seconds"), default=DEFAULT_COMMAND_TIMEOUT
        ),
        max_output_bytes=_coalesce(
            _env_int("MAX_OUTPUT_BYTES"), shell_file.get("max_output_bytes"), default=DEFAULT_MAX_OUTPUT_BYTES
        ),
    )

    # -- Files -------------------------------------------------------------
    files_file = file_data.get("files") or {}
    files = FilesConfig(
        max_read_bytes=_coalesce(
            _env_int("MAX_READ_BYTES"), files_file.get("max_read_bytes"), default=DEFAULT_MAX_READ_BYTES
        ),
        max_write_bytes=_coalesce(
            _env_int("MAX_WRITE_BYTES"), files_file.get("max_write_bytes"), default=DEFAULT_MAX_WRITE_BYTES
        ),
        max_list_entries=_coalesce(
            _env_int("MAX_LIST_ENTRIES"), files_file.get("max_list_entries"), default=DEFAULT_MAX_LIST_ENTRIES
        ),
    )

    wait_max = _coalesce(_env_int("WAIT_MAX_SECONDS"), file_data.get("wait_max_seconds"), default=DEFAULT_WAIT_MAX_SECONDS)

    log_file = _coalesce(cli.get("log_file"), _env("LOG_FILE"), file_data.get("log_file"))
    if log_file:
        log_file = str(Path(log_file).expanduser())
    log_level = _coalesce(cli.get("log_level"), _env("LOG_LEVEL"), file_data.get("log_level"), default="INFO")

    return Config(
        root=root,
        ssh=ssh,
        slurm=slurm,
        shell=shell_cfg,
        files=files,
        wait_max_seconds=int(wait_max),
        log_file=log_file,
        log_level=str(log_level).upper(),
    )

"""Configuration loading for hpc-mcp.

Priority (highest first):

    CLI arguments > environment variables > config file > defaults

Defaults are intentionally minimal-privilege (fail-closed): no allowed
Slurm partitions, a minimal login-node command whitelist and conservative
resource limits unless the user explicitly configures more.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import tempfile
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
    # StrictHostKeyChecking: "yes" (pinned known_hosts) is the safe default;
    # "accept-new" tolerates first-contact key enrollment.
    strict_host_key_checking: str = "yes"
    # Path to the ssh/sftp executables.  May be:
    #   - None            -> search PATH (default)
    #   - "ssh" / "sftp"  -> search PATH
    #   - "/abs/path"     -> exact path (e.g. WSL: "/usr/bin/ssh")
    #   - "@/abs/path"    -> WSL '@' prefix, same as exact path
    ssh_bin: str | None = None
    sftp_bin: str | None = None


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
    # Local directories that transfer tools may read/write.  Defaults to the
    # current working directory plus the system temp dir (TMPDIR or /tmp), so
    # quick local tests in /tmp work out of the box.
    local_roots: list[str] = field(
        default_factory=lambda: [str(Path.cwd().resolve()), str(Path(tempfile.gettempdir()).resolve())]
    )

    @property
    def local_root(self) -> str:
        """Primary local root (first of ``local_roots``) for backwards compat."""
        return self.local_roots[0] if self.local_roots else str(Path.cwd().resolve())

    @property
    def jobs_dir(self) -> str:
        """Remote directory for job metadata and captured output."""
        return f"{self.root.rstrip('/')}/.hpc-mcp/jobs"


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def _env(name: str, environ: dict[str, str] | None = None) -> str | None:
    v = (os.environ if environ is None else environ).get(ENV_PREFIX + name)
    return v if v is not None and v != "" else None


def _env_list(name: str, environ: dict[str, str] | None = None) -> list[str] | None:
    v = _env(name, environ)
    if v is None:
        return None
    return [p.strip() for p in v.split(",") if p.strip()]


def _env_int(name: str, environ: dict[str, str] | None = None) -> int | None:
    v = _env(name, environ)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        # allow simple arithmetic expressions like "4*16" in env vars too
        return _eval_int_expr(v, f"HPC_MCP_{name}")


def _coalesce(*values: Any, default: Any = None) -> Any:
    for v in values:
        if v is not None:
            return v
    return default


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _mapping(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config section {name!r} must be a mapping")
    return value


def _bounded_int(value: Any, name: str, *, minimum: int = 1, maximum: int = 2**31 - 1) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer between {minimum} and {maximum}, got {value!r}")
    if isinstance(value, str):
        value = _eval_int_expr(value, name)
    if not isinstance(value, int) or not (minimum <= value <= maximum):
        raise ConfigError(f"{name} must be an integer between {minimum} and {maximum}, got {value!r}")
    return value


#: Arithmetic expressions (e.g. "4*16", "256/2", "8*(2+1)") are allowed in
#: numeric config fields so administrators can express resource limits in
#: terms of machine properties (cores per node, etc.).  Only + - * / ( )
#: and integers are accepted; everything else is rejected.
_ARITH_RE = re.compile(r"^[\d\s+\-*/()]+$")


def _eval_int_expr(value: str, name: str) -> int:
    """Safely evaluate a simple integer arithmetic expression from config."""
    expr = value.strip()
    if not expr or not _ARITH_RE.match(expr):
        raise ConfigError(f"{name} must be an integer or a simple arithmetic expression (e.g. '4*16'), got {value!r}")
    try:
        result = _arith_eval(expr)
    except Exception as exc:
        raise ConfigError(f"{name}: could not evaluate expression {value!r}: {exc}") from exc
    if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
        raise ConfigError(f"{name} expression must evaluate to a positive integer, got {value!r}")
    return result


def _arith_eval(expr: str) -> int:
    """Evaluate an expression containing only ints and + - * / ( ) using an
    operator-precedence shunting-yard (no eval, no names, no functions)."""
    import ast

    tree = ast.parse(expr, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("unsupported syntax")
        if isinstance(node, ast.Constant) and not isinstance(node.value, int):
            raise ValueError("only integers allowed")
        if isinstance(node, ast.Div) or isinstance(node, ast.FloorDiv):
            # keep it simple: integer division with // semantics
            pass
    import operator

    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.floordiv,
        ast.FloorDiv: operator.floordiv,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.AST) -> int:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp):
            return ops[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op = ops[type(node.op)]
            if isinstance(node.op, (ast.Div, ast.FloorDiv)) and right == 0:
                raise ValueError("division by zero")
            return op(left, right)
        raise ValueError("unsupported syntax")

    result = _eval(tree.body)
    if not isinstance(result, int):
        raise ValueError("expression must evaluate to an integer")
    return result


def _safe_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or _CONTROL_CHARS.search(value):
        raise ConfigError(f"{name} must be a non-empty string without control characters")
    return value


def _normalize_keys(value: Any) -> Any:
    """Accept hyphenated keys (``ssh-bin``) as aliases of snake_case ones.

    CLI flags are spelled with hyphens (``--ssh-bin``) while the internal
    config dataclasses use underscores (``ssh_bin``).  Hyphenated keys used to
    be ignored silently, so a config file could behave differently from the
    equivalent command line (e.g. falling back to the ``ssh`` found on PATH
    instead of the configured one).  When both spellings are present the
    underscore key wins.
    """
    if isinstance(value, list):
        return [_normalize_keys(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, val in value.items():
        if isinstance(key, str) and "-" in key:
            out[key.replace("-", "_")] = _normalize_keys(val)
    for key, val in value.items():
        if not (isinstance(key, str) and "-" in key):
            out[key] = _normalize_keys(val)
    return out


#: Recognised config sections/keys.  Anything else is reported as a warning so
#: a typo cannot silently disable the setting the user intended to apply.
_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "": frozenset(
        {
            "host",
            "port",
            "user",
            "root",
            "local_root",
            "local_roots",
            "identity_file",
            "ssh",
            "slurm",
            "shell",
            "files",
            "wait_max_seconds",
            "log_file",
            "log_level",
            # ssh.* settings may also be written at the top level
            "connect_timeout",
            "command_timeout",
            "strict_host_key_checking",
            "ssh_bin",
            "sftp_bin",
        }
    ),
    "ssh": frozenset(
        {
            "host",
            "port",
            "user",
            "identity_file",
            "connect_timeout",
            "command_timeout",
            "strict_host_key_checking",
            "ssh_bin",
            "sftp_bin",
        }
    ),
    "slurm": frozenset(
        {
            "allowed_partitions",
            "max_nodes",
            "max_cpus",
            "max_memory_mb",
            "max_gpus",
            "max_time",
            "max_concurrent_jobs",
        }
    ),
    "shell": frozenset({"safe_commands", "max_exec_seconds", "max_output_bytes"}),
    "files": frozenset({"max_read_bytes", "max_write_bytes", "max_list_entries"}),
}


def _warn_unknown_keys(data: dict[str, Any], source: Path) -> None:
    """Warn about unrecognised config keys, which are otherwise ignored."""
    logger = logging.getLogger("hpc_mcp")
    for section, known in _KNOWN_KEYS.items():
        mapping = data.get(section) if section else data
        if not isinstance(mapping, dict):
            continue
        prefix = f"{section}." if section else ""
        for key in mapping:
            if isinstance(key, str) and key not in known:
                logger.warning(
                    "Ignoring unknown config key %r in %s (check for typos)",
                    f"{prefix}{key}",
                    source,
                )


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
    data = _normalize_keys(data)
    _warn_unknown_keys(data, p)
    return data


def build_config(cli_args: Any | None = None, environ: dict[str, str] | None = None) -> Config:
    """Build a :class:`Config` from CLI args, env vars and a config file.

    ``cli_args`` is expected to be an ``argparse.Namespace``-like object
    produced by :mod:`hpc_mcp.__main__` (or ``None`` in tests).
    """
    env = os.environ if environ is None else environ
    cli = vars(cli_args) if cli_args is not None else {}
    cli = {k: v for k, v in cli.items() if v is not None}

    file_data = load_config_file(cli.get("config"))

    root = _coalesce(cli.get("root"), _env("ROOT", env), file_data.get("root"))
    if root is None:
        raise ConfigError(
            "No HPC user root configured.\n\n"
            "Set it via one of:\n"
            "  --root /home/<account>/<you>\n"
            f"  {ENV_PREFIX}ROOT=/home/<account>/<you>\n"
            "  config file: root: /home/<account>/<you>"
        )
    if not isinstance(root, str) or _CONTROL_CHARS.search(root) or not root.startswith("/"):
        raise ConfigError(f"Root must be an absolute remote path, got {root!r}")
    root = posixpath.normpath(root)
    if root == "/":
        raise ConfigError("Root must be a dedicated user directory, not filesystem root '/'")

    # -- SSH ---------------------------------------------------------------
    ssh_file = _mapping(file_data, "ssh")
    identity = _coalesce(
        cli.get("identity_file"),
        _env("IDENTITY_FILE", env),
        ssh_file.get("identity_file"),
        file_data.get("identity_file"),
    )
    if identity:
        _safe_text(identity, "SSH identity_file")
        identity = str(Path(identity).expanduser())
    ssh = SshConfig(
        host=_coalesce(cli.get("host"), _env("HOST", env), ssh_file.get("host"), file_data.get("host")),
        port=_coalesce(cli.get("port"), _env_int("PORT", env), ssh_file.get("port"), default=22),
        user=_coalesce(cli.get("user"), _env("USER", env), ssh_file.get("user"), file_data.get("user")),
        identity_file=identity,
        connect_timeout=_coalesce(
            _env_int("CONNECT_TIMEOUT", env), ssh_file.get("connect_timeout"), default=DEFAULT_CONNECT_TIMEOUT
        ),
        command_timeout=_coalesce(
            _env_int("COMMAND_TIMEOUT", env), ssh_file.get("command_timeout"), default=DEFAULT_COMMAND_TIMEOUT
        ),
        strict_host_key_checking=_coalesce(
            _env("STRICT_HOST_KEY_CHECKING", env),
            ssh_file.get("strict_host_key_checking"),
            default="yes",
        ),
        ssh_bin=_coalesce(
            cli.get("ssh_bin"), _env("SSH_BIN", env), ssh_file.get("ssh_bin"), file_data.get("ssh_bin")
        ),
        sftp_bin=_coalesce(
            cli.get("sftp_bin"), _env("SFTP_BIN", env), ssh_file.get("sftp_bin"), file_data.get("sftp_bin")
        ),
    )
    if ssh.strict_host_key_checking not in ("yes", "accept-new"):
        raise ConfigError(
            "ssh.strict_host_key_checking must be 'yes' or 'accept-new', "
            f"got {ssh.strict_host_key_checking!r}"
        )
    if ssh.host is None:
        raise ConfigError(
            "No HPC host configured.\n\n"
            "Set it via --host, " + ENV_PREFIX + "HOST, or 'host' in the config file.\n"
            "A Host alias from ~/.ssh/config is recommended."
        )
    if ssh.host is not None:
        _safe_text(ssh.host, "SSH host")
    if ssh.user is not None:
        _safe_text(ssh.user, "SSH user")
    if not isinstance(ssh.port, int) or isinstance(ssh.port, bool) or not (1 <= ssh.port <= 65535):
        raise ConfigError(f"Invalid SSH port: {ssh.port!r}")
    ssh.connect_timeout = _bounded_int(ssh.connect_timeout, "ssh.connect_timeout", maximum=3600)
    ssh.command_timeout = _bounded_int(ssh.command_timeout, "ssh.command_timeout", maximum=86400)

    # -- Slurm -------------------------------------------------------------
    slurm_file = _mapping(file_data, "slurm")
    allowed_partitions = _coalesce(
        _env_list("ALLOWED_PARTITIONS", env),
        slurm_file.get("allowed_partitions"),
        default=[],
    )
    if not isinstance(allowed_partitions, list):
        raise ConfigError("slurm.allowed_partitions must be a list of names")
    slurm = SlurmConfig(
        allowed_partitions=list(allowed_partitions),
        max_nodes=_coalesce(_env_int("MAX_NODES", env), slurm_file.get("max_nodes"), default=DEFAULT_SLURM_MAX_NODES),
        max_cpus=_coalesce(_env_int("MAX_CPUS", env), slurm_file.get("max_cpus"), default=DEFAULT_SLURM_MAX_CPUS),
        max_memory_mb=_coalesce(
            _env_int("MAX_MEMORY_MB", env), slurm_file.get("max_memory_mb"), default=DEFAULT_SLURM_MAX_MEMORY_MB
        ),
        max_gpus=_coalesce(_env_int("MAX_GPUS", env), slurm_file.get("max_gpus"), default=DEFAULT_SLURM_MAX_GPUS),
        max_time=_coalesce(_env("MAX_TIME", env), slurm_file.get("max_time"), default=DEFAULT_SLURM_MAX_TIME),
        max_concurrent_jobs=_coalesce(
            _env_int("MAX_CONCURRENT_JOBS", env),
            slurm_file.get("max_concurrent_jobs"),
            default=DEFAULT_MAX_CONCURRENT_JOBS,
        ),
    )
    if any(not isinstance(p, str) or not p or _CONTROL_CHARS.search(p) or "/" in p for p in slurm.allowed_partitions):
        raise ConfigError("slurm.allowed_partitions contains an invalid partition name")
    slurm.max_nodes = _bounded_int(slurm.max_nodes, "slurm.max_nodes")
    slurm.max_cpus = _bounded_int(slurm.max_cpus, "slurm.max_cpus")
    slurm.max_memory_mb = _bounded_int(slurm.max_memory_mb, "slurm.max_memory_mb")
    slurm.max_gpus = _bounded_int(slurm.max_gpus, "slurm.max_gpus", minimum=0)
    slurm.max_concurrent_jobs = _bounded_int(slurm.max_concurrent_jobs, "slurm.max_concurrent_jobs")
    from .security.slurm_policy import parse_time_limit
    if not isinstance(slurm.max_time, str):
        raise ConfigError("slurm.max_time must be a string")
    try:
        if parse_time_limit(slurm.max_time) <= 0:
            raise ConfigError("slurm.max_time must be positive")
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Invalid slurm.max_time: {slurm.max_time!r}") from exc

    # -- Shell -------------------------------------------------------------
    shell_file = _mapping(file_data, "shell")
    extra_safe = shell_file.get("safe_commands") or []
    if not isinstance(extra_safe, list):
        raise ConfigError("shell.safe_commands must be a list")
    env_safe = _env_list("SAFE_COMMANDS", env) or []
    if any(not isinstance(c, str) or not c or len(c) > 128 or _CONTROL_CHARS.search(c) or "/" in c for c in [*extra_safe, *env_safe]):
        raise ConfigError("shell.safe_commands entries must be non-empty command basenames")
    safe = list(dict.fromkeys([*DEFAULT_SAFE_COMMANDS, *extra_safe, *env_safe]))
    shell_cfg = ShellConfig(
        safe_commands=safe,
        max_exec_seconds=_coalesce(
            _env_int("SHELL_MAX_EXEC_SECONDS", env), shell_file.get("max_exec_seconds"), default=DEFAULT_COMMAND_TIMEOUT
        ),
        max_output_bytes=_coalesce(
            _env_int("MAX_OUTPUT_BYTES", env), shell_file.get("max_output_bytes"), default=DEFAULT_MAX_OUTPUT_BYTES
        ),
    )

    # -- Files -------------------------------------------------------------
    files_file = _mapping(file_data, "files")
    files = FilesConfig(
        max_read_bytes=_bounded_int(
            _coalesce(_env_int("MAX_READ_BYTES", env), files_file.get("max_read_bytes"), default=DEFAULT_MAX_READ_BYTES),
            "files.max_read_bytes", maximum=2**31 - 1,
        ),
        max_write_bytes=_bounded_int(
            _coalesce(_env_int("MAX_WRITE_BYTES", env), files_file.get("max_write_bytes"), default=DEFAULT_MAX_WRITE_BYTES),
            "files.max_write_bytes", maximum=2**31 - 1,
        ),
        max_list_entries=_bounded_int(
            _coalesce(_env_int("MAX_LIST_ENTRIES", env), files_file.get("max_list_entries"), default=DEFAULT_MAX_LIST_ENTRIES),
            "files.max_list_entries", maximum=1_000_000,
        ),
    )
    shell_cfg.max_exec_seconds = _bounded_int(shell_cfg.max_exec_seconds, "shell.max_exec_seconds", maximum=86400)
    shell_cfg.max_output_bytes = _bounded_int(shell_cfg.max_output_bytes, "shell.max_output_bytes", maximum=2**31 - 1)

    wait_max = _coalesce(_env_int("WAIT_MAX_SECONDS", env), file_data.get("wait_max_seconds"), default=DEFAULT_WAIT_MAX_SECONDS)
    wait_max = _bounded_int(wait_max, "wait_max_seconds", maximum=7 * 86400)

    log_file = _coalesce(cli.get("log_file"), _env("LOG_FILE", env), file_data.get("log_file"))
    if log_file:
        log_file = str(Path(log_file).expanduser())
    log_level = _coalesce(cli.get("log_level"), _env("LOG_LEVEL", env), file_data.get("log_level"), default="INFO")
    if str(log_level).upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"Invalid log level: {log_level!r}")

    local_root_value = _coalesce(cli.get("local_root"), _env("LOCAL_ROOT", env), file_data.get("local_root"))
    local_roots_value = _coalesce(file_data.get("local_roots"), _env_list("LOCAL_ROOTS", env))
    if local_root_value is not None and not isinstance(local_root_value, str):
        raise ConfigError("local_root must be a path string")
    if local_roots_value is not None and not isinstance(local_roots_value, list):
        raise ConfigError("local_roots must be a list of path strings")
    local_roots: list[str] = []
    if local_roots_value is not None:
        raw_roots = local_roots_value
    elif local_root_value is not None:
        raw_roots = [local_root_value]
    else:
        raw_roots = [str(Path.cwd())]  # default: cwd + tempdir
    for entry in raw_roots:
        p = Path(str(entry)).expanduser()
        try:
            resolved = p.resolve(strict=True)
        except OSError as exc:
            raise ConfigError(f"Local root cannot be resolved: {p}") from exc
        if not resolved.is_dir():
            raise ConfigError(f"Local root must be a directory: {resolved}")
        if resolved.name.lower() in {".ssh", ".gnupg"}:
            raise ConfigError("local_root may not be a credential directory")
        local_roots.append(str(resolved))
    # Always include the system temp dir so quick local tests in /tmp work.
    tmp = str(Path(tempfile.gettempdir()).resolve())
    if tmp not in local_roots:
        local_roots.append(tmp)
    if not local_roots:
        raise ConfigError("At least one local root is required")

    return Config(
        root=root,
        local_roots=local_roots,
        ssh=ssh,
        slurm=slurm,
        shell=shell_cfg,
        files=files,
        wait_max_seconds=int(wait_max),
        log_file=log_file,
        log_level=str(log_level).upper(),
    )

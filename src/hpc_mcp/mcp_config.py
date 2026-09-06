"""Automatic registration of hpc-mcp as an MCP server for Codex and Reasonix.

The ``hpc-mcp mcp-add`` command wires this server into the client's MCP
configuration using an ABSOLUTE path to the hpc-mcp executable (resolved
from ``sys.argv[0]`` / ``shutil.which``), so the client works regardless of
which environment (conda/venv) launches it and without requiring hpc-mcp to
be on the client's PATH.

The environment variables carried into the MCP server config are the ones
needed for it to connect (HOST/USER/ROOT/...).  SSH *credentials* (private
keys) are never written here -- they stay in ~/.ssh/config.

Editing strategy: the client TOML files may contain complex nested tables
(Codex providers, Reasonix proxies, comments, ordering).  We therefore edit
them at the *text level*: replace only the ``hpc`` MCP server section and
leave everything else byte-for-byte intact.  No external TOML writer is
needed, and user config is never reformatted or lost.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from .config import Config
from .logging import get_logger

_CODEX_PATH = Path("~/.codex/config.toml").expanduser()
_REASONIX_PATH = Path("~/.reasonix/config.toml").expanduser()


def _resolve_self() -> str:
    """Absolute path to the real hpc-mcp entry executable.

    Prefers the installed console script found on PATH (which points into
    the active conda/venv), then sys.argv[0]; falls back to a plain name.
    """
    found = shutil.which("hpc-mcp")
    if found:
        return str(Path(found).resolve())
    argv0 = sys.argv[0]
    if argv0 and Path(argv0).is_absolute() and Path(argv0).exists():
        return str(Path(argv0).resolve())
    return argv0 or "hpc-mcp"


def _config_env(cfg: Config) -> dict[str, str]:
    """Map a loaded Config to the env vars the server needs on startup."""
    env: dict[str, str] = {
        "HPC_MCP_HOST": cfg.ssh.host or "",
        "HPC_MCP_USER": cfg.ssh.user or "",
        "HPC_MCP_ROOT": cfg.root,
    }
    if cfg.ssh.port != 22:
        env["HPC_MCP_PORT"] = str(cfg.ssh.port)
    if cfg.ssh.identity_file:
        env["HPC_MCP_IDENTITY_FILE"] = cfg.ssh.identity_file
    if cfg.ssh.ssh_bin:
        env["HPC_MCP_SSH_BIN"] = cfg.ssh.ssh_bin
    if cfg.ssh.sftp_bin:
        env["HPC_MCP_SFTP_BIN"] = cfg.ssh.sftp_bin
    if cfg.local_roots:
        env["HPC_MCP_LOCAL_ROOTS"] = ",".join(cfg.local_roots)
    if cfg.slurm.allowed_partitions:
        env["HPC_MCP_ALLOWED_PARTITIONS"] = ",".join(cfg.slurm.allowed_partitions)
    if cfg.log_file:
        env["HPC_MCP_LOG_FILE"] = cfg.log_file
    env["HPC_MCP_LOG_LEVEL"] = cfg.log_level
    # strip empties so the server falls back to its own defaults
    return {k: v for k, v in env.items() if v}


def _render_hpc_section(executable: str, env: dict[str, str], *, header: str) -> str:
    """Render the TOML text for the hpc MCP server section.

    ``header`` is the parent table header (e.g. ``[mcp_servers.hpc]``);
    env vars go into the nested table ``[mcp_servers.hpc.env]``.
    """
    lines = [header, f'command = "{executable}"']
    if env:
        lines.append("")
        env_header = header.rstrip("]") + ".env]"
        lines.append(env_header)
        for k in sorted(env):
            lines.append(f'{k} = "{env[k]}"')
    return "\n".join(lines) + "\n"


def _upsert_section(text: str, header: str, section: str) -> str:
    """Replace or append a TOML section (header .. next sibling header).

    The section body runs until the next table header at the same or higher
    nesting level.  Sub-tables of this section (e.g. ``[mcp_servers.hpc.env]``
    under ``[mcp_servers.hpc]``) are part of the body and must NOT terminate
    it, so we only stop at a header whose dotted prefix differs.
    """
    if not text.strip():
        return section if not text or text.endswith("\n") else "\n" + section

    # anchor: the exact header line
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        # append at end (with blank-line separation)
        body = text if text.endswith("\n") else text + "\n"
        if not body.endswith("\n\n"):
            body += "\n"
        return body + section

    # find end: next line that starts with '[' and is NOT a sub-table of header
    prefix = header.strip().rstrip("]")  # e.g. "[mcp_servers.hpc"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j].strip()
        if line.startswith("[") and not line.startswith(prefix + "."):
            end = j
            break
    new_lines = lines[:start] + [section] + lines[end:]
    return "".join(new_lines)


def mcp_add_codex(cfg: Config, executable: str) -> str:
    """Add/update the hpc MCP server in ~/.codex/config.toml (text edit)."""
    path = _CODEX_PATH
    header = "[mcp_servers.hpc]"
    section = _render_hpc_section(executable, _config_env(cfg), header=header)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_upsert_section(text, header, section), encoding="utf-8")
    return str(path)


def mcp_add_reasonix(cfg: Config, executable: str) -> str:
    """Add the hpc plugin to ~/.reasonix/config.toml (text edit).

    Reasonix uses ``[[plugins]]`` tables.  If a plugin named "hpc" already
    exists, the file is left untouched (idempotent).  Otherwise one block is
    appended after the last existing plugin.  Never deletes other plugins.
    """
    path = _REASONIX_PATH
    env = _config_env(cfg)
    entry_lines = ["[[plugins]]", 'name = "hpc"', f'command = "{executable}"']
    if env:
        rendered_env = ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items())
        entry_lines.append(f"env = {{ {rendered_env} }}")
    entry = "\n".join(entry_lines) + "\n\n"
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    if '"hpc"' in text and 'name = "hpc"' in text:
        return str(path)  # already registered; keep file untouched

    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def cmd_mcp_add(cfg: Config) -> int:
    """Entry point for `hpc-mcp mcp-add`."""
    log = get_logger()
    executable = _resolve_self()
    log.info("registering hpc-mcp executable: %s", executable)
    print(f"Registering hpc-mcp as MCP server (executable: {executable})")
    env = _config_env(cfg)
    written: list[str] = []
    for name, fn in (("codex", mcp_add_codex), ("reasonix", mcp_add_reasonix)):
        try:
            path = fn(cfg, executable)
            written.append(name)
            print(f"  [{name}] wrote {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name}] FAILED: {exc}", file=sys.stderr)
            if name == "reasonix":
                print(
                    "    Manual fallback for Reasonix (run in your own terminal):\n"
                    f"      reasonix mcp remove hpc\n"
                    f"      reasonix mcp add hpc --env HPC_MCP_HOST={env.get('HPC_MCP_HOST','')} "
                    f"--env HPC_MCP_USER={env.get('HPC_MCP_USER','')} "
                    f"--env HPC_MCP_ROOT={env.get('HPC_MCP_ROOT','')} "
                    f"--env HPC_MCP_ALLOWED_PARTITIONS={env.get('HPC_MCP_ALLOWED_PARTITIONS','')} "
                    f"{executable}",
                    file=sys.stderr,
                )
    if written:
        print("\nDone. Restart your MCP client (Codex/Reasonix) to pick up the change.")
        print("Env vars embedded: " + ", ".join(f"{k}={v}" for k, v in env.items()))
    return 0 if len(written) == 2 else 1

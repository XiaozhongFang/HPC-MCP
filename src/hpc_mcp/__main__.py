"""Command-line entry point for hpc-mcp.

Usage::

    hpc-mcp [--config config.yaml] [--host HOST] [--root PATH] ...

stdout carries the stdio MCP protocol only; everything else goes to stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from .config import build_config
from .errors import ConfigError, HpcMcpError
from .logging import get_logger, setup_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hpc-mcp",
        description=(
            "Security-first MCP server for operating a remote HPC cluster "
            "(SSH + Slurm) from coding agents. stdio transport."
        ),
    )
    p.add_argument("--version", action="version", version=f"hpc-mcp {__version__}")
    p.add_argument("--config", metavar="PATH", help="YAML config file path")
    p.add_argument("--host", metavar="HOST", help="HPC SSH host (a ~/.ssh/config Host alias is recommended)")
    p.add_argument("--port", type=int, metavar="PORT", help="SSH port (default 22)")
    p.add_argument("--user", metavar="USER", help="SSH user (may be a shared account)")
    p.add_argument("--root", metavar="PATH", help="Remote user root directory the agent is confined to (required)")
    p.add_argument("--local-root", metavar="PATH", help="Local directory allowed for upload/download (default: current directory)")
    p.add_argument("--identity-file", metavar="PATH", help="SSH identity file (default from ~/.ssh/config)")
    p.add_argument("--ssh-bin", metavar="PATH", help="ssh executable (e.g. /usr/bin/ssh, @/usr/bin/ssh, or @/mnt/c/Windows/System32/OpenSSH/ssh.exe; default: from PATH)")
    p.add_argument("--sftp-bin", metavar="PATH", help="sftp executable (same forms as --ssh-bin; default: from PATH)")
    p.add_argument("--log-file", metavar="PATH", help="Append logs to this file (in addition to stderr)")
    p.add_argument("--log-level", metavar="LEVEL", help="Log level (DEBUG, INFO, WARNING; default INFO)")
    p.add_argument(
        "--check",
        action="store_true",
        help="Load configuration, verify SSH connectivity, print a summary and exit",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    mcp_add = sub.add_parser(
        "mcp-add",
        help="Register hpc-mcp as an MCP server in Codex/Reasonix config "
        "(uses the absolute path of this executable; no PATH dependency)",
    )
    mcp_add.add_argument("--config", dest="add_config", metavar="PATH", help="YAML config file path for the embedded env vars")
    mcp_add.add_argument("--client", choices=["codex", "reasonix", "all"], default="all",
                         help="Which client config to update (default: all)")
    mcp_add.add_argument("--host", dest="add_host", metavar="HOST", help="HPC SSH host (a ~/.ssh/config Host alias is recommended)")
    mcp_add.add_argument("--user", dest="add_user", metavar="USER", help="SSH user (may be a shared account)")
    mcp_add.add_argument("--root", dest="add_root", metavar="PATH", help="Remote user root directory (required)")
    return p


def _cmd_check(cfg) -> int:
    """Verify SSH connectivity and print a short summary. Returns exit code."""
    from .ssh.manager import SshManager

    log = get_logger()
    mgr = SshManager(cfg)
    log.info("using ssh executable: %s", mgr.ssh_bin)

    async def probe_and_close():
        try:
            return await mgr.probe()
        finally:
            await mgr.close()

    try:
        info = asyncio.run(probe_and_close())
    except HpcMcpError as exc:
        print(f"Connection check FAILED: {exc}", file=sys.stderr)
        return 1
    print("Configuration OK. Remote probe succeeded:")
    for k, v in info.items():
        print(f"  {k}: {v}")
    log.info("check completed successfully")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if getattr(args, "command", None) == "mcp-add":
        from .mcp_config import cmd_mcp_add
        from .config import load_config_file

        setup_logging(getattr(args, "log_level", "INFO") or "INFO")
        # Reuse the normal config loader with mcp-add's own args
        ns = argparse.Namespace(
            config=getattr(args, "add_config", None),
            host=getattr(args, "add_host", None),
            user=getattr(args, "add_user", None),
            root=getattr(args, "add_root", None),
            port=None, local_root=None, identity_file=None,
            ssh_bin=None, sftp_bin=None, log_file=None, log_level=None, check=False,
        )
        try:
            cfg = build_config(ns)
        except ConfigError as exc:
            print(f"Configuration error:\n{exc}", file=sys.stderr)
            return 2
        return cmd_mcp_add(cfg)

    try:
        cfg = build_config(args)
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_level, cfg.log_file)

    if args.check:
        return _cmd_check(cfg)

    from .server import run_server

    try:
        asyncio.run(run_server(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    p.add_argument("--identity-file", metavar="PATH", help="SSH identity file (default from ~/.ssh/config)")
    p.add_argument("--log-file", metavar="PATH", help="Append logs to this file (in addition to stderr)")
    p.add_argument("--log-level", metavar="LEVEL", help="Log level (DEBUG, INFO, WARNING; default INFO)")
    p.add_argument(
        "--check",
        action="store_true",
        help="Load configuration, verify SSH connectivity, print a summary and exit",
    )
    return p


def _cmd_check(cfg) -> int:
    """Verify SSH connectivity and print a short summary. Returns exit code."""
    from .ssh.manager import SshManager

    log = get_logger()
    mgr = SshManager(cfg)
    try:
        info = asyncio.run(mgr.probe())
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

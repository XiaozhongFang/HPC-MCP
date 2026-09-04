"""MCP server wiring (stdio transport).

stdout carries only the MCP protocol; all logs go to stderr/file.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import __version__
from .config import Config
from .errors import HpcMcpError
from .filesystem.service import FileService
from .filesystem.transfer import TransferService
from .logging import AuditLogger, ToolTimer, get_logger, sanitize
from .shell.safe_exec import SafeExec
from .slurm.jobs import JobTracker
from .slurm.manager import SlurmManager
from .ssh.manager import SshManager
from .ssh.sftp import SftpClient
from .tools.registry import ToolDef, build_tools


def _to_result(payload: Any) -> list[types.TextContent]:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    return [types.TextContent(type="text", text=text)]


def create_server(cfg: Config) -> tuple[Server, list[ToolDef]]:
    log = get_logger()
    audit = AuditLogger()

    ssh = SshManager(cfg)
    sftp = SftpClient(cfg)
    files = FileService(cfg, ssh)
    transfer = TransferService(cfg, sftp, files)
    safe_exec = SafeExec(cfg, ssh)
    tracker = JobTracker(cfg, ssh)
    slurm = SlurmManager(cfg, ssh, tracker)

    tools = build_tools(cfg, ssh, files, transfer, safe_exec, slurm)
    by_name = {t.name: t for t in tools}

    server: Server = Server("hpc-mcp", version=__version__)

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=t.read_only,
                        destructiveHint=t.destructive,
                        idempotentHint=t.idempotent,
                        openWorldHint=t.open_world,
                    ),
                )
                for t in tools
            ]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        args: dict[str, Any] = dict(params.arguments or {})
        tool = by_name.get(name)
        if tool is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )
        timer = ToolTimer()
        try:
            with timer:
                payload = await tool.handler(args)
        except HpcMcpError as exc:
            audit.record(tool=name, decision="DENY", reason=exc.user_message, args=args, duration=timer.duration)
            return types.CallToolResult(content=_to_result(exc.user_message), isError=True)
        except Exception as exc:  # noqa: BLE001 - fail closed on unexpected errors
            log.exception("tool %s failed unexpectedly", name)
            audit.record(tool=name, decision="DENY", reason=f"unexpected error: {exc}", args=args, duration=timer.duration)
            return types.CallToolResult(
                content=_to_result(
                    "Operation failed.\n\nReason:\nInternal error (fail-closed): " + sanitize(str(exc))
                ),
                isError=True,
            )
        audit.record(tool=name, decision="ALLOW", args=args, duration=timer.duration)
        return types.CallToolResult(content=_to_result(payload))

    from mcp.server.lowlevel.server import HandlerEntry

    server._request_handlers["tools/list"] = HandlerEntry(types.PaginatedRequestParams, on_list_tools)
    server._request_handlers["tools/call"] = HandlerEntry(types.CallToolRequestParams, on_call_tool)
    return server, tools


async def run_server(cfg: Config) -> None:
    log = get_logger()
    server, _tools = create_server(cfg)
    log.info(
        "hpc-mcp %s starting: host=%s user=%s root=%s partitions=%s",
        __version__, cfg.ssh.host, cfg.ssh.user, cfg.root, cfg.slurm.allowed_partitions,
    )
    async with stdio_server() as (read_stream, write_stream):
        init = server.create_initialization_options()
        await server.run(read_stream, write_stream, init)

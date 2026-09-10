"""Tool registry: schemas, annotations, and dispatch.

Each tool definition pairs a JSON input schema with MCP ToolAnnotations
(readOnlyHint/destructiveHint/idempotentHint/openWorldHint) and an async
handler.  Handlers raise HpcMcpError subclasses; the server converts them
into structured denial messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..config import Config
from ..cluster.topology import TopologyService
from ..errors import PolicyDenied
from ..filesystem.search import FileSearchService
from ..filesystem.service import FileService
from ..filesystem.transfer import TransferService
from ..project.snapshot import ProjectService
from ..shell.safe_exec import SafeExec
from ..slurm.manager import SlurmManager
from ..ssh.manager import SshManager

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Handler
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


def validate_tool_args(tool: ToolDef, args: Any) -> dict[str, Any]:
    """Validate the structural part of a tool schema before dispatch."""
    if not isinstance(args, dict):
        raise PolicyDenied("Tool arguments must be a JSON object")
    properties = tool.schema.get("properties", {})
    unknown = set(args) - set(properties)
    if unknown or (tool.schema.get("additionalProperties") is False and unknown):
        raise PolicyDenied(f"Unknown tool argument(s): {', '.join(sorted(map(str, unknown)))}")
    missing = [name for name in tool.schema.get("required", []) if name not in args]
    if missing:
        raise PolicyDenied(f"Missing required tool argument(s): {', '.join(missing)}")
    for name, value in args.items():
        schema = properties.get(name)
        if schema is not None and not _schema_value_valid(value, schema):
            raise PolicyDenied(f"Invalid type or value for tool argument: {name}")
    return args


def _schema_value_valid(value: Any, schema: dict[str, Any]) -> bool:
    if "oneOf" in schema:
        return any(_schema_value_valid(value, option) for option in schema["oneOf"])
    kind = schema.get("type")
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(kind, True)
    if not valid:
        return False
    if "minimum" in schema and value < schema["minimum"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if kind == "array" and "items" in schema and not all(_schema_value_valid(item, schema["items"]) for item in value):
        return False
    if kind == "object" and isinstance(value, dict):
        if schema.get("additionalProperties") is False and any(key not in schema.get("properties", {}) for key in value):
            return False
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict) and not all(_schema_value_valid(item, additional) for item in value.values()):
            return False
    return True


def build_tools(
    cfg: Config,
    ssh: SshManager,
    files: FileService,
    transfer: TransferService,
    safe_exec: SafeExec,
    slurm: SlurmManager,
    search: FileSearchService | None = None,
    projects: ProjectService | None = None,
    topology: TopologyService | None = None,
) -> list[ToolDef]:
    root = cfg.root
    search_svc = search or FileSearchService(cfg, ssh)
    projects_svc = projects or ProjectService(cfg, ssh, files, safe_exec, slurm._tracker)

    def _str(_name: str, desc: str) -> dict:
        return {"type": "string", "description": desc}

    def _int(_name: str, desc: str, default: int | None = None, *, minimum: int | None = None) -> dict:
        d: dict = {"type": "integer", "description": desc}
        if default is not None:
            d["default"] = default
        if minimum is not None:
            d["minimum"] = minimum
        return d

    def _str_arg(args: dict, name: str, *, required: bool = True) -> str | None:
        value = args.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value:
            raise PolicyDenied(f"{name} must be a non-empty string")
        return value

    def _int_arg(args: dict, name: str, default: int | None = None, *, minimum: int | None = None) -> int | None:
        value = args.get(name, default)
        if value is None and default is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
            bound = f" >= {minimum}" if minimum is not None else ""
            raise PolicyDenied(f"{name} must be an integer{bound}")
        return value

    def _bool_arg(args: dict, name: str, default: bool = False) -> bool:
        value = args.get(name, default)
        if not isinstance(value, bool):
            raise PolicyDenied(f"{name} must be a boolean")
        return value

    abs_path = f"Absolute remote path inside {root}"

    tools: list[ToolDef] = []

    # ------------------------------------------------------------------ info
    async def hpc_info(args: dict) -> dict:
        info = await ssh.probe()
        # NOTE: never expose SSH connection details (host/user/ip/port) to the
        # agent -- that would let it bypass the MCP and log in directly.
        # Local roots are also withheld (they can embed usernames/project
        # names); only capability booleans are surfaced.
        info.update(
            {
                "working_root": root,
                "workspace_available": any(isinstance(r, str) and r for r in cfg.local_roots),
                "transfer_enabled": bool(cfg.local_roots),
                "allowed_partitions": cfg.slurm.allowed_partitions,
                "max_cpus": cfg.slurm.max_cpus,
                "max_nodes": cfg.slurm.max_nodes,
                "max_time": cfg.slurm.max_time,
                "max_gpus": cfg.slurm.max_gpus,
            }
        )
        return info

    tools.append(
        ToolDef(
            name="hpc.info",
            description=(
                "Get HPC connection info: sandboxed working root, local transfer "
                "availability (workspace_available/transfer_enabled), Slurm "
                "availability, cluster name, allowed partitions (use one in "
                "hpc.slurm.submit) and Slurm resource limits. Connection details "
                "(host/user) and local path roots are intentionally not exposed."
            ),
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=hpc_info,
            read_only=True,
            idempotent=True,
        )
    )

    # ---------------------------------------------------------------- files
    async def files_list(args: dict) -> Any:
        return await files.list_dir(
            _str_arg(args, "path"),
            recursive=_bool_arg(args, "recursive"),
            max_entries=_int_arg(args, "max_entries", minimum=0),
            page_size=_int_arg(args, "page_size", minimum=0),
            cursor=args.get("cursor"),
            max_depth=_int_arg(args, "max_depth", minimum=1),
        )

    tools.append(
        ToolDef(
            name="hpc.files.list",
            description=(
                f"List directory contents on the HPC. Path must be inside {root}. "
                "Returns a bounded page ({page_size} entries at most, server-capped) "
                "plus a next_cursor. Prefer recursive=false (default). For recursive "
                "listing the server enumerates one depth level per call and stops "
                "early once the page is full, so huge trees are never scanned in a "
                "single call: pass the returned next_cursor to continue from the "
                "next depth. max_depth is clamped by the server."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "recursive": {"type": "boolean", "default": False},
                    "max_entries": _int("max_entries", "Hard cap on returned entries (server-capped)"),
                    "page_size": _int("page_size", "Entries per page (default: max_entries)"),
                    "cursor": _str("cursor", "Opaque cursor from a previous call to continue a recursive listing"),
                    "max_depth": _int("max_depth", "Recursive depth limit (clamped by server config)"),
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=files_list,
            read_only=True,
            idempotent=True,
        )
    )

    async def files_read(args: dict) -> Any:
        return await files.read_file(
            _str_arg(args, "path"),
            max_bytes=_int_arg(args, "max_bytes", minimum=0),
            offset=_int_arg(args, "offset", 0, minimum=0) or 0,
        )
    tools.append(
        ToolDef(
            name="hpc.files.read",
            description=(
                f"Read ONE bounded slice of a remote text file inside {root}. "
                "Each call returns at most min(max_bytes, server slice cap) bytes "
                "starting at 'offset'. For large files, do NOT page from offset=0 "
                "to EOF: first use hpc.files.search to locate the region of "
                "interest, then read a small slice around it. The result reports "
                "size (full file), offset, bytes, end_of_file and next_offset; "
                "use next_offset only when you genuinely need the adjacent "
                "region, never as part of an unguided full-file download."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "max_bytes": _int("max_bytes", "Max bytes to return (server-capped)"),
                    "offset": _int("offset", "Byte offset to start from"),
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=files_read,
            read_only=True,
            idempotent=True,
        )
    )

    async def files_search(args: dict) -> Any:
        return await search_svc.search(
            _str_arg(args, "path"),
            _str_arg(args, "pattern"),
            max_matches=_int_arg(args, "max_matches", minimum=0),
            context_lines=_int_arg(args, "context_lines", minimum=0),
            max_scan_bytes=_int_arg(args, "max_scan_bytes", minimum=0),
            max_files=_int_arg(args, "max_files", minimum=0),
            max_depth=_int_arg(args, "max_depth", minimum=1),
            timeout=_int_arg(args, "timeout", minimum=1),
        )

    tools.append(
        ToolDef(
            name="hpc.files.search",
            description=(
                f"Search a remote file (or directory tree) inside {root} for a "
                "regex pattern, with hard server-side budgets. Use this to "
                "locate the region of interest in a log FIRST, then read a "
                "small slice with hpc.files.read -- never page a whole log. "
                "All of max_matches/context_lines/max_scan_bytes/max_files/"
                "max_depth/timeout are clamped by the server; results report "
                "truncated=true when a budget was hit."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "pattern": _str("pattern", "Extended regex (POSIX ERE) pattern"),
                    "max_matches": _int("max_matches", "Max matches to return (server-capped)"),
                    "context_lines": _int("context_lines", "Lines of context around each match (server-capped)"),
                    "max_scan_bytes": _int("max_scan_bytes", "Max bytes scanned for a single file (server-capped)"),
                    "max_files": _int("max_files", "Max files scanned in a directory tree (approx, server-capped)"),
                    "max_depth": _int("max_depth", "Directory tree depth limit (server-capped)"),
                    "timeout": _int("timeout", "Max seconds for the remote search (server-capped)"),
                },
                "required": ["path", "pattern"],
                "additionalProperties": False,
            },
            handler=files_search,
            read_only=True,
            idempotent=True,
        )
    )

    async def files_write(args: dict) -> Any:
        return await files.write_file(
            _str_arg(args, "path"),
            _str_arg(args, "content"),
            append=_bool_arg(args, "append"),
            expected_size=_int_arg(args, "expected_size", minimum=0),
            expected_mtime=_int_arg(args, "expected_mtime", minimum=0),
            expected_sha256=args.get("expected_sha256"),
        )

    tools.append(
        ToolDef(
            name="hpc.files.write",
            description=(
                f"Write (or append to) a remote file inside {root}. Size-capped. "
                "The result reports exactly what changed: change is 'created' (new "
                "file), 'overwritten' (existing file replaced) or 'appended', with "
                "existed_before, previous_size and new_size.\n"
                "Optimistic concurrency: pass expected_size / expected_mtime / "
                "expected_sha256 (from a previous hpc.files.read / stat) to refuse "
                "overwriting a file that another process changed since you read it."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "content": _str("content", "Text content to write"),
                    "append": {"type": "boolean", "default": False},
                    "expected_size": _int("expected_size", "Expected current file size (bytes); mismatch denies the write"),
                    "expected_mtime": _int("expected_mtime", "Expected current mtime (epoch seconds); mismatch denies the write"),
                    "expected_sha256": _str("expected_sha256", "Expected current SHA-256 (64 hex chars); mismatch denies the write"),
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=files_write,
            destructive=True,
        )
    )

    async def files_mkdir(args: dict) -> Any:
        return await files.mkdir(_str_arg(args, "path"), parents=_bool_arg(args, "parents"))

    tools.append(
        ToolDef(
            name="hpc.files.mkdir",
            description=f"Create a remote directory inside {root}.",
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "parents": {"type": "boolean", "default": False},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=files_mkdir,
            destructive=True,
        )
    )

    async def files_delete(args: dict) -> Any:
        return await files.delete(_str_arg(args, "path"), recursive=_bool_arg(args, "recursive"))

    tools.append(
        ToolDef(
            name="hpc.files.delete",
            description=f"Delete a remote file (or directory with recursive=true) inside {root}.",
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=files_delete,
            destructive=True,
        )
    )

    async def files_upload(args: dict) -> Any:
        return await transfer.upload(_str_arg(args, "local_path"), _str_arg(args, "remote_path"))

    tools.append(
        ToolDef(
            name="hpc.files.upload",
            description=f"Upload a local file to the HPC, destination inside {root}. Uses SFTP.",
            schema={
                "type": "object",
                "properties": {
                    "local_path": _str("local_path", "Local file path"),
                    "remote_path": _str("remote_path", f"Destination path inside {root}"),
                },
                "required": ["local_path", "remote_path"],
                "additionalProperties": False,
            },
            handler=files_upload,
            destructive=True,
        )
    )

    async def files_download(args: dict) -> Any:
        return await transfer.download(_str_arg(args, "remote_path"), _str_arg(args, "local_path"))

    tools.append(
        ToolDef(
            name="hpc.files.download",
            description=f"Download a remote file from inside {root} to a local path. Uses SFTP.",
            schema={
                "type": "object",
                "properties": {
                    "remote_path": _str("remote_path", abs_path),
                    "local_path": _str("local_path", "Local destination path"),
                },
                "required": ["remote_path", "local_path"],
                "additionalProperties": False,
            },
            handler=files_download,
            read_only=True,
        )
    )

    # ---------------------------------------------------------------- shell
    async def shell_run(args: dict) -> Any:
        return await safe_exec.run(
            _str_arg(args, "command"), _str_arg(args, "cwd", required=False), timeout=_int_arg(args, "timeout", minimum=1)
        )

    tools.append(
        ToolDef(
            name="hpc.shell.run_safe",
            description=(
                "Run a whitelisted, read-only login-node command (e.g. 'git status', "
                "'ls', 'squeue', 'module list'). No shell operators, no compute "
                "programs, no build tools -- those are denied; use hpc.slurm.submit "
                f"for computation. cwd must be inside {root}."
            ),
            schema={
                "type": "object",
                "properties": {
                    "command": _str("command", "Single whitelisted command (no pipes/&&/;/redirects)"),
                    "cwd": _str("cwd", f"Working directory inside {root}"),
                    "timeout": _int("timeout", "Max seconds"),
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=shell_run,
            read_only=True,
        )
    )

    # ---------------------------------------------------------------- slurm
    async def job_run(args: dict) -> Any:
        return await slurm.run(
            runtime=_str_arg(args, "runtime"),
            script=_str_arg(args, "script"),
            args=args.get("args"),
            working_directory=_str_arg(args, "working_directory", required=False) or root,
            job_name=_str_arg(args, "job_name", required=False),
            partition=_str_arg(args, "partition", required=False),
            nodes=_int_arg(args, "nodes", minimum=1),
            ntasks=_int_arg(args, "ntasks", minimum=1),
            cpus_per_task=_int_arg(args, "cpus_per_task", minimum=1),
            memory=args.get("memory"),
            time_limit=_str_arg(args, "time_limit", required=False),
            gpus=_int_arg(args, "gpus", minimum=0),
            environment=args.get("environment"),
        )

    tools.append(
        ToolDef(
            name="hpc.job.run",
            description=(
                "High-level job submission from a trusted runtime profile "
                "(julia/python/moose/bash). The server builds the argv from a "
                "fixed profile table and then enforces the exact same Slurm "
                "resource policy, path sandbox and job ownership as "
                "hpc.slurm.submit -- it never bypasses any policy. Experts can "
                "keep using hpc.slurm.submit for full argv control. moose "
                "expects script=the binary and args like ['input.i']."
            ),
            schema={
                "type": "object",
                "properties": {
                    "runtime": {
                        "type": "string",
                        "enum": ["julia", "python", "moose", "bash", "shell"],
                        "description": "Trusted runtime profile",
                    },
                    "script": _str("script", "Script path inside the user root"),
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extra argv (required for moose: binary + input)",
                    },
                    "job_name": _str("job_name", "Short job name (default: runtime)"),
                    "working_directory": _str(
                        "working_directory", f"Job working directory inside {root} (default: {root})"
                    ),
                    "partition": _str("partition", f"Allowed partition (default: configured first)"),
                    "nodes": _int("nodes", "Node count (default 1)"),
                    "ntasks": _int("ntasks", "Task count (default 1)"),
                    "cpus_per_task": _int("cpus_per_task", "CPUs per task (default 1)"),
                    "memory": _str("memory", "Memory, e.g. '16G' (default: none)"),
                    "time_limit": _str("time_limit", "Wall limit, e.g. '00:30:00'"),
                    "gpus": _int("gpus", "GPU count (default 0)"),
                    "environment": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["runtime", "script"],
                "additionalProperties": False,
            },
            handler=job_run,
            open_world=True,
        )
    )

    async def slurm_submit(args: dict) -> Any:
        command = args.get("command")
        if not isinstance(command, (str, list)):
            raise PolicyDenied("command must be a string or argv list")
        return await slurm.submit(
            job_name=_str_arg(args, "job_name", required=False) or "job",
            working_directory=_str_arg(args, "working_directory", required=False) or root,
            command=command,
            partition=_str_arg(args, "partition", required=False),
            nodes=_int_arg(args, "nodes", minimum=1),
            ntasks=_int_arg(args, "ntasks", minimum=1),
            cpus_per_task=_int_arg(args, "cpus_per_task", minimum=1),
            memory=args.get("memory"),
            time_limit=_str_arg(args, "time_limit", required=False),
            gpus=_int_arg(args, "gpus", minimum=0),
            environment=args.get("environment"),
        )

    tools.append(
        ToolDef(
            name="hpc.slurm.submit",
            description=(
                "Submit a compute job to Slurm via sbatch. This is the ONLY way "
                "to run computation (Julia/Python/make/cmake/mpirun/tests) on the "
                "HPC -- running workloads directly on the login node is denied. "
                "Prefer working remotely: edit files with hpc.files.write, then "
                "submit the job here and poll hpc.slurm.status/hpc.slurm.output; "
                "do NOT download source to the local machine and run it locally "
                "unless the environment truly cannot be reached otherwise.\n"
                "Parameter defaults come from the server configuration or, "
                "when 'command' is a single .sh script path, from that script's "
                "#SBATCH directives (e.g. --cpus-per-task, --time, --mem, "
                "--nodes, --ntasks, --partition). Explicit arguments override "
                "script directives. Use 'partition' to pick among the allowed "
                "partitions reported by hpc.info (e.g. a GPU partition); the "
                "value must be on the configured allow-list or the request is "
                "denied. The effective partition is returned with the job.\n"
                "Default working_directory = the configured user root; "
                "cpus_per_task/nodes/ntasks default to 1, gpus to 0, and the "
                "time limit defaults to the configured maximum. Job stdout/err "
                "is captured under .hpc-mcp/jobs/<id>/."
            ),
            schema={
                "type": "object",
                "properties": {
                    "job_name": _str("job_name", "Short job name (default 'job')"),
                    "working_directory": _str(
                        "working_directory",
                        f"Job working directory inside {root} (default: {root})",
                    ),
                    "command": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                        ],
                        "description": "Program argv, e.g. ['julia','--project=.','test/runtests.jl'], or a single path to a .sh job script whose #SBATCH directives provide defaults",
                    },
                    "partition": _str(
                        "partition",
                        f"Slurm partition to use (default: configured one). Allowed: {', '.join(cfg.slurm.allowed_partitions) or '(none)'}",
                    ),
                    "nodes": _int("nodes", "Node count (default 1)"),
                    "ntasks": _int("ntasks", "Task count (default 1)"),
                    "cpus_per_task": _int("cpus_per_task", "CPUs per task (default 1)"),
                    "memory": _str("memory", "Memory, e.g. '16G' (default: none)"),
                    "time_limit": _str("time_limit", "Wall limit, e.g. '00:30:00' (default: configured maximum)"),
                    "gpus": _int("gpus", "GPU count (default 0)"),
                    "environment": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=slurm_submit,
            open_world=True,
        )
    )

    async def slurm_status(args: dict) -> Any:
        return await slurm.status(_str_arg(args, "job_id"))

    tools.append(
        ToolDef(
            name="hpc.slurm.status",
            description="Get the state of a job previously submitted by this server.",
            schema={
                "type": "object",
                "properties": {"job_id": _str("job_id", "Slurm job ID")},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=slurm_status,
            read_only=True,
            idempotent=True,
        )
    )

    async def slurm_queue(args: dict) -> Any:
        return await slurm.queue()

    tools.append(
        ToolDef(
            name="hpc.slurm.queue",
            description="List active jobs submitted by this server.",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=slurm_queue,
            read_only=True,
            idempotent=True,
        )
    )

    async def slurm_output(args: dict) -> Any:
        return await slurm.output(
            _str_arg(args, "job_id"),
            stream=args.get("stream", "stdout"),
            tail_bytes=_int_arg(args, "tail_bytes", minimum=0),
        )

    tools.append(
        ToolDef(
            name="hpc.slurm.output",
            description="Read captured stdout/stderr of a tracked job (tail-capped).",
            schema={
                "type": "object",
                "properties": {
                    "job_id": _str("job_id", "Slurm job ID"),
                    "stream": {"type": "string", "enum": ["stdout", "stderr"], "default": "stdout"},
                    "tail_bytes": _int("tail_bytes", "Max bytes from the end of the log"),
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=slurm_output,
            read_only=True,
            idempotent=True,
        )
    )

    async def slurm_cancel(args: dict) -> Any:
        return await slurm.cancel(_str_arg(args, "job_id"))

    tools.append(
        ToolDef(
            name="hpc.slurm.cancel",
            description="Cancel a job previously submitted by this server.",
            schema={
                "type": "object",
                "properties": {"job_id": _str("job_id", "Slurm job ID")},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=slurm_cancel,
            destructive=True,
        )
    )

    async def slurm_accounting(args: dict) -> Any:
        return await slurm.accounting(_str_arg(args, "job_id"))

    tools.append(
        ToolDef(
            name="hpc.slurm.accounting",
            description="Get sacct accounting data (Elapsed, CPUTime, MaxRSS, State, ExitCode, NodeList).",
            schema={
                "type": "object",
                "properties": {"job_id": _str("job_id", "Slurm job ID")},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=slurm_accounting,
            read_only=True,
            idempotent=True,
        )
    )

    async def jobs_wait(args: dict) -> Any:
        return await slurm.wait(
            _str_arg(args, "job_id"),
            timeout_seconds=_int_arg(args, "timeout_seconds", minimum=0),
            poll_interval=_int_arg(args, "poll_interval", 10, minimum=1) or 10,
        )

    tools.append(
        ToolDef(
            name="hpc.jobs.wait",
            description=(
                "Wait for a tracked job to reach a terminal state (bounded wait; "
                "the server never blocks indefinitely)."
            ),
            schema={
                "type": "object",
                "properties": {
                    "job_id": _str("job_id", "Slurm job ID"),
                    "timeout_seconds": _int("timeout_seconds", "Max wait (capped by server config)"),
                    "poll_interval": _int("poll_interval", "Seconds between polls", 10),
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=jobs_wait,
            read_only=True,
            idempotent=True,
        )
    )

    async def jobs_diagnose(args: dict) -> Any:
        return await slurm.diagnose(
            _str_arg(args, "job_id"),
            stdout_lines=_int_arg(args, "stdout_lines", minimum=0),
            stderr_lines=_int_arg(args, "stderr_lines", minimum=0),
            include_accounting=_bool_arg(args, "include_accounting", default=True),
            include_error_scan=_bool_arg(args, "include_error_scan", default=True),
        )

    tools.append(
        ToolDef(
            name="hpc.jobs.diagnose",
            description=(
                "One-call diagnosis of a tracked job: current state, exit code, "
                "accounting, bounded stdout/stderr tails and a hint scan for "
                "common failure signatures (OOM, segfault, timeout, GPU/MPI "
                "errors, missing files). Prefer this over sequencing "
                "status + output + accounting + read yourself. All queries are "
                "restricted to the given owned job and every tail is capped."
            ),
            schema={
                "type": "object",
                "properties": {
                    "job_id": _str("job_id", "Slurm job ID"),
                    "stdout_lines": _int("stdout_lines", "Approx lines of stdout tail (capped)"),
                    "stderr_lines": _int("stderr_lines", "Approx lines of stderr tail (capped)"),
                    "include_accounting": {"type": "boolean", "default": True},
                    "include_error_scan": {"type": "boolean", "default": True},
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=jobs_diagnose,
            read_only=True,
        )
    )

    async def jobs_wait_and_diagnose(args: dict) -> Any:
        return await slurm.wait_and_diagnose(
            _str_arg(args, "job_id"),
            timeout_seconds=_int_arg(args, "timeout_seconds", minimum=0),
            poll_interval=_int_arg(args, "poll_interval", 10, minimum=1) or 10,
            stdout_lines=_int_arg(args, "stdout_lines", minimum=0),
            stderr_lines=_int_arg(args, "stderr_lines", minimum=0),
        )

    tools.append(
        ToolDef(
            name="hpc.jobs.wait_and_diagnose",
            description=(
                "Wait for a tracked job to finish, then run the full diagnosis "
                "(state, exit code, accounting, bounded stdout/stderr tails, "
                "error signatures) in the same call. Use this instead of "
                "sequencing wait + status + output + accounting yourself. "
                "Bounded by the server wait limit and tail caps."
            ),
            schema={
                "type": "object",
                "properties": {
                    "job_id": _str("job_id", "Slurm job ID"),
                    "timeout_seconds": _int("timeout_seconds", "Max wait (capped by server config)"),
                    "poll_interval": _int("poll_interval", "Seconds between polls", 10),
                    "stdout_lines": _int("stdout_lines", "Approx lines of stdout tail (capped)"),
                    "stderr_lines": _int("stderr_lines", "Approx lines of stderr tail (capped)"),
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=jobs_wait_and_diagnose,
            read_only=True,
        )
    )

    async def project_snapshot(args: dict) -> Any:
        return await projects_svc.snapshot(
            _str_arg(args, "path"),
            depth=_int_arg(args, "depth", minimum=1),
            include_git=_bool_arg(args, "include_git", default=True),
            include_jobs=_bool_arg(args, "include_jobs", default=True),
        )

    tools.append(
        ToolDef(
            name="hpc.project.snapshot",
            description=(
                f"One bounded call to establish project context inside {root}: a "
                "shallow directory overview (depth-capped), sizes of interesting "
                "source/log files, a read-only git status summary and the tracked "
                "jobs of this project. Use this ONCE at the start instead of "
                "sequencing list + read + git status + queue. The snapshot never "
                "scans the whole project recursively."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "depth": _int("depth", "Directory depth to enumerate (clamped by server)"),
                    "include_git": {"type": "boolean", "default": True},
                    "include_jobs": {"type": "boolean", "default": True},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=project_snapshot,
            read_only=True,
            idempotent=True,
        )
    )

    # -------------------------------------------------------------- topology
    if cfg.topology.enabled:
        topo_svc = topology or TopologyService(cfg, files, safe_exec, slurm)

        async def cluster_topo(args: dict) -> Any:
            return await topo_svc.probe(
                partition=_str_arg(args, "partition", required=False),
                refresh=_bool_arg(args, "refresh", default=False),
            )

        tools.append(
            ToolDef(
                name="hpc.cluster.topo",
                description=(
                    "Discover the real compute-node hardware and Slurm view needed for "
                    "parallel-optimization decisions: CPU model/vendor, sockets x cores x "
                    "threads, SIMD capability (AVX2/AVX-512/SVE), NUMA domains + distances, "
                    "cache hierarchy, node memory, and the partition's nodes/features/GRES. "
                    "It also returns recommended parallel parameters (ntasks_per_node, "
                    "cpus_per_task, --cpu-bind/--hint flags, OMP_NUM_THREADS) with the "
                    "reasoning behind them. The login node cannot answer this (lscpu/numactl "
                    "are not whitelisted there), so the first call -- or any call after the "
                    "cache expires, or with refresh=true -- submits ONE tiny probe job "
                    f"(1 CPU, at most {cfg.topology.collect_time_limit} wall time) on the chosen "
                    "partition and parses its output. Results are cached (session + a JSON file "
                    "under the user root, default 24h), so repeated calls do NOT queue new jobs. If the "
                    "probe is still queued the call returns status='pending' with the job_id; "
                    "call it again later to pick that job up instead of submitting another."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "partition": _str(
                            "partition",
                            "Partition to probe (default: the first partition in the server allow-list)",
                        ),
                        "refresh": {
                            "type": "boolean",
                            "default": False,
                            "description": "Force a new probe job, ignoring cached topology",
                        },
                    },
                    "additionalProperties": False,
                },
                handler=cluster_topo,
                read_only=True,
                idempotent=True,
                open_world=True,
            )
        )

    return tools

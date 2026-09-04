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
from ..errors import PolicyDenied
from ..filesystem.service import FileService
from ..filesystem.transfer import TransferService
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
) -> list[ToolDef]:
    root = cfg.root

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
        info.update(
            {
                "host": cfg.ssh.host,
                "user": cfg.ssh.user,
                "working_root": root,
                "allowed_partitions": cfg.slurm.allowed_partitions,
            }
        )
        return info

    tools.append(
        ToolDef(
            name="hpc.info",
            description=(
                "Get HPC connection info: host, user, sandboxed working root, "
                "whether Slurm is available, and the cluster name."
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
        )

    tools.append(
        ToolDef(
            name="hpc.files.list",
            description=f"List directory contents on the HPC. Path must be inside {root}.",
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "recursive": {"type": "boolean", "default": False},
                    "max_entries": _int("max_entries", "Cap on returned entries"),
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
                f"Read a remote text file inside {root}. Reads are size-capped; "
                "use offset/max_bytes to page through large files."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "max_bytes": _int("max_bytes", "Max bytes to return"),
                    "offset": _int("offset", "Byte offset to start from", 0),
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=files_read,
            read_only=True,
            idempotent=True,
        )
    )

    async def files_write(args: dict) -> Any:
        path = _str_arg(args, "path")
        content = _str_arg(args, "content")
        return await files.write_file(path, content, append=_bool_arg(args, "append"))

    tools.append(
        ToolDef(
            name="hpc.files.write",
            description=f"Write (or append to) a remote file inside {root}. Size-capped.",
            schema={
                "type": "object",
                "properties": {
                    "path": _str("path", abs_path),
                    "content": _str("content", "Text content to write"),
                    "append": {"type": "boolean", "default": False},
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
    async def slurm_submit(args: dict) -> Any:
        command = args.get("command")
        if not isinstance(command, (str, list)):
            raise PolicyDenied("command must be a string or argv list")
        return await slurm.submit(
            job_name=_str_arg(args, "job_name", required=False) or "job",
            working_directory=_str_arg(args, "working_directory"),
            command=command,
            partition=_str_arg(args, "partition", required=False),
            nodes=_int_arg(args, "nodes", 1, minimum=1) or 1,
            ntasks=_int_arg(args, "ntasks", 1, minimum=1) or 1,
            cpus_per_task=_int_arg(args, "cpus_per_task", 1, minimum=1) or 1,
            memory=args.get("memory"),
            time_limit=_str_arg(args, "time_limit", required=False),
            gpus=_int_arg(args, "gpus", 0, minimum=0) or 0,
            environment=args.get("environment"),
        )

    tools.append(
        ToolDef(
            name="hpc.slurm.submit",
            description=(
                "Submit a compute job to Slurm via sbatch. All computation "
                "(Julia/Python/make/cmake/mpirun/tests) MUST go through this tool "
                "-- never run workloads on the login node. Resources are "
                "policy-limited; job output is captured under .hpc-mcp/jobs/<id>/."
            ),
            schema={
                "type": "object",
                "properties": {
                    "job_name": _str("job_name", "Short job name"),
                    "working_directory": _str("working_directory", f"Job working directory inside {root}"),
                    "command": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                        ],
                        "description": "Program argv, e.g. ['julia','--project=.','test/runtests.jl']",
                    },
                    "partition": _str("partition", "Slurm partition (must be allowed)"),
                    "nodes": _int("nodes", "Node count", 1),
                    "ntasks": _int("ntasks", "Task count", 1),
                    "cpus_per_task": _int("cpus_per_task", "CPUs per task", 1),
                    "memory": _str("memory", "Memory, e.g. '16G'"),
                    "time_limit": _str("time_limit", "Wall limit, e.g. '00:30:00'"),
                    "gpus": _int("gpus", "GPU count", 0),
                    "environment": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["working_directory", "command"],
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

    return tools

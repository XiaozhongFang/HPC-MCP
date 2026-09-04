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


def build_tools(
    cfg: Config,
    ssh: SshManager,
    files: FileService,
    transfer: TransferService,
    safe_exec: SafeExec,
    slurm: SlurmManager,
) -> list[ToolDef]:
    root = cfg.root

    def _str(p: str, desc: str) -> dict:
        return {"type": "string", "description": desc}

    def _int(p: str, desc: str, default: int | None = None) -> dict:
        d: dict = {"type": "integer", "description": desc}
        if default is not None:
            d["default"] = default
        return d

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
            args["path"],
            recursive=bool(args.get("recursive", False)),
            max_entries=args.get("max_entries"),
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
            args["path"], max_bytes=args.get("max_bytes"), offset=int(args.get("offset", 0))
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
        return await files.write_file(args["path"], args["content"], append=bool(args.get("append", False)))

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
        return await files.mkdir(args["path"], parents=bool(args.get("parents", False)))

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
        )
    )

    async def files_delete(args: dict) -> Any:
        return await files.delete(args["path"], recursive=bool(args.get("recursive", False)))

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
        return await transfer.upload(args["local_path"], args["remote_path"])

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
        return await transfer.download(args["remote_path"], args["local_path"])

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
        return await safe_exec.run(args["command"], args.get("cwd"), timeout=args.get("timeout"))

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
        return await slurm.submit(
            job_name=args.get("job_name", "job"),
            working_directory=args["working_directory"],
            command=args["command"],
            partition=args.get("partition"),
            nodes=int(args.get("nodes", 1)),
            ntasks=int(args.get("ntasks", 1)),
            cpus_per_task=int(args.get("cpus_per_task", 1)),
            memory=args.get("memory"),
            time_limit=args.get("time_limit"),
            gpus=int(args.get("gpus", 0)),
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
        return await slurm.status(str(args["job_id"]))

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
            str(args["job_id"]), stream=args.get("stream", "stdout"), tail_bytes=args.get("tail_bytes")
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
        return await slurm.cancel(str(args["job_id"]))

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
        return await slurm.accounting(str(args["job_id"]))

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
            str(args["job_id"]),
            timeout_seconds=args.get("timeout_seconds"),
            poll_interval=int(args.get("poll_interval", 10)),
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

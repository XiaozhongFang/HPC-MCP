# HPC-MCP Architecture

HPC-MCP is a stdio MCP server. The MCP protocol is the only data written to
stdout; diagnostics and audit records go to stderr or the configured log file.

```text
MCP client
   |
   v
server.py (dispatch, runtime argument shape, audit, lifecycle)
   |
   +--> tools/registry.py (public tool schemas and adapters)
   |       |
   |       +--> security/* (pure policy decisions)
   |       +--> filesystem / shell / slurm services
   |
   +--> ssh/manager.py and ssh/sftp.py (fixed argv, bounded processes)
               |
               v
           one configured HPC host
```

## Request flow

1. `server.on_call_tool` resolves the tool name and rejects non-object,
   missing, or unknown arguments.
2. The registry adapter validates scalar types and forwards only typed values.
3. A service applies its policy before any mutating remote command:
   `path_policy` canonicalizes remote paths, `command_policy` classifies login
   commands, and `slurm_policy` validates resources.
4. The service asks the SSH transport to execute a fixed argv or a trusted,
   quoted internal wrapper. Transport output is streamed into a byte cap and
   every child process is reaped on success, failure, or timeout.
5. Results are returned as text content. Every call receives an ALLOW/DENY
   audit record with recursive credential redaction.

## Isolation boundaries

The remote `root` is a dedicated per-user directory and may not be `/`. Remote
paths are checked lexically and through remote `realpath`; create operations
also reject an existing symlink destination. Transfer tools have an independent
local `local_root` (the process working directory by default), reject symlink
components and credential filenames, and enforce post-transfer size limits.

Login-node commands are a small allow-list. No shell operators, executable
paths, nested shells, symlink-following flags, or direct Slurm query commands
are accepted. Job inspection is exposed only by Slurm methods that validate the
current process session's `tool_session` entry. Output paths are derived from a
numeric job ID and the canonical jobs directory, never from untrusted metadata.
Slurm writes to a flat `%j.stdout.log`/`%j.stderr.log` staging file because it
cannot create an intermediate directory before opening output; the server then
creates the canonical job directory and links those files below it.

## Deliberate residual risk

The remote host is a shared Unix account, so a process with equivalent account
permissions can race a path between canonicalization and the final command.
The server minimizes this window and refuses symlink targets, but complete race
freedom requires a privileged remote helper using `openat(2)`/`O_NOFOLLOW` or a
separate Unix account per user. Deploy that helper when hostile same-account
users are in scope. The JSON tracking file is likewise a logical session
boundary, not an operating-system access-control boundary: an equivalent Unix
account can read or modify it. Use separate Unix accounts or a privileged
metadata service when peers sharing the account are considered hostile.

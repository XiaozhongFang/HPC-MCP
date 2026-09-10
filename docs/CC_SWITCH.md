# CC-Switch setup (MCP JSON)

[CC-Switch](https://github.com/farion1231/cc-switch) manages multiple MCP configurations as **JSON**
(Claude Code / Codex / Roo Code, …) and switches between them with one click.

hpc-mcp is a **stdio** MCP server: describe it in CC-Switch with `type: "stdio"` plus
`command` + `args` + `env`. It does **not** need a `url` (that is only for SSE/HTTP servers).

> Other languages: [简体中文](zh-CN/CC_SWITCH.md) | [日本語](ja/CC_SWITCH.md) | [한국어](ko/CC_SWITCH.md) | [繁體中文](zh-TW/CC_SWITCH.md)

---

## Complete configuration example

When adding a new entry in CC-Switch, paste the JSON below (change the values to match your cluster):

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/alice/venvs/hpc-mcp/bin/hpc-mcp",
  "args": ["--config", "/home/alice/.config/hpc-mcp/192.168.12.12.yaml"],
  "env": {
    "HPC_MCP_HOST": "hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice/yourhome",
    "HPC_MCP_LOCAL_ROOT": "/home/alice/project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug",
    "HPC_MCP_SSH_BIN": "/mnt/c/Windows/System32/OpenSSH/ssh.exe",
    "HPC_MCP_SFTP_BIN": "/mnt/c/Windows/System32/OpenSSH/sftp.exe",
    "HPC_MCP_PORT": "22",
    "HPC_MCP_LOG_FILE": "/home/alice/.local/share/hpc-mcp/hpc-mcp.log",
    "HPC_MCP_LOG_LEVEL": "INFO"
  }
}
```

> If `hpc-mcp` lives in a conda/venv that is not on PATH, point `command` at the executable's
> full path, e.g. `"/home/alice/venvs/hpc-mcp/bin/hpc-mcp"`.
> The repository's `scripts/hpc-mcp-run` launcher locates the local install automatically and is the most portable choice.

---

## Field reference

| Field | Required | Description |
|---|---|---|
| `name` | yes | Server name shown in CC-Switch; anything you like (e.g. `hpc-mcp`) |
| `type` | yes | Always `"stdio"` (hpc-mcp talks to the client over stdin/stdout) |
| `command` | yes | Launch command. Recommended: `<repo>/scripts/hpc-mcp-run` (locates the installed hpc-mcp), or the absolute path of the hpc-mcp executable |
| `args` | optional | Extra CLI arguments, e.g. `["--check"]` for a connectivity self-check only; use `[]` or omit for normal operation |
| `env` | yes (at least host/root) | Environment variables to inject, see below |

### Common `env` variables (identical to the "Configuration" section of the README)

| Variable | Required | Purpose |
|---|---|---|
| `HPC_MCP_HOST` | **yes** | HPC host (IP or a `~/.ssh/config` `Host` alias), e.g. `my-hpc` |
| `HPC_MCP_ROOT` | **yes** | **Remote** sandbox root; the agent may only touch files below it (e.g. `/home/shared_account/alice`) |
| `HPC_MCP_USER` | recommended | SSH login user (the shared account) |
| `HPC_MCP_LOCAL_ROOT` | optional | **Local** directories allowed for upload/download (several separated by commas → `HPC_MCP_LOCAL_ROOTS`); defaults to the startup directory + `/tmp` |
| `HPC_MCP_ALLOWED_PARTITIONS` | recommended | Allowed Slurm partitions, comma-separated. **Empty by default = deny every submission (fail-closed)** |
| `HPC_MCP_PORT` | optional | SSH port (default 22) |
| `HPC_MCP_IDENTITY_FILE` | optional | Private-key path (default: via `~/.ssh/config`) |
| `HPC_MCP_SSH_BIN` | optional | ssh executable, e.g. `/usr/bin/ssh`, `@/mnt/c/Windows/System32/OpenSSH/ssh.exe` (needed in WSL) |
| `HPC_MCP_SFTP_BIN` | optional | sftp executable (same forms as `HPC_MCP_SSH_BIN`) |
| `HPC_MCP_LOG_FILE` | optional | Path to persist logs |
| `HPC_MCP_LOG_LEVEL` | optional | `DEBUG`/`INFO`/`WARNING`/`ERROR`, default `INFO` |

> More parameters (Slurm resource ceilings, shell allow-list, file budgets, cache TTL, …) belong to the
> **config file/CLI**; CC-Switch's `env` only covers the ones above. The full list is in
> [`config/example.yaml`](../config/example.yaml) and [`CONFIGURATION.md`](CONFIGURATION.md), with a walkthrough in [`QUICKSTART.md`](QUICKSTART.md).

---

## Before you start

1. **Set up passwordless login**: hpc-mcp forces `BatchMode=yes` and never prompts for a password. Verify manually:
   ```bash
   ssh -o BatchMode=yes my-hpc "echo OK"
   ```
   It must print `OK`; otherwise run `ssh-copy-id my-hpc` first.
2. **Make sure the network is reachable**: private IPs (e.g. `192.168.x.x`) need the VPN, or a `ProxyJump`
   jump host in `~/.ssh/config`.
3. **Self-check first**: after putting `"HPC_MCP_HOST"` and friends into `env`, you can temporarily set
   `command` + `args: ["--check"]` to verify connectivity, then switch back to normal mode.

---

## FAQ

| Symptom | Cause and fix |
|---|---|
| Stops at `starting: ...` and does nothing | **Normal**: a stdio server waiting for client input; call tools from the client |
| Every tool DENIED with `No Slurm partitions are allowed` | `HPC_MCP_ALLOWED_PARTITIONS` missing or empty → set your real partition names (`sinfo` on the cluster) |
| Connection reports `Permission denied` | No key auth → `ssh-copy-id` |
| Reports `Connection timed out` | Network unreachable (private IP needs VPN/jump host) |
| WSL cannot connect although the CLI can | `HPC_MCP_SSH_BIN`/`HPC_MCP_SFTP_BIN` must point at the Windows OpenSSH (`/mnt/c/...`) |
| First connection asks `Are you sure ... yes/no?` | Host key not pinned → connect manually once, or set `strict_host_key_checking: accept-new` |

The full troubleshooting table is in [QUICKSTART.md](QUICKSTART.md) section 7.

---

## Related files

- Standard project-level config: [`.mcp.json`](../.mcp.json) (MCP standard format, an `mcpServers` object)
- Authoritative parameter reference: [`config/example.yaml`](../config/example.yaml)
- Install and troubleshooting: [`QUICKSTART.md`](QUICKSTART.md)

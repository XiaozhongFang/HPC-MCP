"""Shared exception types for hpc-mcp.

All errors that reach a tool handler are converted into structured,
actionable denial/error messages.  The ``user_message`` attribute carries
the text shown to the agent; it must never leak secrets.
"""

from __future__ import annotations


class HpcMcpError(Exception):
    """Base class for all hpc-mcp errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class ConfigError(HpcMcpError):
    """Configuration is missing or invalid."""


class PolicyDenied(HpcMcpError):
    """A security policy denied the requested operation."""

    def __init__(
        self,
        reason: str,
        *,
        requested: str | None = None,
        use_instead: str | None = None,
        scope: str | None = None,
    ) -> None:
        parts = ["Operation denied.", "", "Reason:", reason]
        if requested:
            parts += ["", "Requested:", requested]
        if scope:
            parts += ["", "Allowed scope:", scope]
        if use_instead:
            parts += ["", "Use:", use_instead]
        super().__init__("\n".join(parts))
        self.reason = reason


class PathSandboxError(PolicyDenied):
    """Path escapes the configured user root sandbox."""


class CommandPolicyError(PolicyDenied):
    """Command is not allowed by the login-node command policy."""


class SlurmPolicyError(PolicyDenied):
    """Slurm request violates the configured resource policy."""


class SshError(HpcMcpError):
    """SSH/SFTP transport failed."""


class RemoteCommandError(HpcMcpError):
    """A remote command exited with a non-zero status."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code

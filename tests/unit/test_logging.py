"""Logging configuration and audit-trail tests.

The audit trail has to reach disk: MCP clients normally hide the server's
stderr, so an attached file handler is the only record of what the agent was
allowed to do that can still be reviewed after the session.
"""

import logging

import pytest

from hpc_mcp import logging as logging_mod
from hpc_mcp.logging import AuditLogger, setup_logging

_LOGGER_NAME = "hpc_mcp"


@pytest.fixture
def clean_logger(monkeypatch):
    """Isolate the module-level logger so each test configures it from scratch."""
    logger = logging.getLogger(_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    for handler in saved_handlers:
        logger.removeHandler(handler)
    monkeypatch.setattr(logging_mod, "_configured", False)
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        logger.addHandler(handler)
    logger.setLevel(saved_level)


def _file_handlers(logger):
    return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]


class TestAuditLogFile:
    def test_audit_record_is_written_to_file(self, tmp_path, clean_logger):
        log_file = tmp_path / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        AuditLogger().record(
            tool="hpc.files.list", decision="ALLOW", args={"path": "/root/proj"}, duration=0.5
        )
        text = log_file.read_text(encoding="utf-8")
        assert "AUDIT" in text
        assert "tool=hpc.files.list" in text
        assert "decision=ALLOW" in text
        assert "duration=0.50s" in text

    def test_missing_parent_directories_are_created(self, tmp_path, clean_logger):
        log_file = tmp_path / "nested" / "deeper" / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        AuditLogger().record(tool="hpc.info", decision="ALLOW")
        assert log_file.is_file()
        assert "tool=hpc.info" in log_file.read_text(encoding="utf-8")

    def test_cache_hit_note_is_accepted_and_recorded(self, tmp_path, clean_logger):
        """The cache-hit path calls record(note=...) -- that must not raise."""
        log_file = tmp_path / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        AuditLogger().record(
            tool="hpc.slurm.status", decision="ALLOW", duration=0.0, note="cache hit"
        )
        text = log_file.read_text(encoding="utf-8")
        assert "note=" in text
        assert "cache hit" in text

    def test_denied_calls_record_the_reason(self, tmp_path, clean_logger):
        log_file = tmp_path / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        AuditLogger().record(
            tool="hpc.shell.run_safe", decision="DENY", reason="not in the allow-list"
        )
        text = log_file.read_text(encoding="utf-8")
        assert "decision=DENY" in text
        assert "reason=" in text
        assert "not in the allow-list" in text

    def test_secrets_never_reach_the_log_file(self, tmp_path, clean_logger):
        log_file = tmp_path / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        AuditLogger().record(
            tool="hpc.files.write", decision="ALLOW", args={"content": "password=hunter2"}
        )
        text = log_file.read_text(encoding="utf-8")
        assert "hunter2" not in text
        assert "[REDACTED]" in text


class TestSetupLogging:
    def test_later_call_still_attaches_the_configured_file(self, tmp_path, clean_logger):
        """A first call without a path (the CLI mcp-add path) must not swallow
        the log file that is configured afterwards."""
        setup_logging("INFO")
        assert not _file_handlers(clean_logger)

        log_file = tmp_path / "nested" / "hpc-mcp.log"
        setup_logging("DEBUG", str(log_file))

        assert [h.baseFilename for h in _file_handlers(clean_logger)] == [str(log_file)]
        assert clean_logger.level == logging.DEBUG

    def test_same_path_is_not_attached_twice(self, tmp_path, clean_logger):
        log_file = tmp_path / "hpc-mcp.log"
        setup_logging("INFO", str(log_file))
        setup_logging("INFO", str(log_file))
        assert len(_file_handlers(clean_logger)) == 1

    def test_unusable_log_file_fails_soft(self, tmp_path, clean_logger):
        """A log path that cannot be opened must not stop the server from
        starting -- the audit trail degrades to stderr."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        setup_logging("INFO", str(blocker / "hpc-mcp.log"))
        assert not _file_handlers(clean_logger)

    def test_repeated_calls_do_not_stack_handlers(self, clean_logger):
        setup_logging("INFO")
        after_first = len(clean_logger.handlers)
        setup_logging("INFO")
        assert len(clean_logger.handlers) == after_first

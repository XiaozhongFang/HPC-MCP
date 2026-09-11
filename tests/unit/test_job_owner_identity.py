"""Tests for restart-stable, explicitly configured job ownership."""

from hpc_mcp.config import Config, SshConfig, build_config
from hpc_mcp.slurm.jobs import JobTracker


class _NoopSsh:
    async def realpath(self, path):
        return path


def test_configured_owner_id_is_loaded_and_validated(monkeypatch):
    monkeypatch.setenv("HPC_MCP_HOST", "h")
    monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/me")
    monkeypatch.setenv("HPC_MCP_JOB_OWNER_ID", "mas1998-client-a")
    cfg = build_config(type("Args", (), {})())
    assert cfg.job_owner_id == "mas1998-client-a"


def test_owner_id_rejects_path_or_control_characters(monkeypatch):
    monkeypatch.setenv("HPC_MCP_HOST", "h")
    monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/me")
    monkeypatch.setenv("HPC_MCP_JOB_OWNER_ID", "../foreign")
    try:
        build_config(type("Args", (), {})())
    except Exception as error:
        assert "job_owner_id" in str(error)
    else:
        raise AssertionError("unsafe owner id was accepted")


def test_same_explicit_owner_survives_tracker_restart():
    cfg = Config(
        root="/home/u/me",
        ssh=SshConfig(host="h", user="u"),
        job_owner_id="persistent-client",
    )
    first = JobTracker(cfg, _NoopSsh())
    second = JobTracker(cfg, _NoopSsh())
    assert first.session_id == "persistent-client"
    assert second.session_id == first.session_id


def test_default_owner_remains_unique_per_tracker():
    cfg = Config(root="/home/u/me", ssh=SshConfig(host="h", user="u"))
    first = JobTracker(cfg, _NoopSsh())
    second = JobTracker(cfg, _NoopSsh())
    assert first.session_id != second.session_id

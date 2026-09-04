"""Config loading tests."""

import os

import pytest

from hpc_mcp.config import build_config
from hpc_mcp.errors import ConfigError


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _base_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("HPC_MCP_"):
            monkeypatch.delenv(k)


class TestBuildConfig:
    def test_cli_minimal(self, monkeypatch):
        _base_env(monkeypatch)
        cfg = build_config(NS(host="h", root="/home/u/me"))
        assert cfg.ssh.host == "h"
        assert cfg.root == "/home/u/me"
        assert cfg.slurm.allowed_partitions == []  # fail-closed

    def test_missing_root_denied(self, monkeypatch):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError, match="root"):
            build_config(NS(host="h"))

    def test_missing_host_denied(self, monkeypatch):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError, match="host"):
            build_config(NS(root="/home/u/me"))

    def test_relative_root_denied(self, monkeypatch):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError):
            build_config(NS(host="h", root="relative/path"))

    def test_env_overrides(self, monkeypatch):
        _base_env(monkeypatch)
        monkeypatch.setenv("HPC_MCP_HOST", "env-host")
        monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/env")
        monkeypatch.setenv("HPC_MCP_ALLOWED_PARTITIONS", "compute, debug")
        monkeypatch.setenv("HPC_MCP_MAX_CPUS", "32")
        cfg = build_config(NS())
        assert cfg.ssh.host == "env-host"
        assert cfg.slurm.allowed_partitions == ["compute", "debug"]
        assert cfg.slurm.max_cpus == 32

    def test_cli_beats_env(self, monkeypatch):
        _base_env(monkeypatch)
        monkeypatch.setenv("HPC_MCP_HOST", "env-host")
        monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/env")
        cfg = build_config(NS(host="cli-host"))
        assert cfg.ssh.host == "cli-host"

    def test_yaml_config(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text(
            "host: yaml-host\nroot: /home/u/yaml\nslurm:\n  allowed_partitions: [compute]\n  max_cpus: 8\n"
        )
        cfg = build_config(NS(config=str(p)))
        assert cfg.ssh.host == "yaml-host"
        assert cfg.slurm.allowed_partitions == ["compute"]
        assert cfg.slurm.max_cpus == 8

    def test_bad_yaml(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text("- a list\n- not a mapping\n")
        with pytest.raises(ConfigError):
            build_config(NS(config=str(p)))

    def test_missing_config_file(self, monkeypatch):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError, match="not found"):
            build_config(NS(config="/nonexistent/x.yaml"))

    def test_bad_env_int(self, monkeypatch):
        _base_env(monkeypatch)
        monkeypatch.setenv("HPC_MCP_HOST", "h")
        monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/me")
        monkeypatch.setenv("HPC_MCP_MAX_CPUS", "lots")
        with pytest.raises(ConfigError):
            build_config(NS())

    def test_root_trailing_slash_stripped(self, monkeypatch):
        _base_env(monkeypatch)
        cfg = build_config(NS(host="h", root="/home/u/me/"))
        assert cfg.root == "/home/u/me"
        assert cfg.jobs_dir == "/home/u/me/.hpc-mcp/jobs"

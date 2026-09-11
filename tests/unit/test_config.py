"""Config loading tests."""

import logging
import os

import pytest

from hpc_mcp.config import _eval_int_expr, build_config
from hpc_mcp.errors import ConfigError


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _base_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("HPC_MCP_"):
            monkeypatch.delenv(k)


class TestBuildConfig:
    def test_explicit_environment_mapping_is_used(self):
        cfg = build_config(
            NS(),
            environ={"HPC_MCP_HOST": "mapped-host", "HPC_MCP_ROOT": "/home/u/mapped"},
        )
        assert cfg.ssh.host == "mapped-host"
        assert cfg.root == "/home/u/mapped"

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

    def test_log_file_defaults_to_a_local_path(self, monkeypatch, tmp_path):
        """Without any setting the audit log lands in a local default path,
        instead of living only on stderr where no one reads it."""
        _base_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = build_config(NS(host="h", root="/home/u/me"))
        assert cfg.log_file == str(tmp_path / ".local/share/hpc-mcp/hpc-mcp.log")
        assert cfg.log_level == "INFO"

    def test_log_file_precedence_cli_env_file(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        config_file = tmp_path / "c.yaml"
        config_file.write_text(
            "host: h\nroot: /home/u/me\nlog_file: /from/config.log\nlog_level: WARNING\n"
        )
        monkeypatch.setenv("HPC_MCP_LOG_FILE", "/from/env.log")
        monkeypatch.setenv("HPC_MCP_LOG_LEVEL", "ERROR")
        cfg = build_config(NS(config=str(config_file)))
        assert cfg.log_file == "/from/env.log"
        assert cfg.log_level == "ERROR"

        cfg = build_config(NS(config=str(config_file), log_file="/from/cli.log", log_level="debug"))
        assert cfg.log_file == "/from/cli.log"
        assert cfg.log_level == "DEBUG"

        monkeypatch.delenv("HPC_MCP_LOG_FILE")
        monkeypatch.delenv("HPC_MCP_LOG_LEVEL")
        cfg = build_config(NS(config=str(config_file)))
        assert cfg.log_file == "/from/config.log"
        assert cfg.log_level == "WARNING"

    def test_explicitly_disabled_log_file_stays_stderr_only(self, monkeypatch, tmp_path):
        """``log_file: ""`` or ``HPC_MCP_LOG_FILE=none`` are the explicit opt-out."""
        _base_env(monkeypatch)
        config_file = tmp_path / "c.yaml"
        config_file.write_text('host: h\nroot: /home/u/me\nlog_file: ""\n')
        cfg = build_config(NS(config=str(config_file)))
        assert cfg.log_file is None

        monkeypatch.setenv("HPC_MCP_LOG_FILE", "none")
        cfg = build_config(NS(host="h", root="/home/u/me"))
        assert cfg.log_file is None

        # An empty env var counts as "unset", so the local default applies.
        monkeypatch.setenv("HPC_MCP_LOG_FILE", "")
        cfg = build_config(NS(host="h", root="/home/u/me"))
        assert cfg.log_file is not None

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

    def test_ssh_bin_cli(self, monkeypatch):
        _base_env(monkeypatch)
        cfg = build_config(NS(host="h", root="/home/u/me", ssh_bin="/usr/bin/ssh", sftp_bin="@/mnt/c/Windows/System32/OpenSSH/ssh.exe"))
        assert cfg.ssh.ssh_bin == "/usr/bin/ssh"
        assert cfg.ssh.sftp_bin == "@/mnt/c/Windows/System32/OpenSSH/ssh.exe"

    def test_ssh_bin_env(self, monkeypatch):
        _base_env(monkeypatch)
        monkeypatch.setenv("HPC_MCP_HOST", "h")
        monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/me")
        monkeypatch.setenv("HPC_MCP_SSH_BIN", "@/usr/bin/ssh")
        cfg = build_config(NS())
        assert cfg.ssh.ssh_bin == "@/usr/bin/ssh"

    def test_ssh_bin_yaml(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text("host: h\nroot: /home/u/me\nssh:\n  ssh_bin: /usr/bin/ssh\n  sftp_bin: /usr/bin/sftp\n")
        cfg = build_config(NS(config=str(p)))
        assert cfg.ssh.ssh_bin == "/usr/bin/ssh"
        assert cfg.ssh.sftp_bin == "/usr/bin/sftp"

    def test_local_roots_default_includes_tmp(self, monkeypatch):
        _base_env(monkeypatch)
        cfg = build_config(NS(host="h", root="/home/u/me"))
        assert isinstance(cfg.local_roots, list)
        assert "/tmp" in cfg.local_roots  # system temp dir always allowed
        assert cfg.local_root == cfg.local_roots[0]  # backwards-compat property

    def test_local_roots_yaml_list(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        d1 = tmp_path / "da"
        d2 = tmp_path / "db"
        d1.mkdir()
        d2.mkdir()
        p = tmp_path / "c.yaml"
        p.write_text(f"host: h\nroot: /home/u/me\nlocal_roots: [{d1}, {d2}]\n")
        cfg = build_config(NS(config=str(p)))
        assert str(d1) in cfg.local_roots
        assert str(d2) in cfg.local_roots
        assert "/tmp" in cfg.local_roots  # temp always appended

    def test_local_root_single_still_works(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        d = tmp_path / "da"
        d.mkdir()
        p = tmp_path / "c.yaml"
        p.write_text(f"host: h\nroot: /home/u/me\nlocal_root: {d}\n")
        cfg = build_config(NS(config=str(p)))
        assert str(d) in cfg.local_roots
        assert "/tmp" in cfg.local_roots

    def test_hyphenated_keys_are_accepted(self, monkeypatch, tmp_path):
        """CLI-style hyphenated keys must work like their snake_case form."""
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text(
            "host: h\n"
            "root: /home/u/me\n"
            "local-root: /tmp\n"
            "ssh-bin: /usr/bin/ssh\n"
            "sftp-bin: /usr/bin/sftp\n"
            "identity-file: ~/.ssh/id_ed25519\n"
            "ssh:\n"
            "  strict-host-key-checking: accept-new\n"
            "slurm:\n"
            "  allowed-partitions: [compute]\n"
            "  max-time: '12:00:00'\n"
        )
        cfg = build_config(NS(config=str(p)))
        assert cfg.ssh.ssh_bin == "/usr/bin/ssh"
        assert cfg.ssh.sftp_bin == "/usr/bin/sftp"
        assert cfg.ssh.identity_file is not None
        assert cfg.ssh.identity_file.endswith("/.ssh/id_ed25519")
        assert cfg.ssh.strict_host_key_checking == "accept-new"
        assert cfg.slurm.allowed_partitions == ["compute"]
        assert cfg.slurm.max_time == "12:00:00"

    def test_snake_case_key_wins_over_hyphenated_alias(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text("host: h\nroot: /home/u/me\nssh_bin: /usr/bin/ssh\nssh-bin: /opt/ssh\n")
        cfg = build_config(NS(config=str(p)))
        assert cfg.ssh.ssh_bin == "/usr/bin/ssh"

    def test_unknown_key_warns(self, monkeypatch, tmp_path, caplog):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text("host: h\nroot: /home/u/me\nssh_bin: /usr/bin/ssh\n")
        with caplog.at_level(logging.WARNING, logger="hpc_mcp"):
            build_config(NS(config=str(p)))
        assert "unknown config key" not in caplog.text

        p2 = tmp_path / "d.yaml"
        p2.write_text("host: h\nroot: /home/u/me\nsssh_bin: /usr/bin/ssh\n")
        with caplog.at_level(logging.WARNING, logger="hpc_mcp"):
            build_config(NS(config=str(p2)))
        assert "sssh_bin" in caplog.text

    def test_bad_nested_section_is_config_error(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        p = tmp_path / "c.yaml"
        p.write_text("host: h\nroot: /home/u/me\nslurm: []\n")
        with pytest.raises(ConfigError, match="slurm"):
            build_config(NS(config=str(p)))

    def test_root_filesystem_denied(self, monkeypatch):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError, match="filesystem root"):
            build_config(NS(host="h", root="/"))


class TestArithExpressions:
    def test_max_cpus_expression(self, monkeypatch, tmp_path):
        _base_env(monkeypatch)
        d = tmp_path / "da"
        d.mkdir()
        p = tmp_path / "c.yaml"
        p.write_text(f"host: h\nroot: /home/u/me\nslurm:\n  max_cpus: '4*16'\n  max_nodes: '64/8'\n  max_memory_mb: '8*(2+1)'\n")
        cfg = build_config(NS(config=str(p)))
        assert cfg.slurm.max_cpus == 64
        assert cfg.slurm.max_nodes == 8
        assert cfg.slurm.max_memory_mb == 24

    def test_env_int_expression(self, monkeypatch):
        _base_env(monkeypatch)
        monkeypatch.setenv("HPC_MCP_HOST", "h")
        monkeypatch.setenv("HPC_MCP_ROOT", "/home/u/me")
        monkeypatch.setenv("HPC_MCP_MAX_CPUS", "4*16")
        cfg = build_config(NS())
        assert cfg.slurm.max_cpus == 64

    @pytest.mark.parametrize("expr", ["__import__('os')", "os.system('x')", "1;2", "1+", "**2", "2**3"])
    def test_expression_injection_denied(self, monkeypatch, expr):
        _base_env(monkeypatch)
        with pytest.raises(ConfigError):
            _eval_int_expr(expr, "max_cpus")

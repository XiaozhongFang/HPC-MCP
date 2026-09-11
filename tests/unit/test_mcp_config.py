"""mcp_config (hpc-mcp mcp-add) tests: idempotent, non-destructive writes."""

import pytest
from pathlib import Path

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.mcp_config import (
    _upsert_section,
    mcp_add_codex,
    mcp_add_reasonix,
    _config_env,
)


def make_cfg() -> Config:
    return Config(
        root="/home/shared_account/alice",
        ssh=SshConfig(host="my-hpc", user="alice", port=22),
        local_roots=["/home/alice", "/tmp"],
    )


class TestResolveSelf:
    def test_prefers_repo_launcher(self, monkeypatch):
        from hpc_mcp.mcp_config import _resolve_self

        # repo root = parents[2] of this test file (tests/unit/test_mcp_config.py)
        repo_root = Path(__file__).resolve().parents[2]
        monkeypatch.chdir(repo_root)
        resolved = _resolve_self()
        assert resolved.endswith("scripts/hpc-mcp-run")
        assert Path(resolved).exists()

    def test_fallback_which(self, monkeypatch):
        from hpc_mcp.mcp_config import _resolve_self

        # chdir to a dir without the launcher, PATH without hpc-mcp
        monkeypatch.chdir("/tmp")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        resolved = _resolve_self()
        # should still return something usable (not raise)
        assert resolved


class TestConfigEnv:
    def test_env_mapping(self):
        env = _config_env(make_cfg())
        assert env["HPC_MCP_HOST"] == "my-hpc"
        assert env["HPC_MCP_USER"] == "alice"
        assert env["HPC_MCP_ROOT"] == "/home/shared_account/alice"
        assert env["HPC_MCP_LOCAL_ROOTS"] == "/home/alice,/tmp"

    def test_restart_stable_owner_is_forwarded(self):
        cfg = Config(
            root="/home/shared_account/alice",
            job_owner_id="persistent-client",
            ssh=SshConfig(host="my-hpc", user="alice", port=22),
            local_roots=["/home/alice", "/tmp"],
        )
        env = _config_env(cfg)
        assert env["HPC_MCP_JOB_OWNER_ID"] == "persistent-client"


class TestUpsertSection:
    def test_append_when_missing(self):
        text = 'model = "x"\n'
        out = _upsert_section(text, "[mcp_servers.hpc]", "[mcp_servers.hpc]\ncommand = \"/x\"\n")
        assert out.count("[mcp_servers.hpc]") == 1
        assert 'command = "/x"' in out
        assert 'model = "x"' in out

    def test_replace_keeps_subtables(self):
        text = (
            'model = "x"\n'
            "[mcp_servers.hpc]\n"
            'command = "/old"\n'
            "[mcp_servers.hpc.env]\n"
            'A = "1"\n'
            "[mcp_servers.other]\n"
            'command = "y"\n'
        )
        out = _upsert_section(text, "[mcp_servers.hpc]", "[mcp_servers.hpc]\ncommand = \"/new\"\n")
        assert 'command = "/new"' in out
        assert 'command = "/old"' not in out
        assert out.count("[mcp_servers.hpc]") == 1
        # the .env sub-table belongs to the replaced section -> gone
        assert "[mcp_servers.hpc.env]" not in out
        # sibling section preserved
        assert "[mcp_servers.other]" in out and 'command = "y"' in out

    def test_idempotent_replace(self):
        section = "[mcp_servers.hpc]\ncommand = \"/x\"\n"
        once = _upsert_section('model = "x"\n', "[mcp_servers.hpc]", section)
        twice = _upsert_section(once, "[mcp_servers.hpc]", section)
        assert once.count("[mcp_servers.hpc]") == 1
        assert twice.count("[mcp_servers.hpc]") == 1


class TestCodex:
    def test_add_and_idempotent(self, tmp_path, monkeypatch):
        from hpc_mcp.mcp_config import _CODEX_PATH

        cfg_path = tmp_path / "codex.toml"
        cfg_path.write_text('model = "x"\n[mcp_servers.other]\ncommand = "other"\n')
        monkeypatch.setattr("hpc_mcp.mcp_config._CODEX_PATH", cfg_path)
        mcp_add_codex(make_cfg(), "/opt/hpc-mcp/bin/hpc-mcp")
        mcp_add_codex(make_cfg(), "/opt/hpc-mcp/bin/hpc-mcp")
        text = cfg_path.read_text()
        assert text.count("[mcp_servers.hpc]") == 1
        assert 'command = "/opt/hpc-mcp/bin/hpc-mcp"' in text
        assert 'command = "other"' in text  # untouched


class TestReasonix:
    def test_append_keeps_other_plugins(self, tmp_path, monkeypatch):
        from hpc_mcp.mcp_config import _REASONIX_PATH

        cfg_path = tmp_path / "reasonix.toml"
        cfg_path.write_text('[[plugins]]\nname = "z"\ncommand = "z"\n\n[[plugins]]\nname = "w"\ncommand = "w"\n')
        monkeypatch.setattr("hpc_mcp.mcp_config._REASONIX_PATH", cfg_path)
        mcp_add_reasonix(make_cfg(), "/opt/hpc-mcp/bin/hpc-mcp")
        text = cfg_path.read_text()
        assert 'name = "hpc"' in text
        assert 'name = "z"' in text and 'name = "w"' in text
        assert text.count('name = "hpc"') == 1

    def test_idempotent(self, tmp_path, monkeypatch):
        from hpc_mcp.mcp_config import _REASONIX_PATH

        cfg_path = tmp_path / "reasonix.toml"
        cfg_path.write_text('[[plugins]]\nname = "hpc"\ncommand = "/old"\n')
        monkeypatch.setattr("hpc_mcp.mcp_config._REASONIX_PATH", cfg_path)
        mcp_add_reasonix(make_cfg(), "/opt/hpc-mcp/bin/hpc-mcp")
        text = cfg_path.read_text()
        assert 'command = "/old"' in text  # untouched (already registered)
        assert text.count('name = "hpc"') == 1

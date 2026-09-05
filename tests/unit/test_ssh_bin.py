"""ssh/sftp executable resolution tests."""

import pytest

from hpc_mcp.errors import SshError
from hpc_mcp.ssh.manager import _resolve_bin


class TestResolveBin:
    def test_default_uses_path(self):
        # 'ssh' should resolve to something in PATH on any normal system
        resolved = _resolve_bin(None, "ssh")
        assert resolved and "/" in resolved

    def test_bare_name_uses_path(self):
        assert _resolve_bin("ssh", "ssh") == _resolve_bin(None, "ssh")

    def test_absolute_path_passthrough(self):
        assert _resolve_bin("/usr/bin/ssh", "ssh") == "/usr/bin/ssh"

    def test_at_prefix_stripped(self):
        assert _resolve_bin("@/usr/bin/ssh", "ssh") == "/usr/bin/ssh"
        assert _resolve_bin("@/mnt/c/Windows/System32/OpenSSH/ssh.exe", "ssh") == "/mnt/c/Windows/System32/OpenSSH/ssh.exe"

    def test_missing_path_passthrough(self):
        # explicit paths are returned as-is (existence is checked at runtime,
        # since a path valid in real WSL may not exist in a Linux sandbox)
        assert _resolve_bin("/nonexistent/definitely/not/here", "ssh") == "/nonexistent/definitely/not/here"

    def test_missing_name_raises(self):
        with pytest.raises(SshError):
            _resolve_bin("definitely-not-a-real-cmd-xyz", "ssh")

    def test_tilde_denied(self):
        with pytest.raises(SshError):
            _resolve_bin("~/bin/ssh", "ssh")

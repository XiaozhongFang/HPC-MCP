"""validate_local_path multi-root tests (cwd + system temp dir)."""

import pytest

from hpc_mcp.errors import PathSandboxError
from hpc_mcp.security.path_policy import validate_local_path


class TestMultiRootLocal:
    def test_tmp_allowed(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        out = validate_local_path(str(f), ["/tmp", str(tmp_path)])
        assert out == str(f.resolve())

    def test_under_any_root_allowed(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("x")
        out = validate_local_path(str(f), ["/tmp", str(tmp_path)])
        assert out == str(f.resolve())

    def test_outside_all_roots_denied(self, tmp_path):
        import os

        if os.path.exists("/etc/hostname"):
            with pytest.raises(PathSandboxError):
                validate_local_path("/etc/hostname", [str(tmp_path)])

    def test_single_root_string_still_works(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("x")
        out = validate_local_path(str(f), str(tmp_path))
        assert out == str(f.resolve())

    def test_symlink_denied(self, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("secret")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        with pytest.raises(PathSandboxError):
            validate_local_path(str(link), [str(tmp_path)])

    def test_credential_name_denied(self, tmp_path):
        f = tmp_path / "id_rsa"
        f.write_text("x")
        with pytest.raises(PathSandboxError):
            validate_local_path(str(f), [str(tmp_path)])

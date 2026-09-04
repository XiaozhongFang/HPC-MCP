"""Local transfer paths must be sandboxed just like remote paths."""

import pytest

from hpc_mcp.errors import PathSandboxError
from hpc_mcp.security.path_policy import validate_local_path


def test_relative_path_is_resolved_under_local_root(tmp_path):
    (tmp_path / "project").mkdir()
    assert validate_local_path("project", str(tmp_path)) == str(tmp_path / "project")


@pytest.mark.parametrize("name", ["../outside", "/etc/passwd", ".ssh/id_ed25519", "config.pem", "a\nb"])
def test_local_escape_and_credentials_denied(tmp_path, name):
    with pytest.raises(PathSandboxError):
        validate_local_path(name, str(tmp_path))


def test_symlink_source_denied_even_when_target_is_inside(tmp_path):
    (tmp_path / "real.txt").write_text("ok")
    (tmp_path / "alias.txt").symlink_to(tmp_path / "real.txt")
    with pytest.raises(PathSandboxError, match="Symlink"):
        validate_local_path("alias.txt", str(tmp_path))


def test_download_destination_symlink_denied(tmp_path):
    (tmp_path / "real.txt").write_text("ok")
    (tmp_path / "alias.txt").symlink_to(tmp_path / "real.txt")
    with pytest.raises(PathSandboxError):
        validate_local_path("alias.txt", str(tmp_path), for_write=True)

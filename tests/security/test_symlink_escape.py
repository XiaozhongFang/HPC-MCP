"""Symlink escape tests at the policy level (canonical parent checks)."""

import pytest

from hpc_mcp.errors import PathSandboxError
from hpc_mcp.security.path_policy import check_canonical_parent

ROOT = "/home/shared_account/fangxiaozhong"


class TestSymlinkCanonicalization:
    """Simulates what happens after remote realpath resolves a symlink."""

    def test_link_to_etc_denied(self) -> None:
        # allowed/link -> /etc ; realpath(parent)=/etc
        with pytest.raises(PathSandboxError):
            check_canonical_parent("/etc", "passwd", ROOT)

    def test_link_to_shared_home_denied(self) -> None:
        with pytest.raises(PathSandboxError):
            check_canonical_parent("/home/shared_account/other_user", "secret.txt", ROOT)

    def test_link_inside_root_allowed(self) -> None:
        # allowed/link -> /home/shared_account/fangxiaozhong/data
        candidate = check_canonical_parent(ROOT + "/data", "results.txt", ROOT)
        assert candidate == ROOT + "/data/results.txt"

    def test_link_to_root_itself_allowed(self) -> None:
        candidate = check_canonical_parent(ROOT, "file.txt", ROOT)
        assert candidate == ROOT + "/file.txt"

    def test_deep_escape_denied(self) -> None:
        with pytest.raises(PathSandboxError):
            check_canonical_parent("/usr/lib", "lib.so", ROOT)

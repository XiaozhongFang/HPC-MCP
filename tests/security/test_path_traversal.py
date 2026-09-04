"""Path sandbox lexical validation tests -- must all deny escapes."""

import pytest

from hpc_mcp.errors import PathSandboxError
from hpc_mcp.security.path_policy import check_canonical_parent, is_within, validate_path

ROOT = "/home/shared_account/fangxiaozhong"


class TestAllowedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            ROOT,
            ROOT + "/",
            ROOT + "/project",
            ROOT + "/project/src/a.jl",
            ROOT + "/project/./src/../src/a.jl",
            ROOT + "/a/b/c/d/e/f.txt",
        ],
    )
    def test_allowed(self, path: str) -> None:
        result = validate_path(path, ROOT)
        assert is_within(result, ROOT)

    def test_root_normalizes_trailing_slash(self) -> None:
        assert validate_path(ROOT + "/", ROOT) == ROOT

    def test_dotdot_inside_root_ok(self) -> None:
        p = validate_path(ROOT + "/a/b/../../c", ROOT)
        assert p == ROOT + "/c"


class TestTraversalDenied:
    @pytest.mark.parametrize(
        "path",
        [
            ROOT + "/..",
            ROOT + "/../other_user",
            ROOT + "/project/../../other_user",
            ROOT + "/../../../etc/passwd",
            "/home/shared_account/other_user",
            "/home/shared_account",
            "/etc",
            "/etc/passwd",
            "/tmp",
            "/opt",
            "/usr/bin/julia",
            "/",
            "/home",
        ],
    )
    def test_escape_denied(self, path: str) -> None:
        with pytest.raises(PathSandboxError):
            validate_path(path, ROOT)

    @pytest.mark.parametrize(
        "path",
        [
            "project/file.txt",          # relative
            "./file.txt",
            "../file.txt",
            "~/file.txt",                # tilde -> shared home, must deny
            "~",
            "",
            "/etc/../" + ROOT.lstrip("/") + "/../../etc/shadow",
        ],
    )
    def test_non_absolute_or_tilde_denied(self, path: str) -> None:
        with pytest.raises(PathSandboxError):
            validate_path(path, ROOT)

    @pytest.mark.parametrize("path", ["/a\x00b", ROOT + "/a\nb", ROOT + "/a\rb"])
    def test_forbidden_chars_denied(self, path: str) -> None:
        with pytest.raises(PathSandboxError):
            validate_path(path, ROOT)

    def test_prefix_sibling_not_allowed(self) -> None:
        # /home/shared_account/fangxiaozhong_evil merely shares a string prefix
        with pytest.raises(PathSandboxError):
            validate_path(ROOT + "_evil/x", ROOT)


class TestCanonicalParent:
    def test_parent_inside_ok(self) -> None:
        candidate = check_canonical_parent(ROOT + "/real/dir", "new.txt", ROOT)
        assert candidate == ROOT + "/real/dir/new.txt"

    def test_symlink_escape_denied(self) -> None:
        # realpath(parent) resolved through a symlink to /etc
        with pytest.raises(PathSandboxError):
            check_canonical_parent("/etc", "shadow", ROOT)

    def test_symlink_to_shared_home_denied(self) -> None:
        with pytest.raises(PathSandboxError):
            check_canonical_parent("/home/shared_account/other_user", "x", ROOT)

    @pytest.mark.parametrize("name", ["..", ".", "a/b", "", "a\x00b"])
    def test_bad_basename_denied(self, name: str) -> None:
        with pytest.raises(PathSandboxError):
            check_canonical_parent(ROOT, name, ROOT)

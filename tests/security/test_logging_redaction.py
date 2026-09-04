from hpc_mcp.logging import sanitize


def test_nested_secret_keys_are_redacted():
    result = sanitize({"environment": {"API_TOKEN": "top-secret", "PATH": "/bin"}})
    assert "top-secret" not in result
    assert "[REDACTED]" in result
    assert "/bin" in result


def test_control_characters_do_not_break_log_lines():
    result = sanitize({"value": "ok\nINJECTED"})
    assert "\nINJECTED" not in result
    assert "\\x0a" in result

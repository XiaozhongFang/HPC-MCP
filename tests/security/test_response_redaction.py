"""Agent-response redaction tests: minimal, diagnostic-safe."""

from hpc_mcp.response_redactor import redact_response


class TestResponseRedactor:
    def test_password_assignment_redacted(self):
        out = redact_response({"content": "export PASSWORD=super-secret123\n"})
        assert "super-secret123" not in out["content"]
        assert "[REDACTED]" in out["content"]

    def test_token_and_api_key_redacted(self):
        out = redact_response(
            {
                "content": (
                    "API_KEY=abc123def456\n"
                    "token: xyz789\n"
                    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.sig\n"
                )
            }
        )
        assert "abc123def456" not in out["content"]
        assert "xyz789" not in out["content"]
        assert "eyJhbGciOiJIUzI1NiJ9.sig" not in out["content"]

    def test_private_key_block_redacted(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\n"
        out = redact_response({"content": text})
        assert "MIIEowIBAAKCAQEA" not in out["content"]
        assert "BEGIN RSA PRIVATE KEY" not in out["content"]

    def test_aws_credentials_redacted(self):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\naws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
        out = redact_response({"content": text})
        assert "AKIAIOSFODNN7EXAMPLE" not in out["content"]
        assert "wJalrXUtnFEMI" not in out["content"]

    def test_normal_log_content_preserved(self):
        """Scientific log content must survive untouched (no over-redaction)."""
        text = "ERROR: mesh 1024 failed at timestep 42; newton iterations 7; residual 1.2e-05\n"
        out = redact_response({"content": text})
        assert out["content"] == text

    def test_nested_payload_redacted(self):
        out = redact_response({"job": {"stderr_tail": {"content": "password=hunter2\n"}}})
        assert "hunter2" not in out["job"]["stderr_tail"]["content"]

    def test_redaction_marks_value_only(self):
        """The secret key name stays (it is useful for diagnosis), only the
        value is masked."""
        out = redact_response({"content": "token=abc\n"})
        assert "token" in out["content"]
        assert "abc" not in out["content"]

    def test_bounded_recursion(self):
        payload = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": "secret=deep\n"}}}}}}}}}
        out = redact_response(payload, max_depth=4)
        # beyond depth the whole subtree is replaced, no exception raised
        assert isinstance(out, dict)

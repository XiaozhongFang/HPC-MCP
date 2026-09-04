"""SSH failure diagnosis: raw stderr must become an actionable message."""

from hpc_mcp.config import Config, SshConfig
from hpc_mcp.ssh.manager import SshManager


def make() -> SshManager:
    cfg = Config(root="/home/u/me", ssh=SshConfig(host="192.168.10.10", port=22, user="u"))
    return SshManager(cfg)


class TestDiagnose:
    def _d(self, stderr: str) -> str:
        return make()._diagnose_ssh_failure(stderr)

    def test_timeout_says_network(self):
        msg = self._d("ssh: connect to host 192.168.10.10 port 22: Connection timed out")
        assert "网络不通" in msg
        assert "VPN" in msg
        assert "192.168.10.10" in msg

    def test_no_route_says_network(self):
        msg = self._d("ssh: connect to host x port 22: No route to host")
        assert "网络不通" in msg

    def test_refused_says_port(self):
        msg = self._d("ssh: connect to host x port 22: Connection refused")
        assert "拒绝" in msg and "22" in msg

    def test_permission_denied_says_key(self):
        msg = self._d("u@x: Permission denied (publickey,password).")
        assert "认证失败" in msg and "ssh-copy-id" in msg

    def test_host_key_says_pin(self):
        msg = self._d("Host key verification failed.")
        assert "主机密钥" in msg and "known_hosts" in msg

    def test_resolve_says_hostname(self):
        msg = self._d("ssh: Could not resolve hostname foo: Name or service not known")
        assert "无法解析主机名" in msg

    def test_unknown_passthrough(self):
        msg = self._d("some weird ssh error")
        assert "无法连接" in msg and "some weird ssh error" in msg

"""2026-09-24 灵安交叉审计 P0 三项修复回归（V1/V2/V3）。

对应两份外部安全审计报告 + 本仓逐行核实后的真漏洞清单：
- V1: LINGCLAUDE_EXTRA_WRITABLE_DIRS env 直通 → bwrap/landlock/Seatland
      可写目录逃逸（/ 或 /etc 挂成沙箱可写）。修复：is_safe_writable_dir
      共享原语（入口 bash.py + provider 纵深）。
- V2: WebFetcher.fetch() 无 SSRF 防御（loopback/RFC1918/link-local/
      metadata 全可达）。修复：_ssrf_guard 请求级防线（全记录判定）。
- V3: sandbox_policy.check_path/check_import startswith 前缀绕过
      （/home/aiexploit 通过 /home/ai；jsonx 通过 json）。
      修复：is_relative_to + 点段对齐。
"""

from __future__ import annotations

import sys
import threading
import http.server
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.engine.web_tools import WebFetcher
from lingclaude.lacp.sandbox_policy import (
    SandboxMode,
    SandboxPolicy,
    is_safe_writable_dir,
)


# ---------- V3: 前缀绕过 ----------

class TestV3PathPrefixBypass:
    def _policy(self) -> SandboxPolicy:
        return SandboxPolicy(
            mode=SandboxMode.RESTRICTED,
            allowed_paths=["/home/ai", "/tmp"],
        )

    def test_sibling_prefix_rejected(self):
        """/home/aiexploit 不得借 /home/ai 前缀通过（PoC 原案）。"""
        assert self._policy().check_path("/home/aiexploit/secret") is False

    def test_trusted_root_itself_allowed(self):
        assert self._policy().check_path("/home/ai") is True

    def test_inside_trusted_root_allowed(self):
        assert self._policy().check_path("/home/ai/lingclaude/x.py") is True

    def test_tmp_inside_allowed(self):
        assert self._policy().check_path("/tmp/abc") is True

    def test_etc_rejected(self):
        assert self._policy().check_path("/etc/passwd") is False

    def test_permissive_still_allows_all(self):
        p = SandboxPolicy(mode=SandboxMode.PERMISSIVE, allowed_paths=["/tmp"])
        assert p.check_path("/etc/passwd") is True

    def test_null_allowed_paths_default_deny(self):
        p = SandboxPolicy(mode=SandboxMode.PARANOID, allowed_paths=None)
        assert p.check_path("/tmp/x") is False


class TestV3ImportPrefixBypass:
    def _policy(self) -> SandboxPolicy:
        return SandboxPolicy(mode=SandboxMode.PARANOID, allowed_imports=["json"])

    def test_exact_allowed(self):
        assert self._policy().check_import("json") is True

    def test_submodule_allowed(self):
        assert self._policy().check_import("json.tool") is True

    def test_sibling_prefix_rejected(self):
        """jsonx 不得借 json 前缀通过。"""
        assert self._policy().check_import("jsonx") is False

    def test_none_allowed_imports_allows_all(self):
        p = SandboxPolicy(mode=SandboxMode.RESTRICTED, allowed_imports=None)
        assert p.check_import("os") is True


# ---------- V1: 可写目录白名单原语 + env 钳制 + provider 纵深 ----------

class TestV1SafeWritableDir:
    @pytest.mark.parametrize("d", [
        "/", "/etc", "/usr", "/usr/bin", "/bin", "/var", "/boot",
        "/proc", "/sys", "/dev",
    ])
    def test_forbidden_roots_rejected(self, d):
        assert is_safe_writable_dir(d) is False

    def test_tmp_allowed(self):
        assert is_safe_writable_dir("/tmp") is True

    def test_home_ai_allowed(self):
        assert is_safe_writable_dir("/home/ai") is True

    def test_inside_home_ai_allowed(self):
        assert is_safe_writable_dir("/home/ai/lingclaude") is True

    def test_sibling_of_trusted_root_rejected(self):
        """/home/aiexploit 不在可信根内 → 拒绝（与 V3 同型攻击面）。"""
        assert is_safe_writable_dir("/home/aiexploit") is False

    def test_relative_rejected(self):
        assert is_safe_writable_dir("relative/path") is False

    def test_empty_rejected(self):
        assert is_safe_writable_dir("") is False

    def test_nonexistent_rejected(self):
        assert is_safe_writable_dir("/home/ai/__definitely_not_exist_xyz__") is False

    def test_symlink_escape_rejected(self, tmp_path):
        """resolve 后命中红线 → 拒绝（符号链接绕过面）。"""
        link = tmp_path / "sbx_link_to_etc"
        link.symlink_to("/etc")
        try:
            assert is_safe_writable_dir(str(link)) is False
        finally:
            link.unlink(missing_ok=True)


class TestV1SandboxCommandClamp:
    """入口钳制：env 显式值经 is_safe_writable_dir 过滤后才进 provider。"""

    class FakeWrapProvider:
        """鸭子注入型 provider（对齐 test_security_sandbox_fixes 既有手法）。"""

        def __init__(self, wrap_fn):
            self._wrap_fn = wrap_fn

        def available(self):
            return True

        def wrap(self, command, working_dir=None, allow_network=False, extra_writable_dirs=None):
            return self._wrap_fn(command, working_dir=working_dir,
                                 allow_network=allow_network,
                                 extra_writable_dirs=extra_writable_dirs)

    def _executor(self, tmp_path):
        from lingclaude.engine.bash import BashExecutor
        return BashExecutor(working_dir=tmp_path)

    def test_env_root_filtered_out(self, tmp_path, monkeypatch):
        ex = self._executor(tmp_path)
        monkeypatch.setenv("LINGCLAUDE_EXTRA_WRITABLE_DIRS", "/")
        from unittest.mock import patch
        captured = {}

        def fake_wrap(command, working_dir=None, allow_network=False, extra_writable_dirs=None):
            captured["dirs"] = extra_writable_dirs
            return command

        ex._sandbox_provider = self.FakeWrapProvider(fake_wrap)
        ex._sandbox_command("echo hi")
        dirs = captured["dirs"] or []
        assert "/" not in dirs

    def test_env_etc_filtered_out(self, tmp_path, monkeypatch):
        ex = self._executor(tmp_path)
        monkeypatch.setenv("LINGCLAUDE_EXTRA_WRITABLE_DIRS", "/etc,/tmp")
        captured = {}

        def fake_wrap(command, working_dir=None, allow_network=False, extra_writable_dirs=None):
            captured["dirs"] = extra_writable_dirs
            return command

        ex._sandbox_provider = self.FakeWrapProvider(fake_wrap)
        ex._sandbox_command("echo hi")
        dirs = captured["dirs"] or []
        assert "/etc" not in dirs
        assert "/tmp" in dirs  # 可信根内合法目录保留

    def test_landlock_backend_depth_defense(self, tmp_path, monkeypatch):
        """纵深：绕过入口直调 landlock provider，恶意目录仍不得出现在可写面。"""
        from lingclaude.engine.sandbox_provider import SeatlandSandboxProvider
        p = SeatlandSandboxProvider()
        monkeypatch.setattr(p, "available", lambda: True)
        out = p.wrap(
            "echo hi",
            working_dir=tmp_path,
            extra_writable_dirs=["/", "/etc"],
        )
        assert "path-substring '/'" not in out
        assert "path-substring '/etc'" not in out


# ---------- V2: SSRF 门 ----------

def _start_local_http() -> tuple[http.server.HTTPServer, int]:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SECRET")

        def log_message(self, *a):  # 静默
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TestV2SSRFGuard:
    @pytest.fixture(autouse=True)
    def _srv(self):
        self._server, self.port = _start_local_http()
        yield
        self._server.shutdown()

    def _fetch(self, url: str):
        return WebFetcher(timeout=5).fetch(url)

    def test_loopback_ip_blocked(self):
        r = self._fetch(f"http://127.0.0.1:{self.port}/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_localhost_name_blocked(self):
        r = self._fetch(f"http://localhost:{self.port}/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_metadata_ip_blocked(self):
        r = self._fetch("http://169.254.169.254/latest/meta-data/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_metadata_gcp_hostname_blocked(self):
        r = self._fetch("http://metadata.google.internal/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    @pytest.mark.parametrize("ip", ["http://10.0.0.1/", "http://172.16.0.1/", "http://192.168.1.1/"])
    def test_rfc1918_blocked(self, ip):
        r = self._fetch(ip)
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_ipv6_loopback_blocked(self):
        r = self._fetch(f"http://[::1]:{self.port}/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_unspecified_blocked(self):
        r = self._fetch("http://0.0.0.0/")
        assert not r.is_ok and r.code == "SSRF_BLOCKED"

    def test_guard_admits_public_ip_literal(self):
        """公网 IP 字面量过门（连接可达与否不属门管辖——门只管地址合法性）。"""
        reason = WebFetcher._ssrf_guard("http://93.184.216.34/")
        assert reason is None

    def test_non_http_scheme_still_rejected_first(self):
        r = self._fetch("file:///etc/passwd")
        assert not r.is_ok and r.code == "INVALID_URL"

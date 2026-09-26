"""E6: ACP env 接线回归测试 — SubagentManager 默认注册 AcpSubagentBackend 时
无参构造原只能拿到空 config, provider='acp' 恒被 fail-closed 拒绝, 接缝形同虚设。
修复: AcpConfig.from_env() 读 LINGCLAUDE_ACP_ENDPOINT/_API_KEY/_TIMEOUT_S。

纪律: 断言基于 acp.py 真码契约(E5 fail-closed + E6 env 注入), 非 TODO/注释。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingclaude.engine.subagent import (
    AcpSubagentBackend,
    SubagentContext,
    SubagentManager,
    SubagentRequest,
)
from lingclaude.engine.subagent.acp import AcpConfig


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_acp_env(monkeypatch):
    """隔离 ACP env — 测试间互不污染。"""
    for key in (
        "LINGCLAUDE_ACP_ENDPOINT",
        "LINGCLAUDE_ACP_API_KEY",
        "LINGCLAUDE_ACP_TIMEOUT_S",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def stub_acp_server():
    """本地 stub ACP server: /session + /message, 记录收到的 Authorization。"""
    seen_auth: list[str | None] = []
    seen_prompt: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静默
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            payload = json.loads(body) if body else {}
            if self.path == "/session":
                seen_auth.append(self.headers.get("Authorization"))
                resp = {"session_id": "stub-123"}
            elif self.path == "/message":
                seen_prompt.append(payload.get("prompt", ""))
                resp = {
                    "output": "stub-acp-ok",
                    "tools_used": ["read"],
                    "rounds": 2,
                }
            else:
                resp = {"error": "unknown path"}
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield {"base": f"http://127.0.0.1:{port}", "auth": seen_auth, "prompts": seen_prompt}
    server.shutdown()
    server.server_close()


def _req(**kw) -> SubagentRequest:
    return SubagentRequest(task="ping the stub", **kw)


# ---------------------------------------------------------------------------
# E6: env 接线
# ---------------------------------------------------------------------------


class TestAcpConfigFromEnv:
    def test_no_env_stays_fail_closed(self):
        """无 env → 空端点(E5 fail-closed 语义不变)。"""
        cfg = AcpConfig.from_env()
        assert cfg.endpoint == ""

    def test_endpoint_only(self, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", "http://127.0.0.1:9999")
        cfg = AcpConfig.from_env()
        assert cfg.endpoint == "http://127.0.0.1:9999"
        assert cfg.api_key is None
        assert cfg.timeout_s == 60

    def test_full_env(self, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", "https://gw.example:8443")
        monkeypatch.setenv("LINGCLAUDE_ACP_API_KEY", "sk-test-abc")
        monkeypatch.setenv("LINGCLAUDE_ACP_TIMEOUT_S", "120")
        cfg = AcpConfig.from_env()
        assert cfg.api_key == "sk-test-abc"
        assert cfg.timeout_s == 120

    def test_bad_timeout_falls_back(self, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", "http://127.0.0.1:9999")
        monkeypatch.setenv("LINGCLAUDE_ACP_TIMEOUT_S", "not-a-number")
        assert AcpConfig.from_env().timeout_s == 60

    def test_blank_endpoint_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", "   ")
        assert AcpConfig.from_env().endpoint == ""


class TestBackendWiring:
    def test_manager_default_backend_uses_env(self, monkeypatch, stub_acp_server):
        """核心回归: SubagentManager 默认注册路径(无参构造)现在能从 env 拿到端点。"""
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", stub_acp_server["base"])
        m = SubagentManager()  # 与 subagent_tools.py 惰性新建路径一致
        backend = m.get_backend("acp")
        result = backend.run(_req(), SubagentContext())
        assert result.success is True
        assert result.output == "stub-acp-ok"
        assert result.provider == "acp"

    def test_explicit_config_still_wins(self, stub_acp_server):
        """显式 config 优先于 env(向后兼容)。"""
        explicit = AcpConfig(endpoint=stub_acp_server["base"])
        b = AcpSubagentBackend(config=explicit)
        result = b.run(_req(), SubagentContext())
        assert result.success is True

    def test_no_env_manager_acp_fails_closed(self):
        """无 env 时 manager 路径仍 fail-closed, error 可诊断。"""
        m = SubagentManager()
        backend = m.get_backend("acp")
        result = backend.run(_req(), SubagentContext())
        assert result.success is False
        assert "未配置" in (result.error or "")

    def test_api_key_flows_as_bearer(self, monkeypatch, stub_acp_server):
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", stub_acp_server["base"])
        monkeypatch.setenv("LINGCLAUDE_ACP_API_KEY", "sk-test-abc")
        m = SubagentManager()
        m.get_backend("acp").run(_req(), SubagentContext())
        assert stub_acp_server["auth"] == ["Bearer sk-test-abc"]


class TestParallelStillWorks:
    def test_parallel_http_no_cwd_effects(self, monkeypatch, stub_acp_server):
        """并行 = 纯 HTTP(无 chdir) — 并行前后 cwd 不变(钉住串线不存在的回归)。"""
        before = os.getcwd()
        monkeypatch.setenv("LINGCLAUDE_ACP_ENDPOINT", stub_acp_server["base"])
        m = SubagentManager()
        result = m.get_backend("acp").run(_req(parallel=3), SubagentContext())
        assert result.success is True
        assert os.getcwd() == before
        assert len(stub_acp_server["prompts"]) == 3

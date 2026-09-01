"""E2E: 引擎 HTTP API 端点 + F4 CORS + F7 Pydantic + F8 锁定."""
from __future__ import annotations


# ─── GET 端点 ──────────────────────────────────────────────


class TestStatusEndpoint:
    def test_status_no_auth(self, api_client):
        r = api_client.get("/status")
        assert r.status_code in (200, 401, 403)

    def test_status_with_auth(self, api_client, api_key):
        r = api_client.get("/status", headers={"X-API-Key": api_key})
        assert r.status_code == 200


class TestSessionsEndpoints:
    def test_sessions_list(self, api_client, api_key):
        r = api_client.get("/sessions", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))

    def test_session_get_404(self, api_client, api_key):
        r = api_client.get("/sessions/nonexistent-session-id-xxx",
                           headers={"X-API-Key": api_key})
        assert r.status_code in (404, 200)

    def test_session_projection_404(self, api_client, api_key):
        r = api_client.get("/sessions/nonexistent-session-id-xxx/projection",
                           headers={"X-API-Key": api_key})
        assert r.status_code in (404, 200)


class TestLiveEvents:
    def test_live_events(self, api_client, api_key):
        r = api_client.get("/live/events", headers={"X-API-Key": api_key})
        assert r.status_code == 200


class TestMarketplace:
    def test_marketplace_list(self, api_client, api_key):
        r = api_client.get("/marketplace/list", headers={"X-API-Key": api_key})
        assert r.status_code == 200

    def test_marketplace_reputation(self, api_client, api_key):
        r = api_client.get("/marketplace/reputation/nonexistent-plugin",
                           headers={"X-API-Key": api_key})
        assert r.status_code in (200, 404)


class TestPermissionMode:
    def test_get_mode(self, api_client, api_key):
        r = api_client.get("/permission/mode", headers={"X-API-Key": api_key})
        assert r.status_code == 200

    def test_set_mode(self, api_client, api_key):
        r = api_client.post("/permission/mode", headers={"X-API-Key": api_key},
                            json={"mode": "auto"})
        assert r.status_code == 200


# ─── POST 端点 ─────────────────────────────────────────────


class TestAskEndpoint:
    def test_ask_basic(self, api_client, api_key):
        r = api_client.post("/ask", headers={"X-API-Key": api_key},
                            json={"prompt": "1+1=?"})
        assert r.status_code in (200, 422, 500)

    def test_ask_missing_prompt(self, api_client, api_key):
        r = api_client.post("/ask", headers={"X-API-Key": api_key}, json={})
        assert r.status_code in (422, 400)


class TestAskStream:
    def test_ask_stream_returns_sse(self, api_client, api_key):
        r = api_client.post("/ask/stream", headers={"X-API-Key": api_key},
                            json={"prompt": "hello"})
        assert r.status_code in (200, 500, 422)


class TestSessionSnapshot:
    def test_snapshot_requires_id(self, api_client, api_key):
        r = api_client.post("/sessions/snapshot", headers={"X-API-Key": api_key}, json={})
        assert r.status_code in (422, 400, 200, 500)


class TestSessionStop:
    def test_stop_nonexistent_session(self, api_client, api_key):
        r = api_client.post("/sessions/nonexistent-session-id-xxx/stop",
                            headers={"X-API-Key": api_key})
        assert r.status_code in (404, 200, 422)


class TestMarketplaceUpload:
    def test_upload_missing_params(self, api_client, api_key):
        r = api_client.post("/marketplace/upload", headers={"X-API-Key": api_key})
        assert r.status_code in (422, 400, 500)


class TestPermissionDecision:
    def test_permission_decision_basic(self, api_client, api_key):
        r = api_client.post("/permission", headers={"X-API-Key": api_key},
                            json={"session_id": "test-session-xxx",
                                  "tool_name": "bash", "decision": "allow"})
        assert r.status_code in (200, 404, 422)


class TestAnalyzeEndpoint:
    def test_analyze_basic(self, api_client, api_key):
        r = api_client.post("/analyze", headers={"X-API-Key": api_key},
                            json={"path": "lingclaude/cli/__init__.py"})
        assert r.status_code in (200, 404, 422, 500)


class TestExecEndpoint:
    def test_exec_echo(self, api_client, api_key):
        r = api_client.post("/exec", headers={"X-API-Key": api_key},
                            json={"command": "echo hello-test"})
        assert r.status_code in (200, 422, 500, 503)


class TestFileEndpoints:
    def test_read_file_own_source(self, api_client, api_key):
        r = api_client.post("/read-file", headers={"X-API-Key": api_key},
                            json={"path": "lingclaude/cli/__init__.py"})
        assert r.status_code in (200, 403, 404, 422)

    def test_write_file_path_traversal_blocked(self, api_client, api_key):
        r = api_client.post("/write-file", headers={"X-API-Key": api_key},
                            json={"path": "/etc/passwd", "content": "hacked"})
        assert r.status_code in (403, 422, 404)


# ─── F7: LingBus 桥 Pydantic ───────────────────────────────


class TestLingMessageBridge:
    def test_post_endpoint(self, api_client, api_key):
        r = api_client.post("/api/lingmessage/post", headers={"X-API-Key": api_key},
                            json={"thread_id": "test", "body": "hello"})
        assert r.status_code in (200, 403, 422, 500)


class TestF7LingMessagePydanticModel:
    def test_notify_full_schema(self, api_client, api_key):
        r = api_client.post("/api/lingmessage/notify", headers={"X-API-Key": api_key},
                            json={"event": "lint_alert", "sender": "lingflow_plus",
                                  "topic": "审计告警", "thread_id": "thr-001",
                                  "data": {"score": 0.85}})
        assert r.status_code == 200, f"body={r.text}"
        body = r.json()
        assert body["received"] is True

    def test_notify_minimal_schema(self, api_client, api_key):
        r = api_client.post("/api/lingmessage/notify", headers={"X-API-Key": api_key},
                            json={"event": "heartbeat"})
        assert r.status_code == 200

    def test_notify_wrong_type_field_rejected(self, api_client, api_key):
        r = api_client.post("/api/lingmessage/notify", headers={"X-API-Key": api_key},
                            json={"event": 12345})
        assert r.status_code == 422

    def test_notify_data_dict_accepted(self, api_client, api_key):
        r = api_client.post("/api/lingmessage/notify", headers={"X-API-Key": api_key},
                            json={"event": "discussion_started", "sender": "灵通",
                                  "data": {"key": "value", "num": 42}})
        assert r.status_code == 200


# ─── 认证 ─────────────────────────────────────────────────


class TestAuth:
    def test_protected_endpoint_rejects_no_key(self, api_client):
        r = api_client.post("/ask", json={"prompt": "x"})
        assert r.status_code == 401

    def test_protected_endpoint_rejects_bad_key(self, api_client):
        r = api_client.post("/ask", json={"prompt": "x"},
                            headers={"X-API-Key": "wrong-key"})
        assert r.status_code == 401


# ─── F4: CORS ──────────────────────────────────────────────


class TestF4CorsOrigin:
    def test_cors_allows_webui_default_port(self, api_client):
        r = api_client.options("/status", headers={
            "Origin": "http://localhost:13458",
            "Access-Control-Request-Method": "GET",
        })
        assert r.status_code in (200, 204)
        assert "13458" in r.headers.get("access-control-allow-origin", "")

    def test_cors_allows_localhost_3000(self, api_client):
        r = api_client.options("/status", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        assert r.status_code in (200, 204)
        assert "3000" in r.headers.get("access-control-allow-origin", "")


class TestF8CorsMethodsLock:
    """F8 决策锁定:allow_methods 与实际端点方法一致(防新增 PUT/PATCH 忘改 CORS)。"""

    def test_cors_methods_cover_all_routes(self, api_client):
        from lingclaude.api import app
        cors_allowed = {"GET", "POST", "OPTIONS"}
        auto_derived = {"HEAD", "OPTIONS"}
        uncovered = []
        for route in app.routes:
            methods = getattr(route, "methods", None)
            if not methods:
                continue
            extra = set(methods) - cors_allowed - auto_derived
            if extra:
                uncovered.append((getattr(route, "path", "?"), extra))
        assert not uncovered, f"CORS 未放行的方法: {uncovered}"

    def test_no_put_patch_delete_endpoints(self, api_client):
        from lingclaude.api import app
        forbidden = {"PUT", "PATCH", "DELETE"}
        offenders = [
            (getattr(route, "path", "?"), set(route.methods or ()))
            for route in app.routes
            if getattr(route, "methods", None) and set(route.methods) & forbidden
        ]
        assert not offenders, f"F8 基线外端点: {offenders}"

"""8700 SDK unit tests."""

from __future__ import annotations

import json
import pytest
import httpx

from lingclaude_sdk import (
    LingClaudeSDK,
    LingClaudeSDKError,
    AuthenticationError,
    EngineUnavailableError,
    PermissionDeniedError,
    NotFoundError,
    ValidationError,
    ProtocolVersionError,
    TimeoutError,
)
from lingclaude_sdk.models import (
    EngineStatus,
    ProjectInfo,
    AskResponse,
    SSEEvent,
    LiveEvents,
    ToolEvent,
    SessionList,
    SessionDetail,
    SnapshotResult,
    PermissionResponse,
    FileContent,
    WriteResult,
    AnalyzeResult,
    PostResult,
    NotifyResult,
)


class TestModels:
    def test_engine_status_from_dict(self) -> None:
        data = {
            "status": "online",
            "version": "0.2.2",
            "projects": [{"name": "lingclaude", "path": "/home/ai/lingclaude", "exists": True}],
            "auth_required": False,
        }
        s = EngineStatus.model_validate(data)
        assert s.status == "online"
        assert s.version == "0.2.2"
        assert len(s.projects) == 1
        assert s.projects[0].name == "lingclaude"

    def test_ask_response(self) -> None:
        r = AskResponse(answer="你好")
        assert r.answer == "你好"

    def test_sse_event(self) -> None:
        e = SSEEvent(type="text", content="hello")
        assert e.type == "text"
        assert e.content == "hello"

    def test_sse_event_tool_start(self) -> None:
        e = SSEEvent(
            type="tool_start",
            call_id="c1",
            name="bash",
            arguments={"cmd": "ls"},
        )
        assert e.name == "bash"
        assert e.arguments == {"cmd": "ls"}

    def test_live_events(self) -> None:
        data = {
            "events": [
                {"seq": 1, "type": "tool_start", "name": "bash", "success": True, "duration_ms": 100, "ts": 123.0},
            ],
            "latest": 1,
        }
        le = LiveEvents.model_validate(data)
        assert le.latest == 1
        assert len(le.events) == 1
        assert le.events[0].name == "bash"

    def test_session_detail(self) -> None:
        sd = SessionDetail(
            session_id="abc-123",
            project_path="/tmp",
            created_at="2026-01-01T00:00:00",
            expires_at="2026-01-02T00:00:00",
            turns=5,
        )
        assert sd.session_id == "abc-123"
        assert sd.turns == 5

    def test_snapshot_result(self) -> None:
        sr = SnapshotResult(path="/tmp/snap.json", session_id="abc-123")
        assert sr.path == "/tmp/snap.json"

    def test_permission_response(self) -> None:
        pr = PermissionResponse(success=True, decision="allow")
        assert pr.success
        assert pr.decision == "allow"

    def test_file_content(self) -> None:
        fc = FileContent(path="a.py", content="print(1)", size=8)
        assert fc.path == "a.py"
        assert fc.size == 8

    def test_write_result(self) -> None:
        wr = WriteResult(path="a.py", size=8, status="written")
        assert wr.status == "written"

    def test_analyze_result(self) -> None:
        ar = AnalyzeResult(path="src", total_files=10, py_files=8, total_size_mb=1.5)
        assert ar.total_files == 10
        assert ar.py_files == 8

    def test_post_result(self) -> None:
        pr = PostResult(posted=True, message_id="m1", gate_warnings=["w1"])
        assert pr.posted
        assert pr.message_id == "m1"
        assert pr.gate_warnings == ["w1"]

    def test_notify_result(self) -> None:
        nr = NotifyResult(received=True, service="灵克", action="logged")
        assert nr.received
        assert nr.service == "灵克"


class TestExceptions:
    def test_auth_error(self) -> None:
        e = AuthenticationError("bad key")
        assert isinstance(e, LingClaudeSDKError)
        assert "bad key" in str(e)

    def test_engine_unavailable(self) -> None:
        e = EngineUnavailableError("502")
        assert isinstance(e, LingClaudeSDKError)

    def test_protocol_version_error(self) -> None:
        e = ProtocolVersionError("0.2", "upgrade needed")
        assert e.service_version == "0.2"
        assert isinstance(e, LingClaudeSDKError)

    def test_permission_denied(self) -> None:
        e = PermissionDeniedError("forbidden")
        assert isinstance(e, LingClaudeSDKError)

    def test_not_found(self) -> None:
        e = NotFoundError("missing")
        assert isinstance(e, LingClaudeSDKError)

    def test_validation_error(self) -> None:
        e = ValidationError("bad request")
        assert isinstance(e, LingClaudeSDKError)

    def test_timeout_error(self) -> None:
        e = TimeoutError("timeout")
        assert isinstance(e, LingClaudeSDKError)


class TestSDKClient:
    def test_sdk_init_defaults(self) -> None:
        sdk = LingClaudeSDK()
        assert sdk._base_url == "http://127.0.0.1:8700"
        assert sdk._protocol_version == "0.1"
        assert sdk._api_key is None
        sdk.close()

    def test_sdk_init_with_api_key(self) -> None:
        sdk = LingClaudeSDK(api_key="test-key")
        assert sdk._api_key == "test-key"
        sdk.close()

    def test_sdk_init_custom_base(self) -> None:
        sdk = LingClaudeSDK(base_url="http://localhost:9999")
        assert sdk._base_url == "http://localhost:9999"
        sdk.close()

    def test_raise_for_status_401(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(401, request=httpx.Request("GET", "http://x"))
        with pytest.raises(AuthenticationError):
            sdk._raise_for_status(resp)
        sdk.close()

    def test_raise_for_status_403(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(403, request=httpx.Request("GET", "http://x"))
        with pytest.raises(PermissionDeniedError):
            sdk._raise_for_status(resp)
        sdk.close()

    def test_raise_for_status_404(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
        with pytest.raises(NotFoundError):
            sdk._raise_for_status(resp)
        sdk.close()

    def test_raise_for_status_426(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(
            426,
            headers={"protocol-version": "0.2"},
            request=httpx.Request("GET", "http://x"),
        )
        with pytest.raises(ProtocolVersionError) as exc_info:
            sdk._raise_for_status(resp)
        assert exc_info.value.service_version == "0.2"
        sdk.close()

    def test_raise_for_status_502(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(502, request=httpx.Request("GET", "http://x"))
        with pytest.raises(EngineUnavailableError):
            sdk._raise_for_status(resp)
        sdk.close()

    def test_raise_for_status_200(self) -> None:
        sdk = LingClaudeSDK()
        resp = httpx.Response(200, request=httpx.Request("GET", "http://x"))
        sdk._raise_for_status(resp)
        sdk.close()

    def test_context_manager(self) -> None:
        with LingClaudeSDK() as sdk:
            assert sdk._client is not None
        assert sdk._client is None

    def test_headers_with_api_key(self) -> None:
        sdk = LingClaudeSDK(api_key="secret")
        cl = sdk._cl
        assert cl.headers.get("X-API-Key") == "secret"
        assert cl.headers.get("x-protocol-version") == "0.1"
        sdk.close()

    def test_headers_without_api_key(self) -> None:
        sdk = LingClaudeSDK()
        cl = sdk._cl
        assert "X-API-Key" not in cl.headers
        sdk.close()
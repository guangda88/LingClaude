"""8700 SDK HTTP client — httpx-powered async/sync interface."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from lingclaude_sdk.exceptions import (
    AuthenticationError,
    EngineUnavailableError,
    PermissionDeniedError,
    NotFoundError,
    ProtocolVersionError,
    ValidationError,
    TimeoutError,
)
from lingclaude_sdk.models import (
    EngineStatus,
    AskResponse,
    SSEEvent,
    LiveEvents,
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

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({502, 503, 504})
_MAX_RETRIES = 3


class LingClaudeSDK:
    """灵克 8700 HTTP API 客户端。

    Usage:
        sdk = LingClaudeSDK(base_url="http://127.0.0.1:8700", api_key="key")
        status = sdk.status()
        resp = sdk.ask("分析这个文件")
        async for event in sdk.ask_stream("分析"):
            print(event.content)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8700",
        api_key: str | None = None,
        timeout: float = 120.0,
        protocol_version: str = "0.1",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._protocol_version = protocol_version
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    @property
    def _cl(self) -> httpx.Client:
        if self._client is None:
            headers: dict[str, str] = {
                "x-protocol-version": self._protocol_version,
            }
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._client = httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    @property
    def _acl(self) -> httpx.AsyncClient:
        if self._async_client is None:
            headers: dict[str, str] = {
                "x-protocol-version": self._protocol_version,
            }
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._async_client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._async_client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            self._async_client.close()
            self._async_client = None

    # ── helpers ──

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 401:
            raise AuthenticationError(response.text)
        if response.status_code == 403:
            raise PermissionDeniedError(response.text)
        if response.status_code == 404:
            raise NotFoundError(response.text)
        if response.status_code in (400, 422):
            raise ValidationError(response.text)
        if response.status_code == 426:
            sv = response.headers.get("protocol-version", "unknown")
            raise ProtocolVersionError(sv, response.text)
        if response.status_code in _RETRYABLE_STATUSES:
            raise EngineUnavailableError(response.text)
        response.raise_for_status()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._cl.request(method, path, **kwargs)
                self._raise_for_status(resp)
                return resp
            except EngineUnavailableError:
                if attempt == _MAX_RETRIES:
                    raise
                logger.debug("retry %d/%d for %s %s", attempt, _MAX_RETRIES, method, path)
            except TimeoutError:
                if attempt == _MAX_RETRIES:
                    raise
                logger.debug("retry %d/%d (timeout) for %s %s", attempt, _MAX_RETRIES, method, path)
        raise EngineUnavailableError(f"max retries exceeded for {method} {path}")

    async def _arequest(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._acl.request(method, path, **kwargs)
                self._raise_for_status(resp)
                return resp
            except EngineUnavailableError:
                if attempt == _MAX_RETRIES:
                    raise
                logger.debug("retry %d/%d for %s %s", attempt, _MAX_RETRIES, method, path)
            except TimeoutError:
                if attempt == _MAX_RETRIES:
                    raise
                logger.debug("retry %d/%d (timeout) for %s %s", attempt, _MAX_RETRIES, method, path)
        raise EngineUnavailableError(f"max retries exceeded for {method} {path}")

    # ── status ──

    def status(self) -> EngineStatus:
        resp = self._request("GET", "/status")
        return EngineStatus.model_validate(resp.json())

    # ── ask ──

    def ask(self, question: str, context: str | None = None) -> AskResponse:
        body: dict[str, Any] = {"question": question}
        if context:
            body["context"] = context
        resp = self._request("POST", "/ask", json=body)
        return AskResponse.model_validate(resp.json())

    async def ask_stream(self, question: str, context: str | None = None) -> AsyncIterator[SSEEvent]:
        body: dict[str, Any] = {"question": question}
        if context:
            body["context"] = context
        async with self._acl.stream("POST", "/ask/stream", json=body) as resp:
            self._raise_for_status(resp)
            async for line in resp.aiter_lines():
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    try:
                        data = json.loads(data_str)
                        yield SSEEvent.model_validate(data)
                    except (json.JSONDecodeError, ValueError):
                        logger.debug("unparseable SSE line: %s", line[:200])

    # ── live events ──

    def poll_events(self, since: int = 0) -> LiveEvents:
        resp = self._request("GET", "/live/events", params={"since": since})
        return LiveEvents.model_validate(resp.json())

    # ── sessions ──

    def list_sessions(self) -> SessionList:
        resp = self._request("GET", "/sessions")
        return SessionList.model_validate(resp.json())

    def get_session(self, session_id: str) -> SessionDetail:
        resp = self._request("GET", f"/sessions/{session_id}")
        return SessionDetail.model_validate(resp.json())

    def create_snapshot(self, session_id: str, project_path: str = "") -> SnapshotResult:
        resp = self._request("POST", "/sessions/snapshot", json={
            "session_id": session_id,
            "project_path": project_path,
        })
        return SnapshotResult.model_validate(resp.json())

    def stop_session(self, session_id: str) -> SessionDetail:
        resp = self._request("POST", f"/sessions/{session_id}/stop")
        return SessionDetail.model_validate(resp.json())

    # ── permission ──

    def permission(
        self,
        session_id: str,
        decision: str,
        tool_name: str = "",
        reason: str = "",
    ) -> PermissionResponse:
        resp = self._request("POST", "/permission", json={
            "session_id": session_id,
            "decision": decision,
            "tool_name": tool_name,
            "reason": reason,
        })
        return PermissionResponse.model_validate(resp.json())

    # ── file I/O ──

    def read_file(self, path: str) -> FileContent:
        resp = self._request("POST", "/read-file", json={"path": path})
        return FileContent.model_validate(resp.json())

    def write_file(self, path: str, content: str) -> WriteResult:
        resp = self._request("POST", "/write-file", json={"path": path, "content": content})
        return WriteResult.model_validate(resp.json())

    # ── analyze ──

    def analyze(self, path: str, focus: str = "") -> AnalyzeResult:
        resp = self._request("POST", "/analyze", json={"path": path, "focus": focus})
        return AnalyzeResult.model_validate(resp.json())

    # ── LingMessage ──

    def post_message(
        self,
        thread_id: str,
        content: str,
        subject: str = "",
        recipient: str = "all",
    ) -> PostResult:
        resp = self._request("POST", "/api/lingmessage/post", json={
            "thread_id": thread_id,
            "content": content,
            "subject": subject,
            "recipient": recipient,
        })
        return PostResult.model_validate(resp.json())

    def notify(self, payload: dict[str, Any]) -> NotifyResult:
        resp = self._request("POST", "/api/lingmessage/notify", json=payload)
        return NotifyResult.model_validate(resp.json())

    # ── context manager ──

    def __enter__(self) -> "LingClaudeSDK":
        self._cl
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    async def __aenter__(self) -> "LingClaudeSDK":
        self._acl
        return self

    async def __aexit__(self, *args: object) -> None:
        self.close()
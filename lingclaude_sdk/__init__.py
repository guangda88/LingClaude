"""灵克 8700 SDK — Python client library for lingclaude HTTP API.

Usage:
    from lingclaude_sdk import LingClaudeSDK

    sdk = LingClaudeSDK(base_url="http://127.0.0.1:8700", api_key="your-key")
    status = sdk.status()
    resp = sdk.ask("分析这个文件")
"""

from __future__ import annotations

from lingclaude_sdk.client import LingClaudeSDK
from lingclaude_sdk.exceptions import (
    LingClaudeSDKError,
    AuthenticationError,
    EngineUnavailableError,
    ProtocolVersionError,
    PermissionDeniedError,
    NotFoundError,
    ValidationError,
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
    SnapshotRequest,
    SnapshotResult,
    PermissionRequest,
    PermissionResponse,
    FileContent,
    WriteResult,
    AnalyzeRequest,
    AnalyzeResult,
    GovernedPostRequest,
    PostResult,
    NotifyResult,
)

__all__ = [
    "LingClaudeSDK",
    "LingClaudeSDKError",
    "AuthenticationError",
    "EngineUnavailableError",
    "ProtocolVersionError",
    "PermissionDeniedError",
    "NotFoundError",
    "ValidationError",
    "TimeoutError",
    "EngineStatus",
    "ProjectInfo",
    "AskResponse",
    "SSEEvent",
    "LiveEvents",
    "ToolEvent",
    "SessionList",
    "SessionDetail",
    "SnapshotRequest",
    "SnapshotResult",
    "PermissionRequest",
    "PermissionResponse",
    "FileContent",
    "WriteResult",
    "AnalyzeRequest",
    "AnalyzeResult",
    "GovernedPostRequest",
    "PostResult",
    "NotifyResult",
]
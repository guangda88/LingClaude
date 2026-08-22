"""8700 SDK data models — Pydantic request/response types."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Engine status ──

class ProjectInfo(BaseModel):
    name: str
    path: str
    exists: bool


class EngineStatus(BaseModel):
    status: str
    version: str
    projects: list[ProjectInfo]
    auth_required: bool


# ── Ask ──

class AskResponse(BaseModel):
    answer: str


# ── SSE events ──

class SSEEvent(BaseModel):
    type: str
    content: str = ""
    call_id: str = ""
    name: str = ""
    arguments: dict = Field(default_factory=dict)
    output: str = ""
    success: bool = True
    session_id: str = ""
    message: str = ""


# ── Live events ──

class ToolEvent(BaseModel):
    seq: int
    type: str
    name: str = ""
    success: bool = True
    duration_ms: int = 0
    ts: float = 0.0


class LiveEvents(BaseModel):
    events: list[ToolEvent]
    latest: int


# ── Sessions ──

class SessionList(BaseModel):
    sessions: list[str]


class SessionDetail(BaseModel):
    session_id: str
    project_path: str
    created_at: str
    expires_at: str
    turns: int


class SnapshotRequest(BaseModel):
    session_id: str
    project_path: str = ""


class SnapshotResult(BaseModel):
    path: str
    session_id: str


# ── Permission ──

class PermissionRequest(BaseModel):
    session_id: str
    decision: str
    tool_name: str = ""
    reason: str = ""


class PermissionResponse(BaseModel):
    success: bool
    decision: str


# ── File I/O ──

class FileContent(BaseModel):
    path: str
    content: str
    size: int


class WriteResult(BaseModel):
    path: str
    size: int
    status: str


# ── Analyze ──

class AnalyzeRequest(BaseModel):
    path: str
    focus: str = ""


class AnalyzeResult(BaseModel):
    path: str = ""
    size: int = 0
    lines: int = 0
    suffix: str = ""
    preview: str = ""
    total_files: int = 0
    py_files: int = 0
    total_size_mb: float = 0.0
    error: str = ""


# ── LingMessage ──

class GovernedPostRequest(BaseModel):
    thread_id: str
    content: str
    subject: str = ""
    recipient: str = "all"


class PostResult(BaseModel):
    posted: bool
    message_id: str
    gate_warnings: list[str] = Field(default_factory=list)


class NotifyResult(BaseModel):
    received: bool
    service: str
    action: str
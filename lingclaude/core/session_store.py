"""LINGKERNEL_v1 task #1 (激进拆包 D1) - SessionStore 模块

dsh 对位: `core/session` - append-only 事件 + checkpoint + resume。
从 query_engine.py 抽取: persist_session / load_session / _save_checkpoint /
_load_checkpoint / _clear_checkpoint / resume_interrupted 的存储层。

设计:
- SessionStore 不知道 QueryEngine (零反向依赖)
- QueryEngine 持有 SessionStore, 所有持久化/恢复走它
- resume 的"重新驱动模型"部分留在 QueryEngine (driver 职责),
  SessionStore 只负责 load/deserialize
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingclaude.core.session import Session, SessionManager
from lingclaude.core.types import Result
from lingclaude.core.redact import redact as _redact_text


logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(".lingclaude/checkpoints")


@dataclass
class CheckpointData:
    """反序列化后的 checkpoint 快照。"""

    session_id: str
    prompt: str
    round_idx: int
    used_tools: bool
    total_input: int
    total_output: int
    raw_messages: list[dict[str, Any]]
    saved_conversation: list[Any]

    def to_model_messages(self) -> list[Any]:
        """raw dict -> ModelMessage 列表 (延迟 import 避免环)。"""
        from lingclaude.core.model_types import ModelMessage, MessageRole, ToolCall

        messages = []
        for rm in self.raw_messages:
            role = MessageRole(rm.get("role", "user"))
            tool_calls = None
            if rm.get("tool_calls"):
                tool_calls = tuple(
                    ToolCall(
                        id=tc["function"].get("id", ""),
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in rm["tool_calls"]
                    if "function" in tc
                )
            messages.append(ModelMessage(
                role=role,
                content=rm.get("content", ""),
                name=rm.get("name"),
                tool_call_id=rm.get("tool_call_id"),
                tool_calls=tool_calls,
            ))
        return messages


class SessionStore:
    """会话持久化 + checkpoint 存储 (query_engine 存储层抽取)。

    职责边界:
    - save/load session (SessionManager 之上的一层)
    - checkpoint save/load/clear (中断恢复)
    - 不负责: 重新驱动模型 (那是 driver 的事)
    """

    def __init__(
        self,
        session_manager: SessionManager,
        session_id: str,
        checkpoint_dir: Path | None = None,
    ) -> None:
        self._sm = session_manager
        self.session_id = session_id
        self._checkpoint_dir = checkpoint_dir or CHECKPOINT_DIR
        self._active_checkpoint: Path | None = None

    # ----- session -----

    def persist(
        self,
        messages: list[str],
        input_tokens: int,
        output_tokens: int,
    ) -> Result[str]:
        session = Session(
            session_id=self.session_id,
            messages=tuple(messages),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        result = self._sm.save(session)
        if result.is_error:
            return result  # type: ignore[return-value]
        return Result.ok(str(result.data))

    def load(self, session_id: str) -> Result[Session]:
        return self._sm.load(session_id)

    # ----- checkpoint -----

    @property
    def has_checkpoint(self) -> bool:
        cp = self._active_checkpoint or (self._checkpoint_dir / f"{self.session_id}.json")
        return cp.exists()

    def clear_checkpoint(self) -> None:
        if self._active_checkpoint and self._active_checkpoint.exists():
            try:
                self._active_checkpoint.unlink()
            except OSError:
                pass
        self._active_checkpoint = None

    def save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
        conversation: list[Any],
        tag: str | None = None,
    ) -> Path | None:
        """序列化并落盘。失败返回 None (checkpoint 是 best-effort)。

        P1 rewind (2026-09-12): 支持多版本 —
          - tag 为 None: 覆盖写 `{session_id}.json`（兼容旧语义：中断恢复用）
          - tag 非 None: 写 `{session_id}@{tag}.json`（可回滚的历史版本）
        两种文件都保留，互不覆盖。
        """
        try:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            if tag:
                safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", tag)
                cp_path = self._checkpoint_dir / f"{self.session_id}@{safe_tag}.json"
            else:
                cp_path = self._checkpoint_dir / f"{self.session_id}.json"
            serialized = []
            for msg in messages:
                d = msg.to_dict()
                # A1: checkpoint 落盘脱敏 —— 历史漏洞：to_dict() 原样序列化，
                # key 一旦进入对话即明文落盘。此处对 content / tool arguments 统一 scrub。
                if isinstance(d.get("content"), str):
                    d["content"] = _redact_text(d["content"])
                if d.get("tool_calls") and isinstance(d["tool_calls"], list):
                    for tc in d["tool_calls"]:
                        fn = tc.get("function")
                        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                            fn["arguments"] = _redact_text(fn["arguments"])
                serialized.append(d)
            data = {
                "session_id": self.session_id,
                "prompt": _redact_text(prompt),
                "round_idx": round_idx,
                "used_tools": used_tools,
                "total_input": total_input,
                "total_output": total_output,
                "messages": serialized,
                "conversation": [
                    _redact_text(str(item)) if isinstance(item, str) else item
                    for item in conversation
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tag": tag,
            }
            cp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._active_checkpoint = cp_path
            logger.info("Checkpoint saved: session=%s round=%d tag=%s", self.session_id, round_idx, tag)
            return cp_path
        except Exception as e:
            logger.warning("Checkpoint save failed: %s", e)
            return None

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """列出当前会话全部 checkpoint 版本（含时间戳/轮次/条数）。"""
        if not self._checkpoint_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        prefix = f"{self.session_id}"
        for p in sorted(self._checkpoint_dir.glob(f"{prefix}*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("session_id") != self.session_id:
                    continue
                results.append({
                    "file": p.name,
                    "tag": data.get("tag"),
                    "round_idx": data.get("round_idx", 0),
                    "message_count": len(data.get("messages", [])),
                    "timestamp": data.get("timestamp", ""),
                })
            except Exception:
                continue
        return sorted(results, key=lambda r: r["timestamp"], reverse=True)

    def load_checkpoint_by_tag(self, tag: str) -> CheckpointData | None:
        """按 tag 加载指定版本 checkpoint（rewind 用）。"""
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", tag)
        cp_path = self._checkpoint_dir / f"{self.session_id}@{safe_tag}.json"
        if not cp_path.exists():
            return None
        try:
            data = json.loads(cp_path.read_text(encoding="utf-8"))
            if data.get("session_id") != self.session_id:
                return None
            logger.info("Checkpoint loaded by tag: session=%s tag=%s round=%d",
                        self.session_id, tag, data.get("round_idx", 0))
            return CheckpointData(
                session_id=data["session_id"],
                prompt=data["prompt"],
                round_idx=data["round_idx"],
                used_tools=data["used_tools"],
                total_input=data.get("total_input", 0),
                total_output=data.get("total_output", 0),
                raw_messages=data.get("messages", []),
                saved_conversation=data.get("conversation", []),
            )
        except Exception as e:
            logger.warning("Checkpoint load by tag failed: %s", e)
            return None

    def load_checkpoint(self) -> CheckpointData | None:
        cp_path = self._active_checkpoint or (self._checkpoint_dir / f"{self.session_id}.json")
        if not cp_path.exists():
            return None
        try:
            data = json.loads(cp_path.read_text(encoding="utf-8"))
            if data.get("session_id") != self.session_id:
                return None
            self._active_checkpoint = cp_path
            logger.info("Checkpoint loaded: session=%s round=%d", self.session_id, data.get("round_idx", 0))
            return CheckpointData(
                session_id=data["session_id"],
                prompt=data["prompt"],
                round_idx=data["round_idx"],
                used_tools=data["used_tools"],
                total_input=data.get("total_input", 0),
                total_output=data.get("total_output", 0),
                raw_messages=data.get("messages", []),
                saved_conversation=data.get("conversation", []),
            )
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)
            return None
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingclaude.core.session import Session, SessionManager
from lingclaude.core.types import Result


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
        from lingclaude.model.types import ModelMessage, MessageRole, ToolCall

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
    ) -> Path | None:
        """序列化并落盘。失败返回 None (checkpoint 是 best-effort)。"""
        try:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            cp_path = self._checkpoint_dir / f"{self.session_id}.json"
            serialized = [msg.to_dict() for msg in messages]
            data = {
                "session_id": self.session_id,
                "prompt": prompt,
                "round_idx": round_idx,
                "used_tools": used_tools,
                "total_input": total_input,
                "total_output": total_output,
                "messages": serialized,
                "conversation": list(conversation),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            cp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._active_checkpoint = cp_path
            logger.info("Checkpoint saved: session=%s round=%d", self.session_id, round_idx)
            return cp_path
        except Exception as e:
            logger.warning("Checkpoint save failed: %s", e)
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
"""Session persistence helpers extracted from QueryEngine."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lingclaude.core.models import UsageSummary
from lingclaude.core.redact import redact as _redact_text
from lingclaude.core.session import Session
from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


class SessionPersister:
    """Encapsulates session persistence logic extracted from QueryEngine."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def persist_session(self) -> Result[str]:
        engine = self._engine
        # 空会话防误写：load_session 后未对话即退出（或 /clear 后退出）时，
        # 空消息列表会把既有存档覆盖成空壳（2026-09-06 7c643abc 0 字节事故）。
        if not engine._messages:
            return Result.fail("当前会话无消息，跳过保存（保护既有存档）", code="EMPTY_SESSION")
        session = Session(
            session_id=engine.session_id,
            messages=tuple(engine._messages),
            input_tokens=engine._usage.input_tokens,
            output_tokens=engine._usage.output_tokens,
            # 2026-09-15（会话问题重构 P1-2）: 保存带 project_path —— 此前
            # Session 的 project_path 字段存在但 persist 从不传，导致所有会话
            # 落 _default/ 目录，跨项目混在一起（-continue 取全局最近）。现
            # 以 os.getcwd() 为项目归属，list_sessions(project_path) 即可按
            # 当前目录过滤，杜绝跨项目会话泄露。
            project_path=os.getcwd(),
        )
        result = engine.session_manager.save(session)
        if result.is_error:
            return result  # type: ignore[return-value]
        return Result.ok(str(result.data))

    def load_session(self, session_id: str) -> bool:
        engine = self._engine
        result = engine.session_manager.load(session_id)
        if result.is_error:
            return False
        session = result.data
        engine.session_id = session.session_id
        engine._messages = list(session.messages)
        engine._usage = UsageSummary(session.input_tokens, session.output_tokens)
        engine._transcript = list(session.messages)
        engine._conversation.clear()
        # 2026-09-21 (前缀缓存优化 P0-2): 恢复路径同样在写入点脱敏——
        # 旧会话可能残留明文 key（升级前落盘），保持 A1b 保险语义。
        for i in range(0, len(session.messages) - 1, 2):
            user_msg = _redact_text(session.messages[i])
            asst_msg = _redact_text(session.messages[i + 1]) if i + 1 < len(session.messages) else ""
            engine._conversation.append(("user", user_msg))
            engine._conversation.append(("assistant", asst_msg))
        return True

    def clear_checkpoint(self) -> None:
        engine = self._engine
        engine._sync_session_store()
        engine.session_store.clear_checkpoint()
        engine._active_checkpoint = None

    def save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
        tag: str | None = None,
    ) -> None:
        engine = self._engine
        engine._sync_session_store()
        cp = engine.session_store.save_checkpoint(
            messages=messages,
            round_idx=round_idx,
            prompt=prompt,
            used_tools=used_tools,
            total_input=total_input,
            total_output=total_output,
            conversation=engine._conversation,
            tag=tag,
        )
        if cp is not None:
            engine._active_checkpoint = cp

    def list_checkpoints(self) -> list[dict[str, Any]]:
        engine = self._engine
        engine._sync_session_store()
        return engine.session_store.list_checkpoints()

    def rewind_to(self, tag: str) -> bool:
        """回滚到指定 tag 的 checkpoint（P1 rewind）。

        恢复 engine._messages / _conversation / _transcript 到该版本，
        不动 session_store 里其它版本（可再前进）。
        """
        engine = self._engine
        engine._sync_session_store()
        cd = engine.session_store.load_checkpoint_by_tag(tag)
        if cd is None:
            return False
        from lingclaude.core.model_types import ModelMessage, MessageRole, ToolCall

        messages: list[ModelMessage] = []
        for rm in cd.raw_messages:
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
        engine._messages = messages
        if cd.saved_conversation:
            engine._conversation = [
                (tuple(item) if isinstance(item, (list, tuple)) and len(item) == 2 else item)
                for item in cd.saved_conversation
            ]
        engine._transcript = [m.content for m in messages if isinstance(m.content, str)]
        # 回滚后 journal 清空（回滚点之后的事件全部作废，防副作用幂等误判）
        try:
            engine._get_journal().clear()
            engine._journal_cache = None
        except Exception:
            pass
        logger.info("Session rewound to tag=%s (round=%d, msgs=%d)", tag, cd.round_idx, len(messages))
        return True

    def load_checkpoint(self) -> dict[str, Any] | None:
        engine = self._engine
        engine._sync_session_store()
        cp_path = Path(engine.session_store._checkpoint_dir) / f"{engine.session_id}.json"
        cd = engine.session_store.load_checkpoint()
        if cd is None:
            return None
        engine._active_checkpoint = cp_path
        return {
            "session_id": cd.session_id,
            "prompt": cd.prompt,
            "round_idx": cd.round_idx,
            "used_tools": cd.used_tools,
            "total_input": cd.total_input,
            "total_output": cd.total_output,
            "messages": cd.raw_messages,
            "conversation": cd.saved_conversation,
        }

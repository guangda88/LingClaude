"""L7 认知桥接 - 灵克侧适配层.

在灵极优 L7Bridge 之上叠加认知能力:
  - LingBus 消息自动分类存入认知层
  - 跨会话记忆按分层加载
  - 文档索引/术语共识/知识图谱

用法:
    from lingclaude.core.l7_cognitive_bridge import L7CognitiveBridge
    bridge = L7CognitiveBridge()
    bridge.on_lingbus_message({"sender": "lingflow", "body": "..."})
    ctx = bridge.get_member_context("lingflow")
"""
from __future__ import annotations

import logging
from typing import Any

from lingclaude.core.l7_cognitive import (
    L7Cognitive, OKFType, MessageCategory,
)

logger = logging.getLogger(__name__)


class L7CognitiveBridge:
    """L7 认知桥接 - 连接 LingBus 与认知层"""

    def __init__(self, cognitive: L7Cognitive | None = None) -> None:
        self.cog = cognitive or L7Cognitive()
        self._member_map = {
            "lingflow": "灵通", "lingminopt": "灵极优", "lingan": "灵安",
            "lingmessage": "灵信", "lingresearch": "灵研", "lingclaude": "灵克",
            "lingxi": "灵犀", "lingzhi": "智桥", "lingyang": "灵扬",
            "lingcreate": "灵创", "lingtongask": "灵通问道", "lingweb": "灵网",
            "lingyi": "灵医", "zhibridge": "智桥", "atomcode": "atomcode",
        }

    def _resolve_member(self, name: str) -> str:
        return self._member_map.get(name, name)

    def on_lingbus_message(self, message: dict) -> dict:
        """处理 LingBus 消息: 分类 + 存入认知层"""
        sender = message.get("sender", "unknown")
        member = self._resolve_member(sender)
        body = message.get("body", "")
        subject = message.get("subject", "")
        thread_id = message.get("thread_id", "")
        session_id = f"lingbus:{thread_id or sender}"

        full_text = f"{subject} {body}".strip()
        if not full_text:
            return {"status": "empty"}

        # 消息分类
        category = self.cog.classifier.classify(full_text)

        # 类型映射
        type_map = {
            MessageCategory.DECISION: (OKFType.DECISION, 7),
            MessageCategory.INCIDENT: (OKFType.BLOCKER, 6),
            MessageCategory.ACHIEVEMENT: (OKFType.PROJECT, 5),
            MessageCategory.PROJECT: (OKFType.PROJECT, 5),
            MessageCategory.PREFERENCE: (OKFType.PREFERENCE, 4),
            MessageCategory.BLOCKER: (OKFType.BLOCKER, 6),
            MessageCategory.GENERAL: (OKFType.CONCEPT, 3),
        }
        okf_type, importance = type_map.get(category, (OKFType.CONCEPT, 3))

        # 存入认知记忆
        mem_id = self.cog.remember(
            key=f"{sender}:{category.value}:{thread_id or 'msg'}",
            value=full_text[:500],
            source=sender,
            importance=importance,
            okf_type=okf_type,
            tags=[sender, category.value, member],
            session_id=session_id,
        )

        # 提取画像
        profile = self.cog.classifier.extract_profile(full_text, source=sender)
        for p in profile:
            self.cog.remember(
                key=p.key, value=p.value, source=sender,
                importance=p.importance, okf_type=p.okf_type,
                tags=p.tags, session_id=session_id,
            )

        return {
            "status": "ok",
            "sender": sender,
            "member": member,
            "category": category.value,
            "memory_id": mem_id,
            "profile_extracted": len(profile),
        }

    def get_member_context(self, member: str, top_k: int = 5) -> list[dict]:
        """获取某成员的记忆上下文"""
        member_name = self._resolve_member(member)
        results = self.cog.recall(member_name, top_k=top_k)
        if not results:
            results = self.cog.recall(member, top_k=top_k)
        return results

    def get_always_context(self) -> list[dict]:
        """获取常驻层记忆"""
        mems = self.cog.store.get_always_context()
        return [{"key": m.key, "value": m.value, "type": m.okf_type.value}
                for m in mems]

    def start_member_session(self, member: str, session_id: str = "") -> dict:
        """成员会话启动"""
        if not session_id:
            import time
            session_id = f"{member}:{int(time.time())}"
        return self.cog.start_session(session_id, member=member)

    def stop_member_session(
        self, member: str, session_id: str,
        last_message: str = "", last_reply: str = "",
    ) -> dict:
        return self.cog.stop_session(session_id, last_message, last_reply)

    def stats(self) -> dict:
        return self.cog.stats()


_global_bridge: L7CognitiveBridge | None = None


def get_bridge() -> L7CognitiveBridge:
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = L7CognitiveBridge()
    return _global_bridge


__all__ = ["L7CognitiveBridge", "get_bridge"]

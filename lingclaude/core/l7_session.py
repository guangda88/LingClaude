"""L7 会话生命周期钩子（灵元 1.0：砍到最薄）。

2026-09-14: 由 lingclaude/core/l7_cognitive.py 剥离 —— 5 节点会话生命周期
(SessionStart/UserPromptSubmit/PostToolUse/PreCompact/Stop) 是独立职责，
仅依赖 CognitiveStore 的存取接口，不持有存储状态。

依赖从 l7_cognitive re-export（避免循环 import）。
"""
from __future__ import annotations

import json

from lingclaude.core.l7_cognitive import (
    CognitiveMemory,
    CognitiveStore,
    MessageCategory,
    MessageClassifier,
    OKFType,
)


class SessionHooks:
    """会话生命周期钩子 - 从 obsidian-mind 5节点模式

    🚀 SessionStart    -> 加载常驻层记忆 (北极星 + 术语词典)
    💬 UserPromptSubmit -> 对消息分类 -> 注入分类路由提示
    ✍️ PostToolUse      -> 记录写入摘要
    💾 PreCompact       -> 备份会话记录
    🏁 Stop             -> 提取新记忆 -> 自动链接同类记忆
    """

    def __init__(self, store: CognitiveStore) -> None:
        self._store = store
        self._classifier = MessageClassifier()

    def on_session_start(self, session_id: str, member: str = "") -> dict:
        """会话启动: 加载 Always 层 + 术语词典"""
        self._store.log_session_event(session_id, "session_start", f"member={member}")

        always_mems = self._store.get_always_context(max_items=5)
        glossary = self._store.all_glossary()

        context_parts: list[str] = []
        for mem in always_mems:
            context_parts.append(f"[记忆:{mem.okf_type.value}] {mem.key}: {mem.value}")
        for term in glossary:
            context_parts.append(f"[术语] {term.term}: {term.definition}")

        context = "\n".join(context_parts) if context_parts else ""
        return {
            "session_id": session_id,
            "member": member,
            "context": context,
            "always_count": len(always_mems),
            "glossary_count": len(glossary),
            "token_estimate": len(context) // 3,
        }

    def on_user_prompt(
        self, session_id: str, message: str, member: str = ""
    ) -> dict:
        """用户消息提交: 分类 + 按需检索"""
        category = self._classifier.classify(message)

        # 按需检索相关记忆
        ondemand = self._store.get_ondemand_context(message, max_items=5)

        # 触发层按分类检索
        triggered = self._store.get_triggered_context(category, max_items=5)

        # 提取画像
        profile = self._classifier.extract_profile(message, source=member)
        for mem in profile:
            self._store.put_memory(mem)

        self._store.log_session_event(
            session_id, "user_prompt",
            f"category={category.value}, ondemand={len(ondemand)}, triggered={len(triggered)}",
        )

        context_parts: list[str] = []
        if category != MessageCategory.GENERAL:
            context_parts.append(f"[消息类型] {category.value}")
        for mem in ondemand + triggered:
            context_parts.append(f"[记忆:{mem.okf_type.value}] {mem.key}: {mem.value}")

        return {
            "session_id": session_id,
            "category": category.value,
            "context": "\n".join(context_parts) if context_parts else "",
            "ondemand_count": len(ondemand),
            "triggered_count": len(triggered),
            "profile_extracted": len(profile),
        }

    def on_post_tool_use(self, session_id: str, tool: str, summary: str = "") -> None:
        """工具使用后: 记录写入摘要"""
        self._store.log_session_event(
            session_id, "post_tool_use", f"tool={tool}, summary={summary[:200]}",
        )

    def on_pre_compact(self, session_id: str, history: list[dict] | None = None) -> None:
        """上下文压缩前: 备份会话记录"""
        summary = json.dumps(history[-5:] if history else [], ensure_ascii=False)[:500]
        self._store.log_session_event(session_id, "pre_compact", summary)

    def on_session_stop(
        self, session_id: str, last_message: str = "", last_reply: str = "",
    ) -> dict:
        """会话结束: 提取新记忆"""
        extracted: list[CognitiveMemory] = []

        # 从最后一条消息提取画像
        if last_message:
            profile = self._classifier.extract_profile(last_message)
            extracted.extend(profile)

        # 从对话中提取决策
        if last_message:
            category = self._classifier.classify(last_message)
            if category == MessageCategory.DECISION:
                extracted.append(CognitiveMemory(
                    key=f"decision:{session_id}",
                    value=last_message[:500],
                    source="session",
                    session_id=session_id,
                    importance=7,
                    okf_type=OKFType.DECISION,
                    tags=["decision", "session"],
                ))

        # 存储提取的记忆
        for mem in extracted:
            self._store.put_memory(mem)

        self._store.log_session_event(
            session_id, "session_stop",
            f"extracted={len(extracted)}, last_msg_len={len(last_message)}",
        )

        return {
            "session_id": session_id,
            "extracted_count": len(extracted),
            "memories": [{"key": m.key, "value": str(m.value)[:100]} for m in extracted],
        }


__all__ = ["SessionHooks"]

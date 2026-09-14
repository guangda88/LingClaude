"""L7 认知层顶层门面 + 全局单例（灵元 1.0：砍到最薄）。

2026-09-14: 由 lingclaude/core/l7_cognitive.py 剥离 —— L7Cognitive 门面
（全部是转发到 CognitiveStore/SessionHooks 的薄方法）与全局单例
get_cognitive() 独立成模块，l7_cognitive 只保留存储层与数据类。

消费方 from lingclaude.core.l7_cognitive import L7Cognitive 由 l7_cognitive
顶层 re-export 保持兼容（本模块不反向依赖 l7_cognitive 的消费方）。
"""
from __future__ import annotations

import threading as _threading
from pathlib import Path
from typing import Any

from lingclaude.core.l7_cognitive import (
    CognitiveMemory,
    CognitiveStore,
    DocIndex,
    GlossaryTerm,
    GraphEdge,
    MessageClassifier,
    OKFType,
    SessionHooks,
)


class L7Cognitive:
    """L7 认知层 - 顶层接口

    用法:
        from lingclaude.core.l7_cognitive import L7Cognitive
        cog = L7Cognitive()
        cog.start_session("session-123", member="lingclaude")
        ctx = cog.on_message("session-123", "我决定用 DeepSeek 模型")
        cog.stop_session("session-123", "我决定用 DeepSeek 模型", "好的...")
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        legacy_sink: Any | None = None,
    ) -> None:
        self.store = CognitiveStore(db_path, legacy_sink=legacy_sink)
        self.hooks = SessionHooks(self.store)
        self.classifier = MessageClassifier()

    def start_session(self, session_id: str, member: str = "") -> dict:
        return self.hooks.on_session_start(session_id, member)

    def on_message(self, session_id: str, message: str, member: str = "") -> dict:
        return self.hooks.on_user_prompt(session_id, message, member)

    def on_tool_use(self, session_id: str, tool: str, summary: str = "") -> None:
        self.hooks.on_post_tool_use(session_id, tool, summary)

    def on_pre_compact(self, session_id: str, history: list[dict] | None = None) -> None:
        self.hooks.on_pre_compact(session_id, history)

    def stop_session(
        self, session_id: str, last_message: str = "", last_reply: str = "",
    ) -> dict:
        return self.hooks.on_session_stop(session_id, last_message, last_reply)

    # ── 记忆管理 ──

    def remember(
        self, key: str, value: Any, source: str = "",
        importance: int = 5, okf_type: OKFType = OKFType.CONCEPT,
        tags: list[str] | None = None, session_id: str = "",
    ) -> str:
        mem = CognitiveMemory(
            key=key, value=value, source=source, session_id=session_id,
            importance=importance, okf_type=okf_type,
            tags=tags or [],
        )
        return self.store.put_memory(mem)

    def recall(self, query: str, top_k: int = 5, okf_type: OKFType | None = None) -> list[dict]:
        mems = self.store.search(query, top_k=top_k, okf_type=okf_type)
        return [{"key": m.key, "value": m.value, "type": m.okf_type.value,
                 "importance": m.importance, "tier": m.tier.value} for m in mems]

    def recall_compact(self, query: str, top_k: int = 5) -> str:
        results = self.recall(query, top_k=top_k)
        if not results:
            return ""
        lines = []
        for r in results:
            lines.append(f"[{r['type']}/{r['importance']}] {r['key']}: {r['value']}")
        return "\n".join(lines)

    # ── 文档索引 ──

    def index_doc(
        self, path: str, title: str, summary: str = "",
        okf_type: OKFType = OKFType.CONCEPT,
        tags: list[str] | None = None, project: str = "", url: str = "",
    ) -> str:
        doc = DocIndex(
            path=path, url=url, title=title, summary=summary,
            okf_type=okf_type, tags=tags or [], project=project,
        )
        return self.store.put_doc(doc)

    def find_docs(self, query: str, top_k: int = 5, project: str = "") -> list[dict]:
        docs = self.store.search_docs(query, top_k=top_k, project=project)
        return [{"path": d.path, "url": d.url, "title": d.title,
                 "summary": d.summary, "type": d.okf_type.value,
                 "project": d.project} for d in docs]

    # ── 术语共识 ──

    def define_term(
        self, term: str, definition: str,
        aliases: list[str] | None = None, source_thread: str = "",
    ) -> str:
        t = GlossaryTerm(
            term=term, definition=definition,
            aliases=aliases or [], source_thread=source_thread,
        )
        return self.store.put_glossary(t)

    def lookup_term(self, term: str) -> str:
        t = self.store.lookup_glossary(term)
        return f"{t.term}: {t.definition}" if t else ""

    # ── 知识图谱 ──

    def link(self, source_id: str, target_id: str,
             relation: str = "related", context: str = "") -> None:
        self.store.put_edge(GraphEdge(
            source_id=source_id, target_id=target_id,
            relation=relation, context=context,
        ))

    def graph(self, node_id: str, depth: int = 1) -> dict:
        return self.store.get_neighbors(node_id, depth=depth)

    # ── 统计 ──

    def stats(self) -> dict[str, Any]:
        return self.store.stats()

    def close(self) -> None:
        self.store.close()


# ── 全局单例 ──

import threading as _threading

_global: L7Cognitive | None = None
_global_lock = _threading.Lock()


def get_cognitive() -> L7Cognitive:
    """全局单例访问点（双检锁）。

    2026-09-11: 竞态修复 — 原版裸双检，两线程同时见 None 会各建一个
    L7Cognitive；败者的 store 是独立 sqlite 句柄，写点双写/P3.3 指标
    会静默分裂到两个 DB。__init__ 开 sqlite 连接属重副作用，必须锁。
    """
    global _global
    if _global is None:
        with _global_lock:
            if _global is None:  # double-check
                _global = L7Cognitive()
    return _global




"""L7 认知层 - obsidian-mind + OKF 模式全族增强.

在灵极优 L7 存储引擎之上，叠加认知能力：
  1. 分层记忆 (Always/OnDemand/Triggered) - 按重要度自动分层加载
  2. 会话生命周期钩子 (5节点) - 标准化会话管理
  3. 消息分类引擎 - decision/incident/achievement/project/preference/blocker
  4. 文档索引 - 链接+摘要+type，避免每次全族搜索
  5. 术语共识 - LingBus 讨论中形成的统一口径
  6. 知识图谱 - 跨项目依赖链 + 决策链

设计原则:
  - 薄叠加: 不修改 L7 存储引擎 (l7_memory.py)，在其上构建认知层
  - 降级安全: L7 存储不可用时降级到本地 SQLite
  - 全族可用: 任何灵族成员均可调用
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from lingclaude.core.safe_db import safe_commit, safe_execute
from lingclaude.core.sqlite_store_base import SqliteStoreBase

logger = logging.getLogger(__name__)


# ── 分层记忆 tier ──

class MemoryTier(str, Enum):
    ALWAYS = "always"        # 重要度 >= 8, 每次会话自动加载, ~2K tokens, 最多 5 条
    ONDEMAND = "ondemand"    # 重要度 3-7, 语义检索触发, 最多 10 条
    TRIGGERED = "triggered"  # 重要度 1-2, 会话钩子触发, 最多 20 条


def importance_to_tier(importance: int) -> MemoryTier:
    if importance >= 8:
        return MemoryTier.ALWAYS
    if importance >= 3:
        return MemoryTier.ONDEMAND
    return MemoryTier.TRIGGERED


# ── OKF type (知识格式) ──

class OKFType(str, Enum):
    DECISION = "decision"      # 架构/设计决策
    PREFERENCE = "preference"  # 用户/成员偏好
    HARDWARE = "hardware"      # 硬件配置
    BLOCKER = "blocker"        # 阻塞/问题
    PROJECT = "project"        # 项目信息
    CONCEPT = "concept"        # 核心概念
    MEMBER = "member"          # 成员信息
    TOOL = "tool"              # 工具/技术
    GLOSSARY = "glossary"      # 术语共识


# ── 消息分类 ──

class MessageCategory(str, Enum):
    DECISION = "decision"
    INCIDENT = "incident"
    ACHIEVEMENT = "achievement"
    PROJECT = "project"
    PREFERENCE = "preference"
    BLOCKER = "blocker"
    GENERAL = "general"


# ── 数据结构 ──

@dataclass
class CognitiveMemory:
    """L7 认知记忆条目 - 在 L7 MemoryEntry 之上加 tier/type/importance"""
    id: str = ""
    key: str = ""
    value: Any = None
    source: str = ""           # 来源成员
    session_id: str = ""
    importance: int = 5        # 1-10
    tier: MemoryTier = MemoryTier.TRIGGERED
    okf_type: OKFType = OKFType.CONCEPT
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        self.updated_at = self.created_at
        self.tier = importance_to_tier(self.importance)


@dataclass
class DocIndex:
    """文档索引条目 - 链接+摘要+type"""
    id: str = ""
    path: str = ""             # 文件路径
    url: str = ""              # 外部链接 (如有)
    title: str = ""
    summary: str = ""          # 摘要 (1-3 句)
    okf_type: OKFType = OKFType.CONCEPT
    tags: list[str] = field(default_factory=list)
    project: str = ""          # 所属项目
    created_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class GlossaryTerm:
    """术语共识条目"""
    id: str = ""
    term: str = ""             # 术语
    definition: str = ""       # 统一口径
    aliases: list[str] = field(default_factory=list)
    source_thread: str = ""    # LingBus thread_id
    created_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class GraphEdge:
    """知识图谱边"""
    source_id: str = ""
    target_id: str = ""
    relation: str = "related"  # depends_on / decided_by / blocked_by / related
    context: str = ""


# ── SQLite Schema ──

_SCHEMA = """
CREATE TABLE IF NOT EXISTS l7_cognitive_memories (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    value TEXT,
    source TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    importance INTEGER DEFAULT 5,
    tier TEXT DEFAULT 'triggered',
    okf_type TEXT DEFAULT 'concept',
    tags TEXT DEFAULT '[]',
    created_at REAL,
    updated_at REAL,
    access_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_l7c_tier ON l7_cognitive_memories(tier);
CREATE INDEX IF NOT EXISTS idx_l7c_type ON l7_cognitive_memories(okf_type);
CREATE INDEX IF NOT EXISTS idx_l7c_importance ON l7_cognitive_memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_l7c_key ON l7_cognitive_memories(key);
CREATE INDEX IF NOT EXISTS idx_l7c_source ON l7_cognitive_memories(source);

CREATE TABLE IF NOT EXISTS l7_doc_index (
    id TEXT PRIMARY KEY,
    path TEXT DEFAULT '',
    url TEXT DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    okf_type TEXT DEFAULT 'concept',
    tags TEXT DEFAULT '[]',
    project TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_l7di_type ON l7_doc_index(okf_type);
CREATE INDEX IF NOT EXISTS idx_l7di_project ON l7_doc_index(project);
CREATE INDEX IF NOT EXISTS idx_l7di_title ON l7_doc_index(title);

CREATE TABLE IF NOT EXISTS l7_glossary (
    id TEXT PRIMARY KEY,
    term TEXT NOT NULL UNIQUE,
    definition TEXT NOT NULL,
    aliases TEXT DEFAULT '[]',
    source_thread TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_l7g_term ON l7_glossary(term);

CREATE TABLE IF NOT EXISTS l7_graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT DEFAULT 'related',
    context TEXT DEFAULT '',
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_l7ge_source ON l7_graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_l7ge_target ON l7_graph_edges(target_id);

CREATE TABLE IF NOT EXISTS l7_session_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event TEXT NOT NULL,
    summary TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_l7sl_session ON l7_session_log(session_id);
"""
# ── 消息分类规则（2026-09-14 灵元：剥离为 l7_classifier 纯规则引擎）──
# 规则与 MessageClassifier 已迁至 lingclaude/core/l7_classifier.py，
# 此处仅 re-export 保持向后兼容（消费方 from l7_cognitive import MessageClassifier 不变）。
from lingclaude.core.l7_classifier import (  # noqa: F401,E402
    MessageClassifier,
    _CLASSIFY_RULES,
    _PROFILE_PATTERNS,
)


# ── 认知存储层 ──



# ── 认知存储层 ──

class CognitiveStore(SqliteStoreBase):
    """L7 认知存储 - 在 L7 存储引擎之上的认知层。

    降级策略: L7 存储引擎 (l7_memory.py) 不可用时，独立运行于本地 SQLite。
    """

    _SCHEMA = _SCHEMA

    def __init__(
        self,
        db_path: str | Path | None = None,
        legacy_sink: Any | None = None,
    ) -> None:
        super().__init__(db_path=db_path, legacy_sink=legacy_sink, db_name="l7_cognitive.db")
        self._l7_available = self._try_load_l7()

    def _try_load_l7(self) -> bool:
        """尝试加载灵极优 L7 存储引擎，不可用时降级"""
        try:
            _l7_path = Path(__file__).parent.parent.parent.parent / "lingminopt" / "lingyuan"
            import sys
            if str(_l7_path) not in sys.path:
                sys.path.insert(0, str(_l7_path))
            from l7_memory import store as l7_store, retrieve as l7_retrieve
            self._l7_store = l7_store
            self._l7_retrieve = l7_retrieve
            return True
        except Exception:
            self._l7_store = None
            self._l7_retrieve = None
            return False

    # ── 认知记忆 CRUD ──

    def put_memory(self, mem: CognitiveMemory) -> str:
        conn = self._get_conn()
        safe_execute(conn, """INSERT OR REPLACE INTO l7_cognitive_memories
            (id, key, value, source, session_id, importance, tier, okf_type,
             tags, created_at, updated_at, access_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mem.id, mem.key, json.dumps(mem.value, ensure_ascii=False),
             mem.source, mem.session_id, mem.importance, mem.tier.value,
             mem.okf_type.value, json.dumps(mem.tags, ensure_ascii=False),
             mem.created_at, mem.updated_at, mem.access_count),
        )
        safe_commit(conn)

        # P3.3 双写旁观者（return 前、L7 引擎同步前）
        self._emit("on_memory", {
            "origin": "memory",
            "origin_id": mem.id,
            "entry_key": mem.key,
            "summary": str(mem.value)[:500] if mem.value is not None else "",
            "importance": int(mem.importance),
            "tier": mem.tier.value,
        })

        # 同步到 L7 存储引擎 (如果可用)
        if self._l7_available and self._l7_store:
            try:
                self._l7_store(
                    key=mem.key, value=mem.value,
                    source=mem.source, session_id=mem.session_id,
                    trust_score=mem.importance / 10.0,
                )
            except Exception:
                pass  # L7 存储失败不影响认知层

        return mem.id

    def get_memory(self, mem_id: str) -> CognitiveMemory | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM l7_cognitive_memories WHERE id = ?", (mem_id,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def _row_to_memory(self, row: sqlite3.Row) -> CognitiveMemory:
        return CognitiveMemory(
            id=row["id"], key=row["key"],
            value=json.loads(row["value"]) if row["value"] else None,
            source=row["source"], session_id=row["session_id"],
            importance=row["importance"],
            tier=MemoryTier(row["tier"]),
            okf_type=OKFType(row["okf_type"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=row["created_at"], updated_at=row["updated_at"],
            access_count=row["access_count"],
        )

    # ── 分层记忆检索 ──

    def get_always_context(self, max_items: int = 5) -> list[CognitiveMemory]:
        """Always 层: 重要度 >= 8, 每次会话自动加载"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM l7_cognitive_memories
               WHERE tier = 'always'
               ORDER BY importance DESC, updated_at DESC LIMIT ?""",
            (max_items,),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get_ondemand_context(self, query: str, max_items: int = 10) -> list[CognitiveMemory]:
        """OnDemand 层: 重要度 3-7, 语义检索触发"""
        conn = self._get_conn()
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM l7_cognitive_memories
               WHERE tier = 'ondemand'
               AND (key LIKE ? OR value LIKE ? OR tags LIKE ?)
               ORDER BY importance DESC, updated_at DESC LIMIT ?""",
            (pattern, pattern, f'%"{query}"%', max_items),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get_triggered_context(
        self, category: MessageCategory | None = None, max_items: int = 20
    ) -> list[CognitiveMemory]:
        """Triggered 层: 重要度 1-2, 会话钩子触发"""
        conn = self._get_conn()
        okf_type: OKFType | None = None
        if category:
            type_map = {
                MessageCategory.DECISION: OKFType.DECISION,
                MessageCategory.INCIDENT: OKFType.BLOCKER,
                MessageCategory.PREFERENCE: OKFType.PREFERENCE,
                MessageCategory.PROJECT: OKFType.PROJECT,
            }
            okf_type = type_map.get(category)
        if okf_type:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE tier = 'triggered' AND okf_type = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (okf_type.value, max_items),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE tier = 'triggered'
                   ORDER BY updated_at DESC LIMIT ?""",
                (max_items,),
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def search(self, query: str, top_k: int = 5, okf_type: OKFType | None = None) -> list[CognitiveMemory]:
        """跨层搜索 - 按 type 过滤"""
        conn = self._get_conn()
        pattern = f"%{query}%"
        if okf_type:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE (key LIKE ? OR value LIKE ? OR tags LIKE ?)
                   AND okf_type = ?
                   ORDER BY importance DESC, updated_at DESC LIMIT ?""",
                (pattern, pattern, f'%"{query}"%', okf_type.value, top_k),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
                   ORDER BY importance DESC, updated_at DESC LIMIT ?""",
                (pattern, pattern, f'%"{query}"%', top_k),
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    # ── 文档索引 ──

    def put_doc(self, doc: DocIndex) -> str:
        conn = self._get_conn()
        safe_execute(conn, """INSERT OR REPLACE INTO l7_doc_index
            (id, path, url, title, summary, okf_type, tags, project, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (doc.id, doc.path, doc.url, doc.title, doc.summary,
             doc.okf_type.value, json.dumps(doc.tags, ensure_ascii=False),
             doc.project, doc.created_at),
        )
        safe_commit(conn)

        # P3.3 双写旁观者（return 前）
        self._emit("on_doc", {
            "origin": "doc",
            "origin_id": doc.id,
            "entry_key": doc.path,
            "summary": f"{doc.title}: {doc.summary}"[:500],
            "project": doc.project,
        })
        return doc.id

    def search_docs(self, query: str, top_k: int = 5, project: str = "") -> list[DocIndex]:
        conn = self._get_conn()
        pattern = f"%{query}%"
        sql = """SELECT * FROM l7_doc_index
                 WHERE title LIKE ? OR summary LIKE ? OR tags LIKE ?
                 {proj_filter}
                 ORDER BY created_at DESC LIMIT ?"""
        if project:
            sql = sql.format(proj_filter="AND project = ?")
            rows = conn.execute(sql, (pattern, pattern, f'%"{query}"%', project, top_k)).fetchall()
        else:
            sql = sql.format(proj_filter="")
            rows = conn.execute(sql, (pattern, pattern, f'%"{query}"%', top_k)).fetchall()
        return [self._row_to_doc(r) for r in rows]

    def _row_to_doc(self, row: sqlite3.Row) -> DocIndex:
        return DocIndex(
            id=row["id"], path=row["path"], url=row["url"],
            title=row["title"], summary=row["summary"],
            okf_type=OKFType(row["okf_type"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            project=row["project"], created_at=row["created_at"],
        )

    # ── 术语共识 ──

    def put_glossary(self, term: GlossaryTerm) -> str:
        conn = self._get_conn()
        safe_execute(conn, """INSERT OR REPLACE INTO l7_glossary
            (id, term, definition, aliases, source_thread, created_at)
            VALUES (?,?,?,?,?,?)""",
            (term.id, term.term, term.definition,
             json.dumps(term.aliases, ensure_ascii=False),
             term.source_thread, term.created_at),
        )
        safe_commit(conn)

        # P3.3 双写旁观者（return 前）
        self._emit("on_glossary", {
            "origin": "glossary",
            "origin_id": term.id,
            "entry_key": term.term,
            "summary": f"{term.term}: {term.definition}"[:500],
            "aliases": list(term.aliases),
        })
        return term.id

    def lookup_glossary(self, term: str) -> GlossaryTerm | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM l7_glossary WHERE term = ?", (term,)
        ).fetchone()
        if row:
            return self._row_to_glossary(row)
        # 搜索别名
        rows = conn.execute("SELECT * FROM l7_glossary").fetchall()
        for r in rows:
            aliases = json.loads(r["aliases"]) if r["aliases"] else []
            if term.lower() in [a.lower() for a in aliases]:
                return self._row_to_glossary(r)
        return None

    def all_glossary(self) -> list[GlossaryTerm]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM l7_glossary ORDER BY term").fetchall()
        return [self._row_to_glossary(r) for r in rows]

    def _row_to_glossary(self, row: sqlite3.Row) -> GlossaryTerm:
        return GlossaryTerm(
            id=row["id"], term=row["term"], definition=row["definition"],
            aliases=json.loads(row["aliases"]) if row["aliases"] else [],
            source_thread=row["source_thread"], created_at=row["created_at"],
        )

    # ── 知识图谱 ──

    def put_edge(self, edge: GraphEdge) -> None:
        conn = self._get_conn()
        safe_execute(conn,
            """INSERT OR REPLACE INTO l7_graph_edges
               (source_id, target_id, relation, context)
               VALUES (?,?,?,?)""",
            (edge.source_id, edge.target_id, edge.relation, edge.context),
        )
        safe_commit(conn)

        # P3.3 双写旁观者
        self._emit("on_edge", {
            "origin": "edge",
            "origin_id": f"{edge.source_id}->{edge.target_id}:{edge.relation}",
            "entry_key": f"{edge.source_id}->{edge.relation}->{edge.target_id}",
            "summary": str(edge.context)[:500],
        })

    def get_neighbors(self, node_id: str, depth: int = 1) -> dict:
        """BFS 知识图谱遍历"""
        conn = self._get_conn()
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(node_id, 0)]

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            # 尝试记忆节点
            mem = self.get_memory(current_id)
            if mem:
                nodes[current_id] = {
                    "type": "memory", "key": mem.key,
                    "okf_type": mem.okf_type.value,
                }
            # 尝试文档节点
            doc_row = conn.execute(
                "SELECT * FROM l7_doc_index WHERE id = ?", (current_id,)
            ).fetchone()
            if doc_row:
                nodes[current_id] = {
                    "type": "doc", "title": doc_row["title"],
                    "path": doc_row["path"],
                }
            # 尝试术语节点
            glossary_row = conn.execute(
                "SELECT * FROM l7_glossary WHERE id = ?", (current_id,)
            ).fetchone()
            if glossary_row:
                nodes[current_id] = {
                    "type": "glossary", "term": glossary_row["term"],
                }

            # 获取邻居
            forward = conn.execute(
                "SELECT target_id, relation, context FROM l7_graph_edges WHERE source_id = ?",
                (current_id,),
            ).fetchall()
            backward = conn.execute(
                "SELECT source_id, relation, context FROM l7_graph_edges WHERE target_id = ?",
                (current_id,),
            ).fetchall()

            for r in forward:
                edges.append({
                    "source": current_id, "target": r["target_id"],
                    "relation": r["relation"], "context": r["context"],
                })
                if current_depth < depth:
                    queue.append((r["target_id"], current_depth + 1))

            for r in backward:
                edges.append({
                    "source": r["source_id"], "target": current_id,
                    "relation": r["relation"], "context": r["context"],
                })
                if current_depth < depth:
                    queue.append((r["source_id"], current_depth + 1))

        return {"nodes": nodes, "edges": edges}

    # ── 会话日志 ──

    def log_session_event(self, session_id: str, event: str, summary: str = "") -> None:
        conn = self._get_conn()
        safe_execute(conn,
            """INSERT INTO l7_session_log (session_id, event, summary, created_at)
               VALUES (?,?,?,?)""",
            (session_id, event, summary, time.time()),
        )
        safe_commit(conn)

        # P3.3 双写旁观者
        self._emit("on_session_log", {
            "origin": "session_log",
            "origin_id": f"{session_id}:{event}:{time.time()}",
            "entry_key": f"{session_id}:{event}",
            "summary": str(summary)[:500],
            "ts": time.time(),
        })

    def get_session_log(self, session_id: str, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM l7_session_log
               WHERE session_id = ? ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [{"event": r["event"], "summary": r["summary"],
                 "created_at": r["created_at"]} for r in rows]

    # ── 统计 ──

    def stats(self) -> dict[str, Any]:
        conn = self._get_conn()
        return {
            "memories": conn.execute(
                "SELECT COUNT(*) FROM l7_cognitive_memories"
            ).fetchone()[0],
            "always_count": conn.execute(
                "SELECT COUNT(*) FROM l7_cognitive_memories WHERE tier = 'always'"
            ).fetchone()[0],
            "ondemand_count": conn.execute(
                "SELECT COUNT(*) FROM l7_cognitive_memories WHERE tier = 'ondemand'"
            ).fetchone()[0],
            "triggered_count": conn.execute(
                "SELECT COUNT(*) FROM l7_cognitive_memories WHERE tier = 'triggered'"
            ).fetchone()[0],
            "docs": conn.execute("SELECT COUNT(*) FROM l7_doc_index").fetchone()[0],
            "glossary": conn.execute("SELECT COUNT(*) FROM l7_glossary").fetchone()[0],
            "edges": conn.execute("SELECT COUNT(*) FROM l7_graph_edges").fetchone()[0],
            "l7_engine_connected": self._l7_available,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None




# ── 会话生命周期钩子（2026-09-14 灵元：剥离为 l7_session）──
from lingclaude.core.l7_session import SessionHooks  # noqa: F401,E402



# ── 顶层门面 + 全局单例（2026-09-14 灵元：剥离为 l7_cognitive_facade）──
from lingclaude.core.l7_cognitive_facade import (  # noqa: F401,E402
    L7Cognitive,
    get_cognitive,
)

__all__ = [
    "MemoryTier", "OKFType", "MessageCategory",
    "CognitiveMemory", "DocIndex", "GlossaryTerm", "GraphEdge",
    "CognitiveStore", "MessageClassifier", "SessionHooks",
    "L7Cognitive", "get_cognitive",
    "importance_to_tier",
]

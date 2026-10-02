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

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from lingclaude.core.safe_db import safe_commit, safe_execute
from lingclaude.core.sqlite_store_base import SqliteStoreBase

logger = logging.getLogger(__name__)

# ── P2② 哈希链（2026-10-02）──
# 记录参与摘要的字段集：**内容字段 + 链字段**，排除 id（uuid 随机生成，进
# 摘要会破坏「同内容同摘要」的可复算性）与 updated_at 由内容决定故纳入。
# access_count 是运行时可变读计数——纳入会让正常读取漂移破坏校验，排除。
_DIGEST_FIELDS = ("key", "value", "source", "session_id", "importance",
                  "tier", "okf_type", "tags", "created_at", "updated_at",
                  "superseded_by", "prev_digest")


def _memory_record_digest(record: dict) -> str:
    """规范 JSON → sha256。与存储层无耦合，导出档案可离线复算同值。

    键排序 + ensure_ascii=False + 分隔符固定，跨进程跨语言可复现。
    tags 已是 JSON 字符串（row 直取）或 list（构建期）——统一先规范化。
    """
    canonical = {}
    for f in _DIGEST_FIELDS:
        v = record.get(f, "")
        if f == "tags" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        canonical[f] = v
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 分层记忆 tier ──
# 2026-09-24 循环清偿：MemoryTier/OKFType/MessageCategory/CognitiveMemory/
# importance_to_tier 定义下沉 l7_types.py（叶子模块），此处从叶子导入并
# 保留 re-export（消费方 ``from l7_cognitive import CognitiveMemory`` 不变）。
from lingclaude.core.l7_types import (  # noqa: F401
    CognitiveMemory,
    MemoryTier,
    MessageCategory,
    OKFType,
    importance_to_tier,
)


# ── 数据结构 ──



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
    access_count INTEGER DEFAULT 0,
    superseded_by TEXT DEFAULT '',
    prev_digest TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_l7c_tier ON l7_cognitive_memories(tier);
CREATE INDEX IF NOT EXISTS idx_l7c_type ON l7_cognitive_memories(okf_type);
CREATE INDEX IF NOT EXISTS idx_l7c_importance ON l7_cognitive_memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_l7c_key ON l7_cognitive_memories(key);
CREATE INDEX IF NOT EXISTS idx_l7c_source ON l7_cognitive_memories(source);
-- 注意：superseded_by 相关索引不放这里——存量库 executescript 先于
-- ALTER 迁移执行会因缺列炸掉，索引统一由 _migrate_supersede_column 创建。

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
        self._migrate_supersede_column()
        self._l7_available = self._try_load_l7()

    def _migrate_supersede_column(self) -> None:
        """存量库补列（2026-10-02 P1③ supersede 链）。

        executescript 的 CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，
        存量 l7_cognitive.db 需 ALTER 补 superseded_by；幂等（列在即跳过）。
        迁移失败仅告警——读路径会用 getattr 兜底（旧行无该列时 sqlite3.Row
        按名取值会 KeyError，故列缺失时检索过滤自动降级为不过滤）。
        """
        try:
            conn = self._get_conn()
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(l7_cognitive_memories)").fetchall()}
            if "superseded_by" not in cols:
                conn.execute(
                    "ALTER TABLE l7_cognitive_memories "
                    "ADD COLUMN superseded_by TEXT DEFAULT ''")
                logger.info("l7_cognitive: superseded_by 列迁移完成")
            # P2②（2026-10-02）：prev_digest 哈希链列（存量补列，链从启用点
            # 起算）。verify_claim 寻父走同 key 全行内容摘要映射，
            # 不按 prev_digest 建索引查询——寻父是 O(n) 遍历而非 O(log n) 点查。
            if "prev_digest" not in cols:
                conn.execute(
                    "ALTER TABLE l7_cognitive_memories "
                    "ADD COLUMN prev_digest TEXT DEFAULT ''")
                logger.info("l7_cognitive: prev_digest 列迁移完成（链从现在起算）")
            # 检索过滤主索引（新库旧库统一在此创建，见 _SCHEMA 注释）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_l7c_active "
                "ON l7_cognitive_memories(key, superseded_by)")
            safe_commit(conn)
        except Exception:
            logger.warning("l7_cognitive: superseded_by 列迁移失败", exc_info=True)

    def _try_load_l7(self) -> bool:
        """尝试加载灵极优 L7 存储引擎（走显式插片契约），不可用时降级。

        D5 (2026-09-15): 原硬编码相对路径 + sys.path.insert 污染
        → lingclaude/lacp/l7_engine_seam.py 显式契约。
        只走注册表（register_l7_engine 外部显式注册）——主干零隐式探测；
        需要 L7 时由 lingminopt 侧显式注册，未注册即降级（零副作用）。
        """
        from lingclaude.lacp.l7_engine_seam import load_l7_engine

        handle = load_l7_engine()
        if handle is not None:
            self._l7_store = handle.store
            self._l7_retrieve = handle.retrieve
            return True
        self._l7_store = None
        self._l7_retrieve = None
        return False

    # ── 认知记忆 CRUD ──

    def put_memory(self, mem: CognitiveMemory) -> str:
        conn = self._get_conn()
        # P1③ supersede 链（2026-10-02）：同 key 旧版标记被取代，不再堆双份。
        # 主键是新生成的 mem.id，INSERT OR REPLACE 防不住同 key 增量写入——
        # 原行为下 get_always/ondemand/triggered/search 会把新旧版本全数返回，
        # 召回噪声随写入次数线性膨胀（豆包坑①「记忆膨胀」在本库的真实形态）。
        # 语义：旧条目保留（superseded_by 指向新 id，链可回溯），检索出口过滤。
        # 冲突面：若旧条目已是 superseded（更新竞态），以 updated_at 新者为准续链。
        # P2② 哈希链（同轮）：新记录 prev_digest = 同 key 现行前驱的规范摘要，
        # 链从启用点起算（存量不回填，前驱无 prev_digest 不阻断写入）。
        prev_digest = ""
        try:
            old_rows = conn.execute(
                """SELECT id, rowid, updated_at, value, source, session_id,
                          importance, tier, okf_type, tags, created_at,
                          updated_at, access_count, superseded_by, prev_digest
                   FROM l7_cognitive_memories
                   WHERE key = ? AND superseded_by = ''""",
                (mem.key,),
            ).fetchall()
            if old_rows:
                # 同 key 单活跃链不变式：现行至多一条；竞态超一条时以
                # updated_at 最新者为前驱（与 supersede 续链口径一致），
                # 多余现行在 UPDATE 中一并标记被取代（检索唯一性保住）。
                newest = max(old_rows, key=lambda r: r["updated_at"] or 0)
                superseded_ids = [r["id"] for r in old_rows]
                qmarks = ",".join("?" for _ in superseded_ids)
                safe_execute(
                    conn,
                    f"UPDATE l7_cognitive_memories SET superseded_by = ? "
                    f"WHERE id IN ({qmarks})",
                    (mem.id, *superseded_ids),
                )
                # 指针盖在「安息态」：先 supersede 后重读，摘要对象是前驱
                # 入史后的最终内容——否则 UPDATE 改字段使指针必然失配。
                post = conn.execute(
                    "SELECT * FROM l7_cognitive_memories WHERE id = ?",
                    (newest["id"],),
                ).fetchone()
                if post is not None:
                    prev_digest = _memory_record_digest(
                        {k: post[k] for k in post.keys()})
        except Exception:
            logger.warning("put_memory: supersede/prev_digest 前驱解析失败"
                           "（新记忆照常写入，链该处断点）", exc_info=True)

        safe_execute(conn, """INSERT OR REPLACE INTO l7_cognitive_memories
            (id, key, value, source, session_id, importance, tier, okf_type,
             tags, created_at, updated_at, access_count, superseded_by,
             prev_digest)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mem.id, mem.key, json.dumps(mem.value, ensure_ascii=False),
             mem.source, mem.session_id, mem.importance, mem.tier.value,
             mem.okf_type.value, json.dumps(mem.tags, ensure_ascii=False),
             mem.created_at, mem.updated_at, mem.access_count, "",
             prev_digest),
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

    def get_memory_history(self, key: str) -> list[CognitiveMemory]:
        """P1③ supersede 链回溯：同 key 全版本（现行在前，历史其后按时间降序）。

        检索出口（get_always/ondemand/triggered/search）只回现行版本；
        需要看演变史（审计/争议仲裁/回滚参考）时走本入口。
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM l7_cognitive_memories WHERE key = ?
               ORDER BY CASE WHEN superseded_by = '' THEN 0 ELSE 1 END,
                        updated_at DESC""",
            (key,),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def _row_to_memory(self, row: sqlite3.Row) -> CognitiveMemory:
        # P1③ 加固：value/tags 假定为 JSON，但存量/手写行可能非 JSON——
        # 解析失败回退原文（检索健壮性优先于数据纯度，坏行不该炸掉整个查询）。
        try:
            value = json.loads(row["value"]) if row["value"] else None
        except (ValueError, TypeError):
            value = row["value"]
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except (ValueError, TypeError):
            tags = []
        return CognitiveMemory(
            id=row["id"], key=row["key"],
            value=value,
            source=row["source"], session_id=row["session_id"],
            importance=row["importance"],
            tier=MemoryTier(row["tier"]),
            okf_type=OKFType(row["okf_type"]),
            tags=tags,
            created_at=row["created_at"], updated_at=row["updated_at"],
            access_count=row["access_count"],
            # P1③：存量行迁移失败时无该列 → sqlite3.Row 按名取键 KeyError，
            # 降级返回空（= 视为现行，检索不因迁移失败而丢条目）。
            superseded_by=(row["superseded_by"]
                           if "superseded_by" in row.keys() else ""),
            prev_digest=(row["prev_digest"]
                         if "prev_digest" in row.keys() else ""),
        )

    # ── P2② 哈希链校验 ──

    def verify_claim(self, key: str) -> dict:
        """沿哈希链校验同 key 记忆演变是否被篡改（P2②，2026-10-02）。

        寻父语义：子记录的 prev_digest = 前驱「安息态」（被 supersede 标记
        后）的内容摘要。校验时同 key 全行取回，逐行复算内容摘要建映射，
        从现行条目沿 prev_digest 在映射中向历史逐跳比对。

        返回机器可读结论：

        ``{"claim_ok": bool, "chain_len": int, "pre_chain": bool,
           "breaks": [{"at": id, "reason": str}], "latest": {...}}``

        - ``claim_ok``：链上每条记录内容与指向它的指针一致（未篡改）。
        - ``pre_chain``：链头之前同 key 还有未入链的更早记录（链从启用点
          起算，正常链头此前无记录时为 False）。
        - 边界（诚实声明）：本链锚定的是**历史演变**——任意非头节点被改
          都会使其子记录的指针失配；**现行头节点自身是活值**（可变存储的
          设计语义），其内容不在校验面内，需外部锚点（如 journal 行自摘要
          那样的 head 印章）才能闭合，超出本层职责。
        - 幂等只读，不改库；异常路径 fail-soft 返回 claim_ok=False。
        """
        conn = self._get_conn()
        result: dict = {"claim_ok": True, "chain_len": 0, "pre_chain": False,
                        "breaks": [], "latest": {}}
        try:
            rows = conn.execute(
                "SELECT * FROM l7_cognitive_memories WHERE key = ?", (key,)
            ).fetchall()
            if not rows:
                result["breaks"].append(
                    {"at": "", "reason": "no_active_record_for_key"})
                result["claim_ok"] = False
                return result
            # 内容摘要 → 行 映射（同 key 一次取回一次复算；版本数小，O(n) 足够）。
            digest_map: dict = {}
            current = None
            for r in rows:
                d = _memory_record_digest({c: r[c] for c in r.keys()})
                digest_map[d] = r
                if (r["superseded_by"] or "") == "":
                    # 同 key 单活跃链不变式；竞态多条时取 updated_at 最新。
                    if current is None or (r["updated_at"] or 0) > (current["updated_at"] or 0):
                        current = r
            if current is None:
                result["breaks"].append(
                    {"at": "", "reason": "no_active_record_for_key"})
                result["claim_ok"] = False
                return result
            result["latest"] = {"id": current["id"], "key": current["key"],
                                "updated_at": current["updated_at"]}
            seen: set = set()
            row = current
            while row is not None:
                seen.add(row["id"])
                result["chain_len"] += 1
                pd = row["prev_digest"] if "prev_digest" in row.keys() else ""
                if not pd:
                    # 链头语义分型：全行已入链 = 启用点起的正常链头；
                    # 尚有链外更早记录 = 链头落在 pre-chain 存量之后。
                    result["pre_chain"] = bool(len(rows) > len(seen))
                    break
                parent = digest_map.get(pd)
                if parent is None:
                    result["breaks"].append({
                        "at": row["id"],
                        "reason": "prev_digest_no_match_or_tampered"})
                    result["claim_ok"] = False
                    break
                if parent["id"] in seen:
                    result["breaks"].append(
                        {"at": parent["id"], "reason": "cycle_detected"})
                    result["claim_ok"] = False
                    break
                row = parent
        except Exception as exc:
            result["claim_ok"] = False
            result["breaks"].append({"at": "", "reason": f"internal:{exc}"})
        return result

    # ── 分层记忆检索 ──

    def get_always_context(self, max_items: int = 5) -> list[CognitiveMemory]:
        """Always 层: 重要度 >= 8, 每次会话自动加载（P1③: 只回现行版本）"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM l7_cognitive_memories
               WHERE tier = 'always' AND superseded_by = ''
               ORDER BY importance DESC, updated_at DESC LIMIT ?""",
            (max_items,),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get_ondemand_context(self, query: str, max_items: int = 10) -> list[CognitiveMemory]:
        """OnDemand 层: 重要度 3-7, 语义检索触发（P1③: 只回现行版本）"""
        conn = self._get_conn()
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM l7_cognitive_memories
               WHERE tier = 'ondemand'
               AND superseded_by = ''
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
                   AND superseded_by = ''
                   ORDER BY updated_at DESC LIMIT ?""",
                (okf_type.value, max_items),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE tier = 'triggered'
                   AND superseded_by = ''
                   ORDER BY updated_at DESC LIMIT ?""",
                (max_items,),
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def search(self, query: str, top_k: int = 5, okf_type: OKFType | None = None) -> list[CognitiveMemory]:
        """跨层搜索 - 按 type 过滤（P1③: 只回现行版本）"""
        conn = self._get_conn()
        pattern = f"%{query}%"
        if okf_type:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE (key LIKE ? OR value LIKE ? OR tags LIKE ?)
                   AND okf_type = ? AND superseded_by = ''
                   ORDER BY importance DESC, updated_at DESC LIMIT ?""",
                (pattern, pattern, f'%"{query}"%', okf_type.value, top_k),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM l7_cognitive_memories
                   WHERE (key LIKE ? OR value LIKE ? OR tags LIKE ?)
                   AND superseded_by = ''
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

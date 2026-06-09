"""灵族轻量版记忆引擎 — 基于 M-flow 四层锥形图，SQLite 存储，无 embedding 依赖。

核心数据模型：
  Episode (情景)  — 有界语义焦点：事件、决策、事故
  Facet (维度)    — Episode 的一个切面
  FacetPoint (原子事实) — 从 Facet 提取的原子断言
  Entity (实体)   — 跨 Episode 关联的命名事物

检索方式：标签 + 别名匹配 → Entity 关联 → 沿证据链评分 → 返回最相关 Episode

设计原则：
  - M9 简洁优先：SQLite 单文件，零外部依赖
  - 灵族积累：从 CRUSH.md / LingBus / crush.db 自动摄取
  - 按任务精准唤醒：不是全量注入，是根据当前上下文检索最相关的经验
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from lingclaude.core.safe_db import safe_connect

logger = logging.getLogger(__name__)


# ── 数据模型 ──


class EpisodeType(str, Enum):
    INCIDENT = "incident"
    DECISION = "decision"
    TASK = "task"
    PATTERN = "pattern"
    RULE = "rule"


class EntityType(str, Enum):
    MEMBER = "member"
    TOOL = "tool"
    FILE = "file"
    CONCEPT = "concept"
    METRIC = "metric"


class EdgeType(str, Enum):
    INVOLVED = "involved"
    CAUSED = "caused"
    RELATED = "related"
    CONTRADICTS = "contradicts"


@dataclass
class Episode:
    id: str = ""
    title: str = ""
    body: str = ""
    episode_type: EpisodeType = EpisodeType.INCIDENT
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: str = ""
    weight: float = 1.0
    recall_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Facet:
    id: str = ""
    episode_id: str = ""
    name: str = ""
    body: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]


@dataclass
class FacetPoint:
    id: str = ""
    facet_id: str = ""
    claim: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]


@dataclass
class Entity:
    id: str = ""
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    entity_type: EntityType = EntityType.CONCEPT
    description: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]


@dataclass
class Edge:
    source_id: str = ""
    target_id: str = ""
    edge_type: EdgeType = EdgeType.RELATED
    context: str = ""


# ── 存储层 ──

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    episode_type TEXT DEFAULT 'incident',
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    recall_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS facets (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    body TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facet_points (
    id TEXT PRIMARY KEY,
    facet_id TEXT NOT NULL REFERENCES facets(id) ON DELETE CASCADE,
    claim TEXT NOT NULL,
    tags TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT DEFAULT '[]',
    entity_type TEXT DEFAULT 'concept',
    description TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT DEFAULT 'related',
    context TEXT DEFAULT '',
    PRIMARY KEY (source_id, target_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_episodes_tags ON episodes(tags);
CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type);
CREATE INDEX IF NOT EXISTS idx_episodes_weight ON episodes(weight DESC);
CREATE INDEX IF NOT EXISTS idx_facets_episode ON facets(episode_id);
CREATE INDEX IF NOT EXISTS idx_fp_facet ON facet_points(facet_id);
CREATE INDEX IF NOT EXISTS idx_fp_tags ON facet_points(tags);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
"""


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            root = Path(__file__).parent.parent.parent / ".lingclaude"
            root.mkdir(parents=True, exist_ok=True)
            db_path = str(root / "memory.db")
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = safe_connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Episode CRUD ──

    def put_episode(self, ep: Episode) -> str:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO episodes
               (id, title, body, episode_type, tags, source, created_at, weight, recall_count)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ep.id, ep.title, ep.body, ep.episode_type.value,
             json.dumps(ep.tags, ensure_ascii=False), ep.source,
             ep.created_at, ep.weight, ep.recall_count),
        )
        conn.commit()
        return ep.id

    def get_episode(self, ep_id: str) -> Episode | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        return self._row_to_episode(row) if row else None

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"], title=row["title"], body=row["body"],
            episode_type=EpisodeType(row["episode_type"]),
            tags=json.loads(row["tags"]), source=row["source"],
            created_at=row["created_at"], weight=row["weight"],
            recall_count=row["recall_count"],
        )

    # ── Facet CRUD ──

    def put_facet(self, facet: Facet) -> str:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO facets (id, episode_id, name, body) VALUES (?,?,?,?)",
            (facet.id, facet.episode_id, facet.name, facet.body),
        )
        conn.commit()
        return facet.id

    def get_facets(self, episode_id: str) -> list[Facet]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM facets WHERE episode_id = ?", (episode_id,)
        ).fetchall()
        return [Facet(id=r["id"], episode_id=r["episode_id"],
                      name=r["name"], body=r["body"]) for r in rows]

    # ── FacetPoint CRUD ──

    def put_facet_point(self, fp: FacetPoint) -> str:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO facet_points (id, facet_id, claim, tags) VALUES (?,?,?,?)",
            (fp.id, fp.facet_id, fp.claim, json.dumps(fp.tags, ensure_ascii=False)),
        )
        conn.commit()
        return fp.id

    def get_facet_points(self, facet_id: str) -> list[FacetPoint]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM facet_points WHERE facet_id = ?", (facet_id,)
        ).fetchall()
        return [FacetPoint(id=r["id"], facet_id=r["facet_id"],
                           claim=r["claim"], tags=json.loads(r["tags"])) for r in rows]

    # ── Entity CRUD ──

    def put_entity(self, entity: Entity) -> str:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO entities (id, name, aliases, entity_type, description)
               VALUES (?,?,?,?,?)""",
            (entity.id, entity.name, json.dumps(entity.aliases, ensure_ascii=False),
             entity.entity_type.value, entity.description),
        )
        conn.commit()
        return entity.id

    def find_entity(self, name: str) -> Entity | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM entities WHERE name = ?", (name,)).fetchone()
        if row:
            return self._row_to_entity(row)
        rows = conn.execute("SELECT * FROM entities").fetchall()
        for r in rows:
            aliases = json.loads(r["aliases"])
            if name.lower() in [a.lower() for a in aliases]:
                return self._row_to_entity(r)
        return None

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"], name=row["name"],
            aliases=json.loads(row["aliases"]),
            entity_type=EntityType(row["entity_type"]),
            description=row["description"],
        )

    def list_entities(self, entity_type: EntityType | None = None) -> list[Entity]:
        conn = self._get_conn()
        if entity_type:
            rows = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ?",
                (entity_type.value,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM entities").fetchall()
        return [self._row_to_entity(r) for r in rows]

    # ── Edge CRUD ──

    def put_edge(self, edge: Edge) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO edges (source_id, target_id, edge_type, context)
               VALUES (?,?,?,?)""",
            (edge.source_id, edge.target_id, edge.edge_type.value, edge.context),
        )
        conn.commit()

    def get_neighbors(self, node_id: str) -> list[tuple[str, EdgeType, str]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT target_id, edge_type, context FROM edges WHERE source_id = ?",
            (node_id,),
        ).fetchall()
        back = conn.execute(
            "SELECT source_id, edge_type, context FROM edges WHERE target_id = ?",
            (node_id,),
        ).fetchall()
        result = [(r["target_id"], EdgeType(r["edge_type"]), r["context"]) for r in rows]
        result += [(r["source_id"], EdgeType(r["edge_type"]), r["context"]) for r in back]
        return result

    # ── 检索 ──

    def search_episodes_by_tag(self, tags: list[str], limit: int = 10) -> list[Episode]:
        conn = self._get_conn()
        results: list[Episode] = []
        seen: set[str] = set()
        for tag in tags:
            pattern = f'%"{tag}"%'
            rows = conn.execute(
                """SELECT * FROM episodes
                   WHERE tags LIKE ? OR title LIKE ? OR body LIKE ?
                   ORDER BY weight DESC LIMIT ?""",
                (pattern, f"%{tag}%", f"%{tag}%", limit),
            ).fetchall()
            for r in rows:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    results.append(self._row_to_episode(r))
        results.sort(key=lambda e: e.weight, reverse=True)
        return results[:limit]

    def search_facet_points_by_tag(self, tags: list[str], limit: int = 10) -> list[FacetPoint]:
        conn = self._get_conn()
        results: list[FacetPoint] = []
        seen: set[str] = set()
        for tag in tags:
            pattern = f'%"{tag}"%'
            rows = conn.execute(
                """SELECT * FROM facet_points
                   WHERE tags LIKE ? OR claim LIKE ?
                   LIMIT ?""",
                (pattern, f"%{tag}%", limit),
            ).fetchall()
            for r in rows:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    results.append(FacetPoint(
                        id=r["id"], facet_id=r["facet_id"],
                        claim=r["claim"], tags=json.loads(r["tags"]),
                    ))
        return results[:limit]

    def record_recall(self, ep_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            """UPDATE episodes SET recall_count = recall_count + 1, weight = weight * 1.05
               WHERE id = ?""",
            (ep_id,),
        )
        conn.commit()

    # ── 统计 ──

    def stats(self) -> dict[str, int]:
        conn = self._get_conn()
        return {
            "episodes": conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
            "facets": conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0],
            "facet_points": conn.execute("SELECT COUNT(*) FROM facet_points").fetchone()[0],
            "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        }


# ── 检索引擎 ──


class MemoryRetriever:
    """图路由检索：关键词 → Entity → 关联 Episode → 沿证据链评分。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def retrieve(self, query: str, tags: list[str] | None = None, limit: int = 5) -> list[dict]:
        all_tags = tags or []
        query_lower = query.lower()
        for word in query_lower.split():
            if len(word) >= 2:
                all_tags.append(word)

        scored: dict[str, float] = {}

        direct_eps = self._store.search_episodes_by_tag(all_tags, limit=limit * 3)
        for ep in direct_eps:
            scored[ep.id] = scored.get(ep.id, 0) + ep.weight * 1.0

        for tag in all_tags:
            entity = self._store.find_entity(tag)
            if entity:
                neighbors = self._store.get_neighbors(entity.id)
                for neighbor_id, _etype, _ctx in neighbors:
                    ep = self._store.get_episode(neighbor_id)
                    if ep and ep.id not in scored:
                        scored[ep.id] = scored.get(ep.id, 0) + ep.weight * 0.6

        ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:limit]

        results = []
        for ep_id, score in ranked:
            ep = self._store.get_episode(ep_id)
            if ep:
                self._store.record_recall(ep_id)
                facets = self._store.get_facets(ep_id)
                facet_data = []
                for f in facets:
                    fps = self._store.get_facet_points(f.id)
                    facet_data.append({
                        "name": f.name,
                        "body": f.body,
                        "points": [{"claim": p.claim, "tags": p.tags} for p in fps],
                    })
                results.append({
                    "score": round(score, 3),
                    "episode": {
                        "id": ep.id, "title": ep.title, "body": ep.body,
                        "type": ep.episode_type.value, "tags": ep.tags,
                        "source": ep.source, "weight": ep.weight,
                    },
                    "facets": facet_data,
                })
        return results

    def retrieve_compact(self, query: str, tags: list[str] | None = None, limit: int = 5) -> str:
        results = self.retrieve(query, tags, limit)
        if not results:
            return ""
        lines = []
        for r in results:
            lines.append(f"[{r['score']:.2f}] {r['episode']['title']}")
            for f in r["facets"]:
                for p in f.get("points", []):
                    lines.append(f"  - {p['claim']}")
        return "\n".join(lines)


# ── 摄取管线 ──


class MemoryIngester:
    """从灵族数据源摄取知识到记忆库。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def ingest_rule(
        self, title: str, body: str, tags: list[str],
        source: str = "CRUSH.md", severity: str = "high",
    ) -> str:
        existing = self._store.search_episodes_by_tag(tags[:1], limit=50)
        for ex in existing:
            if ex.title == title:
                return ex.id
        ep = Episode(
            title=title, body=body,
            episode_type=EpisodeType.RULE,
            tags=tags + [severity],
            source=source,
        )
        self._store.put_episode(ep)
        for tag in tags:
            entity = self._store.find_entity(tag)
            if not entity:
                entity = Entity(name=tag, entity_type=EntityType.CONCEPT)
                self._store.put_entity(entity)
            self._store.put_edge(Edge(
                source_id=entity.id, target_id=ep.id,
                edge_type=EdgeType.RELATED,
            ))
        return ep.id

    def ingest_incident(
        self, title: str, body: str, tags: list[str],
        facets: list[dict] | None = None, source: str = "",
    ) -> str:
        existing = self._store.search_episodes_by_tag([title.split()[0]] if title else [], limit=50)
        for ex in existing:
            if ex.title == title:
                return ex.id
        ep = Episode(
            title=title, body=body,
            episode_type=EpisodeType.INCIDENT,
            tags=tags, source=source,
            weight=1.5,
        )
        self._store.put_episode(ep)

        if facets:
            for f_data in facets:
                facet = Facet(episode_id=ep.id, name=f_data.get("name", ""), body=f_data.get("body", ""))
                self._store.put_facet(facet)
                for claim in f_data.get("points", []):
                    fp_tags = f_data.get("tags", tags)
                    fp = FacetPoint(facet_id=facet.id, claim=claim, tags=fp_tags)
                    self._store.put_facet_point(fp)

        for tag in tags:
            entity = self._store.find_entity(tag)
            if not entity:
                entity = Entity(name=tag, entity_type=EntityType.CONCEPT)
                self._store.put_entity(entity)
            self._store.put_edge(Edge(
                source_id=entity.id, target_id=ep.id,
                edge_type=EdgeType.RELATED,
            ))
        return ep.id

    def ingest_member(self, name: str, aliases: list[str], role: str) -> str:
        entity = self._store.find_entity(name)
        if entity:
            entity.aliases = list(set(entity.aliases + aliases))
            entity.description = role
            self._store.put_entity(entity)
            return entity.id
        entity = Entity(name=name, aliases=aliases, entity_type=EntityType.MEMBER, description=role)
        self._store.put_entity(entity)
        return entity.id

    def ingest_crush_rules(self, crush_path: str | Path) -> int:
        path = Path(crush_path)
        if not path.exists():
            return 0
        content = path.read_text(encoding="utf-8")
        count = 0
        rule_sections = {
            "安全三原则": ["safety", "security"],
            "交付铁律": ["delivery", "verification"],
            "记忆铁律": ["memory", "data_safety"],
            "L3 行为规则": ["behavior", "L3"],
        }
        for section, tags in rule_sections.items():
            if section in content:
                start = content.index(section)
                chunk = content[start:start + 500]
                self.ingest_rule(
                    title=f"灵克: {section}",
                    body=chunk[:300],
                    tags=tags,
                    source=str(path),
                )
                count += 1
        return count

    def ingest_ling_family_members(self) -> int:
        members = [
            ("灵克", ["lingclaude", "灵克"], "工程执行者，代码审计与优化"),
            ("灵通", ["lingflow", "灵通"], "工程流，工作流引擎"),
            ("灵研", ["lingresearch", "灵研"], "研究员，认知研究与行为分析"),
            ("灵通+", ["lingflow_plus", "灵通+"], "协调者，全族调度与基础设施"),
            ("灵知", ["lingzhi", "灵知"], "知识管理，知识库系统"),
            ("灵扬", ["lingyang", "灵扬"], "市场推广与传播"),
            ("灵网", ["lingweb", "灵网"], "全栈网站开发"),
            ("灵信", ["lingmessage", "灵信"], "消息总线，异步通讯协议"),
            ("灵犀", ["lingxi", "灵犀"], "MCP终端服务器"),
            ("灵通问道", ["lingtongask", "灵通问道"], "播客内容生成"),
            ("灵极优", ["lingminopt", "灵极优"], "极简自优化框架"),
            ("灵创", ["lingcreate", "灵创"], "多模态生成+3D建模"),
            ("智桥", ["zhibridge", "智桥"], "非成员共享服务"),
        ]
        for name, aliases, role in members:
            self.ingest_member(name, aliases, role)
        return len(members)


# ── 顶层接口 ──


class LingMemory:
    """灵族记忆引擎 — 顶层接口。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.store = MemoryStore(db_path)
        self.retriever = MemoryRetriever(self.store)
        self.ingester = MemoryIngester(self.store)

    def recall(self, query: str, tags: list[str] | None = None, limit: int = 5) -> str:
        return self.retriever.retrieve_compact(query, tags, limit)

    def recall_detailed(self, query: str, tags: list[str] | None = None, limit: int = 5) -> list[dict]:
        return self.retriever.retrieve(query, tags, limit)

    def learn_rule(self, title: str, body: str, tags: list[str], source: str = "") -> str:
        return self.ingester.ingest_rule(title, body, tags, source)

    def learn_incident(
        self, title: str, body: str, tags: list[str],
        facets: list[dict] | None = None, source: str = "",
    ) -> str:
        return self.ingester.ingest_incident(title, body, tags, facets, source)

    def bootstrap(self, crush_path: str | Path | None = None) -> dict[str, int]:
        self.ingester.ingest_ling_family_members()
        count = 0
        if crush_path:
            count = self.ingester.ingest_crush_rules(crush_path)

        self.learn_incident(
            title="灵克杀进程事故 2026-05-15",
            body="灵克手动kill重复进程导致全族中断。根因：kill父进程→子进程全部重启→更多进程。教训：先停wrapper再操作。",
            tags=["灵克", "进程管理", "危险操作", "wrapper"],
            facets=[
                {"name": "根因", "body": "kill父进程导致wrapper restart loop触发更多进程",
                 "points": ["先停wrapper再操作", "不能边跑边杀"], "tags": ["进程管理"]},
                {"name": "教训", "body": "修改运行中的进程管理机制必须先停wrapper",
                 "points": ["H8操作预审必须覆盖kill命令", "系统容错>个体完美"], "tags": ["安全"]},
            ],
            source="灵克会话 2026-05-15",
        )

        self.learn_incident(
            title="Proxy全族静默2h17m 2026-05-14",
            body="GLM配额耗尽→proxy单worker堵死→guardian误判杀proxy→重启失败→guardian自身死亡→无人恢复。",
            tags=["proxy", "灵通+", "GLM", "429", "guardian"],
            facets=[
                {"name": "事故链", "body": "GLM 429→proxy堵死→guardian杀proxy→guardian死亡→2h17m静默",
                 "points": ["守护者悖论：保护机制成为最大威胁", "70+模型可用但90%流量走GLM"], "tags": ["proxy"]},
                {"name": "修复", "body": "MiniMax模型名修正+豆包安全体验模式关闭+全族fallback配置",
                 "points": ["全族统一最小fallback配置", "被动429降级优先于主动均衡"], "tags": ["修复"]},
            ],
            source="灵通+事故报告 2026-05-14",
        )

        self.learn_rule(
            title="M9 简洁优先",
            body="加一条规则必须删一条。200行能减到50行就重写。不写超出需求的功能。",
            tags=["简洁", "规则", "M9"],
            source="灵族共识 2026-05-15",
        )

        self.learn_rule(
            title="系统容错 > 个体完美",
            body="别让灵克更聪明，让系统阻止错误。沙箱隔离>行为规则。验证契约>自律。",
            tags=["容错", "系统设计", "沙箱"],
            source="灵族共识 2026-05-15",
        )

        return {"crush_rules": count, "incidents": 2, "rules": 2}

    def stats(self) -> dict[str, int]:
        return self.store.stats()

    def close(self) -> None:
        self.store.close()

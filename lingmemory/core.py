# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 (lingmemory) — 灵元V1.0薄主干

主干 = 2表 + 3操作
  出入: create(接信息进records) + query(读records)
  流转: transition(状态变化, 内置灰区校验)
  
插片(独立模块, 可插拔):
  fts.py — 全文搜索索引同步
  events.py — 审计日志
  未来: visibility.py, encryption.py, compression.py
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

DB_PATH = Path(__file__).parent / "lingmemory.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
REGISTRY_PATH = Path(__file__).parent / "type_registry.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid() -> str:
    return str(uuid.uuid4())

def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(db_path: Path | str = DB_PATH):
    conn = _connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()


def _maybe_migrate_v0_3(conn: sqlite3.Connection):
    """v0.3 迁移: 添加灰区字段 confidence/bound/escalate_level

    灵元存储落地第一梯度 (2026-07-06):
    - records 表: 加 confidence REAL + bound TEXT 字段
    - events 表: 加 escalate_level INTEGER 字段
    零数据丢失, 现有记录 confidence=1.0, bound=NULL, escalate_level=0
    """
    try:
        conn.execute("SELECT confidence FROM records LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE records ADD COLUMN confidence REAL DEFAULT 1.0")
        conn.execute("ALTER TABLE records ADD COLUMN bound TEXT")
    try:
        conn.execute("SELECT escalate_level FROM events LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE events ADD COLUMN escalate_level INTEGER DEFAULT 0")

    # 索引 (IF NOT EXISTS 风格)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_records_confidence ON records(confidence)",
        "CREATE INDEX IF NOT EXISTS idx_records_bound      ON records(bound)",
        "CREATE INDEX IF NOT EXISTS idx_events_escalate    ON events(escalate_level)",
    ]:
        conn.execute(idx_sql)
    conn.commit()


class TypeRegistry:
    """流转规则 — 插片
    
    定义每种type的合法状态、转换规则、data schema。
    主干代码通过它校验transition合法性(灰区判断前置)。
    """
    def __init__(self, registry_path: Path = REGISTRY_PATH):
        raw = yaml.safe_load(registry_path.read_text())
        self._types = {
            name: spec for name, spec in raw.items()
            if not name.startswith("#") and isinstance(spec, dict) and "states" in spec
        }

    def exists(self, type_name: str) -> bool:
        return type_name in self._types

    def get_default_state(self, type_name: str) -> str:
        return self._types[type_name].get("default_state", "created")

    def get_states(self, type_name: str, variant: str | None = None) -> list[str]:
        spec = self._types[type_name]
        key = f"states_{variant}" if variant else "states"
        return spec.get(key, spec.get("states", []))

    def is_valid_transition(self, type_name: str, from_state: str, event_type: str) -> tuple[bool, str | None]:
        """灰区校验：这条流转合法吗？"""
        spec = self._types.get(type_name, {})
        for t in spec.get("transitions", []):
            if (t["from"] == from_state or t["from"] == "*") and t["event"] == event_type:
                return True, t["to"]
        return False, None

    def validate_data(self, type_name: str, data: dict) -> list[str]:
        """基础数据校验"""
        schema = self._types.get(type_name, {}).get("data_schema", {})
        errors = []
        for field, rule in schema.items():
            if rule.get("required") and field not in data:
                errors.append(f"missing required field: {field}")
                continue
            if field in data and "enum" in rule and data[field] not in rule["enum"]:
                errors.append(f"{field}={data[field]} not in {rule['enum']}")
        return errors


class LingMemory:
    """灵元V1.0薄主干：create(出入) / transition(流转) / query(出入)
    
    三个操作，永远只有三个。
    插片通过参数注入，不硬编码在主干里。
    """

    def __init__(self, db_path: Path | str = DB_PATH):
        self.conn = _connect(db_path)
        _maybe_migrate_v0_3(self.conn)
        self.registry = TypeRegistry()
        self._fts = None   # 插片：全文搜索
        self._events = None  # 插片：审计日志

    def close(self):
        self.conn.close()

    def use_fts(self, fts):
        """注入全文搜索插片"""
        self._fts = fts
        return self

    def use_events(self, events):
        """注入事件审计插片"""
        self._events = events
        return self

    # ============================================================
    # create — 出入：接信息进records
    # ============================================================
    def create(self, type: str, data: dict | None = None,
               parent_id: str | None = None, created_by: str = "system") -> str:
        if data is None: data = {}
        if not self.registry.exists(type):
            raise ValueError(f"unknown type: {type}")
        if errors := self.registry.validate_data(type, data):
            raise ValueError(f"data validation failed: {errors}")

        record_id = _uuid()
        state = self.registry.get_default_state(type)
        now = _now()
        data_str = json.dumps(data, ensure_ascii=False)

        self.conn.execute(
            "INSERT INTO records (id, type, state, data, parent_id, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, type, state, data_str, parent_id, created_by, now, now))

        if self._fts: self._fts.index(record_id, data)
        if self._events: self._events.record(record_id, "create", None, state, created_by, now)
        self.conn.commit()
        return record_id

    # ============================================================
    # transition — 流转：状态变化（内置灰区校验）
    # ============================================================
    def transition(self, record_id: str, event_type: str,
                   actor: str = "system", data: dict | None = None) -> str:
        if data is None: data = {}
        row = self.conn.execute(
            "SELECT type, state FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError(f"record not found: {record_id}")

        type_name, from_state = row["type"], row["state"]
        valid, to_state = self.registry.is_valid_transition(type_name, from_state, event_type)
        if not valid:
            raise ValueError(f"illegal transition: {type_name}.{from_state} --{event_type}--> ?")

        now = _now()
        self.conn.execute("UPDATE records SET state=?, updated_at=? WHERE id=?",
                          (to_state, now, record_id))
        if self._events:
            self._events.record(record_id, event_type, from_state, to_state, actor, now, data)
        self.conn.commit()
        return to_state

    # ============================================================
    # query — 出入：读records（游标分页）
    # ============================================================
    def query(self, type: str | None = None, state: str | None = None,
              parent_id: str | None = None, created_by: str | None = None,
              data_filter: dict | None = None,
              cursor: int | None = None, limit: int = 20) -> dict:
        sql = "SELECT *, rowid as _rowid FROM records WHERE 1=1"
        params: list = []
        self._apply_field_filters(sql, params, type=type, state=state,
                                  parent_id=parent_id, created_by=created_by)
        if cursor:
            sql += " AND _rowid < ?"
            params.append(cursor)
        if data_filter:
            for key, value in data_filter.items():
                sql += f" AND json_extract(data, '$.{key}') = ?"
                params.append(json.dumps(value) if not isinstance(value, str) else value)

        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit + 1)
        rows = self.conn.execute(sql, params).fetchall()
        has_next = len(rows) > limit
        return {
            "items": [self._decode(r) for r in rows[:limit]],
            "next_cursor": rows[limit - 1]["_rowid"] if has_next and rows else None,
        }

    @staticmethod
    def _apply_field_filters(sql: list, params: list, **fields):
        """应用等值字段过滤到 SQL 查询"""
        for key, val in fields.items():
            if val is not None:
                sql += f" AND {key} = ?"
                params.append(val)

    def get(self, record_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        return self._decode(row) if row else None

    def get_children(self, parent_id: str, type: str | None = None) -> list[dict]:
        return self.query(parent_id=parent_id, type=type, limit=1000)["items"]

    # ============================================================
    # 灵元灰区 API (v0.3) — confidence / bound / query_bound
    # ============================================================
    def set_confidence(self, record_id: str, confidence: float,
                       actor: str = "system") -> None:
        """灵元灰区: 设置置信度 0-1

        Args:
            record_id: 记录 ID
            confidence: 置信度 0-1
            actor: 操作者
        """
        if not (0 <= confidence <= 1):
            raise ValueError("confidence must be between 0 and 1")
        now = _now()
        self.conn.execute(
            "UPDATE records SET confidence=?, updated_at=? WHERE id=?",
            (confidence, now, record_id))
        self.conn.execute(
            """INSERT INTO events
               (record_id, event_type, from_state, to_state, actor, data, timestamp)
               VALUES (?, 'confidence_change', NULL, 'no_state_change', ?, ?, ?)""",
            (record_id, actor, json.dumps({"confidence": confidence}), now))
        self.conn.commit()

    def set_bound(self, record_id: str, bound_event: dict,
                  actor: str = "system") -> None:
        """灵元灰区: 记录 bound 事件 (寿命/坏块/临界/温度)

        Args:
            record_id: 记录 ID
            bound_event: bound 事件 dict, 结构如 {"type":"temperature","value":75,"escalate":2}
            actor: 操作者
        """
        now = _now()
        bound_str = json.dumps(bound_event, ensure_ascii=False)
        escalate = bound_event.get("escalate", 0)
        self.conn.execute(
            "UPDATE records SET bound=?, updated_at=? WHERE id=?",
            (bound_str, now, record_id))
        self.conn.execute(
            """INSERT INTO events
               (record_id, event_type, from_state, to_state, actor, escalate_level, data, timestamp)
               VALUES (?, 'bound', NULL, 'no_state_change', ?, ?, ?, ?)""",
            (record_id, actor, escalate, bound_str, now))
        self.conn.commit()

    def query_bound(self, escalate_min: int = 1) -> list[dict]:
        """灵元灰区: 查询所有 escalate >= 阈值的 bound (上行信号)

        Args:
            escalate_min: 最低 escalate 级别 (默认 1)

        Returns:
            list[dict]: 符合条件的 bound 事件记录
        """
        rows = self.conn.execute(
            """SELECT r.id, r.type, r.state, r.bound, r.confidence,
                      e.escalate_level, e.timestamp, e.data
               FROM records r
               JOIN events e ON r.id = e.record_id
               WHERE e.escalate_level >= ? AND e.event_type = 'bound'
               ORDER BY e.escalate_level DESC, e.timestamp DESC""",
            (escalate_min,)).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            if isinstance(item.get("data"), str):
                try:
                    item["data"] = json.loads(item["data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(item)
        return result

    # ============================================================
    # 内部工具
    # ============================================================
    def get_events(self, record_id: str) -> list[dict]:
        return self._events.get_history(record_id) if self._events else []

    @staticmethod
    def _decode(row) -> dict:
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item

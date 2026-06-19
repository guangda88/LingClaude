"""Tests for 灵忆V2.0 基础设施 — split_db / rule_lifecycle / event_miner.

用 tmp_path + monkeypatch 覆盖模块级 DB 常量，绝不碰生产 lingmemory.db。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


# ============================================================
# helpers
# ============================================================

def _utc(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_main_db(path: Path) -> sqlite3.Connection:
    """建一个和 lingmemory/schema.sql 兼容的主库(records+events)。"""
    conn = sqlite3.connect(str(path))
    conn.executescript((Path(__file__).resolve().parent.parent /
                        "lingmemory" / "schema.sql").read_text())
    return conn


def _make_rules_db(path: Path) -> sqlite3.Connection:
    """建一个 split 后的 rule 库(records 带 last_hit 列)。"""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'created',
            data TEXT,
            parent_id TEXT,
            created_by TEXT DEFAULT 'system',
            created_at TEXT,
            updated_at TEXT,
            last_hit TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            record_id TEXT,
            event_type TEXT,
            from_state TEXT,
            to_state TEXT,
            actor TEXT,
            ts TEXT,
            data TEXT
        );
    """)
    return conn


def _insert_record(conn, rtype, state="created", data=None, rid=None,
                   last_hit=None, created_by="test"):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
    if "last_hit" in cols:
        conn.execute(
            "INSERT INTO records (id,type,state,data,parent_id,created_by,created_at,updated_at,last_hit) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rid or str(uuid.uuid4()), rtype, state, json.dumps(data or {}),
             None, created_by, _utc(10), _utc(10), last_hit),
        )
    else:
        conn.execute(
            "INSERT INTO records (id,type,state,data,parent_id,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid or str(uuid.uuid4()), rtype, state, json.dumps(data or {}),
             None, created_by, _utc(10), _utc(10)),
        )


# ============================================================
# split_db
# ============================================================

class TestSplitDb:

    def test_split_distributes_by_type(self, monkeypatch, tmp_path):
        from lingmemory import split_db as mod
        main_path = tmp_path / "lingmemory.db"
        rules_path = tmp_path / "lingmemory_rules.db"
        events_path = tmp_path / "lingmemory_events.db"
        monkeypatch.setattr(mod, "MAIN_DB", main_path)
        monkeypatch.setattr(mod, "RULES_DB", rules_path)
        monkeypatch.setattr(mod, "EVENTS_DB", events_path)

        conn = _make_main_db(main_path)
        # rule 类
        _insert_record(conn, "coding_rule", state="validated",
                       data={"rule": "no eval"}, created_by="lc")
        _insert_record(conn, "ops_rule", state="validated",
                       data={"rule": "check mem"})
        # event 类
        _insert_record(conn, "audit_finding", state="open",
                       data={"check_id": "X", "severity": "high"})
        # code_trace 同时在 RULE_TYPES 和 EVENT_TYPES —— split_db 先判 RULE_TYPES,进rule库
        _insert_record(conn, "code_trace", state="created",
                       data={"language": "python", "test_result": "pass"})
        conn.commit()
        conn.close()

        mod.split_db()

        # rule 库应有 coding_rule/ops_rule/code_trace
        r = sqlite3.connect(str(rules_path))
        r.row_factory = sqlite3.Row
        rtypes = {row[0] for row in r.execute(
            "SELECT DISTINCT type FROM records")}
        assert "coding_rule" in rtypes
        assert "ops_rule" in rtypes
        assert "code_trace" in rtypes  # code_trace 属于 RULE_TYPES
        assert "audit_finding" not in rtypes
        r.close()

        # event 库应有 audit_finding
        e = sqlite3.connect(str(events_path))
        etypes = {row[0] for row in e.execute(
            "SELECT DISTINCT type FROM records")}
        assert "audit_finding" in etypes
        assert "coding_rule" not in etypes
        e.close()

    def test_split_creates_backup(self, monkeypatch, tmp_path):
        from lingmemory import split_db as mod
        main_path = tmp_path / "lingmemory.db"
        monkeypatch.setattr(mod, "MAIN_DB", main_path)
        monkeypatch.setattr(mod, "RULES_DB", tmp_path / "r.db")
        monkeypatch.setattr(mod, "EVENTS_DB", tmp_path / "e.db")

        conn = _make_main_db(main_path)
        _insert_record(conn, "coding_rule", data={"r": 1})
        conn.commit()
        conn.close()

        mod.split_db()

        backup = main_path.with_suffix(".db.bak_v2")
        assert backup.exists()
        # 备份内容应和原库一致
        b = sqlite3.connect(str(backup))
        assert b.execute("SELECT COUNT(*) FROM records").fetchone()[0] >= 1
        b.close()

    def test_split_missing_main_db_noop(self, monkeypatch, tmp_path):
        from lingmemory import split_db as mod
        monkeypatch.setattr(mod, "MAIN_DB", tmp_path / "nope.db")
        monkeypatch.setattr(mod, "RULES_DB", tmp_path / "r.db")
        monkeypatch.setattr(mod, "EVENTS_DB", tmp_path / "e.db")
        # 主库不存在 → 静默返回
        mod.split_db()
        assert not (tmp_path / "r.db").exists()


# ============================================================
# rule_lifecycle
# ============================================================

class TestRuleLifecycle:

    @pytest.fixture
    def rules_db(self, monkeypatch, tmp_path):
        from lingmemory import rule_lifecycle as mod
        path = tmp_path / "rules.db"
        monkeypatch.setattr(mod, "RULES_DB", path)
        conn = _make_rules_db(path)
        return conn

    def test_mark_stale_rules(self, rules_db, tmp_path):
        from lingmemory import rule_lifecycle as mod
        # 三条 validated rule: 老/新/无 last_hit
        _insert_record(rules_db, "coding_rule", state="validated",
                       data={"rule": "old"}, last_hit=_utc(40))
        _insert_record(rules_db, "coding_rule", state="validated",
                       data={"rule": "fresh"}, last_hit=_utc(1))
        _insert_record(rules_db, "coding_rule", state="validated",
                       data={"rule": "never_hit"}, last_hit=None)
        # 一条非 validated 不应被动
        _insert_record(rules_db, "coding_rule", state="hypothesized",
                       data={"rule": "hypo"}, last_hit=_utc(40))
        rules_db.commit()
        rules_db.close()

        marked = mod.mark_stale_rules()
        # 老(40天)+never_hit(NULL) = 2条; fresh(1天)不动; hypothesized不动
        assert marked == 2

        conn = sqlite3.connect(str(tmp_path / "rules.db"))
        states = {row[0]: row[1] for row in conn.execute(
            "SELECT json_extract(data,'$.rule'), state FROM records")}
        assert states["old"] == "review"
        assert states["never_hit"] == "review"
        assert states["fresh"] == "validated"
        assert states["hypo"] == "hypothesized"
        conn.close()

    def test_hit_rule_revives_review(self, rules_db, tmp_path):
        from lingmemory import rule_lifecycle as mod
        rid = str(uuid.uuid4())
        rules_db.execute(
            "INSERT INTO records (id,type,state,data,created_by,created_at,updated_at,last_hit) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, "coding_rule", "review", '{"rule":"x"}', "t", _utc(1), _utc(1), _utc(40)))
        rules_db.commit()
        rules_db.close()

        mod.hit_rule(rid)

        conn = sqlite3.connect(str(tmp_path / "rules.db"))
        row = conn.execute(
            "SELECT state, last_hit FROM records WHERE id=?", (rid,)).fetchone()
        assert row[0] == "validated"  # review → validated
        assert row[1] is not None  # last_hit 更新了
        conn.close()

    def test_archive_rule(self, rules_db, tmp_path):
        from lingmemory import rule_lifecycle as mod
        rid = str(uuid.uuid4())
        rules_db.execute(
            "INSERT INTO records (id,type,state,data,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, "coding_rule", "validated", '{"rule":"bad"}', "t", _utc(1), _utc(1)))
        rules_db.commit()
        rules_db.close()

        mod.archive_rule(rid, "验证失败")

        conn = sqlite3.connect(str(tmp_path / "rules.db"))
        state = conn.execute(
            "SELECT state FROM records WHERE id=?", (rid,)).fetchone()[0]
        assert state == "deprecated"
        conn.close()

    def test_rule_stats(self, rules_db, tmp_path):
        from lingmemory import rule_lifecycle as mod
        _insert_record(rules_db, "coding_rule", state="validated", data={})
        _insert_record(rules_db, "coding_rule", state="review", data={})
        _insert_record(rules_db, "ops_rule", state="validated", data={})
        rules_db.commit()
        rules_db.close()

        s = mod.rule_stats()
        assert s["total"] == 3
        assert s["by_state"]["validated"] == 2
        assert s["by_state"]["review"] == 1
        assert s["by_type"]["coding_rule"] == 2


# ============================================================
# event_miner
# ============================================================

class TestEventMiner:

    def test_mine_failure_patterns(self, monkeypatch, tmp_path):
        from lingmemory import event_miner as mod
        main_path = tmp_path / "lingmemory.db"
        rules_path = tmp_path / "rules.db"
        monkeypatch.setattr(mod, "MAIN_DB", main_path)
        monkeypatch.setattr(mod, "RULES_DB", rules_path)

        conn = _make_main_db(main_path)
        # 6条 python fail (>=5 阈值)
        for i in range(6):
            conn.execute(
                "INSERT INTO records (id,type,state,data,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "code_trace", "created",
                 json.dumps({"language": "python", "test_result": "fail",
                             "prompt": "fix stream proxy error"}), "t", _utc(1), _utc(1)))
        # 2条 python pass (不触发)
        for i in range(2):
            conn.execute(
                "INSERT INTO records (id,type,state,data,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "code_trace", "created",
                 json.dumps({"language": "python", "test_result": "pass"}), "t", _utc(1), _utc(1)))
        conn.commit()
        conn.close()

        rules = mod.mine_failure_patterns()
        # 应发现 python fail 模式 + stream/proxy 关键词模式
        rule_text = " ".join(r["rule"] for r in rules)
        assert "python" in rule_text.lower()
        # 关键词 stream/proxy 各6次 >= 5 阈值
        assert "stream" in rule_text.lower() or "proxy" in rule_text.lower()
        for r in rules:
            assert 0 < r["confidence"] <= 0.9

    def test_mine_switch_patterns_no_db(self, monkeypatch, tmp_path):
        from lingmemory import event_miner as mod
        # flywheel_db 不存在 → 返回空
        monkeypatch.setattr(mod, "RULES_DB", tmp_path / "r.db")
        monkeypatch.setattr("lingmemory.event_miner.Path.home",
                            lambda: tmp_path)
        rules = mod.mine_switch_patterns()
        assert rules == []

    def test_mine_switch_patterns_with_data(self, monkeypatch, tmp_path):
        from lingmemory import event_miner as mod
        flywheel_path = tmp_path / ".lingclaude" / "proxy21_flywheel.db"
        flywheel_path.parent.mkdir(parents=True)
        monkeypatch.setattr("lingmemory.event_miner.Path.home",
                            lambda: tmp_path)
        monkeypatch.setattr(mod, "RULES_DB", tmp_path / "r.db")

        conn = sqlite3.connect(str(flywheel_path))
        conn.execute("""
            CREATE TABLE code_trace (
                model TEXT, provider TEXT, switched INTEGER, status INTEGER
            )
        """)
        # modelA@provX 切换3次
        for i in range(3):
            conn.execute(
                "INSERT INTO code_trace VALUES (?,?,?,?)",
                ("modelA", "provX", 1, 200))
        # modelB@provY 失败4次
        for i in range(4):
            conn.execute(
                "INSERT INTO code_trace VALUES (?,?,?,?)",
                ("modelB", "provY", 0, 500))
        conn.commit()
        conn.close()

        rules = mod.mine_switch_patterns()
        rule_text = " ".join(r["rule"] for r in rules)
        assert "modelA@provX" in rule_text  # 切换模式
        assert "modelB@provY" in rule_text  # 失败模式

    def test_save_mined_rules(self, monkeypatch, tmp_path):
        from lingmemory import event_miner as mod
        rules_path = tmp_path / "rules.db"
        monkeypatch.setattr(mod, "RULES_DB", rules_path)
        conn = _make_rules_db(rules_path)
        conn.close()

        rules = [
            {"rule": "test rule A", "category": "coding",
             "evidence": "ev", "confidence": 0.5},
            {"rule": "test rule B", "category": "ops",
             "evidence": "ev2", "confidence": 0.8},
        ]
        saved = mod.save_mined_rules(rules)
        assert saved == 2

        conn = sqlite3.connect(str(rules_path))
        rows = conn.execute(
            "SELECT type, state, json_extract(data,'$.rule') FROM records").fetchall()
        assert len(rows) == 2
        assert all(r[0] == "coding_rule" for r in rows)
        assert all(r[1] == "hypothesized" for r in rows)
        conn.close()

    def test_save_empty_rules(self, monkeypatch, tmp_path):
        from lingmemory import event_miner as mod
        monkeypatch.setattr(mod, "RULES_DB", tmp_path / "rules.db")
        assert mod.save_mined_rules([]) == 0

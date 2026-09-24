"""Tests for lingclaude.core.lingmemory_token_bridge — P3.4 TokenMonitor 双写灵忆.

覆盖（行为级，承 P3.3 家族模板）：
- 双写闭环：record_usage → 灵忆 3 条镜像（input/output/total 分立，registry P3-14）
- 追加型 telemetry 语义：重复 record 不去重，事实流追加留史
- 缺省字段：metadata 缺省 → 镜像不含 session_ref；optional 字段不落 None
- 开关关闭：不写灵忆、不建实例（默认行为与历史版本一致）
- 旁路纪律：sink 抛异常不影响主路（主路吞 + warning）
- 桥内熔断：lingmemory 不可用 → 首错停摆，后续静默
- 自指卫兵：桥内/主路 _emitting 双保险，不递归
- 线程隔离：跨线程 record 不炸（每线程独立灵忆实例）
- 向后兼容：不传 legacy_sink 的 TokenMonitor 行为不变
"""
from __future__ import annotations

import threading

import pytest

from lingclaude.core.lingmemory_bridge import dualwrite_enabled
from lingclaude.core.lingmemory_token_bridge import LingMemoryTokenSink
from lingclaude.core.token_monitor import TokenMonitor


@pytest.fixture()
def pair(tmp_path, monkeypatch):
    """独立灵忆库 + 强制开双写开关（不碰生产库 lingmemory.db）"""
    monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
    db = tmp_path / "lingmemory_test.db"
    from lingmemory import init_db

    init_db(db)
    sink = LingMemoryTokenSink(db_path=db)
    monitor = TokenMonitor(db_path=tmp_path / "token.db", legacy_sink=sink)
    return monitor, sink, db


def _recs(lm, **kw):
    return lm.query(type="token_usage_record", **kw)["items"]


def _record(tm, **kw):
    defaults = dict(model="GLM-4.7", task_type="test", total_tokens=100,
                    input_tokens=60, output_tokens=40)
    defaults.update(kw)
    tm.record_usage(**defaults)


class TestDualWrite:
    def test_record_usage_mirrors_three_records(self, pair):
        tm, _, db = pair
        _record(tm, metadata={"session_ref": "s1"})
        from lingmemory import LingMemory

        lm = LingMemory(db_path=db)
        items = _recs(lm)
        assert len(items) == 3
        by_kind = {i["data"]["usage_kind"]: i["data"] for i in items}
        assert set(by_kind) == {"input", "output", "total"}
        assert by_kind["input"]["token_count"] == 60
        assert by_kind["output"]["token_count"] == 40
        assert by_kind["total"]["token_count"] == 100
        # optional 字段镜像
        assert by_kind["total"]["model_name"] == "GLM-4.7"
        assert by_kind["total"]["task_type"] == "test"
        assert by_kind["total"]["session_ref"] == "s1"
        # default_state 走 registry（recorded）
        assert all(i["state"] == "recorded" for i in items)

    def test_append_semantics_no_dedup(self, pair):
        """追加型 telemetry：与前四桥状态实体语义相反，重复 record 追加不合并"""
        tm, _, db = pair
        _record(tm)
        _record(tm)
        from lingmemory import LingMemory

        assert len(_recs(LingMemory(db_path=db))) == 6

    def test_missing_metadata_omits_session_ref(self, pair):
        tm, _, db = pair
        _record(tm)  # metadata=None
        from lingmemory import LingMemory

        items = _recs(LingMemory(db_path=db))
        assert all("session_ref" not in i["data"] for i in items)
        assert all("model_name" in i["data"] for i in items)

    def test_switch_off_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "")
        assert not dualwrite_enabled()
        db = tmp_path / "lm.db"
        from lingmemory import init_db, LingMemory

        init_db(db)
        sink = LingMemoryTokenSink(db_path=db)
        tm = TokenMonitor(db_path=tmp_path / "t.db", legacy_sink=sink)
        _record(tm)
        assert _recs(LingMemory(db_path=db)) == []


class TestBypassDiscipline:
    def test_bomb_sink_does_not_break_main_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")

        class BombSink:
            def on_usage(self, u):
                raise RuntimeError("bomb")

        tm = TokenMonitor(db_path=tmp_path / "t.db", legacy_sink=BombSink())
        _record(tm)  # 主路必须无异常走完
        _record(tm)  # 再来一次也不炸
        # 主路数据权威不受影响
        stats = tm.get_daily_stats()
        assert stats.total_tokens == 200

    def test_bridge_fuse_on_unavailable_lingmemory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        import lingclaude.core.lingmemory_bridge as lb

        monkeypatch.setattr(lb, "_get_lingmemory", lambda: None)
        sink = LingMemoryTokenSink(db_path=tmp_path / "unused.db")
        tm = TokenMonitor(db_path=tmp_path / "t.db", legacy_sink=sink)
        _record(tm)  # 主路不受影响
        assert sink._broken is True  # 桥内熔断置位
        _record(tm)  # 熔断后静默跳过，不再重复尝试
        assert sink._broken is True

    def test_sink_reentry_guard(self, pair):
        """桥内 _emitting 卫兵：sink 链路内回触 on_usage 直接短路"""
        tm, sink, _ = pair
        sink._emitting = True
        sink.on_usage({"model": "X", "input_tokens": 1})  # 应直接 return
        sink._emitting = False

    def test_monitor_reentry_guard(self, tmp_path, monkeypatch):
        """主路 _emitting 卫兵：sink 回触 record_usage 时业务写入保留，
        但其镜像被短路（防镜像递归；业务写是用户显式行为，不拦）"""
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        from lingmemory import init_db, LingMemory

        db = tmp_path / "lm.db"
        init_db(db)

        class ReentrantSink:
            def __init__(self):
                self.tm = None

            def on_usage(self, u):
                self.tm.record_usage(model="reentry", task_type="t",
                                     total_tokens=1, input_tokens=1,
                                     output_tokens=0)

        sink = ReentrantSink()
        tm = TokenMonitor(db_path=tmp_path / "t.db", legacy_sink=sink)
        sink.tm = tm
        _record(tm)
        # 递归终止：单 dict emit 下 reentry 恰回触 1 次；其业务写入保留，
        # 其镜像被主路卫兵短路（_emitting 窗口内）
        import sqlite3

        conn = sqlite3.connect(tmp_path / "t.db")
        rows = conn.execute(
            "SELECT model, total_tokens FROM usage_records").fetchall()
        conn.close()
        assert len(rows) == 2  # 原 1 条 + reentry 1 条（业务写不拦）
        assert {r[0] for r in rows} == {"GLM-4.7", "reentry"}
        # 镜像侧：本测试 sink=ReentrantSink（不镜像灵忆），
        # 且 reentry 回触的镜像被主路卫兵短路 → 灵忆侧恰 0 条
        assert _recs(LingMemory(db_path=db)) == []


class TestThreadIsolation:
    def test_cross_thread_records(self, pair):
        """跨线程 record 不炸（threading.local 每线程独立灵忆实例）"""
        tm, _, db = pair
        errors: list[Exception] = []

        def work():
            try:
                _record(tm, model="thread-worker")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        from lingmemory import LingMemory

        # 3 线程 × 3 条 = 9
        assert len(_recs(LingMemory(db_path=db))) == 9


class TestBackwardCompat:
    def test_no_sink_unchanged(self, tmp_path):
        """不传 legacy_sink：行为与历史版本一致"""
        tm = TokenMonitor(db_path=tmp_path / "t.db")
        _record(tm)
        stats = tm.get_daily_stats()
        assert stats.total_tokens == 100
        assert stats.input_tokens == 60
        assert stats.output_tokens == 40

    def test_default_db_path_untouched_by_bridge(self, tmp_path, monkeypatch):
        """工厂装配形态：wiring 侧 sink=None 时 Monitor 不持旁观者"""
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "")
        tm = TokenMonitor(db_path=tmp_path / "t.db", legacy_sink=None)
        assert tm._legacy_sink is None
        _record(tm)  # 无 sink 也必须正常走完


class TestSessionBacklink:
    def test_session_id_metadata_backlinks(self, pair):
        """清偿③: D3 sink 链传 session_id（非 session_ref），镜像须带回链。"""
        monitor, sink, db = pair
        _record(monitor, metadata={"session_id": "sess-9"})
        from lingmemory import LingMemory

        items = _recs(LingMemory(db_path=db))
        assert items, "镜像应存在"
        assert all(i["data"].get("session_ref") == "sess-9" for i in items)

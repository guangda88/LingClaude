"""P3.2 灵忆双写桥接器测试

覆盖：
- 双写闭环：ContextCache.read_file → 灵忆 record 落库（active）
- 逐出闭环：invalidate/cleanup_expired → transition(evicted)
- 开关关闭：不写灵忆、不建实例（默认行为与历史版本一致）
- 旁路纪律：sink 抛异常不影响主路返回值
- 熔断：桥接器首错停摆，不再重复写
"""
from __future__ import annotations

import pytest

from lingclaude.core.context_cache import ContextCache
from lingclaude.core.lingmemory_bridge import (
    LingMemoryCacheBridge,
    dualwrite_enabled,
)


@pytest.fixture()
def lm_pair(tmp_path, monkeypatch):
    """独立灵忆库 + 强制开双写开关（不碰生产库 lingmemory.db）"""
    monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
    db = tmp_path / "lingmemory_test.db"
    from lingmemory import init_db

    init_db(db)
    bridge = LingMemoryCacheBridge(db_path=db)
    cache = ContextCache(
        cache_size=5, ttl_hours=24,
        db_path=tmp_path / "cache.db", memory_sink=bridge,
    )
    return cache, bridge, db


def _recs(lm, **kw):
    return lm.query(type="context_cache", **kw)["items"]


class TestDualWrite:
    def test_store_creates_active_record(self, lm_pair, tmp_path):
        cache, bridge, db = lm_pair
        f = tmp_path / "hello.txt"
        f.write_text("hello 灵忆", encoding="utf-8")

        cache.read_file(str(f))

        lm = bridge._lm
        items = _recs(lm)
        assert len(items) == 1
        rec = items[0]
        assert rec["state"] == "active"
        assert rec["data"]["cache_key"] == str(f)
        assert rec["data"]["content"] == "hello 灵忆"
        assert rec["data"]["layer"] == "l1"
        assert rec["created_by"] == "lingclaude.context_cache"

    def test_re_read_no_duplicate(self, lm_pair, tmp_path):
        """同文件重复 read 不产生重复 record（单文件单 record 语义）"""
        cache, bridge, _ = lm_pair
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        cache.read_file(str(f))
        cache.read_file(str(f))  # 内存命中
        cache.read_file(str(f))  # 再次命中
        assert len(_recs(bridge._lm)) == 1

    def test_invalidate_transitions_evicted(self, lm_pair, tmp_path):
        cache, bridge, _ = lm_pair
        f = tmp_path / "b.txt"
        f.write_text("y", encoding="utf-8")
        cache.read_file(str(f))
        rid = bridge._record_ids[str(f)]
        cache.invalidate(str(f))
        assert bridge._lm.get(rid)["state"] == "evicted"

    def test_cleanup_all_transitions_evicted(self, lm_pair, tmp_path):
        from datetime import datetime, timedelta, timezone

        cache, bridge, _ = lm_pair
        for name in ("c", "d"):
            f = tmp_path / f"{name}.txt"
            f.write_text(name, encoding="utf-8")
            cache.read_file(str(f))
        cache.cleanup_expired()  # ttl 内未过期也会被 cutoff 扫到吗？——不，只扫过期的
        # cleanup_expired 只清理过期条目；这里全部新鲜 → 无逐出
        assert len(_recs(bridge._lm)) == 2
        # 手动把 db 条目改成过期，再清
        import sqlite3

        conn = sqlite3.connect(cache.db_path)
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        conn.execute("UPDATE cache_entries SET last_read_at = ?", (past,))
        conn.commit()
        conn.close()
        n = cache.cleanup_expired()
        assert n == 2
        states = {r["state"] for r in _recs(bridge._lm)}
        assert states == {"evicted"}

    def test_record_id_reused_after_invalidate_then_reread(self, lm_pair, tmp_path):
        """invalidate 后重读：新 store → record_ids 里无旧映射 → create 新 record
        （旧 record 已 evicted，新 record active，符合状态机语义）"""
        cache, bridge, _ = lm_pair
        f = tmp_path / "e.txt"
        f.write_text("z", encoding="utf-8")
        cache.read_file(str(f))
        old_rid = bridge._record_ids.pop(str(f))
        cache.invalidate(str(f))
        cache.read_file(str(f))
        new_rid = bridge._record_ids[str(f)]
        assert new_rid != old_rid
        assert bridge._lm.get(old_rid)["state"] == "evicted"
        assert bridge._lm.get(new_rid)["state"] == "active"


class TestSwitchOff:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        assert dualwrite_enabled() is False

    def test_no_sink_when_disabled(self, monkeypatch):
        """开关关闭：装配层不建桥实例"""
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        from lingclaude.core.lingmemory_bridge import dualwrite_enabled as dw
        assert dw() is False

    def test_sink_events_ignored_when_disabled(self, lm_pair, tmp_path, monkeypatch):
        """开关中途关闭：事件被静默丢弃"""
        cache, bridge, _ = lm_pair
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        f = tmp_path / "f.txt"
        f.write_text("w", encoding="utf-8")
        cache.read_file(str(f))
        assert bridge._record_ids == {}


class TestBypassDiscipline:
    def test_sink_exception_does_not_break_main_path(self, tmp_path):
        """旁路纪律：sink 崩溃不影响主路读文件"""
        class BombSink:
            def on_store(self, **kw):
                raise RuntimeError("boom")

        cache = ContextCache(
            cache_size=5, ttl_hours=24,
            db_path=tmp_path / "cache.db", memory_sink=BombSink(),
        )
        f = tmp_path / "g.txt"
        f.write_text("boom-content", encoding="utf-8")
        # 若旁路异常外泄，这里会抛 RuntimeError
        content, hit = cache.read_file(str(f))
        assert content == "boom-content"
        assert hit is False

    def test_bridge_fuse_on_failure(self, lm_pair, tmp_path, monkeypatch):
        """桥接器熔断：灵忆 create 抛错后停摆，不再重复尝试"""
        cache, bridge, _ = lm_pair
        calls = {"n": 0}

        def exploding_create(*a, **kw):
            calls["n"] += 1
            raise ValueError("db locked")

        # 预置一个坏的 _lm 触发 _trip（query 正常返回空，让流程走到 create 再炸）
        bridge._lm = type("BadLM", (), {
            "query": lambda self, **kw: {"items": []},
            "create": exploding_create,
        })()
        f = tmp_path / "h.txt"
        f.write_text("k", encoding="utf-8")
        cache.read_file(str(f))  # 旁路失败，主路不受影响
        assert calls["n"] == 1  # 只试一次
        assert bridge._broken is True
        # 后续事件不再触达灵忆
        cache.read_file(str(f), force_refresh=True)
        assert calls["n"] == 1


class TestWiringIntegration:
    def test_make_cache_attaches_bridge_when_enabled(self, monkeypatch):
        """装配层：开关开 → memory_sink 是桥实例；开关关 → None"""
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        from lingclaude.core.wiring import _make_cache

        class FakeCtx:
            engine = None

        cache = _make_cache(FakeCtx())
        assert isinstance(cache.memory_sink, LingMemoryCacheBridge)

    def test_make_cache_no_bridge_when_disabled(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        from lingclaude.core.wiring import _make_cache

        class FakeCtx:
            engine = None

        cache = _make_cache(FakeCtx())
        assert cache.memory_sink is None

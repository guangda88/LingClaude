"""双写对账器测试（建闸期②）。用内存版假灵忆后端，不依赖真实 PostgreSQL。

覆盖：三值判定（consistent/drift/error）/ 缺失方向性 / lingyi 不可达不误判
drift / 报告聚合语义（ok/冻结/待恢复）。
"""
from __future__ import annotations

import pytest

from lingclaude.engine.state_reconcile import (
    VERDICT_CONSISTENT,
    VERDICT_DRIFT,
    VERDICT_ERROR,
    StateReconciler,
    payload_hash,
)
from lingclaude.core.state_store import JsonFileBackend


class FakeLingYi:
    """内存版灵忆后端：同协议（async load/save/list_keys）。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict] = {}
        self.unreachable = False

    async def load(self, record_type, key, root=None):
        if self.unreachable:
            raise RuntimeError("lingyi down")
        return self._data.get((record_type, key))

    async def save(self, record_type, key, payload, root=None):
        if self.unreachable:
            raise RuntimeError("lingyi down")
        self._data[(record_type, key)] = payload

    async def list_keys(self, record_type, root=None):
        if self.unreachable:
            raise RuntimeError("lingyi down")
        return sorted(k for (t, k) in self._data if t == record_type)


def ly_save(ly, record_type, key, payload):
    """测试助手：驱动 async save（裸调用 async 函数不会执行）。"""
    import asyncio
    asyncio.run(ly.save(record_type, key, payload))


@pytest.fixture()
def env(tmp_path):
    jb = JsonFileBackend(root=tmp_path / "json")
    ly = FakeLingYi()
    return jb, ly, StateReconciler(json_backend=jb, lingyi_backend=ly)


def test_hash_normalization_ignores_key_order_and_whitespace():
    import json as _json
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = _json.loads(_json.dumps(a, indent=4))  # 重新序列化键序不变，但换个写法
    b2 = {"a": {"x": 3, "y": 2}, "b": 1}       # 键序不同、内容相同
    assert payload_hash(a) == payload_hash(b2)
    assert payload_hash(a) != payload_hash({**a, "b": 2})


def test_consistent_identical_payload(env):
    jb, ly, rec = env
    jb.save("task", "t1", {"status": "done", "n": 1})
    ly_save(ly, "task", "t1", {"n": 1, "status": "done"})  # 键序不同，语义相同
    report = rec.reconcile("task")
    assert report.ok()
    assert report.counts[VERDICT_CONSISTENT] == 1
    v = report.verdicts[0]
    assert v.json_hash == v.lingyi_hash


def test_drift_content_mismatch(env):
    jb, ly, rec = env
    jb.save("task", "t1", {"status": "done"})
    ly_save(ly, "task", "t1", {"status": "running"})
    report = rec.reconcile("task")
    assert not report.ok() and report.has_drift
    assert report.verdicts[0].verdict == VERDICT_DRIFT
    assert report.verdicts[0].json_hash != report.verdicts[0].lingyi_hash
    assert "冻结" in report.summary()


def test_drift_missing_in_lingyi(env):
    jb, ly, rec = env
    jb.save("task", "t1", {"status": "done"})
    report = rec.reconcile("task")
    assert report.has_drift
    assert report.verdicts[0].detail == "missing_in_lingyi"


def test_drift_missing_in_json(env):
    jb, ly, rec = env
    ly_save(ly, "task", "ghost", {"extra": True})
    report = rec.reconcile("task")
    assert report.has_drift
    assert report.verdicts[0].detail == "missing_in_json"


def test_lingyi_unreachable_is_error_not_drift(env):
    """三值语义核心：不可达 ≠ 写偏，error 不冻结迁移但阻止读切换判定。"""
    jb, ly, rec = env
    jb.save("task", "t1", {"status": "done"})
    ly.unreachable = True
    report = rec.reconcile("task")
    assert not report.ok()
    assert report.has_error and not report.has_drift
    v = report.verdicts[0]
    assert v.verdict == VERDICT_ERROR and "lingyi down" in v.detail
    assert "不可达" in report.summary() and "不判写偏" in report.summary()


def test_explicit_keys_reconcile_subset(env):
    jb, ly, rec = env
    jb.save("task", "a", {"x": 1})
    jb.save("task", "b", {"x": 2})
    ly_save(ly, "task", "a", {"x": 1})
    ly_save(ly, "task", "b", {"x": 999})
    report = rec.reconcile("task", keys=["a"])  # 只对账 a
    assert report.ok() and len(report.verdicts) == 1


def test_empty_both_sides_ok(env):
    jb, ly, rec = env
    report = rec.reconcile("nothing_here")
    assert report.ok()


def test_reconciler_does_not_write_either_side(env):
    """对账只读：跑完对账，两侧数据不变。"""
    jb, ly, rec = env
    jb.save("task", "t1", {"v": 1})
    ly_save(ly, "task", "t1", {"v": 1})
    before_json = jb.load("task", "t1")
    before_ly = dict(ly._data)
    rec.reconcile("task")
    assert jb.load("task", "t1") == before_json
    assert ly._data == before_ly

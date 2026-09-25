"""免事件化白名单 event_exempt 测试（建闸期④）。

覆盖（synthesis 修正版 A 裁定五验收）：
- 边界三事件必落：enter/exit 落 boundary、error 落 full 且 escalated=True
- 中间态豁免：在表 fiber 的非边界 phase 不落账（emit 返回 False）
- 到期自动失效：review_due 过期 → 回全记账
- error 永不豁免：在表 fiber 出错仍全粒度升格
- 豁免 record 在册可查询可撤销：add/revoke 经 StateStore 持久化
- fail-open：表损坏/落账异常 → 全记账或安全返回，绝不炸主流程
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lingclaude.engine.event_exempt import (
    EVENT_EXEMPT_RECORD_KEY,
    EVENT_EXEMPT_RECORD_TYPE,
    PHASE_ENTER,
    PHASE_ERROR,
    PHASE_EXIT,
    RECORD_BOUNDARY,
    RECORD_EXEMPT,
    RECORD_FULL,
    add_exemption,
    emit_boundary_event,
    load_policy,
    revoke_exemption,
)
from lingclaude.core.state_store import StateStore


@pytest.fixture()
def store(tmp_path):
    return StateStore(root=tmp_path)


def _payload_of(store: StateStore, fiber: str, ts: str, root) -> dict | None:
    return store.load("plugin_events", f"{fiber}/{ts}", root)


def test_no_exemption_all_full(store, tmp_path):
    """空表：一切全记账（fail-open 默认世界）。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    for phase in (PHASE_ENTER, PHASE_EXIT, "step", PHASE_ERROR):
        verdict = load_policy(store, tmp_path).should_record("tool_pipeline", phase, now)
        assert verdict == RECORD_FULL


def test_boundary_phases_recorded_as_boundary(store, tmp_path):
    """在表 fiber 的 enter/exit → 边界粒度落账。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="高频热路径", review_days=30,
                  root=tmp_path, now=now)
    policy = load_policy(store, tmp_path)
    assert policy.should_record("tool_pipeline", PHASE_ENTER, now) == RECORD_BOUNDARY
    assert policy.should_record("tool_pipeline", PHASE_EXIT, now) == RECORD_BOUNDARY
    # 落账并核验粒度标记
    ts = now.isoformat(timespec="microseconds")
    assert emit_boundary_event(store, "tool_pipeline", PHASE_ENTER, root=tmp_path, now=now)
    payload = _payload_of(store, "tool_pipeline", ts, tmp_path)
    assert payload is not None
    assert payload["granularity"] == "boundary"


def test_intermediate_exempt(store, tmp_path):
    """在表 fiber 的中间态 → 豁免不落账（emit 返回 False）。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="高频热路径", root=tmp_path, now=now)
    policy = load_policy(store, tmp_path)
    assert policy.should_record("tool_pipeline", "step", now) == RECORD_EXEMPT
    assert emit_boundary_event(store, "tool_pipeline", "step", root=tmp_path, now=now) is False


def test_three_boundary_events_minimum(store, tmp_path):
    """纪律红线：任何白名单动作至少留 3 条账——enter/exit/error 全部落。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="x", root=tmp_path, now=now)
    for i, phase in enumerate((PHASE_ENTER, PHASE_EXIT, PHASE_ERROR)):
        ts = (now + timedelta(seconds=i)).isoformat(timespec="microseconds")
        assert emit_boundary_event(
            store, "tool_pipeline", phase, root=tmp_path, now=now + timedelta(seconds=i)
        )
        assert _payload_of(store, "tool_pipeline", ts, tmp_path) is not None


def test_error_never_exempt_and_escalated(store, tmp_path):
    """条款3：error 永不豁免，升格全粒度 + escalated 标记。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="x", root=tmp_path, now=now)
    policy = load_policy(store, tmp_path)
    assert policy.should_record("tool_pipeline", PHASE_ERROR, now) == RECORD_FULL
    ts = now.isoformat(timespec="microseconds")
    assert emit_boundary_event(
        store, "tool_pipeline", PHASE_ERROR, data={"exc": "ValueError"}, root=tmp_path, now=now
    )
    payload = _payload_of(store, "tool_pipeline", ts, tmp_path)
    assert payload["granularity"] == "full"
    assert payload["escalated"] is True


def test_expiry_auto_reverts_to_full(store, tmp_path):
    """条款2：到期未复查自动失效回全记账。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="x", review_days=30, root=tmp_path, now=now)
    after_due = now + timedelta(days=31)
    policy = load_policy(store, tmp_path)
    assert policy.should_record("tool_pipeline", "step", now) == RECORD_EXEMPT
    assert policy.should_record("tool_pipeline", "step", after_due) == RECORD_FULL


def test_revoke_immediately_full(store, tmp_path):
    """撤销豁免：立即回全记账，record 可查询。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    add_exemption(store, "tool_pipeline", reason="x", root=tmp_path, now=now)
    # 在册可查询：成员含理由与复查日
    record = store.load(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, tmp_path)
    member = record["members"][0]
    assert member["fiber"] == "tool_pipeline"
    assert member["reason"] == "x"
    assert member["review_due"]
    # 撤销
    revoke_exemption(store, "tool_pipeline", root=tmp_path)
    assert load_policy(store, tmp_path).should_record("tool_pipeline", "step", now) == RECORD_FULL


def test_corrupt_record_fails_open(store, tmp_path):
    """表损坏 → fail-open 全记账（账本宁可膨胀，审计不可瞎掉）。"""
    store.save(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, "not-a-dict", tmp_path)
    now = datetime(2026, 9, 25, 12, 0, 0)
    assert load_policy(store, tmp_path).should_record("tool_pipeline", "step", now) == RECORD_FULL
    # 部分损坏：成员缺 fiber 字段被跳过，好条目存活
    now2 = datetime(2026, 9, 26, 12, 0, 0)
    add_exemption(store, "ok_fiber", reason="x", root=tmp_path, now=now2)
    record = store.load(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, tmp_path)
    record["members"].append({"reason": "broken"})  # 缺 fiber
    store.save(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, record, tmp_path)
    policy = load_policy(store, tmp_path)
    assert policy.should_record("ok_fiber", "step", now2) == RECORD_EXEMPT


def test_emit_persist_failure_safe(store, tmp_path, monkeypatch):
    """落账异常：返回 False + 不上抛（best-effort 纪律）。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    monkeypatch.setattr(
        type(store), "save",
        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    assert emit_boundary_event(store, "tool_pipeline", PHASE_ENTER, root=tmp_path, now=now) is False


def test_policy_load_store_error_fails_open(store, tmp_path, monkeypatch):
    """表读取异常 → 空 policy（全记账），不阻断事件路径。"""
    now = datetime(2026, 9, 25, 12, 0, 0)
    monkeypatch.setattr(
        type(store), "load",
        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("backend down")),
    )
    assert load_policy(store, tmp_path).should_record("tool_pipeline", "step", now) == RECORD_FULL

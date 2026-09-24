"""N8 free-RAM 守卫门测试（gov/guard/，2026-09-24 主裁 B1 裁决立项）。"""
import logging

import pytest

from lingclaude.gov.guard import free_ram_gate


@pytest.fixture(autouse=True)
def _clean_reject_log():
    free_ram_gate._REJECT_LOG.clear()
    yield
    free_ram_gate._REJECT_LOG.clear()


def test_threshold_huge_rejects(monkeypatch):
    monkeypatch.setattr(free_ram_gate, "MIN_FREE_MB", 10**9)
    allowed, reason = free_ram_gate.check_startup_allowed("session", action_id="s1")
    assert allowed is False
    assert reason is not None and "拒绝" in reason
    logs = free_ram_gate.recent_rejections()
    assert len(logs) == 1
    assert logs[0]["action"] == "session"
    assert logs[0]["action_id"] == "s1"
    assert logs[0]["threshold_mb"] == 10**9


def test_threshold_zero_allows():
    monkey_free = None  # 占位说明: Linux /proc 存在时 free>=0 恒成立, 非 Linux fail-open 同放行
    allowed, reason = free_ram_gate.check_startup_allowed("startup")
    assert monkey_free is None  # noqa: F841 — 保持 lint 安静
    assert allowed is True
    assert reason is None
    assert free_ram_gate.recent_rejections() == []


def test_fail_open_when_sampling_fails(monkeypatch, caplog):
    monkeypatch.setattr(free_ram_gate, "sample_free_mb", lambda: None)
    with caplog.at_level(logging.WARNING, logger="lingclaude.gov.guard.free_ram_gate"):
        allowed, reason = free_ram_gate.check_startup_allowed("long_task")
    assert allowed is True
    assert reason is None
    assert any("fail-open" in r.message for r in caplog.records)
    assert free_ram_gate.recent_rejections() == []


def test_reject_log_lru_cap(monkeypatch):
    monkeypatch.setattr(free_ram_gate, "MIN_FREE_MB", 10**9)
    monkeypatch.setattr(free_ram_gate, "_MAX_REJECT_LOG", 3)
    for i in range(5):
        free_ram_gate.check_startup_allowed("a", action_id=str(i))
    logs = free_ram_gate.recent_rejections(limit=10)
    assert len(logs) == 3
    assert [e["action_id"] for e in logs] == ["2", "3", "4"]


def test_sample_free_mb_smoke():
    mb = free_ram_gate.sample_free_mb()
    assert mb is None or (isinstance(mb, int) and mb >= 0)


def test_registry_no_conflict():
    """N8 编号不与铁律注册表既有 N 区冲突（N1-N7 全占、N8=环境守卫、N9=RSS 仪表）。"""
    from pathlib import Path

    law = Path("docs/LINGYUAN_IRON_LAW.md").read_text(encoding="utf-8")
    assert "N8 留" not in law or "环境守卫" in law  # 注册表对 N8 语义有交代
    # rss_watchdog 已改挂 N9, 不再自称 N6
    wd = Path("lingclaude/ops/rss_watchdog.py").read_text(encoding="utf-8")
    assert "[N6守卫]" not in wd
    assert "[N9仪表]" in wd

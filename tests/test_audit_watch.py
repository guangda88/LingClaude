"""F4 值守测试（2026-09-23）：audit_watch 三级节流 + 报告强制出口。

测试边界：不跑真实返审（run_audit/sweep_debts 打桩）——真实脚本
会跑守卫 pytest 并写仓库 data/arch_ledger 状态，属集成层行为，
由 scripts 脚本手工/值守真跑覆盖。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from lingclaude.self_optimizer.audit_watch import AuditWatch, AuditWatchState


@pytest.fixture()
def watch(tmp_path, monkeypatch):
    """标准被测对象：触发器深跑打桩，KB 指向 tmp。"""
    w = AuditWatch(
        state_dir=tmp_path / "state",
        kb_path=tmp_path / "kb" / "knowledge.db",
    )
    monkeypatch.setattr(w.trigger, "run_audit", lambda: 0)
    monkeypatch.setattr(w.trigger, "sweep_debts", lambda: [])
    return w


class TestAuditWatch:
    def test_run_once_returns_diag_and_writes_kb(self, watch):
        diag = watch.run_once()
        assert diag["exit"] == 0
        assert diag["open_tasks"] >= 0
        assert diag["sweep_expired"] == []
        assert diag["kb_rule"] == f"audit_report_{datetime.now().strftime('%Y%m%d')}"
        # 报告强制出口：KB 落行，category=audit
        conn = sqlite3.connect(watch.kb_path)
        row = conn.execute(
            "SELECT name, category, status FROM rules WHERE id = ?",
            (diag["kb_rule"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[1] == "audit"
        assert row[2] == "active"

    def test_same_day_reports_merge_to_one_row(self, watch):
        """同日多次值守 → 幂等 upsert 合并为一行（F3 add_rule 语义）。"""
        watch.run_once()
        watch.run_once()
        conn = sqlite3.connect(watch.kb_path)
        n = conn.execute(
            "SELECT count(*) FROM rules WHERE id LIKE 'audit_report_%'"
        ).fetchone()[0]
        conn.close()
        assert n == 1

    def test_kb_exit_best_effort(self, watch, monkeypatch):
        """出口失败不阻塞值守：diag['kb_rule']=None，run_once 正常返回。"""
        from lingclaude.self_optimizer.learner import knowledge as kb_mod

        class _BoomKB:
            def __init__(self, *a, **k):
                raise RuntimeError("kb 不可用")

        monkeypatch.setattr(kb_mod, "KnowledgeBase", _BoomKB)
        diag = watch.run_once()
        assert diag["kb_rule"] is None

    def test_sweep_throttled_within_window(self, watch, monkeypatch):
        """层级2：24h 窗口内不重复例行核账。"""
        calls = []
        monkeypatch.setattr(
            watch.trigger, "sweep_debts", lambda: calls.append(1) or []
        )
        watch.state.last_full_sweep = datetime.now().isoformat()
        watch.run_once()
        assert calls == []

    def test_sweep_due_when_stale(self, watch, monkeypatch):
        """层级2：超窗（含首次）执行例行核账并持久化时间戳。"""
        monkeypatch.setattr(
            watch.trigger, "sweep_debts", lambda: ["spec-debt-x"]
        )
        watch.state.last_full_sweep = (
            datetime.now() - timedelta(hours=25)
        ).isoformat()
        diag = watch.run_once()
        assert diag["sweep_expired"] == ["spec-debt-x"]
        saved = json.loads(watch.state_path.read_text(encoding="utf-8"))
        assert saved["last_full_sweep"] is not None

    def test_first_run_sweeps(self, watch, monkeypatch):
        """首次值守（无时间戳）即例行核账。"""
        calls = []
        monkeypatch.setattr(
            watch.trigger, "sweep_debts", lambda: calls.append(1) or []
        )
        watch.run_once()
        assert len(calls) == 1


class TestAuditWatchState:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "s.json"
        AuditWatchState(last_full_sweep="2026-09-23T00:00:00").dump(p)
        assert AuditWatchState.load(p).last_full_sweep == "2026-09-23T00:00:00"

    def test_load_missing_returns_default(self, tmp_path):
        assert AuditWatchState.load(tmp_path / "nope.json").last_full_sweep is None

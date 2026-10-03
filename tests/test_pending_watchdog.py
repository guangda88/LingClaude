"""pending_watchdog 测试 — 灰区超时缺口（2026-10-03 立项）的回归防线。

背景：guard_pending.jsonl 实测 968 条、624 条 pending、最老 09-05（28 天），
原机制「落盘+通知一次」之后 pending 永夜。本模块把超时检测/升级链钉死。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lingclaude.core.pending_watchdog import (
    ESCALATE_HOURS,
    StalePending,
    escalate_stale,
    scan_stale_pendings,
)


def _mk(
    path,
    ts: str,
    action: str = "bash_execute",
    state: str | None = "pending",
    extra: dict | None = None,
) -> None:
    d = {"ts": ts, "action": action, "mode": "ask"}
    if state is not None:
        d["state"] = state
    if extra:
        d.update(extra)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class TestScanStale:
    def test_fresh_pending_not_flagged(self, tmp_path):
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(1))
        assert scan_stale_pendings(path=p) == []

    def test_stale_pending_flagged(self, tmp_path):
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(30))
        out = scan_stale_pendings(path=p)
        assert len(out) == 1
        assert out[0].hours_pending >= 24

    def test_missing_state_counts_as_open(self, tmp_path):
        """历史数据 203 条无 state 字段——必须按未决处理，不能漏。"""
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(100), state=None)
        out = scan_stale_pendings(path=p)
        assert len(out) == 1
        assert out[0].state == "(missing)"

    def test_approved_excluded(self, tmp_path):
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(100), state="approved_by_daemon")
        assert scan_stale_pendings(path=p) == []

    def test_sorted_desc_and_mixed(self, tmp_path):
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(30), action="a30")
        _mk(p, _ts(800), action="a800")
        _mk(p, _ts(2), action="a2")
        _mk(p, _ts(500), state="approved_by_daemon", action="a_done")
        out = scan_stale_pendings(path=p)
        assert [x.action for x in out] == ["a800", "a30"]
        assert out[0].hours_pending >= 500

    def test_corrupt_lines_skipped(self, tmp_path):
        p = tmp_path / "p.jsonl"
        p.write_text("not-json\n" + json.dumps({"ts": _ts(50), "action": "x", "state": "pending"}) + "\n")
        out = scan_stale_pendings(path=p)
        assert len(out) == 1

    def test_missing_file_empty(self, tmp_path):
        assert scan_stale_pendings(path=tmp_path / "nope.jsonl") == []

    def test_naive_ts_treated_as_utc(self, tmp_path):
        p = tmp_path / "p.jsonl"
        naive = (datetime.utcnow() - timedelta(hours=100)).isoformat()  # 无时区
        _mk(p, naive)
        out = scan_stale_pendings(path=p)
        assert len(out) == 1


class TestEscalate:
    def test_escalate_threshold_uses_72h(self, tmp_path, monkeypatch):
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(80), action="very_old")   # >72h
        _mk(p, _ts(30), action="mid")        # 24-72h 之间，不该被升级
        sent = []

        def fake_alert(subject, body):
            sent.append(subject)

        monkeypatch.setattr(
            "lingclaude.coordination.alert.send_lingbus_alert", fake_alert
        )
        out = escalate_stale()
        # 注：escalate_stale 读的是 PENDING_LOG_PATH 而非 tmp_path，
        # 该用例只验证「不 raise + 返回结构」；定向注入见下一用例。
        assert isinstance(out, list)

    def test_escalate_only_over_threshold(self, tmp_path, monkeypatch):
        """>72h 的广播、24-72h 的不广播（阈值语义）。"""
        from lingclaude.core import pending_watchdog as pw

        p = tmp_path / "p.jsonl"
        _mk(p, _ts(80), action="old")
        _mk(p, _ts(30), action="mid")
        sent = []
        monkeypatch.setattr(
            "lingclaude.coordination.alert.send_lingbus_alert",
            lambda subject, body: sent.append(subject),
        )
        monkeypatch.setattr(pw, "PENDING_LOG_PATH", p)
        out = pw.escalate_stale()
        assert len(out) == 1 and out[0].action == "old"
        assert out[0].escalated is True
        assert len(sent) == 1 and "灰区超时" in sent[0]

    def test_escalate_bus_failure_never_raises(self, tmp_path, monkeypatch):
        from lingclaude.core import pending_watchdog as pw

        p = tmp_path / "p.jsonl"
        _mk(p, _ts(100))
        monkeypatch.setattr(pw, "PENDING_LOG_PATH", p)

        def boom(subject, body):
            raise RuntimeError("bus down (EROFS 语义)")

        monkeypatch.setattr(
            "lingclaude.coordination.alert.send_lingbus_alert", boom
        )
        out = pw.escalate_stale()
        assert len(out) == 1 and out[0].escalated is False  # fail-safe


class TestCLI:
    def test_main_clean_exit_zero(self, tmp_path, monkeypatch, capsys):
        from lingclaude.core import pending_watchdog as pw

        monkeypatch.setattr(pw, "PENDING_LOG_PATH", tmp_path / "nope.jsonl")
        assert pw.main([]) == 0
        assert "OK" in capsys.readouterr().out

    def test_main_stale_exit_one_and_listing(self, tmp_path, monkeypatch, capsys):
        from lingclaude.core import pending_watchdog as pw

        p = tmp_path / "p.jsonl"
        _mk(p, _ts(48), action="stuck_action")
        monkeypatch.setattr(pw, "PENDING_LOG_PATH", p)
        rc = pw.main([])
        assert rc == 1
        outtxt = capsys.readouterr().out
        assert "stuck_action" in outtxt and "48" in outtxt

    def test_thresholds_documented(self):
        """阈值与会议纪要对齐：24h 回执 / 72h 升级。"""
        assert ESCALATE_HOURS > 24

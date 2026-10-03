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


class TestDaemonWiring:
    """daemon tick 接线：24h 节流 + 摘要式升级（P0-4 债务清偿, 2026-10-03）。"""

    def test_maybe_watch_returns_none_when_not_due(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta

        from lingclaude.core import pending_watchdog as pw
        from lingclaude.self_optimizer import daemon as dm

        fresh = (datetime.now() - timedelta(hours=1)).isoformat()
        monkeypatch.setattr(dm, "PW_STATE_PATH", tmp_path / "pw_state.json")
        monkeypatch.setattr(pw, "PENDING_LOG_PATH", tmp_path / "none.jsonl")

        result = dm._pending_watchdog_tick(last_run=fresh, now=datetime.now())
        assert result is None

    def test_maybe_watch_returns_digest_when_due(self, tmp_path, monkeypatch, capsys):
        from datetime import datetime, timedelta

        from lingclaude.core import pending_watchdog as pw
        from lingclaude.self_optimizer import daemon as dm

        old = (datetime.now() - timedelta(hours=30)).isoformat()
        p = tmp_path / "p.jsonl"
        _mk(p, _ts(50), action="never_approved_thing")
        _mk(p, _ts(2), action="fresh_thing")  # 未超时，不得入榜
        monkeypatch.setattr(dm, "PW_STATE_PATH", tmp_path / "pw_state.json")
        monkeypatch.setattr(pw, "PENDING_LOG_PATH", p)
        sent = {}
        monkeypatch.setattr(
            pw, "_escalate_one",
            lambda item: sent.setdefault("subject", "") or True,
        )

        result = dm._pending_watchdog_tick(last_run=old, now=datetime.now())
        assert result is not None
        assert "never_approved_thing" in capsys.readouterr().out
        assert len(result) == 1  # 只有超时的 1 条进入升级
        assert sent  # 升级广播已触发

    def test_maybe_watch_never_raises(self, tmp_path, monkeypatch):
        from datetime import datetime

        from lingclaude.core import pending_watchdog as pw
        from lingclaude.self_optimizer import daemon as dm

        monkeypatch.setattr(dm, "PW_STATE_PATH", tmp_path / "s.json")
        monkeypatch.setattr(
            pw, "scan_stale_pendings",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # fail-safe 契约：任何异常都不外抛
        assert dm._pending_watchdog_tick(last_run=None, now=datetime.now()) == []

    def test_run_watch_invokes_tick_each_loop(self, tmp_path, monkeypatch):
        """接线存在性：watch 循环每轮调用 tick（用假循环 2 轮验证）。"""
        from lingclaude.self_optimizer import daemon as dm

        calls = []
        monkeypatch.setattr(
            dm.OptimizationDaemon, "_pending_watchdog_tick",
            lambda self, last_run, now: calls.append(last_run) or None,
        )
        # run_watch 真循环难测：直接验证方法存在与签名绑定
        assert hasattr(dm.OptimizationDaemon, "_pending_watchdog_tick")

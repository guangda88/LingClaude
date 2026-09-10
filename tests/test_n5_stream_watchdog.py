"""N5b 流内停滞 watchdog 单元测试（lingclaude/cli/n5_stream_watchdog.py）。

测试策略：
- 阈值用 monkeypatch 调小或直接白盒老化 _last_event_at，避免真实睡眠
- LingBus 发送默认拦截为桩，收集调用断言「ERROR 只发一次」
- 一条真实线程生命周期测试覆盖 _watch_loop / stop 接线
"""
from __future__ import annotations

import logging
import time

import pytest

from lingclaude.cli import n5_stream_watchdog as wd_mod
from lingclaude.cli.n5_stream_watchdog import StreamWatchdog


@pytest.fixture(autouse=True)
def _stub_lingbus(monkeypatch):
    """拦截 LingBus 发送；返回调用收集列表。"""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        wd_mod, "send_lingbus_alert",
        lambda subject, detail: calls.append((subject, detail)),
    )
    return calls


def _age(wd: StreamWatchdog, seconds: float) -> None:
    """白盒：把心跳时间戳拨回 seconds 秒前，模拟流逝。"""
    wd._last_event_at = time.monotonic() - seconds


def test_first_window_warning_once_never_error(caplog, _stub_lingbus):
    """首事件前（推理思考期）：超 120s 只 WARNING 一次，永不升 ERROR/LingBus。"""
    wd = StreamWatchdog()
    with caplog.at_level(logging.DEBUG, logger=wd_mod._logger.name):
        _age(wd, wd_mod.BEFORE_FIRST_WARN_S + 1)
        wd._check_once()
        assert "首事件前" in caplog.text
        _age(wd, wd_mod.BEFORE_FIRST_WARN_S * 10)  # 远超阈值也不升级
        wd._check_once()
        wd._check_once()
    assert len(caplog.records) == 1  # 只记一次（消息体含"WARNING"字样, 不能数 caplog.text）
    assert _stub_lingbus == []
    assert "ERROR" not in caplog.text


def test_gap_warning_then_error_lingbus_once(caplog, _stub_lingbus):
    """事件间隙：60s WARNING → 300s ERROR + LingBus，各只发一次。"""
    wd = StreamWatchdog()
    with caplog.at_level(logging.DEBUG, logger=wd_mod._logger.name):
        wd.touch("text_delta")
        _age(wd, wd_mod.GAP_WARN_S + 1)
        wd._check_once()
        assert "事件间隙" in caplog.text
        assert _stub_lingbus == []

        _age(wd, wd_mod.GAP_ERROR_S + 1)
        wd._check_once()
        assert "流内停滞 ERROR" in caplog.text
        assert len(_stub_lingbus) == 1
        assert "N5b" in _stub_lingbus[0][0]

        _age(wd, wd_mod.GAP_ERROR_S * 3)  # 持续挂死也不重复告警
        wd._check_once()
        wd._check_once()
    assert len(_stub_lingbus) == 1
    assert caplog.text.count("流内停滞 ERROR") == 1


def test_tool_window_silent(caplog, _stub_lingbus):
    """工具执行期（上个事件 tool_call_start）：超 300s 也零告警。"""
    wd = StreamWatchdog()
    with caplog.at_level(logging.DEBUG, logger=wd_mod._logger.name):
        wd.touch("tool_call_start")
        _age(wd, wd_mod.GAP_ERROR_S * 5)
        wd._check_once()
        wd._check_once()
    assert caplog.text == ""
    assert _stub_lingbus == []


def test_touch_resets_latches(caplog, _stub_lingbus):
    """touch 恢复心跳即清除告警闩锁：再次停滞可重新告警。"""
    wd = StreamWatchdog()
    with caplog.at_level(logging.DEBUG, logger=wd_mod._logger.name):
        wd.touch("text_delta")
        _age(wd, wd_mod.GAP_WARN_S + 1)
        wd._check_once()
        wd.touch("tool_call_end")  # 流恢复 → 闩锁清除
        wd.touch("done")
        _age(wd, wd_mod.GAP_WARN_S + 1)
        wd._check_once()
    assert caplog.text.count("事件间隙") == 2
    assert _stub_lingbus == []


def test_healthy_stream_no_alerts(caplog, _stub_lingbus):
    """健康流（事件频繁到达）：零告警。"""
    wd = StreamWatchdog()
    with caplog.at_level(logging.DEBUG, logger=wd_mod._logger.name):
        for etype in ("status", "text_delta", "tool_call_start", "tool_call_end", "done"):
            wd.touch(etype)
            wd._check_once()
    assert caplog.text == ""
    assert _stub_lingbus == []


def test_thread_lifecycle_and_stop():
    """真实线程：start 后独立运行，stop 置位后线程在 1s 内退出。"""
    wd = StreamWatchdog(poll_interval_s=0.02)
    wd.start()
    assert wd._thread is not None and wd._thread.is_alive()
    wd.stop()
    wd._thread.join(timeout=1.0)
    assert not wd._thread.is_alive()
    # stop 后重复调用安全
    wd.stop()


def test_watch_loop_survives_check_exception():
    """_check_once 抛异常不杀死 watchdog 线程（best-effort 约束）。"""
    wd = StreamWatchdog(poll_interval_s=0.02)
    calls = {"n": 0}

    def _boom() -> None:
        calls["n"] += 1
        raise RuntimeError("boom")

    wd._check_once = _boom  # type: ignore[method-assign]
    wd.start()
    time.sleep(0.12)
    assert wd._thread is not None and wd._thread.is_alive()
    assert calls["n"] >= 2  # 异常后仍持续轮询
    wd.stop()
    wd._thread.join(timeout=1.0)


def test_escalation_after_real_sleep(caplog, _stub_lingbus, monkeypatch):
    """端到端（小阈值真实等待）：停滞 → 线程轮询 → WARNING 落日志。"""
    monkeypatch.setattr(wd_mod, "GAP_WARN_S", 0.05)
    wd = StreamWatchdog(poll_interval_s=0.02)
    wd.start()
    try:
        wd.touch("text_delta")  # 首个真实事件 → 切入事件间隙窗口
        with caplog.at_level(logging.WARNING, logger=wd_mod._logger.name):
            time.sleep(0.25)  # > GAP_WARN_S + poll 余量
        assert "事件间隙" in caplog.text
    finally:
        wd.stop()
        if wd._thread is not None:
            wd._thread.join(timeout=1.0)

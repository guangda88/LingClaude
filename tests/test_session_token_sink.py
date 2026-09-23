#!/usr/bin/env python3
"""Tests for session_token_sink.py — D3 清偿（session 维度 token 落盘）。

验收口径（memory lingclaude-token-schema-blind-spot）：
- 落盘 schema 必含 session_id/round_idx/input_tokens/output_tokens/model/provider
- 无 session 上下文不落脏数据；env 开关可关；旁路失败不炸主路
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.session_token_sink import (
    _RECORD_TYPE,
    SessionTokenSink,
    record_turn_usage,
)
from lingclaude.core.state_store import StateStore

@pytest.fixture(autouse=True)
def _sink_tests_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块单测需要走默认开启路径（conftest 全局 off 是防真链路写生产目录）。

    本模块全部用例显式注入 root=tmp_path（或只检类型不触发 on_usage），
    开启不会落生产目录；test_env_* 两例自行 setenv("off") 覆盖，开关语义仍被验证。
    """
    monkeypatch.setenv("LINGCLAUDE_SESSION_TOKEN_SINK", "on")


@pytest.fixture
def tmp_state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _usage(**over):
    base = {
        "model": "glm-5.3-flash",
        "task_type": "turn",
        "input_tokens": 1200,
        "output_tokens": 340,
        "total_tokens": 1540,
        "metadata": {
            "session_id": "sess-abc",
            "round_idx": 3,
            "provider": "openai",
            "cached_tokens": 800,
            "timestamp": "2026-09-24T00:00:00+00:00",
        },
    }
    base.update(over)
    return base


class TestSessionTokenSink:
    def test_writes_json_record(self, tmp_state):
        SessionTokenSink(root=tmp_state).on_usage(_usage())
        rec = StateStore(root=tmp_state).load("session_token_usage", "sess-abc/000003")
        assert rec is not None
        # schema 盲区要求的字段全集
        assert rec["session_id"] == "sess-abc"
        assert rec["round_idx"] == 3
        assert rec["input_tokens"] == 1200
        assert rec["output_tokens"] == 340
        assert rec["cached_tokens"] == 800
        assert rec["total_tokens"] == 1540
        assert rec["model"] == "glm-5.3-flash"
        assert rec["provider"] == "openai"
        assert rec["created_at"] == "2026-09-24T00:00:00+00:00"
        # 目录形态与 StateStore 约定一致
        p = tmp_state / "session_token_usage" / "sess-abc" / "000003.json"
        assert p.is_file()

    def test_no_session_id_no_write(self, tmp_state):
        SessionTokenSink(root=tmp_state).on_usage(_usage(metadata={"round_idx": 1}))
        assert StateStore(root=tmp_state).load("session_token_usage", "unknown/000001") is None
        assert list((tmp_state / "session_token_usage").rglob("*.json")) == []

    def test_env_off_disables(self, tmp_state, monkeypatch):
        # 新语义（2026-09-24）：off 只约束默认生产路径；显式 root（测试隔离注入）不受影响
        monkeypatch.setenv("LINGCLAUDE_SESSION_TOKEN_SINK", "off")
        SessionTokenSink(root=tmp_state).on_usage(_usage())
        assert list((tmp_state / "session_token_usage").rglob("*.json")), \
            "显式 root 应不受 off 影响（测试隔离依赖此语义）"
        # 默认生产路径被 off 止写（端到端由回归钉 test_env_toggle_stops_default_root_writes 加锁）
        probe_sid = "s-off-default-root"
        SessionTokenSink().on_usage(_usage(metadata={"session_id": probe_sid, "round_idx": 1}))
        assert not (Path.home() / ".lingclaude" / "state" / "session_token_usage" / probe_sid).exists()

    def test_broken_payload_never_raises(self, tmp_state):
        # 旁路纪律：payload 缺字段/类型错误只 warning 不抛
        SessionTokenSink(root=tmp_state).on_usage({"metadata": {"session_id": "s"}})

    def test_round_idx_zero_clamped(self, tmp_state):
        SessionTokenSink(root=tmp_state).on_usage(_usage(metadata={"session_id": "s0", "round_idx": -5}))
        rec = StateStore(root=tmp_state).load("session_token_usage", "s0/000000")
        assert rec is not None and rec["round_idx"] == 0


class TestChainedSinkInWiring:
    def test_chain_dispatch_and_isolation(self, tmp_state, monkeypatch):
        from lingclaude.core.wiring import _ChainedSink

        calls: list[str] = []

        class Ok:
            def on_usage(self, u):
                calls.append("ok")

        class Boom:
            def on_usage(self, u):
                calls.append("boom")
                raise RuntimeError("boom")

        chain = _ChainedSink([Boom(), SessionTokenSink(root=tmp_state), Ok()])
        chain.on_usage(_usage())  # Boom 抛错不阻断后续
        assert calls == ["boom", "ok"]
        assert list((tmp_state / "session_token_usage").rglob("*.json"))

    def test_make_monitor_wires_session_sink(self, monkeypatch):
        from lingclaude.core import wiring

        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        monitor = wiring._make_monitor(None)
        sink = monitor._legacy_sink
        assert sink is not None
        sinks = getattr(sink, "_sinks", [])
        assert any(isinstance(s, SessionTokenSink) for s in sinks)


class TestRecordTurnUsage:
    def test_passes_metadata_through(self, tmp_state):
        class FakeMonitor:
            def __init__(self):
                self.captured = None

            def record_usage(self, **kw):
                self.captured = kw

        m = FakeMonitor()
        record_turn_usage(
            m, session_id="sx", round_idx=2, model="m1", provider="anthropic",
            input_tokens=10, output_tokens=20, cached_tokens=5,
        )
        kw = m.captured
        assert kw["total_tokens"] == 30  # 聚合口径：input+output
        assert kw["task_type"] == "turn"
        assert kw["metadata"]["session_id"] == "sx"
        assert kw["metadata"]["round_idx"] == 2
        assert kw["metadata"]["provider"] == "anthropic"
        assert kw["metadata"]["cached_tokens"] == 5
        assert kw["metadata"]["timestamp"]  # ISO 时间戳已注入


class TestMonitorChainIntegration:
    def test_record_usage_reaches_sink(self, tmp_state):
        # 真链路：TokenMonitor.record_usage → _emit_legacy_sink → SessionTokenSink
        from lingclaude.core.token_monitor import TokenMonitor

        monitor = TokenMonitor(legacy_sink=SessionTokenSink(root=tmp_state))
        monitor.record_usage(
            model="m1", task_type="turn", total_tokens=99,
            input_tokens=66, output_tokens=33,
            metadata={"session_id": "s-int", "round_idx": 1,
                      "provider": "openai", "cached_tokens": 0,
                      "timestamp": "2026-09-24T01:02:03+00:00"},
        )
        rec = StateStore(root=tmp_state).load("session_token_usage", "s-int/000001")
        assert rec is not None
        assert rec["input_tokens"] == 66 and rec["output_tokens"] == 33


def test_env_toggle_stops_default_root_writes(monkeypatch) -> None:
    """回归钉（2026-09-24 污染事故）：环境开关必须在默认生产 root 上止写。

    事故：wiring._make_monitor 无条件挂 SessionTokenSink（默认开），
    真链路测试经 legacy_sink 把桩数据写进 ~/.lingclaude/state/。
    本钉锁住「off ⇒ 默认 root 也不写」这条防线（conftest 全局隔离依赖它）。
    """
    probe_sid = "pin-regression-probe-do-not-exist"
    monkeypatch.setenv("LINGCLAUDE_SESSION_TOKEN_SINK", "off")
    SessionTokenSink().on_usage(_usage(metadata={
        "session_id": probe_sid,
        "round_idx": 1,
        "provider": "pin-probe",
    }))
    prod_dir = Path.home() / ".lingclaude" / "state" / _RECORD_TYPE / probe_sid
    assert not prod_dir.exists(), "env=off 时默认 root 仍被写入——conftest 隔离防线失效"

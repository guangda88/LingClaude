"""LINGKERNEL_v1 D3 - MV-1 不变量端到端测试（query_engine 接线验证）。

验证:
- QueryEngine 持有 model_request_log / model_adapter / audit_collector / session_store
- _call_model 路径上 MV-1 断言生效（正常时不违规）
- mv1_violations 属性可消费
"""

from __future__ import annotations

from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig


class FakeUsage:
    input_tokens = 1
    output_tokens = 1


class FakeResponse:
    content = "ok"
    tool_calls = ()
    usage = FakeUsage()
    finish_reason = "stop"


class FakeProvider:
    def __init__(self):
        self.calls = []

    def complete(self, messages, config=None, tools=None):
        self.calls.append((messages, config, tools))
        from lingclaude.core.types import Result
        return Result.ok(FakeResponse())


def _mk_engine(provider=None):
    provider = provider or FakeProvider()
    cfg = QueryEngineConfig()
    return QueryEngine(config=cfg, model_provider=provider), provider


def test_engine_has_dsh_spine_modules():
    eng, _ = _mk_engine()
    assert hasattr(eng, "model_request_log")
    assert hasattr(eng, "model_adapter")
    assert hasattr(eng, "audit_collector")
    assert hasattr(eng, "session_store")


def test_call_model_logs_request():
    eng, prov = _mk_engine()
    out = eng._call_model("hello")
    assert "ok" in out or out  # 完整 turn 走通
    assert eng.model_request_log.last_seq() >= 1
    assert len(prov.calls) >= 1


def test_mv1_no_violation_on_normal_path():
    eng, _ = _mk_engine()
    eng._call_model("hello")
    assert eng.mv1_violations == ()


def test_mv1_violation_detectable():
    """log 被篡改后 (删掉快照), 断言应记违规。"""
    eng, _ = _mk_engine()
    eng._call_model("hello")
    # 模拟 log 损坏: 清空快照
    ev = eng.model_request_log.events[-1]
    ev.messages_snapshot = [{"role": "user", "content": "TAMPERED"}]
    # 手动触发一次断言 (复现 _call_model 内部逻辑)
    eng._assert_model_visible(ev.seq, [{"role": "user", "content": "hello"}])
    assert len(eng.mv1_violations) >= 1
    assert "MV-1 violated" in eng.mv1_violations[0]


def test_mv1_violation_list_grows():
    eng, _ = _mk_engine()
    eng._assert_model_visible(1, [{"role": "user", "content": "x"}])  # log 为空 -> 违规
    # D8: MV-1a + MV-1b 双断言各记 1 条
    assert len(eng.mv1_violations) == 2


def test_model_adapter_wired_to_provider():
    eng, _ = _mk_engine()
    assert eng.model_adapter.provider is eng._provider


def test_session_store_uses_engine_session_id():
    eng, _ = _mk_engine()
    assert eng.session_store.session_id == eng.session_id
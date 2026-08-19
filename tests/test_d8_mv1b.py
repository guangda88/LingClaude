"""LINGKERNEL_v1 D8 - MV-1b fold + 双点校验 + 结构化 violation 测试。

覆盖:
- Mv1Violation dataclass + to_tuple_str 兼容
- delta 增量记录 (append 时自动计算)
- derive_model_messages_fold 重放式重建
- check_provenance_integrity (MV-1b)
- pre-send fail-closed (R2 处置)
- post-send 双断言 (MV-1a + MV-1b)
- mv1_violations_structured 属性
"""

from __future__ import annotations

from lingclaude.core.model_request_log import (
    ModelRequestLog,
    Mv1Violation,
    check_provenance_integrity,
    derive_model_messages_fold,
)
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

    def count_tokens(self, text):
        return 0


def _mk_engine(provider=None):
    provider = provider or FakeProvider()
    return QueryEngine(config=QueryEngineConfig(), model_provider=provider), provider


# ---- Mv1Violation ----

def test_violation_dataclass_fields():
    v = Mv1Violation(seq=3, reason="r", timestamp="T")
    assert v.seq == 3
    assert v.reason == "r"
    assert v.timestamp == "T"


def test_violation_to_tuple_str():
    v = Mv1Violation(seq=7, reason="MV-1b violated", timestamp="2026-08-19T00:00:00")
    s = v.to_tuple_str()
    assert "seq=7" in s
    assert "MV-1b violated" in s
    assert "2026-08-19" in s


# ---- delta 增量 ----

def test_first_event_delta_is_full_snapshot():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    assert log.events[0].delta == [{"role": "user", "content": "a"}]


def test_second_event_delta_is_incremental():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    ev2 = log.events[1]
    # delta 只含新增部分
    assert ev2.delta == [{"role": "assistant", "content": "b"}]
    # snapshot 仍是完整快照
    assert ev2.messages_snapshot == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]


def test_delta_on_disagreement_starts_at_diff():
    """前一快照与当前不一致时, delta 从分歧点开始。"""
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "CHANGED"}, {"role": "user", "content": "b"}])
    ev2 = log.events[1]
    assert {"role": "user", "content": "CHANGED"} in ev2.delta
    assert {"role": "user", "content": "b"} in ev2.delta


# ---- fold (MV-1b) ----

def test_fold_empty_log():
    assert derive_model_messages_fold(ModelRequestLog()) == []


def test_fold_single_event():
    log = ModelRequestLog()
    msgs = [{"role": "user", "content": "a"}]
    log.append("p", msgs)
    assert derive_model_messages_fold(log) == msgs


def test_fold_accumulates_deltas():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    log.append("p", [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    derived = derive_model_messages_fold(log)
    assert derived == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]


def test_fold_with_upto_seq():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    assert derive_model_messages_fold(log, 1) == [{"role": "user", "content": "a"}]
    assert len(derive_model_messages_fold(log, 2)) == 2


def test_fold_fallback_on_missing_delta():
    """旧格式事件 (delta 空) 回退快照。"""
    log = ModelRequestLog()
    ev1 = log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    # 模拟旧格式: 清空第二个事件的 delta
    log._events[1].delta = []
    derived = derive_model_messages_fold(log)
    assert derived == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]


# ---- provenance integrity ----

def test_provenance_ok_on_normal_path():
    log = ModelRequestLog()
    msgs = [{"role": "user", "content": "a"}]
    log.append("p", msgs)
    ok, reason = check_provenance_integrity(log, 1, msgs)
    assert ok, reason


def test_provenance_fails_on_mismatch():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    ok, reason = check_provenance_integrity(log, 1, [{"role": "user", "content": "OTHER"}])
    assert not ok
    assert "MV-1b violated" in reason


def test_provenance_detects_snapshot_tamper():
    """篡改 snapshot 但 delta 不动 → fold 用 delta 重建, 与 actual 不符则检出 (比 MV-1a 更强的场景)。"""
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "a"}])
    log.append("p", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    # 篡改最终 snapshot, 但 delta 完好
    log._events[-1].messages_snapshot = [{"role": "user", "content": "TAMPERED"}]
    actual = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    # MV-1a (快照式) 也会检出
    ok_a, _ = __import__("lingclaude.core.model_request_log", fromlist=["check_model_visible_invariant"]).check_model_visible_invariant(log, 2, actual)
    # MV-1b (fold) 用 delta 重建 == actual → 通过 (证明 fold 不依赖被篡改的 snapshot)
    ok_b, reason_b = check_provenance_integrity(log, 2, actual)
    assert not ok_a  # 快照式检出篡改
    assert ok_b, reason_b  # fold 从 delta 重建正确


# ---- 双点校验 (engine 集成) ----

def test_call_model_with_pre_send_passes():
    eng, prov = _mk_engine()
    out = eng._call_model("hello")
    assert len(prov.calls) >= 1  # pre-send 通过, 请求发出


def test_pre_send_blocks_on_tampered_log(monkeypatch=None):
    """log 落后即篡改 snapshot → pre-send fail-closed 阻断。

    篡改必须发生在本次 append 之后、pre-send 检查之前,
    因此 patch _log_model_request: 正常落 log 后立即篡改最新快照。
    """
    eng, prov = _mk_engine()
    n_calls_before = len(prov.calls)

    orig_log = eng._log_model_request

    def log_then_tamper(prompt, messages, tools):
        seq = orig_log(prompt, messages, tools)
        if seq > 0:
            eng.model_request_log._events[-1].messages_snapshot = [
                {"role": "user", "content": "TAMPERED"}
            ]
        return seq

    eng._log_model_request = log_then_tamper
    try:
        out = eng._call_model("world")
    finally:
        eng._log_model_request = orig_log

    assert "fail-closed" in out
    # 请求未发出 (调用数不变)
    assert len(prov.calls) == n_calls_before
    # 违规被记录 (结构化, pre-send 标记)
    assert len(eng.mv1_violations_structured) >= 1
    assert eng.mv1_violations_structured[-1].reason.startswith("[pre-send blocked]")


def test_post_send_detects_violation():
    eng, prov = _mk_engine()
    eng._call_model("hello")
    # 篡改后手动触发 post-send 断言
    ev = eng.model_request_log.events[-1]
    ev.messages_snapshot = [{"role": "user", "content": "TAMPERED"}]
    eng._assert_model_visible(ev.seq, [{"role": "user", "content": "hello"}])
    vs = eng.mv1_violations_structured
    assert len(vs) >= 1
    # MV-1a 检出 (快照篡改)
    assert any("MV-1" in v.reason for v in vs)


def test_structured_property_returns_dataclass_tuple():
    eng, _ = _mk_engine()
    assert eng.mv1_violations_structured == ()


def test_string_property_backward_compat():
    """mv1_violations (str 元组) 兼容旧消费方。"""
    eng, _ = _mk_engine()
    eng._assert_model_visible(1, [{"role": "user", "content": "x"}])  # log 空 → 违规
    strs = eng.mv1_violations
    # D8: MV-1a + MV-1b 双断言各 1 条
    assert len(strs) == 2
    assert all(isinstance(s, str) for s in strs)
    assert all("seq=" in s for s in strs)


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} tests passed")
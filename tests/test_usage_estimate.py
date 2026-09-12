"""P0: token 遥测兜底 — usage 缺失时 _finalize_turn/journal 层估算，保证遥测非 0。"""
from dataclasses import dataclass

from lingclaude.core.model_call import _accumulate_usage, _estimate_tokens
from lingclaude.model.types import ModelUsage


@dataclass
class FakeResp:
    content: str
    usage: object = None


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_chinese():
    n = _estimate_tokens("你好世界，这是中文文本测试")
    assert n >= 1


def test_accumulate_real_usage_wins():
    r = FakeResp(content="abc", usage=ModelUsage(input_tokens=100, output_tokens=50))
    ti, to = _accumulate_usage(0, 0, r, "abc")
    assert ti == 100 and to == 50


def test_accumulate_no_usage_keeps_zero():
    # _accumulate_usage 只累真实值；估算在 _finalize_turn/journal 层
    r = FakeResp(content="这是一段中文内容" * 10, usage=ModelUsage())
    ti, to = _accumulate_usage(0, 0, r, r.content)
    assert ti == 0 and to == 0


def test_accumulate_usage_none_object_keeps_zero():
    # stream 路径传 ModelUsage(0,0) → 保持 0（估算在 journal 层）
    ti, to = _accumulate_usage(0, 0, ModelUsage(), "hello world")
    assert ti == 0 and to == 0


def test_accumulate_preserves_accumulated():
    r = FakeResp(content="x", usage=ModelUsage())
    ti, to = _accumulate_usage(50, 30, r, "x")
    assert ti == 50 and to == 30

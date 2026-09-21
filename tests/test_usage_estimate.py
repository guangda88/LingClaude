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
    # 2026-09-21: 契约升级为三元组（第三项 cached_tokens）
    r = FakeResp(content="abc", usage=ModelUsage(input_tokens=100, output_tokens=50))
    ti, to, tc = _accumulate_usage(0, 0, r, "abc")
    assert ti == 100 and to == 50 and tc == 0


def test_accumulate_cached_tokens_accumulates():
    # 2026-09-21: cached_tokens 逐轮累加
    r1 = FakeResp(content="a", usage=ModelUsage(input_tokens=1000, output_tokens=50, cached_tokens=800))
    r2 = FakeResp(content="b", usage=ModelUsage(input_tokens=1200, output_tokens=30, cached_tokens=1000))
    ti, to, tc = _accumulate_usage(0, 0, r1, "a")
    ti, to, tc = _accumulate_usage(ti, to, r2, "b", tc)
    assert ti == 2200 and to == 80 and tc == 1800


def test_accumulate_no_usage_keeps_zero():
    # _accumulate_usage 只累真实值；估算在 _finalize_turn/journal 层
    r = FakeResp(content="这是一段中文内容" * 10, usage=ModelUsage())
    ti, to, tc = _accumulate_usage(0, 0, r, r.content)
    assert ti == 0 and to == 0 and tc == 0


def test_accumulate_usage_none_object_keeps_zero():
    # stream 路径传 ModelUsage(0,0) → 保持 0（估算在 journal 层）
    ti, to, tc = _accumulate_usage(0, 0, ModelUsage(), "hello world")
    assert ti == 0 and to == 0 and tc == 0


def test_accumulate_preserves_accumulated():
    # 2026-09-21: 三元组契约（第三项 cached 累计保持）
    r = FakeResp(content="x", usage=ModelUsage())
    ti, to, tc = _accumulate_usage(50, 30, r, "x", 70)
    assert ti == 50 and to == 30 and tc == 70

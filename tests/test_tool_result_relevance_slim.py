"""P1-5 接线增强测试：_slim_tool_output 的 task_hint 相关性剪枝两级瘦身。

覆盖（2026-09-23 补测试缺口——剪枝消费链此前零覆盖）：
- prune_by_relevance 内核：段数短路 / 无收益 None 信号 / 预算收敛 / 首尾保底
- _slim_tool_output 消费点：小结果直通 / 剪枝优先 / fail-open 回退固定截断
"""

from lingclaude.engine.context_pruning import prune_by_relevance
from lingclaude.engine.loop.loop_body import (
    _TOOL_RESULT_SLIM_KEEP,
    _TOOL_RESULT_SLIM_THRESHOLD,
    _slim_tool_output,
)

NOISE = "lorem ipsum dolor sit amet consectetur adipiscing elit "  # 纯噪声词：无数字/路径/报错词/代码标记


def _noise_para(n_chars: int = 1200) -> str:
    para = (NOISE * ((n_chars // len(NOISE)) + 1))[:n_chars]
    assert "lorem" in para
    return para


def _large_mixed_output() -> str:
    """首段 + 两段大噪声 + 高相关段 + 尾段：剪枝应弃中段噪声、保相关与首尾。"""
    return "\n\n".join([
        "run summary: agent task started",
        _noise_para(),
        _noise_para(),
        "kpirelevant parser.py traceback error at line 42: ValueError bad token",
        "run finished ok",
    ])


# ---------------------------------------------------------------- 内核短路


def test_prune_kernel_few_paragraphs_returns_none():
    # 段数 ≤ 首尾保底数 → 剪枝无意义 → None（fail-open 信号）
    assert prune_by_relevance("a\n\nb", "hint") is None
    assert prune_by_relevance("", "hint") is None


def test_prune_kernel_none_text_returns_empty():
    assert prune_by_relevance(None, "hint") == ""


def test_prune_kernel_no_benefit_returns_none():
    # 保留段仍占原文一半以上（keep_ratio=0.5）→ 无收益 → None
    hint = "parser error"
    paras = ["parser error " + "y" * 500 for _ in range(3)]
    assert prune_by_relevance("\n\n".join(paras), hint) is None


# ---------------------------------------------------------------- 内核预算


def test_budget_caps_kept_content_and_keeps_head_tail():
    hint = "parser error line"
    # 30 段纯噪声（首尾保底）+ 5 段高相关：初始保留段 ≈800+5×300=2300，
    # 既 > max_chars(1600) 又 < 原文 50%（≈6100）→ 才能进入预算裁剪路径。
    # （无收益闸门在预算裁剪之前：保留段 ≥ 原文 50% 会先返回 None。）
    noise = _noise_para(400)
    rel = lambda i: f"REL{i} parser error line 42 ValueError " + "z" * 250
    paras = [noise for _ in range(30)]
    for slot in (5, 10, 15, 20, 24):
        paras[slot] = rel(slot)
    text = "\n\n".join(paras)
    pruned = prune_by_relevance(text, hint, max_chars=1600)
    assert pruned is not None
    kept_core = pruned.split("\n[工具输出相关性剪枝")[0]
    assert len(kept_core) <= 1600                    # 核心内容入预算（标记不计入）
    assert kept_core.startswith(paras[0])            # 首段保底
    assert kept_core.endswith(paras[-1])             # 末段保底
    assert any(f"REL{s}" in kept_core for s in (5, 10, 15, 20, 24))  # 相关段优先于噪声存活


# ---------------------------------------------------------------- 消费点接线


def test_slim_threshold_semantics_anchored():
    # 阈值语义锚定：防未来无意改动消费点行为
    assert _TOOL_RESULT_SLIM_THRESHOLD == 1600
    assert _TOOL_RESULT_SLIM_KEEP == 800


def test_short_output_returns_as_is():
    out = "short result"
    assert _slim_tool_output("read", out) == out
    assert _slim_tool_output("read", out, task_hint="anything") == out


def test_large_output_prefers_relevance_pruning():
    out = _large_mixed_output()
    hint = "debug parser.py traceback error line 42"
    slimmed = _slim_tool_output("read", out, task_hint=hint)
    assert len(slimmed) < len(out)
    assert "[工具输出相关性剪枝:" in slimmed  # 走的剪枝路径而非固定截断
    assert "kpirelevant" in slimmed            # 高相关段保留
    assert "run summary" in slimmed            # 首段保底
    assert "run finished" in slimmed           # 尾段保底
    assert "lorem" not in slimmed              # 中段纯噪声被剪


def test_empty_task_hint_degrades_to_significance():
    # task_hint 空 → 退化为段落显著性（报错/路径段显著性高 → 留，噪声 → 剪）
    slimmed = _slim_tool_output("read", _large_mixed_output(), task_hint="")
    assert "[工具输出相关性剪枝:" in slimmed
    assert "kpirelevant" in slimmed
    assert "lorem" not in slimmed


def test_unprunable_output_falls_back_to_fixed_truncation():
    # 仅 2 段 → 剪枝返回 None → 回退固定截断（前缀 + 总长引用）
    out = _noise_para(1800) + "\n\n" + "final tail line"
    slimmed = _slim_tool_output("read", out, task_hint="debug")
    assert len(out) > _TOOL_RESULT_SLIM_THRESHOLD
    assert "[工具结果瘦身:" in slimmed
    assert "历史仅保留前" in slimmed
    assert slimmed.startswith(out[:_TOOL_RESULT_SLIM_KEEP])
    assert "final tail line" not in slimmed  # 尾部被截断（该路径无首尾保底）


def test_prune_failure_falls_back_to_fixed_truncation(monkeypatch):
    # 剪枝内核故障 → fail-open → 固定截断兜底，不抛异常不阻塞主循环
    def _boom(*_a, **_k):
        raise RuntimeError("prune kernel down")

    monkeypatch.setattr(
        "lingclaude.engine.context_pruning.prune_by_relevance", _boom
    )
    out = _large_mixed_output()
    slimmed = _slim_tool_output("read", out, task_hint="debug")
    assert "[工具结果瘦身:" in slimmed
    assert slimmed.startswith(out[:_TOOL_RESULT_SLIM_KEEP])
    assert len(slimmed) < len(out)

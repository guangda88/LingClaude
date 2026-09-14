"""P20: source_span 打标契约 —— 打标绑定原文切片，替代 replace(a.text) 全文替换。

背景：74be62a/6eedba 事故 —— 输出文本本身包含模板文字
（如调试输出/diff/错误信息里的 "工具动作声明无证据 → 标记" 字样）时，
旧 replace(a.text) 会把标记错插进无关位置。

方案 A：Assertion 记录 source_span（start/end），打标按原文偏移切片定位，
只对"真实声明行的原文切片"打标，即使文本包含模板文字也不误插。
"""
from __future__ import annotations

import unittest

from lingclaude.core.prior_verifier import Assertion, AssertionLevel, PriorVerifier


class TestPriorVerifierSpanTags(unittest.TestCase):
    def setUp(self):
        self.pv = PriorVerifier()

    def test_normal_claim_tagged_at_declared_position(self):
        """正常声明句 → 标记插在声明文字前（原文切片位置）"""
        r = self.pv.analyze("已运行 sed 检查源码（bash 执行成功）", used_tools=False)
        self.assertIn("⚠ [工具结果未验证]", r.corrected_text)
        # 标记必须紧贴真实声明文字（"已运行"），而非全文任意 replace 位置
        self.assertIn("⚠ [工具结果未验证] 已运行 sed", r.corrected_text)

    def test_output_containing_template_text_not_tagged(self):
        """输出文本包含模板文字 → 不误插标记（74be62a 事故回归）"""
        # 模拟调试输出/diff 里出现 "工具动作声明无证据" 字样，但并非真实声明句
        text = (
            "检查日志：发现工具动作声明无证据 → 标记 日志片段。"
            "结论：已运行 sed 检查（bash 执行成功）。"
        )
        r = self.pv.analyze(text, used_tools=False)
        # 模板文字本身（"工具动作声明无证据 → 标记"）不应被打标
        self.assertNotIn("⚠ [工具结果未验证] 工具动作声明无证据", r.corrected_text)
        # 真实声明句（"已运行"）应被打标
        self.assertIn("⚠ [工具结果未验证] 已运行 sed", r.corrected_text)

    def test_no_span_assertion_falls_back_to_replace(self):
        """外部手工构造无偏移断言 → 降级 replace（向后兼容）"""
        a = Assertion(
            text="已提交 commit",
            level=AssertionLevel.HARD_FACT,
            reason="Tool action claim (commit_claim) without tool verification",
            source="manual",
        )
        from lingclaude.core.prior_verifier import _apply_span_tags

        out = _apply_span_tags("内容：已提交 commit 并推送", [a], "⚠ [工具结果未验证]")
        self.assertIn("⚠ [工具结果未验证] 已提交 commit", out)

    def test_overlapping_spans_no_duplicate_tag(self):
        """同一原文片段被多个断言命中 → 只插一次标（去重）"""
        # "已运行" 同时命中 action_claim 与 code 相关断言时，不应重复插标
        text = "已运行 sed 检查源码"
        r = self.pv.analyze(text, used_tools=False)
        # 统计标记出现次数：无论命中多少个断言，同一原文切片只应被打一次标
        self.assertEqual(r.corrected_text.count("⚠ [工具结果未验证]"), 1)
        # 且标记数量不能超过声明文字出现次数（旧 replace 可能多插）
        self.assertLessEqual(
            r.corrected_text.count("⚠ [工具结果未验证]"),
            text.count("已运行"),
        )

    def test_assertion_exposes_span(self):
        """Assertion 应暴露 start/end 偏移（方案 A 契约）"""
        r = self.pv.analyze("已运行 sed 检查源码", used_tools=False)
        with_span = [a for a in r.assertions if a.start >= 0]
        self.assertGreater(len(with_span), 0)
        for a in with_span:
            self.assertGreaterEqual(a.end, a.start)
            # 偏移应指向原文内真实文字
            self.assertEqual(r.original[a.start:a.end], a.text)


if __name__ == "__main__":
    unittest.main()

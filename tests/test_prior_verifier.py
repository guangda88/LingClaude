"""Tests for lingclaude.core.prior_verifier"""
from __future__ import annotations

import unittest

from lingclaude.core.prior_verifier import (
    AssertionLevel,
    PriorVerifier,
)


class TestPriorVerifier(unittest.TestCase):
    def setUp(self):
        self.pv = PriorVerifier()

    def test_plain_text_verified(self):
        r = self.pv.analyze("Hello world")
        self.assertTrue(r.verified)
        self.assertEqual(len(r.assertions), 0)

    def test_code_claim_without_tools(self):
        r = self.pv.analyze("函数 foo 返回 bar")
        hard = [a for a in r.assertions if a.level == AssertionLevel.HARD_FACT]
        self.assertGreater(len(hard), 0)
        self.assertFalse(r.verified)

    def test_code_claim_with_tools(self):
        r = self.pv.analyze("函数 foo 返回 bar", used_tools=True)
        hard = [a for a in r.assertions if a.level == AssertionLevel.HARD_FACT]
        self.assertEqual(len(hard), 0)

    def test_inference_markers(self):
        r = self.pv.analyze("这大概是对的，可能没问题")
        inferences = [a for a in r.assertions if a.level == AssertionLevel.SOFT_INFERENCE]
        self.assertGreater(len(inferences), 0)

    def test_unsupported_overconfident(self):
        r = self.pv.analyze("这肯定是100%正确的，毫无疑问")
        unsupported = [a for a in r.assertions if a.level == AssertionLevel.UNSUPPORTED]
        self.assertGreater(len(unsupported), 0)
        self.assertFalse(r.verified)

    def test_corrected_text_contains_tags(self):
        r = self.pv.analyze("肯定是对的")
        if r.corrected_text:
            self.assertIn("过度自信", r.corrected_text)

    def test_should_trigger_re_verification(self):
        r = self.pv.analyze("函数 foo 在第42行调用了 bar，肯定正确")
        self.assertTrue(self.pv.should_trigger_re_verification(r))

    def test_should_not_trigger_few(self):
        r = self.pv.analyze("Hello world")
        self.assertFalse(self.pv.should_trigger_re_verification(r))

    def test_mark_inferences(self):
        marked = self.pv.mark_inferences("这可能是对的")
        self.assertIn("*", marked)

    def test_strict_mode_warnings(self):
        pv = PriorVerifier(strict_mode=True)
        r = pv.analyze("函数 foo 返回 bar")
        self.assertGreater(len(r.warnings), 0)

    def test_line_number_detection(self):
        r = self.pv.analyze("在第42行有问题")
        hard = [a for a in r.assertions if a.level == AssertionLevel.HARD_FACT]
        self.assertGreater(len(hard), 0)

    def test_file_reference_detection(self):
        r = self.pv.analyze("文件 test.py 包含错误")
        hard = [a for a in r.assertions if a.level == AssertionLevel.HARD_FACT]
        self.assertGreater(len(hard), 0)

    def test_tool_action_commit_claim_without_tools(self):
        """编造 commit 声明（无工具）→ 警告 + 标记"""
        r = self.pv.analyze("已完成修复，commit 到 e11b9ce")
        self.assertIn("工具动作声明未验证", " ".join(r.warnings))
        self.assertIn("⚠ [工具结果未验证]", r.corrected_text)

    def test_tool_action_test_claim_without_tools(self):
        """编造测试全绿声明（无工具）→ 警告"""
        r = self.pv.analyze("测试全部通过，14/14 passed")
        self.assertIn("工具动作声明未验证", " ".join(r.warnings))

    def test_tool_action_file_write_claim_without_tools(self):
        """编造落盘声明（无工具）→ 警告"""
        r = self.pv.analyze("报告已写入 docs/audit/")
        self.assertIn("工具动作声明未验证", " ".join(r.warnings))

    def test_tool_action_claim_with_tools_strict_warns(self):
        """即使 used_tools=True，strict 模式仍警告（工具存在≠动作发生）"""
        pv = PriorVerifier(strict_mode=True)
        r = pv.analyze("commit 到 abc1234", used_tools=True)
        self.assertIn("工具动作声明未验证", " ".join(r.warnings))

    def test_tool_action_no_claim_no_warning(self):
        """无工具动作声明 → 无工具相关警告"""
        r = self.pv.analyze("当前目录文件列表如下")
        self.assertEqual(len(r.warnings), 0)


if __name__ == "__main__":
    unittest.main()

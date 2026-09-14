"""
T10 回归测试（2026-09-14）：幻觉治理部署配置 + 工具失败检测

背景：2026-09-12 起多次"推送成功"幻觉事件 —— 模型在工具实际失败/未执行时
仍声称"已推送/测试通过/已写入"。

T9 修复（部署配置）：
  1. hallucination_guard 原引用不存在的 lingresearch.response_harvester
     （全仓 0 命中）→ 复用本仓 PriorVerifier，守卫真正可验证。
  2. query_engine_turn_mixin.should_validate("", ...) 传空任务名
     → 改为传真实 prompt（触发 push/deploy/fix/repair 关键词）。

T10 修复（prior_verifier 工具失败检测）：
  3. PriorVerifier.analyze 新增 tool_results 参数 —— 即使调用了工具，
     只要该工具执行失败，声称"成功"的完成式声明仍打 ⚠ [工具结果未验证]。
     （修复前：used_tools=True 即不再打标，工具失败也会被当成"有据可查"。）

本文件固化以上 3 项为防回归测试。
"""
from __future__ import annotations

import unittest

from lingclaude.core.prior_verifier import PriorVerifier
from lingclaude.core.hallucination_guard import (
    validate_task_result,
    should_validate,
    HallucinationDetectedError,
)


class TestT9DeployGuard(unittest.TestCase):
    """T9: 部署配置 —— hallucination_guard 真正可验证（不再依赖外部缺失模块）"""

    def test_should_validate_push_keyword(self):
        """任务名含 push/deploy/fix 应触发验证"""
        self.assertTrue(should_validate("推送代码到远端", "推送成功"))
        self.assertTrue(should_validate("部署到生产", "部署完成"))
        self.assertTrue(should_validate("修复 bug", "已修复"))

    def test_should_validate_message_keyword(self):
        """消息含成功/失败/commit 等关键词应触发验证"""
        self.assertTrue(should_validate("", "推送成功"))
        self.assertTrue(should_validate("", "测试全部通过"))
        self.assertFalse(should_validate("", "Python 是一种语言"))

    def test_validate_detects_unverified_claim(self):
        """无工具调用的完成式声明 → 应检测为潜在幻觉"""
        r = validate_task_result("代码已推送 gitea master")
        self.assertTrue(r["has_hallucination"])
        self.assertGreater(len(r["issues"]), 0)
        # 验证后的消息应带 ⚠ 标记
        self.assertIn("工具结果未验证", r["verified_message"])

    def test_validate_passes_normal_statement(self):
        """正常陈述不应误报为幻觉"""
        r = validate_task_result("Python 是一种解释型编程语言")
        self.assertFalse(r["has_hallucination"])
        self.assertEqual(r["issues"], [])

    def test_validate_strict_mode_raises(self):
        """严格模式下检测到幻觉应抛异常"""
        with self.assertRaises(HallucinationDetectedError):
            validate_task_result("代码已推送 gitea master", strict=True)


class TestT10ToolFailureDetection(unittest.TestCase):
    """T10: prior_verifier 工具失败检测 —— 工具失败仍声称成功必须打标记"""

    def setUp(self):
        self.pv = PriorVerifier()

    def test_no_tool_claim_blocked(self):
        """无工具调用声称已推送 → 打 ⚠ [工具结果未验证]"""
        r = self.pv.analyze("代码已推送 gitea master", used_tools=False)
        self.assertIn("工具结果未验证", r.corrected_text)

    def test_tool_failure_still_blocked(self):
        """调用 git_push 但失败声称已推送 → 仍打 ⚠ [工具结果未验证]（本次修复核心）"""
        r = self.pv.analyze(
            "代码已推送 gitea master",
            used_tools=True,
            tool_evidence=("git_push",),
            tool_results={"git_push": False},
        )
        self.assertIn("工具结果未验证", r.corrected_text)
        # 且产生警告
        self.assertTrue(any("工具动作声明未验证" in w for w in r.warnings))

    def test_tool_success_clears_marker(self):
        """调用 git_push 且成功声称已推送 → 不打标记"""
        r = self.pv.analyze(
            "代码已推送 gitea master",
            used_tools=True,
            tool_evidence=("git_push",),
            tool_results={"git_push": True},
        )
        self.assertNotIn("工具结果未验证", r.corrected_text)

    def test_bash_failure_test_claim_blocked(self):
        """调用 bash 但失败声称测试通过 → 打 ⚠ [工具结果未验证]"""
        r = self.pv.analyze(
            "测试全部通过 80 passed",
            used_tools=True,
            tool_evidence=("bash",),
            tool_results={"bash": False},
        )
        self.assertIn("工具结果未验证", r.corrected_text)

    def test_pytest_success_test_claim_cleared(self):
        """调用 pytest 且成功声称测试通过 → 不打标记"""
        r = self.pv.analyze(
            "测试全部通过 80 passed",
            used_tools=True,
            tool_evidence=("pytest",),
            tool_results={"pytest": True},
        )
        self.assertNotIn("工具结果未验证", r.corrected_text)

    def test_file_write_failure_blocked(self):
        """调用 write 但失败声称已写入 → 打 ⚠ [工具结果未验证]"""
        r = self.pv.analyze(
            "报告已写入 docs/audit/",
            used_tools=True,
            tool_evidence=("write",),
            tool_results={"write": False},
        )
        self.assertIn("工具结果未验证", r.corrected_text)

    def test_empty_tool_results_keeps_old_semantics(self):
        """不传 tool_results 时保持原语义（向后兼容）"""
        r = self.pv.analyze(
            "代码已推送 gitea master",
            used_tools=True,
            tool_evidence=("git_push",),
        )
        # tool_results 缺省 → 不因"工具失败"逻辑新增标记
        # used_tools=True 且 evidence 命中 → 原语义不打标
        self.assertNotIn("工具结果未验证", r.corrected_text)


if __name__ == "__main__":
    unittest.main()

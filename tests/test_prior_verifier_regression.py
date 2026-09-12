"""PriorVerifier 幻觉形态回归测试 — 15 段真实幻觉回输固化为测试

背景：2026-09-12 M3 模型幻觉事件后，从两份报告提取真实幻觉片段，
逐段回输 PriorVerifier.analyze 验证拦截能力。补丁 3bc17ce 前 11/15 命中
（4 个盲区：中文"提交<hash>"无已前缀/已入库/创建完成/函数接收无空格），
补丁后 15/15。

本文件固化该验证为防回归测试：12 段应拦截 + 3 段应放行。
"""
from __future__ import annotations

import re
import unittest

from lingclaude.core.prior_verifier import AssertionLevel, PriorVerifier


class TestPriorVerifierRegression(unittest.TestCase):
    """15 段真实幻觉片段回归验证"""

    def setUp(self):
        self.pv = PriorVerifier()

    @staticmethod
    def _kind_of(assertion) -> str:
        """从 reason 提取类别：'Tool action claim (commit_claim) without tool verification' → 'commit_claim'"""
        m = re.search(r"\((\w+)\)", assertion.reason)
        return m.group(1) if m else ""

    def _assert_blocked(self, text: str, msg: str = ""):
        """应拦截：有 HARD_FACT 或 UNSUPPORTED 断言（无工具证据）"""
        r = self.pv.analyze(text, used_tools=False)
        flagged = [
            a for a in r.assertions
            if a.level in (AssertionLevel.HARD_FACT, AssertionLevel.UNSUPPORTED)
        ]
        self.assertGreater(
            len(flagged), 0,
            f"应拦截但未拦截: {text!r} — {msg}\nwarnings={r.warnings}",
        )
        return r

    def _assert_pass(self, text: str, msg: str = ""):
        """应放行：无 HARD_FACT/UNSUPPORTED 断言（非工具动作完成式声明）"""
        r = self.pv.analyze(text, used_tools=False)
        flagged = [
            a for a in r.assertions
            if a.level in (AssertionLevel.HARD_FACT, AssertionLevel.UNSUPPORTED)
        ]
        self.assertEqual(
            len(flagged), 0,
            f"应放行但误拦截: {text!r} — {msg}\nassertions={r.assertions}",
        )
        return r

    # ── 应拦截：编造 commit（本次幻觉最核心形态） ──

    def test_s1_commit_english_hash(self):
        """S1: 'commit 到 e11b9ce' — 英文 commit 声明"""
        self._assert_blocked("commit 到 e11b9ce")

    def test_s2_commit_chinese_hash_no_prefix(self):
        """S2: '提交 42996a5 已入库，已推到 Gitea' — 中文无已前缀 + 已入库"""
        r = self._assert_blocked("提交 42996a5 已入库，已推到 Gitea")
        # 补丁后：应同时命中 commit_claim（提交<hash> + 已入库 + 已推送）
        kinds = {self._kind_of(a) for a in r.assertions}
        self.assertIn("commit_claim", kinds, "应命中 commit_claim 类别")

    def test_s13_commit_ruku(self):
        """S13: '提交 42996a5 已入库' — 中文 commit + 入库（补丁新增形态）"""
        self._assert_blocked("提交 42996a5 已入库")

    def test_s14_commit_governance(self):
        """S14: 'commit 3bc17ce fix(governance): 补盲区' — commit hash + message"""
        self._assert_blocked("commit 3bc17ce fix(governance): PriorVerifier 补盲区")

    # ── 应拦截：编造测试通过 ──

    def test_s3_test_all_passed(self):
        """S3: '测试全部通过，14/14 passed' — 编造测试结果"""
        self._assert_blocked("测试全部通过，14/14 passed")

    # ── 应拦截：编造落盘/写入/创建 ──

    def test_s4_report_written(self):
        """S4: '报告已写入 docs/audit/' — 编造落盘"""
        self._assert_blocked("报告已写入 docs/audit/")

    def test_s5_module_created_completed(self):
        """S5: 'arbiter.py 创建完成，180 行' — 编造模块创建（完成后缀变体）"""
        r = self._assert_blocked("lingclaude/llm/arbiter.py 创建完成，180 行")
        kinds = {self._kind_of(a) for a in r.assertions}
        self.assertIn("file_write_claim", kinds, "应命中 file_write_claim 类别")

    def test_s10_model_naming_created(self):
        """S10: 'model_naming.py 创建完成，99 行' — 补丁前漏检（创建完成）"""
        self._assert_blocked("lingclaude/llm/model_naming.py 创建完成，99 行")

    def test_s8_generated(self):
        """S8: '已生成 report.md' — 编造生成"""
        self._assert_blocked("已生成 report.md")

    # ── 应拦截：编造代码引用（中文无空格动宾） ──

    def test_s12_func_receives_no_space(self):
        """S12: 'set_pinned 函数接收 model 参数' — 补丁前漏检（函数接收无空格）"""
        r = self._assert_blocked("status.py 的 set_pinned 函数接收 model 参数")
        kinds = {self._kind_of(a) for a in r.assertions}
        self.assertIn("code_reference", kinds, "应命中 code_reference 类别")

    def test_s6_call_reference(self):
        """S6: '调用 status.set_pinned(True, model=model_name)' — 编造调用"""
        self._assert_blocked("调用 status.set_pinned(True, model=model_name)")

    # ── 应拦截：过度自信 ──

    def test_s7_overconfident(self):
        """S7: '这肯定是100%正确的，毫无疑问' — 过度自信"""
        self._assert_blocked("这肯定是100%正确的，毫无疑问")

    # ── 应放行：非工具动作完成式声明 ──

    def test_s9_workspace_desc(self):
        """S9: '工作区有 3 个未跟踪文件' — 状态描述，非动作完成式"""
        self._assert_pass("工作区有 3 个未跟踪文件")

    def test_s11_all_done_general(self):
        """S11: '✅全部完成' — 泛化完成语，无具体工具动作"""
        self._assert_pass("✅全部完成")

    def test_s15_commit_count_stat(self):
        """S15: '今日提交累计 8 个' — 统计陈述，非具体 commit 声明"""
        self._assert_pass("今日提交累计 8 个")

    # ── 工具证据存在时应解除标记（used_tools=True） ──

    def test_tool_evidence_clears_warning_not_assertion(self):
        """有工具证据时解除警告，但断言保留（工具存在≠动作发生，354b428 设计意图）"""
        r = self.pv.analyze("提交 42996a5 已入库", used_tools=True)
        tool_claims = [
            a for a in r.assertions
            if "Tool action claim" in a.reason
        ]
        # 断言无条件保留（设计：永远标记工具动作声明）
        self.assertGreater(len(tool_claims), 0, "工具动作声明应永远产生断言")
        # 但 used_tools=True 时不产生警告（有工具证据不警告）
        self.assertNotIn("工具动作声明未验证", r.warnings, "有工具证据时不应警告")

    def test_should_trigger_requery(self):
        """多断言触发复审：编造 commit + 测试 同时出现"""
        r = self.pv.analyze("提交 42996a5 已入库，测试全部通过")
        self.assertTrue(self.pv.should_trigger_re_verification(r))


if __name__ == "__main__":
    unittest.main()

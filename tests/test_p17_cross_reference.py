"""P17: Cross-reference claims —— 执行性声明必须有据可查（灵元：治理插片 fail-soft）。

背景：v1 幻觉治理文档 P0-1 指 lingclaude 缺 CC 的「Cross-reference claims」
（每条 claim 必须有源）。实测：PriorVerifier 已有先验正则标记，但缺
「声明类型 ↔ 本 turn 真实工具证据」的语义匹配。

本测试锁定：
1. 提供工具证据且命中映射 → 声明不打「未验证」标（有据可查）
2. 提供工具证据但未命中 → 仍打标（无据声明）
3. 不提供证据 → 保持向后兼容（原语义）
4. 映射前缀匹配防工具名演化断链
"""

from __future__ import annotations

import pytest

from lingclaude.core.prior_verifier import PriorVerifier


class TestP17CrossReference:
    def setup_method(self) -> None:
        self.pv = PriorVerifier()

    def test_commit_claim_with_git_push_evidence_not_tagged(self):
        """声明「已提交 xxx」+ 证据 git_push → 有据，不打「未验证」标。"""
        text = "我已提交 abc1234 到远端。"
        r = self.pv.analyze(text, used_tools=False, tool_evidence=("git_push",))
        assert "未验证" not in r.corrected_text, (
            f"git_push 应证明 commit_claim 有据，实际被打标: {r.corrected_text}"
        )

    def test_commit_claim_without_evidence_tagged(self):
        """声明「已提交」但无任何工具证据 → 打「未验证」标。"""
        text = "我已提交 abc1234 到远端。"
        r = self.pv.analyze(text, used_tools=False, tool_evidence=("read",))
        assert "未验证" in r.corrected_text, (
            f"read 不能证明 commit_claim，应打标: {r.corrected_text}"
        )

    def test_write_claim_with_edit_evidence_not_tagged(self):
        """声明「已写入」+ 证据 edit → 有据。"""
        text = "我已将配置写入 config.yaml。"
        r = self.pv.analyze(text, used_tools=False, tool_evidence=("edit",))
        assert "未验证" not in r.corrected_text

    def test_test_claim_with_bash_pytest_evidence_not_tagged(self):
        """声明「测试通过」+ 证据 bash（跑过 pytest）→ 有据。"""
        text = "测试全部通过，共 42 个用例。"
        r = self.pv.analyze(text, used_tools=False, tool_evidence=("bash",))
        assert "未验证" not in r.corrected_text

    def test_no_evidence_backward_compatible(self):
        """不传 tool_evidence → 保持原语义（未验证仍打标）。"""
        text = "我已提交 abc1234 到远端。"
        r = self.pv.analyze(text, used_tools=False)
        assert "未验证" in r.corrected_text

    def test_prefix_match_robust(self):
        """前缀匹配：工具名带后缀（如 git_push_preflight）仍命中 git_push。"""
        text = "我已提交 abc1234 到远端。"
        r = self.pv.analyze(
            text, used_tools=False, tool_evidence=("git_push_preflight",),
        )
        assert "未验证" not in r.corrected_text

    def test_used_tools_with_evidence_still_works(self):
        """used_tools=True + 证据命中 → 不打标（双重保障）。"""
        text = "文件已保存到 /tmp/x。"
        r = self.pv.analyze(
            text, used_tools=True, tool_evidence=("write",),
        )
        assert "未验证" not in r.corrected_text

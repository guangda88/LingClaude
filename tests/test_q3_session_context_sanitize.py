"""Q3 (2026-09-14): 系统提示渲染侧过滤治理标记 —— 提交消息含 ⚠ 不污染系统提示。

背景：5ff2ba1 提交消息自述「修复有据声明被误打 ⚠[工具结果未验证]」，
system_prompt_builder._build_session_context 用 `git show -s` 注入最近提交到
SESSION_CONTEXT，导致 test_adaptive 健康状态断言失败（系统提示含 ⚠）。

修法（渲染侧过滤，不篡改 git 历史）：
- ⚠ / 💡 是幻觉治理运行时标记，作为系统提示上下文应去除干扰符号。
- 契约：即使提交消息含治理标记字符，SESSION_CONTEXT 渲染结果也不含 ⚠/💡。
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

from lingclaude.core.system_prompt_builder import _build_session_context


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(cmd, *, capture_output=False, text=False, timeout=None):
    """伪造 git show 输出：最近提交里含治理标记字符。"""
    if cmd[0] == "git" and cmd[1] == "status":
        return _FakeCompleted(0, "## master\n M lingclaude/core/x.py\n")
    if cmd[0] == "git" and cmd[1] == "show":
        return _FakeCompleted(
            0,
            "5ff2ba1 fix(p17): 修复有据声明被误打 ⚠[工具结果未验证] (2026-09-14 09:54:51 +0800)\n"
            "9e7f08b fix(p20): 幻觉治理标记绑定 source_span (2026-09-14 10:29:59 +0800)\n",
        )
    return _FakeCompleted(1, "")


def test_commit_message_with_governance_mark_sanitized(monkeypatch):
    """契约：提交消息含 ⚠ 时，SESSION_CONTEXT 渲染结果不含治理标记字符。"""
    monkeypatch.setattr(subprocess, "run", _fake_run)
    ctx = _build_session_context()
    assert "⚠" not in ctx
    assert "💡" not in ctx
    # 但提交信息本身仍保留（只是 ⚠ 被替换为 !）
    assert "5ff2ba1" in ctx
    assert "修复有据声明被误打 ![工具结果未验证]" in ctx


def test_clean_commit_messages_unchanged(monkeypatch):
    """契约：不含治理标记的提交消息原样保留（不误伤正常内容）。"""
    def _clean_run(cmd, *, capture_output=False, text=False, timeout=None):
        if cmd[0] == "git" and cmd[1] == "status":
            return _FakeCompleted(0, "## master\n")
        if cmd[0] == "git" and cmd[1] == "show":
            return _FakeCompleted(0, "abc1234 feat: 正常提交 (2026-09-14 10:00:00 +0800)\n")
        return _FakeCompleted(1, "")

    monkeypatch.setattr(subprocess, "run", _clean_run)
    ctx = _build_session_context()
    assert "abc1234 feat: 正常提交" in ctx
    assert "⚠" not in ctx


def test_git_failure_graceful():
    """契约：git 失败时 SESSION_CONTEXT 不含提交段但不抛异常（吞异常纪律）。"""
    import os
    import subprocess as sp
    real_run = sp.run

    def _fail_run(cmd, **kwargs):
        raise FileNotFoundError("git not found")

    sp.run = _fail_run
    try:
        ctx = _build_session_context()
    finally:
        sp.run = real_run
    # git 失败 → 不崩，可能只有目录/日期，也可能全空
    assert isinstance(ctx, str)

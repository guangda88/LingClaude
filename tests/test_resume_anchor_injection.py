"""层3 接线测试：/resume 恢复成功后注入验证台账锚点块。

锚定 2026-09-21 幻觉审计治理：恢复的不是「我记得验证过」，而是带
digest 的台账原文。同时锚定 fail-open：台账故障绝不阻断恢复主路径。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lingclaude.core.verify_ledger as vl_mod
from lingclaude.core.verify_ledger import KIND_TEST_PASSED, VerifyLedger
from lingclaude.cli.commands import SlashCommandProcessor


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """构造最小 engine + 隔离的验证台账 + fake persister。"""
    ledger = VerifyLedger(root=tmp_path / "verify_log")
    monkeypatch.setattr(vl_mod, "_default", ledger)

    engine = SimpleNamespace(
        _conversation=[],
        _hooks=None,
        _session_persister=MagicMock(),
        # _cmd_resume 会按 CWD 过滤列出会话；给空列表 → 短前缀匹配
        # 无候选，直接用参数作为 target_id（对齐 commands.py:599-605 语义）
        session_manager=SimpleNamespace(list_sessions=MagicMock(return_value=[])),
    )
    engine._session_persister.load_session.return_value = True

    status = SimpleNamespace()
    proc = SlashCommandProcessor(engine=engine, status=status, reader=None)
    return proc, engine, ledger


class TestResumeAnchorInjection:
    def test_resume_injects_anchor_block(self, env):
        """有台账条目 → 恢复后 _conversation 里出现锚点 system 消息。"""
        proc, engine, ledger = env
        ledger.record(
            KIND_TEST_PASSED, "test_x 7/7 全绿", "7 passed", trace_id="sess-anchor-1")
        ok = proc.handle("/resume sess-anchor-1")
        assert ok
        roles = [r for r, _ in engine._conversation]
        assert "system" in roles
        anchor_text = next(t for r, t in engine._conversation if r == "system")
        assert "验证台账锚点" in anchor_text
        assert "test_x 7/7 全绿" in anchor_text
        assert "digest=" in anchor_text

    def test_resume_without_ledger_no_injection(self, env):
        """无台账条目 → 不注入空块（恢复输出照常）。"""
        proc, engine, _ = env
        assert proc.handle("/resume sess-empty")
        assert engine._conversation == []

    def test_resume_ledger_failure_fail_open(self, env, monkeypatch):
        """锚定 fail-open：anchor_block 抛异常 → 恢复主路径不受影响。"""
        proc, engine, _ = env

        def boom(*a, **kw):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(vl_mod.VerifyLedger, "anchor_block", boom)
        assert proc.handle("/resume sess-boom")  # 主路径不受台账故障阻断
        assert engine._conversation == []  # 且没有半截注入

    def test_resume_load_failure_no_injection(self, env):
        """恢复失败路径完全不触台账（命令仍算已消费，但零注入）。"""
        proc, engine, ledger = env
        engine._session_persister.load_session.return_value = False
        ledger.record(KIND_TEST_PASSED, "c", "e", trace_id="sess-x")
        assert proc.handle("/resume sess-x")  # 命令已消费（失败分支有打印）
        assert engine._conversation == []  # 但零注入

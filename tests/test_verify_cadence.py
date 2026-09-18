"""VerifyCadenceHook 单元测试 — 零推理行为校验 (2026-09-18 P2)。

红线断言：全部行为纯字符串匹配驱动，无任何模型调用。
"""

from __future__ import annotations

import pytest

from lingclaude.core.verify_cadence import (
    DEDUP_THRESHOLD,
    VerifyCadenceHook,
    _is_verify_command,
)


# ── 校验节奏：编辑 → 未验证 → nudge ──────────────────────────────────


class TestVerifyCadence:
    def test_edit_creates_pending_then_nudge_once(self):
        h = VerifyCadenceHook(enabled=True)
        # 编辑成功
        h.observe_call("edit", {"path": "a.py"})
        h.observe_result("edit", {"path": "a.py"}, error=None)
        assert h._pending_edits == 1
        # 下一个非编辑调用触发一次性 nudge
        nudge = h.observe_call("grep", {"pattern": "x"})
        assert nudge is not None and "verify_cadence" in nudge
        # 同批 pending 不再刷屏
        assert h.observe_call("read", {"path": "a.py"}) is None

    def test_verify_command_clears_pending(self):
        h = VerifyCadenceHook(enabled=True)
        h.observe_result("edit", {"path": "a.py"}, error=None)
        assert h._pending_edits == 1
        # 验证命令成功 → 清零
        h.observe_call("bash", {"command": "python3 -m pytest tests/ -q"})
        h.observe_result("bash", {"command": "python3 -m pytest tests/ -q"}, error=None)
        assert h._pending_edits == 0
        # 清零后不再提醒
        assert h.observe_call("grep", {"pattern": "x"}) is None

    def test_non_verify_bash_does_not_clear(self):
        h = VerifyCadenceHook(enabled=True)
        h.observe_result("write", {"path": "a.py"}, error=None)
        h.observe_call("bash", {"command": "ls -la"})
        h.observe_result("bash", {"command": "ls -la"}, error=None)
        assert h._pending_edits == 1, "ls 不是验证命令，不应清 pending"

    def test_failed_edit_no_pending(self):
        h = VerifyCadenceHook(enabled=True)
        h.observe_call("edit", {"path": "a.py"})
        h.observe_result("edit", {"path": "a.py"}, error="boom")
        assert h._pending_edits == 0

    def test_new_edit_re_arms_nudge(self):
        h = VerifyCadenceHook(enabled=True)
        h.observe_result("edit", {"path": "a.py"}, error=None)
        assert h.observe_call("grep", {"pattern": "x"})  # 第一次提醒
        h.observe_result("edit", {"path": "b.py"}, error=None)  # 新编辑 → 新批次
        assert h.observe_call("read", {"path": "b.py"})  # 再次提醒

    def test_disabled_hook_is_silent(self):
        h = VerifyCadenceHook(enabled=False)
        h.observe_result("edit", {"path": "a.py"}, error=None)
        assert h.observe_call("grep", {"pattern": "x"}) is None
        assert h._pending_edits == 0

    def test_env_default_enabled(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_VERIFY_CADENCE", raising=False)
        assert VerifyCadenceHook().enabled is True
        monkeypatch.setenv("LINGCLAUDE_VERIFY_CADENCE", "0")
        assert VerifyCadenceHook().enabled is False


# ── 死循环检测 ────────────────────────────────────────────────────────


class TestLoopDetection:
    def test_repeated_calls_trigger_loop_nudge(self):
        h = VerifyCadenceHook(enabled=True)
        kwargs = {"pattern": "x", "path": "."}
        nudges = []
        for _ in range(DEDUP_THRESHOLD):
            n = h.observe_call("grep", kwargs)
            if n:
                nudges.append(n)
        assert len(nudges) == 1, "第 3 次重复触发且只提醒一次"
        assert "死循环" in nudges[0]

    def test_distinct_calls_do_not_trigger(self):
        h = VerifyCadenceHook(enabled=True)
        for i in range(DEDUP_THRESHOLD + 2):
            assert h.observe_call("read", {"path": f"f{i}.py"}) is None

    def test_loop_nudge_only_once_per_key(self):
        h = VerifyCadenceHook(enabled=True)
        kwargs = {"pattern": "loop"}
        for _ in range(DEDUP_THRESHOLD):
            h.observe_call("grep", kwargs)
        # 继续重复 → 不再提醒
        for _ in range(3):
            assert h.observe_call("grep", kwargs) is None

    def test_reset_clears_state(self):
        h = VerifyCadenceHook(enabled=True)
        kwargs = {"pattern": "x"}
        for _ in range(DEDUP_THRESHOLD):
            h.observe_call("grep", kwargs)
        h.reset()
        assert h.observe_call("grep", kwargs) is None


# ── 验证命令识别 ──────────────────────────────────────────────────────


class TestVerifyCommandDetection:
    @pytest.mark.parametrize("cmd", [
        "python3 -m pytest tests/ -q",
        "pytest -x",
        "python3 -m unittest discover",
        "ruff check .",
        "python3 -c 'import a; a.main()'",
        "make test",
    ])
    def test_verify_commands_recognized(self, cmd):
        assert _is_verify_command(cmd)

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "cat file.txt",
        "echo hello",
        "git status",
    ])
    def test_non_verify_commands_rejected(self, cmd):
        assert not _is_verify_command(cmd)

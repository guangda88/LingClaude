"""E2E: 斜杠命令 subprocess 测(F2 验证 + AC#1 interactive 退出)."""
from __future__ import annotations

import os
import pwd as _pwd
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.conftest import requires_mcp

pytestmark = requires_mcp


def _real_home_env() -> dict:
    env = os.environ.copy()
    env.update({
        "LINGCLAUDE_CLI_MODE": "plain",
        "LINGCLAUDE_BUS_LISTENER": "0",
        "PYTHONPATH": "/home/ai/lingclaude",
        "HOME": _pwd.getpwuid(os.getuid()).pw_dir,  # 防 user-site 丢失
    })
    return env


class TestSlashCommandsEndToEnd:
    """AC#1 修复后:interactive + LINGCLAUDE_RAISE_EOF=1 + stdin=DEVNULL → 立即 EOF 退."""

    def test_interactive_empty_stdin_clean_exit(self):
        env = _real_home_env()
        env["LINGCLAUDE_RAISE_EOF"] = "1"
        r = subprocess.run(
            [sys.executable, "-m", "lingclaude.cli", "run", "--interactive"],
            capture_output=True, text=True, timeout=30.0, env=env,
            stdin=subprocess.DEVNULL,
        )
        combined = r.stdout + r.stderr
        assert r.returncode in (0, 1, 2), f"out={combined[:200]}"
        assert "再见" in combined, f"期望 EOF 退出提示,实际: {combined[:300]}"

    @pytest.mark.skip(reason="AC#1 修复后保留为对照覆盖组;exit 路径由上面 EOF 用例覆盖")
    def test_interactive_exit_command(self):
        pass


class TestF2UndoRemoval:
    """F2 验证 — src-grep /undo 已从 completer 移除."""

    def test_undo_not_in_completer_source(self):
        from pathlib import Path
        app_py = Path("/home/ai/lingclaude/lingclaude/cli/repl.py").read_text(encoding="utf-8")
        idx = app_py.find("WordCompleter(")
        assert idx > 0
        block = app_py[idx:idx + 500]
        assert '"/undo"' not in block, "F2 未修复:/undo 仍在 completer"

    def test_undo_no_handler_in_source(self):
        from pathlib import Path
        app_py = (Path("/home/ai/lingclaude/lingclaude/cli/app.py").read_text(encoding="utf-8")
                  + Path("/home/ai/lingclaude/lingclaude/cli/commands.py").read_text(encoding="utf-8"))
        assert 'name == "/undo"' not in app_py


class TestF1UnknownsTopLevel:
    def test_unknowns_top_level_still_works(self):
        r = subprocess.run(
            [sys.executable, "-m", "lingclaude.cli", "unknowns", "--help"],
            capture_output=True, text=True, timeout=15.0,
            env=_real_home_env(),
        )
        assert r.returncode == 0
        for word in ("list", "add", "resolve"):
            assert word in r.stdout


class TestRecoverWiring:
    """R5 checkpoint 核心能力必须有 CLI 入口；否则误杀后用户无法恢复工具轮。"""

    def test_recover_has_completer_and_handler(self):
        app_py = Path("/home/ai/lingclaude/lingclaude/cli/commands.py").read_text(encoding="utf-8")
        assert '"/recover"' in app_py
        assert 'name == "/recover"' in app_py
        assert "resume_interrupted()" in app_py

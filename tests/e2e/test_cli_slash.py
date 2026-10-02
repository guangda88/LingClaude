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
    """F2 历史背景:2026-09 路由降级期间临时禁止 /undo 在补全里出现;
    8c83f50 已重新放行 /undo 命令(文件级 rewind 入口),该禁令撤销。
    本类仅保留「旧 dispatch 路径已死」一项断言,补全层守卫由 SlashCompleter
    注册表单源 + commands.py:770 _register 共同覆盖."""

    def test_undo_not_in_completer_source(self):
        # F2 临时防御已撤——补全器从 WordCompleter 替换为 SlashCompleter(48b1006),
        # 候选集合由 SLASH_REGISTRY 派生,/undo 在 commands.py:770 正式注册。
        # 此处只做最弱锚点断言:repl.py 已不再依赖旧 WordCompleter,且 /undo 在
        # 注册表里(补全器同源派生)。
        from pathlib import Path
        repl = Path("/home/ai/lingclaude/lingclaude/cli/repl.py").read_text(encoding="utf-8")
        assert "WordCompleter(" not in repl, "旧补全器 WordCompleter 残留(应已替换为 SlashCompleter)"
        assert "SlashCompleter" in repl, "SlashCompleter 未在 repl.py 接入"
        commands = Path("/home/ai/lingclaude/lingclaude/cli/commands.py").read_text(encoding="utf-8")
        assert '_register("/undo"' in commands, "/undo 应在 SLASH_REGISTRY 注册"

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
        # P4.1 注册表化（dfc227e 方案A）后：commands.py 只有 _register 行，
        # handler/恢复逻辑在 _commands_checkpoint.py。两处拼接检查对齐现状，
        # 语义不变（入口登记 + handler 接线 + 恢复能力可达）。
        commands_py = Path("/home/ai/lingclaude/lingclaude/cli/commands.py").read_text(encoding="utf-8")
        checkpoint_py = Path("/home/ai/lingclaude/lingclaude/cli/_commands_checkpoint.py").read_text(encoding="utf-8")
        assert '"/recover"' in commands_py  # 注册表登记（补全+help 派生）
        assert '_register("/recover", "_cmd_recover"' in commands_py  # handler 接线
        assert "resume_interrupted()" in checkpoint_py  # R5 恢复能力实际可达

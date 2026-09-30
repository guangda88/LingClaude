"""P6: /policy reload 斜杠命令回归测试。

背景：自然 mtime watch 有 30s 节流窗，运行中改 yaml 最多等 30s。
/policy reload 内部调 policy_loader.hot_update() 立即生效；
TUI 下刷新后追加 resync() 全量重绘。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lingclaude.cli.commands import SlashCommandProcessor, SLASH_REGISTRY  # noqa: E402
from lingclaude.core import policy_loader  # noqa: E402


@pytest.fixture()
def sgr_policy(tmp_path, monkeypatch):
    """临时 sgr_styles.yaml，走真实 policies_dir 解析。"""
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    target = pol_dir / "sgr_styles.yaml"
    target.write_text("styles:\n  italic: reject\n", encoding="utf-8")
    monkeypatch.setattr(policy_loader, "_POLICIES_DIR", pol_dir)
    yield target
    policy_loader.reset()


def _policy_handler():
    """从注册表取 /policy 插件 handler（主干方法已迁 slash_plugins/policy.py）。"""
    return SLASH_REGISTRY["/policy"].handler


class TestPolicyReloadCommand:
    def test_registered(self):
        assert "/policy" in SLASH_REGISTRY
        assert SLASH_REGISTRY["/policy"].handler is not None

    def test_reload_detects_change_immediately(self, sgr_policy, monkeypatch):
        """改 yaml 后 0s 内 /policy reload 必须捕获变化（免 30s 节流）。"""
        proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
        proc.session = None
        policy_loader.reset()
        assert policy_loader.get("sgr_styles") == {"styles": {"italic": "reject"}}
        policy_loader.get("sgr_styles")  # 第2次：真实 mtime 检查并盖节流戳（模拟活跃会话）

        sgr_policy.write_text("styles:\n  italic: map8\n", encoding="utf-8")
        time.sleep(0.02)  # 确保 mtime_ns 前进
        # 节流窗（30s）内：自然路径不感知（回归锚：证明节流真实存在）
        assert policy_loader.get("sgr_styles") == {"styles": {"italic": "reject"}}

        _policy_handler()(proc, "reload")
        assert policy_loader.get("sgr_styles") == {"styles": {"italic": "map8"}}

    def test_reload_no_change_message(self, sgr_policy, capsys):
        proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
        proc.session = None
        policy_loader.reset()
        policy_loader.get("sgr_styles")  # 填充缓存
        _policy_handler()(proc, "reload")
        out = capsys.readouterr().out
        assert "无变化" in out
        assert "热更失败" not in out

    def test_bad_arg_shows_usage(self, sgr_policy, capsys):
        proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
        proc.session = None
        _policy_handler()(proc, "bogus subcommand")
        out = capsys.readouterr().out
        assert "用法" in out

    def test_tui_resync_after_reload(self, sgr_policy, capsys):
        """TUI 会话（有 resync）时刷新后自动重绘。"""
        proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
        proc.session = type("S", (), {})()
        called = []
        proc.session.resync = lambda: called.append(1)
        policy_loader.reset()
        policy_loader.get("sgr_styles")
        _policy_handler()(proc, "reload")
        assert called == [1]

    def test_no_resync_on_plain(self, sgr_policy):
        proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
        proc.session = None
        policy_loader.reset()
        policy_loader.get("sgr_styles")
        _policy_handler()(proc, "reload")  # 不应抛 AttributeError

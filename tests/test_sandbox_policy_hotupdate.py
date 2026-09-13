"""P2-1 sandbox 策略 YAML 化 + PolicyLoader 热更契约测试。

验收口径（灵元 P2）：
- 网络白名单/默认可写目录读 policies/sandbox_policy.yaml（PolicyLoader）
- 改 YAML → 不重启进程，下条命令生效（mtime watch）
- 读失败回退内置默认（graceful degrade，零行为变化）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl
from lingclaude.engine.bash import _is_network_allowed, _network_allowed_commands


@pytest.fixture(autouse=True)
def _reset_cache():
    pl.reset()
    yield
    pl.reset()


def test_network_whitelist_defaults_match_builtin():
    """策略文件白名单与内置默认一致（迁移零行为变化）。"""
    cmds = _network_allowed_commands()
    assert "git push" in cmds
    assert "git fetch" in cmds
    assert "git clone" in cmds
    assert "git ls-remote" in cmds
    assert len(cmds) >= 6


def test_network_allowed_runtime_hotupdate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """改 sandbox_policy.yaml 白名单 → 运行时 _is_network_allowed 立即生效。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "sandbox_policy.yaml"
    policy.write_text(
        "network_allowed_commands:\n"
        "  - git push\n"
        "  - git fetch\n",
        encoding="utf-8",
    )

    try:
        # 首次：仅 git push/fetch 放行（git 本身整体放行，白名单约束非 git 命令）
        assert _is_network_allowed("git push origin main") is True
        assert _is_network_allowed("rsync -avz /tmp/x user@host:/tmp") is False

        # 热更：新增 rsync 放行（按路径独立节流 + 按缓存路径重读）
        policy.write_text(
            "network_allowed_commands:\n"
            "  - git push\n"
            "  - git fetch\n"
            "  - rsync\n",
            encoding="utf-8",
        )
        assert pl.hot_update() is True
        assert _is_network_allowed("rsync -avz /tmp/x user@host:/tmp") is True
    finally:
        monkeypatch.undo()


def test_missing_policy_falls_back_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """策略文件缺失 → 回退内置白名单（graceful degrade）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    cmds = _network_allowed_commands()
    assert "git push" in cmds  # 内置默认仍在


def test_sandbox_policy_yaml_present():
    """真实策略文件存在且含白名单 + 默认可写目录。"""
    data = pl.load("sandbox_policy")
    assert "network_allowed_commands" in data
    assert "default_writable_dirs" in data
    assert data["default_writable_dirs"] == ["/home/ai"]
    assert data["network_isolation_enabled"] is True

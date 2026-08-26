from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolate_git_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试仓库不继承用户全局 core.hooksPath（~/.git-hooks 的 post-commit
    会撤销无审计记录的提交，干扰测试夹具仓库）。通过 GIT_CONFIG_* 环境变量
    强制 core.hooksPath 指向空目录，对测试内所有 git 子进程生效。"""
    empty_hooks = tempfile.mkdtemp(prefix="empty_git_hooks_")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", empty_hooks)


# RFC v0.1 §3 A1-1 修订:pytest 环境默认禁用 BusResponder 后台线程
# 灵克 cli 默认 = 1(生产开),pytest 通过 conftest 设 = 0 防卡死
# 用户主动 export LINGCLAUDE_BUS_LISTENER=1 可强制开(覆盖 conftest)
os.environ["LINGCLAUDE_BUS_LISTENER"] = "0"

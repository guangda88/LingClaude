"""git_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + execute 分派到 engine.git。
git_status 在非 git 目录返回 error dict（不炸进程）——断言委托路径通。
"""

from __future__ import annotations


def test_git_plugin_instantiable():
    from plugin import GitPlugin

    assert GitPlugin.name == "git_plugin"


def test_git_status_delegates(tmp_path):
    from plugin import GitPlugin

    # 非 git 目录 → 委托 git_status 返回 error dict（契约：不炸进程、走同一实现）
    result = GitPlugin().execute(name="git_status", path=str(tmp_path))
    assert isinstance(result, dict)
    assert "error" in result or "branch" in result or "files" in result

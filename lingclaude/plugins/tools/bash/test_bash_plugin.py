"""bash_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + execute 委托 BashExecutor。
不测 BashExecutor 本体（那是主干实现，有 tests/test_bash*.py 覆盖）。
"""

from __future__ import annotations


def test_bash_plugin_instantiable():
    from plugin import BashPlugin

    assert BashPlugin.name == "bash_plugin"


def test_bash_plugin_delegates_to_executor():
    from plugin import BashPlugin

    result = BashPlugin().execute(name="bash", command="echo lingyuan_gate", timeout=10)
    # BashPlugin 委托 BashExecutor.run → 返回 BashResult（dataclass，非 dict）
    assert result.exit_code == 0
    assert "lingyuan_gate" in result.stdout

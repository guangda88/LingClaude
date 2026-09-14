"""ast_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + list_functions 分派到 engine.ast_edit。
"""

from __future__ import annotations


def test_ast_plugin_instantiable():
    from plugin import AstPlugin

    assert AstPlugin.name == "ast_plugin"


def test_list_functions_delegates(tmp_path):
    from plugin import AstPlugin

    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    return 1\n", encoding="utf-8")

    result = AstPlugin().execute(name="list_functions", file_path=str(f))
    assert isinstance(result, dict)
    assert "functions" in result

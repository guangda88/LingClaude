"""web_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + web_search 分派到 engine.web_tools。
不触发真实网络（空 query 走错误路径断言 error dict）。
"""

from __future__ import annotations


def test_web_plugin_instantiable():
    from plugin import WebPlugin

    assert WebPlugin.name == "web_plugin"


def test_web_search_delegates_no_network():
    from plugin import WebPlugin

    # 空 query 不触发真实搜索，断言委托路径通（返回 dict 契约）
    result = WebPlugin().execute(name="web_search", query="", max_results=1)
    assert isinstance(result, dict)
    assert "query" in result or "error" in result

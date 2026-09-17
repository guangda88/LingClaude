"""web_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + web_search 分派到 engine.web_tools。
不触发真实网络（空 query 走错误路径断言 error dict）。
"""

from __future__ import annotations


def test_web_plugin_instantiable():
    from plugin import WebPlugin

    assert WebPlugin.name == "web_plugin"


def test_web_search_delegates_no_network():
    import os
    from plugin import WebPlugin

    # 2026-09-17 修复：空 query 实际会触发真实搜索（searxng 超时 ~30s 才降级
    # duckduckgo），测试名 "no_network" 是谎言——实测 30.4s 墙钟全耗在此。
    # 改走 NOT_CONFIGURED fast-fail 路径：非法 backend 在 search 入口即拒，
    # 零网络、毫秒级返回，同时仍验证「插件分派→WebSearcher→dict 契约」链路。
    old = os.environ.get("LINGCLAUDE_SEARCH_BACKEND")
    os.environ["LINGCLAUDE_SEARCH_BACKEND"] = "__test_not_configured__"
    try:
        result = WebPlugin().execute(name="web_search", query="anything", max_results=1)
    finally:
        if old is None:
            os.environ.pop("LINGCLAUDE_SEARCH_BACKEND", None)
        else:
            os.environ["LINGCLAUDE_SEARCH_BACKEND"] = old
    assert isinstance(result, dict)
    # NOT_CONFIGURED fast-fail：web_tools 返回的 Result.fail 含 code 与 error；
    # 插件侧经 getattr(result,"error",...) 转 dict 时只保留 error 文本
    # （"Unknown web search backend: ...（允许: auto / searxng / duckduckgo）"）。
    assert "error" in result
    assert "web search backend" in result["error"]

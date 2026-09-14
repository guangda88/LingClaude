"""web 工具组插件（P1: plugins/tools 载体第三批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 不复制主干逻辑：execute 委托 engine/web_tools.WebFetcher / WebSearcher
  （与 tool_handlers/web_tools.py 共用同一实现）。
- 插件提供 web_fetch / web_search 两种能力，按 name 分派。
"""

from __future__ import annotations

from typing import Any


class WebPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "web_plugin"

    def execute(self, name: str = "web_search", **kwargs: Any) -> Any:
        """按工具名分派到 engine.web_tools 对应类。

        - web_fetch → WebFetcher(timeout).fetch(url)
        - web_search → WebSearcher().search(query, max_results)
        """
        from lingclaude.engine.web_tools import WebFetcher, WebSearcher

        if name == "web_fetch":
            url = kwargs.pop("url", "")
            timeout = kwargs.pop("timeout", 30)
            result = WebFetcher(timeout=timeout).fetch(url)
            if getattr(result, "is_error", False):
                return {"error": getattr(result, "error", "unknown fetch error")}
            return {"url": url, "content": result.data}

        query = kwargs.pop("query", "")
        max_results = kwargs.pop("max_results", 5)
        result = WebSearcher().search(query, max_results=max_results)
        if getattr(result, "is_error", False):
            return {"error": getattr(result, "error", "unknown search error")}
        return {"query": query, "results": result.data}

"""Web 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

WebToolsMixin: web_fetch / web_search（依赖 engine.web_tools 模块）。
"""

from __future__ import annotations

from typing import Any


class WebToolsMixin:
    """web_fetch / web_search 工具 handler。"""

    def _web_fetch_handler(
        self,
        url: str,
        timeout: int = 30,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from lingclaude.engine.web_tools import WebFetcher
        fetcher = WebFetcher(timeout=timeout)
        result = fetcher.fetch(url)
        if result.is_error:
            return {"error": result.error}
        return {"url": url, "content": result.data}

    def _web_search_handler(
        self,
        query: str,
        max_results: int = 5,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from lingclaude.engine.web_tools import WebSearcher
        searcher = WebSearcher()
        result = searcher.search(query, max_results=max_results)
        if result.is_error:
            return {"error": result.error}
        return {"query": query, "results": result.data}

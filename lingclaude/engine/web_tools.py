from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


@dataclass
class WebFetchResult:
    url: str
    content: str
    status_code: int
    content_type: str = ""


class WebFetcher:
    def __init__(self, timeout: int = 30, max_size: int = 5 * 1024 * 1024) -> None:
        self._timeout = timeout
        self._max_size = max_size

    def fetch(self, url: str) -> Result[str]:
        if not url.startswith(("http://", "https://")):
            return Result.fail(f"Invalid URL scheme: {url}", code="INVALID_URL")

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "lingclaude/0.2 WebFetcher",
                "Accept": "text/html,text/plain,application/json,*/*;q=0.1",
            })
            # 2026-09-13（灵安审计 P0-④）：走 HTTP(S) 代理（若配置）——
            # 此前 urllib 直连，抓 github 等外网时通时断（宿主代理如 9527/
            # clash 已配但 lingclaude 未用）。若环境无代理则直连（原行为）。
            proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"https": proxy, "http": proxy})
                )
                resp = opener.open(req, timeout=self._timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=self._timeout)
            with resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read(self._max_size + 1)
                if len(raw) > self._max_size:
                    return Result.fail(f"Response too large (>{self._max_size} bytes)", code="TOO_LARGE")

                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()

                try:
                    text = raw.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    text = raw.decode("utf-8", errors="replace")

                if "application/json" in content_type:
                    try:
                        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass

                # NanoJev 契约消费层（消费点④ 安全守门，2026-09-22）：
                # 注入筛查——只提醒不拦截不改写。命中嫌疑时把独立提醒块追加到
                # 内容尾部（内容原文不动，模型可区分"数据"与"提醒"）；fail-open
                # （筛查故障不影响 fetch 结果本身）。
                try:
                    from lingclaude.engine.injection_screen import screen_injection
                    verdict = screen_injection(text)
                    if verdict.suspicious and verdict.alert_text:
                        text = text + "\n\n" + verdict.alert_text
                except Exception:  # noqa: BLE001 — 筛查故障 fail-open（内容原样返回）
                    logger.debug("injection screen failed (fail-open)", exc_info=True)

                return Result.ok(text)

        except urllib.error.HTTPError as e:
            return Result.fail(f"HTTP {e.code}: {e.reason}", code="HTTP_ERROR")
        except urllib.error.URLError as e:
            return Result.fail(f"URL error: {e.reason}", code="URL_ERROR")
        except TimeoutError:
            return Result.fail(f"Request timed out after {self._timeout}s", code="TIMEOUT")
        except Exception as e:
            return Result.fail(f"Fetch failed: {e}", code="FETCH_ERROR")


class WebSearcher:
    """T0-6: web 搜索 — 后端链 searxng（本地实例，真搜索）→ duckduckgo（Instant Answer 兜底）。

    backend 优先级：显式参数 > LINGCLAUDE_SEARCH_BACKEND 环境变量 > auto。
    auto = searxng 失败时降级 duckduckgo（不再是 NOT_CONFIGURED 死路）。
    """

    DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"

    def __init__(self, backend: str | None = None, searxng_url: str | None = None) -> None:
        self._backend = backend
        self._searxng_url = (searxng_url or os.environ.get("SEARXNG_URL") or self.DEFAULT_SEARXNG_URL).rstrip("/")

    def search(self, query: str, max_results: int = 5) -> Result[list[dict[str, str]]]:
        backend = (self._backend or os.environ.get("LINGCLAUDE_SEARCH_BACKEND") or "auto").lower()
        if backend not in ("auto", "searxng", "duckduckgo"):
            return Result.fail(
                f"Unknown web search backend: {backend}（允许: auto / searxng / duckduckgo）",
                code="NOT_CONFIGURED",
            )
        if backend in ("auto", "searxng"):
            res = self._search_searxng(query, max_results)
            if not res.is_error:
                return res
            if backend == "searxng":
                return res
            logger.warning("searxng 搜索失败(%s)，降级 duckduckgo", res.error)
        return self._search_duckduckgo(query, max_results)

    def _search_searxng(self, query: str, max_results: int) -> Result[list[dict[str, str]]]:
        try:
            url = (
                f"{self._searxng_url}/search?q={urllib.parse.quote(query)}"
                f"&format=json&language=zh-CN&safesearch=0"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "lingclaude/0.3"})
            # searxng 是本地实例（127.0.0.1），直连不走代理
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 — 固定本地 SearXNG URL
                data = json.loads(resp.read().decode("utf-8"))

            results: list[dict[str, str]] = []
            for item in (data.get("results") or [])[:max_results]:
                results.append({
                    "title": str(item.get("title", ""))[:200],
                    "url": str(item.get("url", "")),
                    "snippet": str(item.get("content", ""))[:500],
                })
            _screen_search_results(results)
            return Result.ok(results[:max_results])

        except Exception as e:
            return Result.fail(f"SearXNG search failed: {e}", code="SEARCH_ERROR")

    def _search_duckduckgo(self, query: str, max_results: int) -> Result[list[dict[str, str]]]:
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
            req = urllib.request.Request(url, headers={"User-Agent": "lingclaude/0.2"})
            # 2026-09-13（灵安审计 P0-④）：duckduckgo 是外网 API，走代理（若配置）
            proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"https": proxy, "http": proxy})
                )
                resp = opener.open(req, timeout=15)
            else:
                resp = urllib.request.urlopen(req, timeout=15)
            with resp:
                data = json.loads(resp.read().decode("utf-8"))

            results: list[dict[str, str]] = []
            for item in data.get("RelatedTopics", [])[:max_results]:
                if isinstance(item, dict) and "Text" in item:
                    results.append({
                        "title": item.get("Text", "")[:200],
                        "url": item.get("FirstURL", ""),
                        "snippet": item.get("Text", ""),
                    })
            if data.get("AbstractText"):
                results.insert(0, {
                    "title": data.get("Heading", query),
                    "url": data.get("AbstractURL", ""),
                    "snippet": data["AbstractText"],
                })
            _screen_search_results(results)
            return Result.ok(results[:max_results])

        except Exception as e:
            return Result.fail(f"Search failed: {e}", code="SEARCH_ERROR")


def _screen_search_results(results: list[dict[str, str]]) -> None:
    """NanoJev 消费点④：搜索结果的 snippet 注入筛查（只提醒不拦截不改写）。

    命中嫌疑的条目挂 injection_alert 字段（提醒文本），snippet 原文不动。
    纯本地先验（0 模型调用主路径），fail-open（筛查异常不影响结果本身）。
    """
    try:
        from lingclaude.engine.injection_screen import screen_injection
    except Exception:  # noqa: BLE001 — 筛查模块不可用则跳过（不反噬搜索）
        return
    for r in results:
        snippet = r.get("snippet", "") or ""
        try:
            v = screen_injection(snippet)
            if v.suspicious and v.alert_text:
                r["injection_alert"] = v.alert_text
        except Exception:  # noqa: BLE001
            continue

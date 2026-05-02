from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

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
                "User-Agent": "LingClaude/0.2 WebFetcher",
                "Accept": "text/html,text/plain,application/json,*/*;q=0.1",
            })
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
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
    def __init__(self, backend: str | None = None) -> None:
        self._backend = backend

    def search(self, query: str, max_results: int = 5) -> Result[list[dict[str, str]]]:
        if self._backend == "duckduckgo":
            return self._search_duckduckgo(query, max_results)
        return Result.fail(
            "Web search not configured. Set web_search.backend in config (e.g., 'duckduckgo').",
            code="NOT_CONFIGURED",
        )

    def _search_duckduckgo(self, query: str, max_results: int) -> Result[list[dict[str, str]]]:
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
            req = urllib.request.Request(url, headers={"User-Agent": "LingClaude/0.2"})
            with urllib.request.urlopen(req, timeout=15) as resp:
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
            return Result.ok(results[:max_results])

        except Exception as e:
            return Result.fail(f"Search failed: {e}", code="SEARCH_ERROR")

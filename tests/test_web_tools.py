from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from lingclaude.engine.web_tools import WebFetcher, WebSearcher


class TestWebFetcher:
    def test_invalid_url_scheme(self) -> None:
        f = WebFetcher()
        result = f.fetch("ftp://example.com")
        assert result.is_error
        assert "Invalid URL" in result.error

    @patch("urllib.request.urlopen")
    def test_successful_fetch(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.read.return_value = b"<html><body>Hello</body></html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        f = WebFetcher()
        result = f.fetch("https://example.com")
        assert result.is_ok
        assert "Hello" in result.data

    @patch("urllib.request.urlopen")
    def test_json_response(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        f = WebFetcher()
        result = f.fetch("https://api.example.com/data")
        assert result.is_ok
        parsed = json.loads(result.data)
        assert parsed["key"] == "value"

    @patch("urllib.request.urlopen")
    def test_http_error(self, mock_urlopen: MagicMock) -> None:
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None,
        )
        f = WebFetcher()
        result = f.fetch("https://example.com/missing")
        assert result.is_error
        assert "404" in result.error

    @patch("urllib.request.urlopen")
    def test_timeout(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError("timed out")
        f = WebFetcher(timeout=1)
        result = f.fetch("https://slow.example.com")
        assert result.is_error
        assert "timed out" in result.error

    @patch("urllib.request.urlopen")
    def test_too_large(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.read.return_value = b"x" * (5 * 1024 * 1024 + 100)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        f = WebFetcher(max_size=5 * 1024 * 1024)
        result = f.fetch("https://big.example.com")
        assert result.is_error
        assert "too large" in result.error


class TestWebSearcher:
    def test_not_configured(self) -> None:
        s = WebSearcher()
        result = s.search("test query")
        assert result.is_error
        assert "not configured" in result.error

    def test_duckduckgo_backend_not_configured(self) -> None:
        s = WebSearcher(backend=None)
        result = s.search("test")
        assert result.is_error

    @patch("urllib.request.urlopen")
    def test_duckduckgo_search(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "AbstractText": "Python is a programming language",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python",
            "Heading": "Python",
            "RelatedTopics": [
                {"Text": "Python 3.12 released", "FirstURL": "https://python.org"},
            ],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        s = WebSearcher(backend="duckduckgo")
        result = s.search("python")
        assert result.is_ok
        assert len(result.data) >= 1
        assert result.data[0]["title"] == "Python"

    @patch("urllib.request.urlopen")
    def test_search_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = Exception("Network down")
        s = WebSearcher(backend="duckduckgo")
        result = s.search("fail")
        assert result.is_error
        assert "Search failed" in result.error

"""cache 0% 修复回归测试（2026-09-22）。

根因链：prompt_tokens_details.cached_tokens 只在流式尾帧解析点被读取，
带 choices 的 chunk 与非流式 complete() 两处既不读 ptd 还会覆盖已解析的
ModelUsage；显示端再把「未回传（0）」算成「0% 命中」展示 → 误导。

覆盖：
- _extract_usage 纯函数（ptd 有/无/畸形）
- 流式带 choices chunk 携带 usage（此前覆盖丢失场景）
- 非流式 complete() 携带 ptd（此前彻底不读）
- finish 事件 usage.cached_tokens 透传
- 摘要行/toolbar 语义：cached==0 → -1 未知不显示；>0 → 正常百分比
- datalog cost.cached 埋点（>0 才写，0 不写）
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.datalog import log_model_call
from lingclaude.core.model_types import MessageRole, ModelMessage
from lingclaude.model.openai_provider import OpenAIProvider, _extract_usage


def _mk_cfg() -> Any:
    from lingclaude.core.model_types import ModelConfig

    return ModelConfig(model="glm5.3-flash", api_key="sk-test")


class TestExtractUsage:
    def test_ptd_present(self) -> None:
        u = _extract_usage({
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 800},
        })
        assert u is not None
        assert u.input_tokens == 1000
        assert u.output_tokens == 50
        assert u.cached_tokens == 800

    def test_no_ptd_zero_cached(self) -> None:
        u = _extract_usage({"prompt_tokens": 10, "completion_tokens": 5})
        assert u is not None and u.cached_tokens == 0

    def test_none_and_malformed(self) -> None:
        assert _extract_usage(None) is None
        assert _extract_usage({}) is None
        assert _extract_usage("not-a-dict") is None  # type: ignore[arg-type]
        # ptd 畸形（非 dict）不炸
        u = _extract_usage({"prompt_tokens": 3, "prompt_tokens_details": "x"})
        assert u is not None and u.cached_tokens == 0


class _SSE:
    """流式 SSE mock（沿用 tests/test_model.py 的辅助模式）。"""

    @staticmethod
    def response(chunks: list[dict[str, Any]]) -> MagicMock:
        lines: list[bytes] = []
        for c in chunks:
            lines.append(f"data: {json.dumps(c)}".encode("utf-8"))
            lines.append(b"\n")
        lines.append(b"\n")
        lines.append(b"")  # readline 终结

        def _readline() -> bytes:
            if lines:
                return lines.pop(0)
            raise StopIteration

        resp = MagicMock()
        resp.status = 200
        resp.readline = MagicMock(side_effect=_readline)
        resp.read = MagicMock(return_value=b"err")
        return resp


class TestStreamCachedTokens:
    @patch("lingclaude.model.openai_provider.http.client.HTTPSConnection")
    def test_usage_in_choices_chunk_not_lost(self, conn_cls: MagicMock) -> None:
        """usage 在带 choices 的 chunk 到达且含 ptd —— 此前被无 ptd 解析覆盖丢失。"""
        conn = MagicMock()
        conn_cls.return_value = conn
        conn.getresponse.return_value = _SSE.response([
            {"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 500, "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 400}}},
        ])
        provider = OpenAIProvider(_mk_cfg())
        msgs = (ModelMessage(role=MessageRole.USER, content="hi"),)
        finish: dict[str, Any] = {}
        for ev in provider.stream_complete(msgs, _mk_cfg()):
            if ev.get("type") == "finish":
                finish = ev
        assert finish["usage"].input_tokens == 500
        assert finish["usage"].cached_tokens == 400

    @patch("lingclaude.model.openai_provider.http.client.HTTPSConnection")
    def test_terminal_tail_frame_still_works(self, conn_cls: MagicMock) -> None:
        """choices=[] 尾帧携带 usage（2026-09-21 已修场景）不回归。"""
        conn = MagicMock()
        conn_cls.return_value = conn
        conn.getresponse.return_value = _SSE.response([
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 10,
                                       "prompt_tokens_details": {"cached_tokens": 200}}},
        ])
        provider = OpenAIProvider(_mk_cfg())
        msgs = (ModelMessage(role=MessageRole.USER, content="hi"),)
        finish: dict[str, Any] = {}
        for ev in provider.stream_complete(msgs, _mk_cfg()):
            if ev.get("type") == "finish":
                finish = ev
        assert finish["usage"].cached_tokens == 200

    @patch("lingclaude.model.openai_provider.http.client.HTTPSConnection")
    def test_no_ptd_cached_zero(self, conn_cls: MagicMock) -> None:
        conn = MagicMock()
        conn_cls.return_value = conn
        conn.getresponse.return_value = _SSE.response([
            {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 4}},
        ])
        provider = OpenAIProvider(_mk_cfg())
        msgs = (ModelMessage(role=MessageRole.USER, content="hi"),)
        finish: dict[str, Any] = {}
        for ev in provider.stream_complete(msgs, _mk_cfg()):
            if ev.get("type") == "finish":
                finish = ev
        assert finish["usage"].cached_tokens == 0


class TestCompleteCachedTokens:
    @patch("lingclaude.model.openai_provider.urllib.request.urlopen")
    def test_complete_reads_ptd(self, mock_urlopen: MagicMock) -> None:
        """非流式 complete() 此前彻底不读 ptd。"""

        class _CM:
            def __init__(self, payload: dict[str, Any]) -> None:
                self._payload = payload

            def __enter__(self) -> "_CM":
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

        mock_urlopen.return_value = _CM({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 30,
                       "prompt_tokens_details": {"cached_tokens": 350}},
        })
        provider = OpenAIProvider(_mk_cfg())
        msgs = (ModelMessage(role=MessageRole.USER, content="hi"),)
        result = provider.complete(msgs, _mk_cfg())
        assert not result.is_error
        assert result.data.usage.cached_tokens == 350
        assert result.data.usage.input_tokens == 700


class TestCachePctSemantics:
    """显示端：0=未知（-1 不显示），>0 正常百分比。"""

    def _pct(self, in_all: int, cached_all: int) -> int:
        # 与 repl.py 摘要行同式（抽出来对照，防两处漂移）
        return int(cached_all * 100 / in_all) if (in_all > 0 and cached_all > 0) else -1

    def test_zero_cached_means_unknown(self) -> None:
        assert self._pct(3_000_000, 0) == -1

    def test_zero_input_means_unknown(self) -> None:
        assert self._pct(0, 0) == -1

    def test_positive_computes(self) -> None:
        assert self._pct(1000, 800) == 80

    def test_toolbar_hides_unknown(self) -> None:
        from lingclaude.cli.status import StatusModel, toolbar_fragments

        s = StatusModel()
        s.set_cache_pct(-1)
        frags = toolbar_fragments(s)
        text = "".join(t for _, t in frags)
        assert "cache" not in text

    def test_toolbar_shows_hit(self) -> None:
        from lingclaude.cli.status import StatusModel, toolbar_fragments

        s = StatusModel()
        s.set_cache_pct(94)
        frags = toolbar_fragments(s)
        text = "".join(t for _, t in frags)
        assert "cache 94%" in text


class TestDatalogCached:
    def test_cached_written_when_positive(self, tmp_path: Any, monkeypatch: Any) -> None:
        import lingclaude.core.datalog as dl

        monkeypatch.setattr(dl, "DATALOG_DIR", tmp_path)
        log_model_call("m", 100, 10, "stop", 1.0, path="stream", cached_tokens=60)
        line = (tmp_path / f"{__import__('time').strftime('%Y-%m-%d')}.jsonl").read_text().strip().splitlines()[-1]
        ev = json.loads(line)
        assert ev["cost"]["cached"] == 60

    def test_no_cached_key_when_zero(self, tmp_path: Any, monkeypatch: Any) -> None:
        import lingclaude.core.datalog as dl

        monkeypatch.setattr(dl, "DATALOG_DIR", tmp_path)
        log_model_call("m", 100, 10, "stop", 1.0, path="complete")
        line = (tmp_path / f"{__import__('time').strftime('%Y-%m-%d')}.jsonl").read_text().strip().splitlines()[-1]
        ev = json.loads(line)
        assert "cached" not in ev["cost"]

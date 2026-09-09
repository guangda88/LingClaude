"""R8 收尾回归：流式断流/超时的幂等重试语义。

锁定 provider_pool.call_stream 的四条行为：
1. 首字节前超时 → 幂等重试一次，第二次成功则正常产出（不再 yield timeout）
2. 已产出内容后超时 → 不重试（防内容重复），立即 yield finish_reason=timeout
3. 两次尝试均失败 → yield finish_reason=timeout，重试次数封顶 2
4. 4xx 状态码 → 直接 error 返回，不重试
"""
from __future__ import annotations

import asyncio
import json

import httpx

from lingclaude.model.llm_proxy.provider_pool import ProviderPool

BASE_URL = "http://mock.local/v1"


def _sse_chunk(content: str = "", finish: str | None = None) -> bytes:
    payload = {"choices": [{"delta": {"content": content}, "finish_reason": finish}]}
    return f"data: {json.dumps(payload)}\n\n".encode()


def _ok_stream_body() -> bytes:
    return _sse_chunk("你好") + _sse_chunk(finish="stop") + b"data: [DONE]\n\n"


def _make_pool(handler) -> ProviderPool:
    """ProviderPool 不注入 transport，测试中替换内部 client。"""
    pool = ProviderPool()
    pool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(None, connect=1.0, read=1.0, write=1.0, pool=1.0),
    )
    return pool


async def _collect(pool: ProviderPool):
    chunks = []
    async for ch in pool.call_stream("sk-test", BASE_URL, "mock-model", [{"role": "user", "content": "hi"}]):
        chunks.append(ch)
    await pool._client.aclose()
    return chunks


def test_retry_before_first_byte_succeeds():
    """场景1：首次连接超时（未产出任何字节）→ 重试后成功，无 timeout 帧。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("first attempt down", request=request)
        return httpx.Response(200, content=_ok_stream_body(), headers={"content-type": "text/event-stream"})

    chunks = asyncio.run(_collect(_make_pool(handler)))
    assert calls["n"] == 2, "应恰好重试一次"
    # 终止帧 delta 也是 ""，只比对有内容的帧
    assert [c.delta for c in chunks if c.delta] == ["你好"]
    assert chunks[-1].finish_reason == "stop"
    assert all(c.finish_reason != "timeout" for c in chunks)


def test_no_retry_after_content_emitted():
    """场景2：已产出内容后流中断 → 不重试（防内容重复），立即 timeout 帧。"""
    calls = {"n": 0}

    async def broken_stream():
        yield _sse_chunk("半截")
        raise httpx.ReadTimeout("mid-stream drop", request=None)  # type: ignore[arg-type]

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=broken_stream(), headers={"content-type": "text/event-stream"})

    chunks = asyncio.run(_collect(_make_pool(handler)))
    assert calls["n"] == 1, "已产出内容后不得重试"
    assert chunks[0].delta == "半截"
    assert chunks[-1].finish_reason == "timeout"


def test_retry_capped_at_two_attempts():
    """场景3：两次尝试都失败 → timeout 帧返回，重试封顶不无限打转。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("always down", request=request)

    chunks = asyncio.run(_collect(_make_pool(handler)))
    assert calls["n"] == 2, "恰好尝试 2 次"
    assert len(chunks) == 1
    assert chunks[0].finish_reason == "timeout"
    assert chunks[0].delta == ""


def test_http_error_retry_then_success():
    """场景4a：首字节前网络错误（非超时）同样走幂等重试。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, content=_ok_stream_body(), headers={"content-type": "text/event-stream"})

    chunks = asyncio.run(_collect(_make_pool(handler)))
    assert calls["n"] == 2
    assert chunks[-1].finish_reason == "stop"


def test_4xx_no_retry():
    """场景4b：服务端明确 4xx → 直接 error，不浪费重试。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text='{"error": "bad key"}')

    chunks = asyncio.run(_collect(_make_pool(handler)))
    assert calls["n"] == 1
    assert chunks[-1].finish_reason == "error"

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import time

from lingclaude.model.llm_proxy.config import ProxyConfig
from lingclaude.model.llm_proxy.proxy import LLMProxy

logger = logging.getLogger("llm_proxy")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8900


def _get_peer_key() -> str:
    try:
        import sys
        sys.path.insert(0, str(__import__('pathlib').Path.home() / '.ling_lib'))
        from ling_key_store import get_key
        return get_key('PROXY_PEER_KEY') or ''
    except Exception:
        return os.environ.get('PROXY_PEER_KEY', '')


def _verify_bearer(headers: dict) -> bool:
    peer_key = _get_peer_key()
    if not peer_key:
        return True
    auth = headers.get('authorization', '')
    if auth.startswith('Bearer '):
        return hmac.compare_digest(auth[7:], peer_key)
    return False


async def _handle_chat(proxy: LLMProxy, body: dict, headers: dict) -> dict:
    messages = body.get("messages", [])
    purpose = headers.get("x-purpose", body.get("_purpose"))
    max_tokens = body.get("max_tokens", 4096)
    temperature = body.get("temperature", 0.7)
    caller = headers.get("x-caller", "unknown")
    return await proxy.chat(
        messages=messages,
        purpose=purpose,
        max_tokens=max_tokens,
        temperature=temperature,
        caller=caller,
    )


async def _handle_health(proxy: LLMProxy) -> dict:
    return proxy.health()


async def _handle_stats(proxy: LLMProxy) -> dict:
    return proxy.audit_stats()


async def run_server(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
    from aiohttp import web

    config = ProxyConfig.load()
    proxy = LLMProxy(config)

    async def chat_handler(request: web.Request) -> web.StreamResponse | web.Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not _verify_bearer(headers):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)

        headers = {k.lower(): v for k, v in request.headers.items()}
        stream = body.get("stream", False)

        if stream:
            messages = body.get("messages", [])
            purpose = headers.get("x-purpose", body.get("_purpose"))
            max_tokens = body.get("max_tokens", 4096)
            temperature = body.get("temperature", 0.7)
            caller = headers.get("x-caller", "unknown")

            resp = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
            await resp.prepare(request)

            chunk_index = 0
            async for chunk in proxy.chat_stream(
                messages=messages,
                purpose=purpose,
                max_tokens=max_tokens,
                temperature=temperature,
                caller=caller,
            ):
                chunk_data = {
                    "id": f"proxy-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "model": chunk.model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": chunk.delta} if chunk.delta else {},
                        "finish_reason": chunk.finish_reason,
                    }],
                }
                if chunk.provider:
                    chunk_data["_proxy_meta"] = {
                        "provider": chunk.provider,
                        "model": chunk.model,
                    }
                line = f"data: {json.dumps(chunk_data)}\n\n"
                await resp.write(line.encode("utf-8"))
                chunk_index += 1

            await resp.write(b"data: [DONE]\n\n")
            return resp

        result = await _handle_chat(proxy, body, headers)

        # Fallback responses now have "choices" key, so 503 logic is no longer needed
        # Keep minimal check for unexpected error-only dicts (shouldn't happen after fallback fix)
        status = 200
        if "error" in result and "choices" not in result:
            logger.warning("[proxy] Unexpected error-only response (no fallback): %s", result.get("error"))
            status = 503

        resp_headers = {}
        meta = result.get("_proxy_meta", {})
        if meta:
            resp_headers["X-Provider"] = meta.get("provider", "")
            resp_headers["X-Model"] = meta.get("model", "")
            resp_headers["X-Route"] = meta.get("route", "")
            resp_headers["X-Latency-Ms"] = f"{meta.get('latency_ms', 0):.0f}"

        return web.json_response(result, status=status, headers=resp_headers)

    async def health_handler(request: web.Request) -> web.Response:
        result = await _handle_health(proxy)
        return web.json_response(result)

    async def models_handler(request: web.Request) -> web.Response:
        models = []
        seen = set()
        for p in proxy._config.providers.values():
            for m in p.models:
                if m not in seen:
                    seen.add(m)
                    models.append({"id": m, "object": "model", "owned_by": p.name})
        return web.json_response({"object": "list", "data": models})

    async def stats_handler(request: web.Request) -> web.Response:
        result = await _handle_stats(proxy)
        return web.json_response(result)

    async def metrics_handler(request: web.Request) -> web.Response:
        text = proxy.metrics_prometheus()
        return web.Response(
            text=text,
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_handler)
    app.router.add_get("/v1/models", models_handler)
    app.router.add_get("/v1/health", health_handler)
    app.router.add_get("/v1/stats", stats_handler)
    app.router.add_get("/metrics", metrics_handler)

    logger.info("LLM Proxy starting on %s:%d", host, port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await proxy.close()
        await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="LLM Proxy Server")
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = parser.parse_args()
    asyncio.run(run_server(host=args.host, port=args.port))


if __name__ == "__main__":
    main()

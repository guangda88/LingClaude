"""灵克 MCP HTTP Proxy — StreamableHTTP transport on port 9531.

将现有的 stdio MCP server 改为 HTTP 模式运行，避免多 agent 并发时的进程爆炸。

架构：
  Agent 会话 ──HTTP──→ 单例代理进程(:9531) ──内部──→ FastMCP(26工具)
"""

from __future__ import annotations

import os
import signal
import sys
import time

DEFAULT_PORT = 9531


def create_app():
    """创建带 /health 端点的 Starlette ASGI app。"""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from .server import mcp

    mcp_app = mcp.streamable_http_app()
    start_time = time.time()

    async def health(request):
        return JSONResponse({
            "status": "ok",
            "server": "lingclaude",
            "uptime": round(time.time() - start_time, 1),
        })

    return Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.router.lifespan_context if hasattr(mcp_app.router, "lifespan_context") else None,
    )


def main():
    """HTTP proxy entry point."""
    port = int(os.environ.get("LINGCLAUDE_PROXY_PORT", DEFAULT_PORT))
    host = os.environ.get("LINGCLAUDE_PROXY_HOST", "127.0.0.1")

    import uvicorn

    app = create_app()

    def _shutdown(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"灵克 MCP HTTP Proxy starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

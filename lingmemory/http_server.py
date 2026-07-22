# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 MCP HTTP Server — StreamableHTTP transport on port 9530

架构：
  成员 Agent ──HTTP──→ :9530 ──内部──→ FastMCP(19工具)
                                        ↓
                                   Adapter → Core → SQLite WAL
"""

from __future__ import annotations

import os
import signal
import sys
import time

DEFAULT_PORT = 9530


def create_app():
    """创建带 /health 端点的 Starlette ASGI app。"""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from lingmemory.mcp_server import mcp

    mcp_app = mcp.streamable_http_app()
    start_time = time.time()

    async def health(request):
        return JSONResponse({
            "status": "ok",
            "server": "lingmemory",
            "port": DEFAULT_PORT,
            "uptime": round(time.time() - start_time, 1),
        })

    async def l10_claim_pending(request: Request):
        """L10-A 声明审计事件写入点 (9530 路由, 异步 fire-and-forget).

        接收 a_l10_claim_audit.py 插件发来的声明审计事件,
        写入今日 Datalog 文件, 供 L10-B verify_claim 查询.
        """
        try:
            body = await request.json()
            # 写入 Datalog (import inside handler 避免模块加载顺序问题)
            import sys
            _LM_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "lingminopt")
            if _LM_PATH not in sys.path:
                sys.path.insert(0, _LM_PATH)
            from lingyuan.datalog import write_event
            data = {
                "caller": body.get("caller", "unknown"),
                "route": body.get("route", ""),
                "model": body.get("model", ""),
                "claim_count": body.get("claim_count", 0),
                "claims": body.get("claims", []),
                "duration_ms": body.get("duration_ms", 0),
                "request_id": body.get("request_id", ""),
            }
            write_event(
                event_type="l10_claim.pending",
                data=data,
                session_id=body.get("request_id", ""),
                status="success",
            )
            return JSONResponse({"status": "ok"}, status_code=200)
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/api/l10_claim.pending", l10_claim_pending, methods=["POST"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.router.lifespan_context if hasattr(mcp_app.router, "lifespan_context") else None,
    )


def main():
    """HTTP server entry point."""
    port = int(os.environ.get("LINGMEMORY_PORT", DEFAULT_PORT))
    host = os.environ.get("LINGMEMORY_HOST", "127.0.0.1")

    import uvicorn

    app = create_app()

    def _shutdown(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"灵忆 MCP HTTP Server starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

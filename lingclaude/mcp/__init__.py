"""lingclaude MCP server 包。

R-logfix 约束: 本包必须保持「完全惰性」——server.py 一旦被 import，
其模块级 get_mcp() 装饰器链会构造 FastMCP，而 FastMCP.__init__ 无条件
调 configure_logging() -> basicConfig() 往 root logger 塞 RichHandler。
宿主 CLI 进程的导包链（lingclaude.engine -> bash_lingxi ->
lingxi_client -> lingclaude.mcp）一旦触发，root 即被第三方 handler
污染，包内所有日志（session_store "Checkpoint saved"、N5b 守卫告警等）
被劫持成 rich 时间戳格式插进 TUI 输出流形成乱入行。

因此任何符号（mcp / main）都只在被真实访问时才加载 server.py；
独立 MCP server 进程入口（python -m lingclaude.mcp.server）不受影响。
"""


def __getattr__(name: str):
    if name == "mcp":
        from .server import get_mcp

        return get_mcp()
    if name == "main":
        from .server import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

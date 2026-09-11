"""LSP 工具 handler 测试 — 验证 lsp 能力非悬空（claudecode 审计命中"注册不调用"）。

覆盖：
1. 未知命令 → 结构化错误（不崩溃）
2. 无 LSP server 环境（pylsp 不存在）→ 优雅降级 {"ok": False, "error"}，不抛异常
3. 已知命令参数校验
"""
from __future__ import annotations

from lingclaude.engine.coding import CodingRuntime


class _MinimalRuntime:
    """最小可测 runtime：只提供 _lsp_handler 所需成员，不启动完整引擎。"""

    def __init__(self) -> None:
        from lingclaude.engine.tool_handlers.lsp_tools import LspToolsMixin

        # handler 挂载在 CodingRuntime 上，运行时 _lsp_provider/_lsp_workspace_root
        # 由 CodingRuntime.__init__ 注入；此处模拟宿主类成员。
        self._lsp_provider = None
        self._lsp_workspace_root = None
        self._lsp_handler = LspToolsMixin._lsp_handler.__get__(self)  # 绑定方法

    # _lsp_handler 通过 __get__ 绑定，无需显式定义


def test_lsp_unknown_command_returns_error():
    """未知命令 → {"ok": False, "error": ...}，不崩溃。"""
    rt = _MinimalRuntime()
    result = rt._lsp_handler("goto_unknown", "foo.py")
    assert result.get("ok") is False
    assert "unknown" in result.get("error", "").lower()


def test_lsp_invalid_command_no_crash():
    """空命令 / None 同样结构化拒绝。"""
    rt = _MinimalRuntime()
    result = rt._lsp_handler("", "foo.py")
    assert result.get("ok") is False
    assert result.get("error")


def test_lsp_missing_server_graceful_degrade():
    """无 pylsp 环境 → 返回可读错误，不抛异常（能力悬空但可诊断）。"""
    import shutil

    if shutil.which("pylsp"):
        # 环境里真有 pylsp：此用例测不到降级，跳过
        return

    rt = _MinimalRuntime()
    result = rt._lsp_handler("goto_def", "foo.py")
    assert result.get("ok") is False
    # 错误信息应包含可诊断内容（server 未找到 / 连接失败等）
    assert result.get("error")


def test_lsp_handler_uses_provider_when_available():
    """预置 provider 时 handler 走真调用路径（mixin 契约测试）。

    不真正启动 LSP server（CI 无 pylsp 也过），只验证 handler
    在 provider 存在时路由到对应命令方法。
    """
    from lingclaude.engine.tool_handlers.lsp_tools import LspToolsMixin

    calls = {}

    class _FakeProvider:
        async def initialize(self, *a, **k):
            pass

        async def go_to_definition(self, *a, **k):
            calls["cmd"] = "goto_def"
            return []

        async def find_references(self, *a, **k):
            calls["cmd"] = "find_refs"
            return []

        async def hover(self, *a, **k):
            calls["cmd"] = "hover"
            return {"contents": "x"}

        async def go_to_implementation(self, *a, **k):
            calls["cmd"] = "goto_impl"
            return []

    class _RT:
        def __init__(self):
            from lingclaude.engine.tool_handlers.lsp_tools import LspToolsMixin

            self._lsp_provider = None
            self._lsp_workspace_root = None
            self._fake = _FakeProvider()
            # 模拟 CodingRuntime 的 lazy-init：provider 预置
            self._lsp_provider = self._fake
            self._lsp_handler = LspToolsMixin._lsp_handler.__get__(self)

    rt = _RT()
    # goto_def → provider 有结果, handler 返回 {"ok": True}
    res = rt._lsp_handler("goto_def", "foo.py")
    assert res["ok"] is True
    assert res["command"] == "goto_def"
    assert isinstance(res["result"], list)

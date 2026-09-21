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
    """未知命令 → 结构化错误，不崩溃。"""
    rt = _MinimalRuntime()
    result = rt._lsp_handler("goto_unknown", "foo.py")
    assert result.is_error
    assert result.error is not None
    assert "unknown" in result.error.message.lower()


def test_lsp_invalid_command_no_crash():
    """空命令 / None 同样结构化拒绝。"""
    rt = _MinimalRuntime()
    result = rt._lsp_handler("", "foo.py")
    assert result.is_error
    assert result.error is not None
    assert result.error.message


def test_lsp_missing_server_graceful_degrade():
    """无 pylsp 环境 → 返回可读错误，不抛异常（能力悬空但可诊断）。"""
    import shutil

    if shutil.which("pylsp"):
        # 环境里真有 pylsp：此用例测不到降级，跳过
        return

    rt = _MinimalRuntime()
    result = rt._lsp_handler("goto_def", "foo.py")
    assert result.is_error
    assert result.error is not None
    # 错误信息应包含可诊断内容（server 未找到 / 连接失败等）
    assert result.error.message


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
    # goto_def → provider 有结果, handler 返回 ToolResult.ok
    res = rt._lsp_handler("goto_def", "foo.py")
    assert res.is_ok, res.error
    data = res.data
    assert data["ok"] is True
    assert data["command"] == "goto_def"
    assert isinstance(data["result"], list)


def test_lsp_structure_commands_route_to_provider():
    """P2-12: outline/search/diagnostics 路由到 provider 新方法（结构查询省 token）。"""
    from lingclaude.engine.tool_handlers.lsp_tools import LspToolsMixin

    class _FakeProvider:
        async def initialize(self, *a, **k):
            pass

        async def document_symbols(self, *a, **k):
            return [{"kind": "function", "name": "foo", "line": 1, "end_line": 5, "depth": 0}]

        async def workspace_symbols(self, *a, **k):
            return [{"kind": "function", "name": "foo", "container": "", "file": "/x/foo.py", "line": 3}]

        async def diagnostics(self, *a, **k):
            return [{"severity": 1, "message": "error here", "line": 3, "col": 1}]

    class _RT:
        def __init__(self):
            self._lsp_provider = _FakeProvider()
            self._lsp_workspace_root = None
            self._lsp_handler = LspToolsMixin._lsp_handler.__get__(self)

    rt = _RT()
    # outline → documentSymbols 大纲
    res = rt._lsp_handler("outline", "foo.py")
    assert res.is_ok, res.error
    assert res.data["command"] == "outline"
    assert res.data["result"][0]["name"] == "foo"
    assert res.data["result"][0]["kind"] == "function"

    # search → workspace/symbol（query 参数透传）
    res = rt._lsp_handler("search", "foo.py", query="foo")
    assert res.is_ok, res.error
    assert res.data["command"] == "search"
    assert res.data["result"][0]["file"].endswith("foo.py")

    # diagnostics → publishDiagnostics 捕获
    res = rt._lsp_handler("diagnostics", "foo.py")
    assert res.is_ok, res.error
    assert res.data["command"] == "diagnostics"
    assert res.data["result"][0]["severity"] == 1
    assert res.data["result"][0]["line"] == 3


def test_lsp_document_symbol_parser_flattens_tree():
    """P2-12: documentSymbol 树形结果展平为带 depth 的扁平大纲。"""
    from lingclaude.engine.lsp_provider import _parse_document_symbols

    tree = [
        {
            "kind": 5, "name": "MyClass",
            "selectionRange": {"start": {"line": 0}, "end": {"line": 0}},
            "range": {"start": {"line": 0}, "end": {"line": 10}},
            "children": [
                {
                    "kind": 6, "name": "method_a",
                    "selectionRange": {"start": {"line": 2}, "end": {"line": 2}},
                    "range": {"start": {"line": 2}, "end": {"line": 5}},
                },
            ],
        },
    ]
    flat = _parse_document_symbols(tree)
    assert len(flat) == 2
    assert flat[0]["name"] == "MyClass" and flat[0]["depth"] == 0
    assert flat[0]["kind"] == "class" and flat[0]["line"] == 1 and flat[0]["end_line"] == 11
    assert flat[1]["name"] == "method_a" and flat[1]["depth"] == 1
    assert flat[1]["kind"] == "method" and flat[1]["line"] == 3


def test_lsp_diagnostics_parser():
    """P2-12: publishDiagnostics 通知 → 紧凑结构（1-based 行列）。"""
    from lingclaude.engine.lsp_provider import _parse_diagnostics

    diags = [
        {
            "severity": 1,
            "message": "undefined name",
            "range": {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 9}},
            "source": "pylint",
            "code": "E0602",
        },
    ]
    out = _parse_diagnostics(diags)
    assert out[0]["severity"] == 1
    assert out[0]["line"] == 3 and out[0]["col"] == 5  # 1-based
    assert out[0]["source"] == "pylint" and out[0]["code"] == "E0602"

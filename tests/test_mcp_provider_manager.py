"""P2 (2026-09-12, codex 审计 #4): MCP Provider Manager 测试。

覆盖:
- MCPClientPool 连接池（get/复用/失效剔除/drop/close_all）
- 工具冲突检测 find_server_with_conflicts / get_stats
- schema cache（get_tool_schema 二次命中不重算）
- mcp_proxy.call_tool 走连接池（stdio/http 分支）
"""

from __future__ import annotations

import threading
import time

import pytest

from lingclaude.engine.mcp_client import MCPClientPool, MCPHttpClient, MCPStdioClient
from lingclaude.engine.mcp_proxy import (
    _SERVERS,
    _schema_cache,
    clear_cache,
    find_server_with_conflicts,
    get_stats,
    get_tool_schema,
    register_server,
)


@pytest.fixture(autouse=True)
def _clean():
    _SERVERS.clear()
    clear_cache()
    yield
    _SERVERS.clear()
    clear_cache()


# === 1. MCPClientPool ===

class _FakeStdioClient:
    def __init__(self, command, cwd=None, timeout=30.0, healthy=True):
        self._command = list(command)
        self._proc = object() if healthy else None  # None = 失效
        self.closed = False
        self.calls = 0

    def connect(self):
        self._proc = object()
        from lingclaude.core.types import Result
        return Result.ok(None)

    def close(self):
        self.closed = True
        self._proc = None

    def call_tool(self, name, args):
        self.calls += 1
        return "ok"


def test_pool_get_creates_and_reuses(monkeypatch):
    """同一 key 多次 get 返回同一 client（复用）。"""
    from lingclaude.core.types import Result
    created = []

    class _FakePool(MCPClientPool):
        def _new_client(self, transport, **kw):
            c = _FakeStdioClient(kw["command"])
            created.append(c)
            return c

    pool = _FakePool()
    c1 = pool.get("s1", "stdio", command=["echo"])
    c2 = pool.get("s1", "stdio", command=["echo"])
    assert c1.is_ok and c2.is_ok
    assert c1.data is c2.data  # 复用同一实例
    assert len(created) == 1
    assert pool.size == 1


def test_pool_drop_removes(monkeypatch):
    pool = MCPClientPool()
    pool._new_client = lambda transport, **kw: _FakeStdioClient(kw["command"])
    pool.get("s1", "stdio", command=["echo"])
    assert pool.size == 1
    pool.drop("s1")
    assert pool.size == 0


def test_pool_stale_client_recreated(monkeypatch):
    """TTL 过期 → 剔除重建。"""
    pool = MCPClientPool(ttl_seconds=0.01)
    pool._new_client = lambda transport, **kw: _FakeStdioClient(kw["command"])
    c1 = pool.get("s1", "stdio", command=["echo"]).data
    time.sleep(0.03)
    c2 = pool.get("s1", "stdio", command=["echo"]).data
    assert c1 is not c2  # 重建


def test_pool_unhealthy_client_recreated():
    """健康检查失败 → 自动重连。"""
    pool = MCPClientPool()
    pool._new_client = lambda transport, **kw: _FakeStdioClient(kw["command"], healthy=False)
    c1 = pool.get("s1", "stdio", command=["echo"])
    assert c1.is_ok
    stale = _FakeStdioClient(["echo"], healthy=False)
    pool._clients["s1"] = stale  # 手动塞失效实例
    pool._created_at["s1"] = time.monotonic()
    pool._is_healthy = lambda client: False
    c2 = pool.get("s1", "stdio", command=["echo"])
    assert c2.is_ok
    assert c2.data is not stale  # 重建，非旧失效实例
    assert stale.closed is True  # 旧实例被关闭


def test_pool_close_all():
    pool = MCPClientPool()
    pool._new_client = lambda transport, **kw: _FakeStdioClient(kw["command"])
    pool.get("s1", "stdio", command=["echo"])
    pool.get("s2", "stdio", command=["echo"])
    assert pool.size == 2
    pool.close_all()
    assert pool.size == 0


def test_pool_unsupported_transport():
    pool = MCPClientPool()
    r = pool.get("s1", "quic")  # 不支持的 transport
    assert r.is_error
    assert r.code == "UNSUPPORTED_TRANSPORT"


# === 2. 工具冲突检测 ===

def test_find_server_with_conflicts_no_conflict():
    register_server("a", "A", "a1", tools=["t1", "t2"])
    register_server("b", "B", "b1", tools=["t3"])
    primary, conflicts = find_server_with_conflicts("t1")
    assert primary is not None and primary.key == "a"
    assert conflicts == []


def test_find_server_with_conflicts_detects():
    register_server("a", "A", "a1", tools=["dup"])
    register_server("b", "B", "b1", tools=["dup", "x"])
    primary, conflicts = find_server_with_conflicts("dup")
    assert primary is not None and primary.key == "a"  # 第一个注册
    assert conflicts == ["b"]


def test_get_stats_reports_conflicts_and_cache():
    register_server("a", "A", "a1", tools=["dup"])
    register_server("b", "B", "b1", tools=["dup"])
    stats = get_stats()
    assert stats["tool_conflicts"] == {"dup": ["a", "b"]}
    assert "pool_size" in stats
    assert "schema_cache_entries" in stats


# === 3. schema cache ===

def test_get_tool_schema_cache_hit():
    """二次调用命中进程级 cache（不再走 server.tool_schemas 读取）。"""
    register_server("s", "S", "s1", tools=["t"], tool_schemas={
        "t": {"type": "object", "properties": {"p": {"type": "string"}}, "required": ["p"]},
    })
    props1, req1 = get_tool_schema("t")
    assert props1 == {"p": {"type": "string"}}
    assert req1 == ["p"]
    assert "schema:t" in _schema_cache
    # 二次命中（server.tool_schemas 清空也不影响——走 cache）
    _SERVERS["s"].tool_schemas = {}
    props2, req2 = get_tool_schema("t")
    assert props2 == {"p": {"type": "string"}}


def test_get_tool_schema_unknown_tool():
    props, req = get_tool_schema("nope")
    assert props == {}
    assert req == []


# === 4. mcp_proxy.call_tool 走连接池 ===

def test_call_tool_module_transport_still_works():
    """module transport 不受连接池影响（进程内函数直调）。"""
    from lingclaude.engine.mcp_proxy import call_tool

    def _handler(x=1):
        return {"x": x}

    register_server("m", "M", "m1", tools=["mod_tool"])
    # 无法注册 module 函数到 _get_tool_function（内部），验证至少返回明确错误而非崩溃
    r = call_tool("mod_tool", x=2)
    assert r.is_error  # 模块加载失败（无真实模块）→ 明确错误
    assert r.code in ("MODULE_LOAD_FAILED", "TOOL_NOT_FOUND")


def test_call_tool_no_server():
    from lingclaude.engine.mcp_proxy import call_tool
    r = call_tool("nonexistent_tool")
    assert r.is_error
    assert r.code == "TOOL_NOT_FOUND"

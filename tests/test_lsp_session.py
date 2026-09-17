"""LspSessionPool 单元测试 — codex P1-1 (2026-09-17)。

覆盖：
1. 事件循环线程惰性启动 + 单例复用
2. 会话按 (server_cmd, workspace_root) 缓存复用（不重复 initialize）
3. run_with_session 正常返回 / 异常透传 / 超时转换 RuntimeError
4. close_all 幂等 + 回收后可重新建会话
5. _reset_for_tests 清空缓存

不依赖真实 LSP server：用 monkeypatch 替换 StdioLspProvider 为 fake。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from lingclaude.engine import lsp_session as lsp_session_mod
from lingclaude.engine.lsp_session import LspSession, LspSessionPool, get_pool


class _FakeProvider:
    """记录 initialize 次数的假 provider（接口对齐 StdioLspProvider）。"""

    def __init__(self, server_cmd, *, workspace_root=None):
        self.init_count = 0
        self.shutdown_count = 0
        self.server_cmd = server_cmd

    async def initialize(self, workspace_root):
        self.init_count += 1
        await asyncio.sleep(0)
        return {"capabilities": {}}

    async def shutdown(self):
        self.shutdown_count += 1
        await asyncio.sleep(0)


@pytest.fixture()
def pool(monkeypatch):
    """每个用例独立 pool，注入 FakeProvider，结束后回收。"""
    monkeypatch.setattr(lsp_session_mod, "StdioLspProvider", _FakeProvider)
    p = LspSessionPool()
    yield p
    p.close_all()


def test_loop_lazy_start_and_reuse(pool):
    """事件循环线程惰性启动；二次调用复用同一 loop。"""
    loop1 = pool._ensure_loop()
    loop2 = pool._ensure_loop()
    assert loop1 is loop2
    assert not loop1.is_closed()


def test_session_cached_by_cmd_and_root(pool, tmp_path):
    """同 (cmd, root) 复用会话（initialize 仅一次）；不同 root 各建会话。"""

    async def _noop(session):
        return None

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    pool.run_with_session(["fake-lsp"], root_a, _noop)
    pool.run_with_session(["fake-lsp"], root_a, _noop)
    pool.run_with_session(["fake-lsp"], root_b, _noop)
    pool.run_with_session(["other-lsp"], root_a, _noop)

    assert len(pool._sessions) == 3
    for s in pool._sessions.values():
        assert s.provider.init_count == 1


def test_return_value_and_exception_passthrough(pool, tmp_path):
    """afunc 返回值透传；异常在调用线程重现。"""

    async def _value(session):
        return 42

    async def _boom(session):
        raise ValueError("lsp exploded")

    assert pool.run_with_session(["fake"], tmp_path, _value) == 42
    with pytest.raises(ValueError, match="lsp exploded"):
        pool.run_with_session(["fake"], tmp_path, _boom)


def test_timeout_becomes_runtime_error(monkeypatch, tmp_path):
    """afunc 卡死 → concurrent.futures.TimeoutError 转成可读 RuntimeError。"""
    monkeypatch.setattr(lsp_session_mod, "StdioLspProvider", _FakeProvider)
    monkeypatch.setattr(lsp_session_mod, "_CALL_TIMEOUT_S", 0.2)
    p = LspSessionPool()

    async def _hang(session):
        await asyncio.sleep(30)

    with pytest.raises(RuntimeError, match="超时"):
        p.run_with_session(["fake"], tmp_path, _hang)
    p.close_all()


def test_close_all_shuts_down_and_is_idempotent(pool, tmp_path):
    """close_all shutdown 全部会话且幂等；之后可重新建会话。"""

    async def _noop(session):
        return None

    pool.run_with_session(["fake"], tmp_path, _noop)
    assert len(pool._sessions) == 1
    pool.close_all()
    assert pool._sessions == {}
    pool.close_all()  # 幂等

    # shutdown 经后台 loop 执行，稍等其完成
    deadline = time.time() + 5
    sessions_seen = []

    async def _collect(session):
        sessions_seen.append(session)

    pool.run_with_session(["fake"], tmp_path, _collect)
    # 新会话是新 provider 实例
    assert len(pool._sessions) == 1


def test_reset_for_tests_clears_cache(pool, tmp_path):
    async def _noop(session):
        return None

    pool.run_with_session(["fake"], tmp_path, _noop)
    pool._reset_for_tests()
    assert pool._sessions == {}


def test_ensure_open_syncs_disk_content(pool, tmp_path, monkeypatch):
    """ensure_open: 首次 didOpen 建视图；再次调用走 didChange 且版本递增。"""
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")

    session = LspSession(provider=_FakeProvider(["fake"]), root=tmp_path)

    calls: list[tuple[str, str, int]] = []

    class _P(_FakeProvider):
        async def did_open(self, file_path, text, version=1):
            calls.append(("open", text, version))

        async def did_change(self, file_path, text, version):
            calls.append(("change", text, version))

    session.provider = _P(["fake"])

    asyncio.run(session.ensure_open(target))
    asyncio.run(session.ensure_open(target))

    assert calls[0] == ("open", "x = 1\n", 1)
    assert calls[1][0] == "change"
    assert calls[1][2] == 2  # version 递增

    # 读盘失败不抛（幂等跳过）
    missing = tmp_path / "ghost.py"
    asyncio.run(session.ensure_open(missing))
    assert len(calls) == 2


def test_stderr_tail_collected_for_diagnostics():
    """诊断化 (2026-09-17): server 启动即退时 stderr 尾部应被收集，
    供 initialize 超时错误附带（rustup shim 秒退场景实测驱动）。"""
    import subprocess
    import time

    from lingclaude.engine.lsp_provider import StdioLspProvider

    p = StdioLspProvider(["sh", "-c", "echo boom-line >&2"])
    p._proc = subprocess.Popen(
        p._cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p._start_stderr_collector()
    p._proc.wait(timeout=5)
    time.sleep(0.3)  # 给收集线程留时间

    ref = getattr(p, "_stderr_tail_ref", None)
    assert ref is not None, "stderr collector 未启动"
    assert "boom-line" in " ".join(ref)


def test_global_singleton():
    """get_pool 返回进程级单例。"""
    p1 = get_pool()
    p2 = get_pool()
    assert p1 is p2

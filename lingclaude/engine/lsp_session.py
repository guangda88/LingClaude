"""LSP 常驻会话池 — codex P1-1 落地 (2026-09-17)。

修复三个实证缺陷（suggestions.md codex P1-1 + 本轮精读复检）：
1. 旧 lsp_tools 每次调用 ``asyncio.run(run())``：一次性事件循环随 run()
   结束即销毁，StdioLspProvider._read_loop 协程随之死亡 → 后续
   ``_call`` 挂起的 future 永无响应 → 第二次调用起必然 30s 超时。
2. 每次调用前后从不 shutdown LSP server 子进程 → 每次调用泄漏一个进程。
3. CodingRuntime 单 ``_lsp_provider`` 槽位：混合语言项目第二次调用
   复用错误的 server。

方案：进程级单例「后台事件循环线程 + 按 (server_cmd, workspace_root)
缓存的 LspSession」。所有 LSP 调用经 run_coroutine_threadsafe 投递到
常驻 loop，reader task 因此持续存活；didOpen/didChange 全量文本同步
保证 server 端文档视图与磁盘一致；atexit + close_all() 双保险回收。
"""

from __future__ import annotations

import atexit
import asyncio
import concurrent.futures
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from lingclaude.engine.lsp_provider import StdioLspProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 单次调用总超时：需覆盖「server 冷启动 + 首次索引」；pylsp 冷启可达 20s+
_CALL_TIMEOUT_S = 90.0
# close_all 回收超时
_SHUTDOWN_TIMEOUT_S = 15.0


@dataclass
class LspSession:
    """一个常驻 LSP server 会话：provider + 已打开文档版本表。"""

    provider: StdioLspProvider
    root: Path
    # uri -> version（didOpen/didChange 全量同步）
    opened_versions: dict[str, int] = field(default_factory=dict)

    async def ensure_open(self, file_path: str | Path) -> None:
        """确保 server 端文档视图与磁盘一致（幂等；读盘失败则跳过同步）。"""
        try:
            path = Path(file_path).resolve()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("lsp ensure_open 读盘失败 %s: %s", file_path, e)
            return
        uri = _path_to_uri(path)
        if uri in self.opened_versions:
            self.opened_versions[uri] += 1
            await self.provider.did_change(str(path), text, self.opened_versions[uri])
        else:
            self.opened_versions[uri] = 1
            await self.provider.did_open(str(path), text, 1)


class LspSessionPool:
    """进程级 LSP 会话池：后台常驻事件循环 + 按 (cmd, root) 缓存会话。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[tuple[str, ...], str], LspSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()

    # ----- 后台事件循环线程 -----

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """惰性启动常驻事件循环线程（进程内唯一）。"""
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            self._loop_ready.clear()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._loop_ready.set()
                loop.run_forever()

            t = threading.Thread(target=_run, daemon=True, name="lsp-session-loop")
            t.start()
            self._loop_ready.wait(timeout=10)
            if self._loop is None:
                raise RuntimeError("LSP 会话池事件循环启动失败")
            return self._loop

    # ----- 对外同步接口 -----

    def run_with_session(
        self,
        server_cmd: list[str],
        workspace_root: Path,
        afunc: Callable[[LspSession], Awaitable[T]],
    ) -> T:
        """在池化会话上执行异步任务并同步等待结果。

        afunc 接收已初始化的 LspSession（didOpen 由调用方按需触发）。
        会话按 (server_cmd, workspace_root) 缓存复用 — 修复进程泄漏与
        二次调用超时两个缺陷的关键：provider 与其 reader task 常驻于
        后台 loop，不再随每次调用的临时事件循环一起销毁。
        """
        loop = self._ensure_loop()
        key = (tuple(server_cmd), str(Path(workspace_root).resolve()))

        async def _with_session() -> T:
            session = self._sessions.get(key)
            if session is None:
                provider = StdioLspProvider(list(key[0]), workspace_root=Path(key[1]))
                await provider.initialize(Path(key[1]))
                session = LspSession(provider=provider, root=Path(key[1]))
                self._sessions[key] = session
            return await afunc(session)

        fut = asyncio.run_coroutine_threadsafe(_with_session(), loop)
        try:
            return fut.result(timeout=_CALL_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise RuntimeError(
                f"LSP 会话池调用超时（{_CALL_TIMEOUT_S}s）: server={key[0][0]}"
            ) from None

    def close_all(self) -> None:
        """回收全部会话（shutdown server 子进程）。进程退出/atexit 调用。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        loop = self._loop
        if not sessions or loop is None or loop.is_closed():
            return

        async def _shutdown_all() -> None:
            for s in sessions:
                try:
                    await s.provider.shutdown()
                except Exception as e:  # noqa: BLE001 — 回收尽力而为
                    logger.warning("lsp session shutdown 异常: %s", e)

        fut = asyncio.run_coroutine_threadsafe(_shutdown_all(), loop)
        try:
            fut.result(timeout=_SHUTDOWN_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            logger.warning("lsp close_all 超时/异常: %s", e)

    # ----- 测试隔离 -----

    def _reset_for_tests(self) -> None:
        """清空缓存（不关进程）— 仅测试用。"""
        with self._lock:
            self._sessions.clear()


# ----- 模块级单例 -----

_POOL: LspSessionPool | None = None
_POOL_LOCK = threading.Lock()


def get_pool() -> LspSessionPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = LspSessionPool()
            atexit.register(_POOL.close_all)
        return _POOL


def _path_to_uri(path: str | Path) -> str:
    """与 lsp_provider._path_to_uri 同构（避免 import 私有符号）。

    2026-09-17 对齐 percent-encoding 修复（原裸拼对含空格/# 路径生成非法 URI）。
    """
    from urllib.parse import quote

    return f"file://{quote(str(Path(path).resolve()))}"

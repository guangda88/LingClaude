"""P1-1: LSP Service Definition + stdio JSON-RPC Provider.

对标 AtomCode kernel/codeintel/lsp/ 四方法契约：
  goToDefinition / findReferences / hover / goToImplementation

subprocess 模式：启动 LSP server（pylsp / rust-analyzer）后通过 JSON-RPC 通信。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import quote, unquote, urlparse

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types (from LSP spec + AtomCode codeintel)
# ---------------------------------------------------------------------------


@dataclass
class Position:
    line: int  # 0-based
    character: int  # 0-based


@dataclass
class Range:
    start: Position
    end: Position


@dataclass
class LocationLink:
    uri: str
    range: Range
    origin_selection_range: Range | None = None


@dataclass
class ReferenceContext:
    include_declaration: bool = True


@dataclass
class Hover:
    contents: str  # Markdown
    range: Range | None = None


# ---------------------------------------------------------------------------
# LspProvider Protocol (Service Definition)
# ---------------------------------------------------------------------------


class LspProvider(Protocol):
    """可替换的 LSP 后端实现（DSH lsp-stdio provider 对位）。"""

    async def initialize(self, workspace_root: Path) -> dict[str, Any]:
        """初始化 LSP session，返回 server capabilities。"""

    async def shutdown(self) -> None:
        """优雅关闭 LSP session。"""

    async def go_to_definition(
        self, file_path: str, line: int, character: int
    ) -> list[LocationLink]:
        ...

    async def find_references(
        self, file_path: str, line: int, character: int, *, include_declaration: bool = True
    ) -> list[LocationLink]:
        ...

    async def hover(self, file_path: str, line: int, character: int) -> Hover | None:
        ...

    async def go_to_implementation(
        self, file_path: str, line: int, character: int
    ) -> list[LocationLink]:
        ...


# ---------------------------------------------------------------------------
# StdioLspProvider: stdio JSON-RPC 实现
# ---------------------------------------------------------------------------


@dataclass
class _PendingCall:
    future: asyncio.Future
    id: int


class StdioLspProvider:
    """AtomCode LSP stdio JSON-RPC Provider（subprocess 模式）。

    启动 LSP server（pylsp / rust-analyzer）后通过 stdin/stdout JSON-RPC 通信。
    对应 DSH lsp-stdio provider。
    """

    _METHODS = (
        "textDocument/definition",
        "textDocument/references",
        "textDocument/hover",
        "textDocument/implementation",
    )

    def __init__(
        self,
        server_cmd: list[str],
        *,
        workspace_root: Path | None = None,
    ):
        self._cmd = server_cmd
        self._root = workspace_root or Path.cwd()
        self._proc: subprocess.Popen | None = None
        self._counter = 0
        self._pending: dict[int, _PendingCall] = {}
        self._reader_task: asyncio.Task | None = None
        self._initialized = False
        self._caps: dict[str, Any] = {}
        # 2026-09-17 (诊断化): server stderr 尾部环形缓冲 — rustup shim
        # 等启动即退的场景，initialize 超时若不含 stderr 会不可诊断
        # （实测 "Unknown binary 'rust-analyzer'" 曾被完全吞掉）。
        # 2026-09-17 (统一): 删除死字段 _stderr_tail（实际使用的是
        # _start_stderr_collector 动态创建的 _stderr_tail_ref）。
        self._stderr_thread: threading.Thread | None = None

    # ----- LspProvider interface -----

    async def initialize(self, workspace_root: Path) -> dict[str, Any]:
        if self._proc is not None:
            return self._caps

        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workspace_root),
        )
        self._root = workspace_root
        self._start_stderr_collector()

        reader = asyncio.create_task(self._read_loop())

        # 2026-09-17 修复: rootUri 此前传裸路径，违反 LSP spec（须 file:// URI），
        # 部分 server（如 rust-analyzer）会拒收或行为异常。
        try:
            resp = await self._call(
                "initialize",
                {
                    "processId": None,
                    "rootUri": _path_to_uri(workspace_root),
                    "rootPath": str(workspace_root),
                    "capabilities": {},
                },
            )
        except BaseException:
            # 2026-09-17 修复 (初始化失败泄漏): _call 失败（超时/进程秒退）时
            # reader task 尚未挂到 self._reader_task、无 shutdown 清理 →
            # 孤儿进程 + 孤儿 task + stderr 线程残留。此处就地回收。
            reader.cancel()
            proc = self._proc
            self._proc = None
            if proc is not None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            raise
        self._caps = resp.get("capabilities", {})
        self._initialized = True

        # send initialized notification (no response)
        # 2026-09-17 修复: 原 create_task fire-and-forget 在一次性事件循环里
        # 可能从未执行；改为确定性 await（常驻 loop 下两者等价，此处取严格序）。
        await self._notify("initialized", {})
        self._reader_task = reader

        return self._caps

    async def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            await self._call("shutdown", {})
            # 2026-09-17 修复: 原 create_task fire-and-forget 后立即 terminate,
            # exit notification 可能尚未写出; 改为确定性 await。
            await self._notify("exit", {})
        except Exception:
            pass
        finally:
            if self._reader_task:
                self._reader_task.cancel()
                self._reader_task = None
            proc, self._proc = self._proc, None
            self._initialized = False
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                # 2026-09-17 修复: 原 wait(timeout=5) 超时抛 TimeoutExpired →
                # self._proc=None 不执行 → 二次 shutdown 重复 terminate；
                # wait 也置 None 后兜底 kill，保证状态一致。
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            th = self._stderr_thread
            if th is not None and th.is_alive():
                th.join(timeout=1.0)

    async def go_to_definition(
        self, file_path: str, line: int, character: int
    ) -> list[LocationLink]:
        resp = await self._call(
            "textDocument/definition",
            {
                "textDocument": {"uri": _path_to_uri(file_path)},
                "position": {"line": line, "character": character},
            },
        )
        return _parse_locations(resp)

    async def find_references(
        self,
        file_path: str,
        line: int,
        character: int,
        *,
        include_declaration: bool = True,
    ) -> list[LocationLink]:
        resp = await self._call(
            "textDocument/references",
            {
                "textDocument": {"uri": _path_to_uri(file_path)},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return _parse_locations(resp)

    async def hover(self, file_path: str, line: int, character: int) -> Hover | None:
        resp = await self._call(
            "textDocument/hover",
            {
                "textDocument": {"uri": _path_to_uri(file_path)},
                "position": {"line": line, "character": character},
            },
        )
        contents = resp.get("contents", "")
        if not contents:
            return None
        if isinstance(contents, dict):
            contents = contents.get("value", "")
        rng = None
        if "range" in resp:
            rng = _parse_range(resp["range"])
        return Hover(contents=contents, range=rng)

    async def go_to_implementation(
        self, file_path: str, line: int, character: int
    ) -> list[LocationLink]:
        resp = await self._call(
            "textDocument/implementation",
            {
                "textDocument": {"uri": _path_to_uri(file_path)},
                "position": {"line": line, "character": character},
            },
        )
        return _parse_locations(resp)

    # ----- document sync (codex P1-1: LSP 常驻会话池配套, 2026-09-17) -----

    async def did_open(self, file_path: str, text: str, version: int = 1) -> None:
        """textDocument/didOpen 通知 — server 端建立文档视图。"""
        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _path_to_uri(file_path),
                    "languageId": _language_id(file_path),
                    "version": version,
                    "text": text,
                }
            },
        )

    async def did_change(self, file_path: str, text: str, version: int) -> None:
        """textDocument/didChange 全量同步 — 以磁盘内容为准刷新 server 视图。"""
        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": _path_to_uri(file_path), "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    # ----- internal -----

    # 2026-09-17 修复 (P0: LSP 帧格式违规): LSP 标准是 JSON-RPC over stdio
    # 的 Content-Length 帧协议（LSP spec: Base Protocol），此前发送裸 JSON+\n、
    # 按行解析——pylsp/pyright/rust-analyzer 等标准 server 无法解析请求，
    # initialize 都过不去。改为统一帧读写。
    def _write_frame(self, body: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("LSP provider not initialized")
        payload = body.encode("utf-8")
        self._proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
        self._proc.stdin.flush()

    def _read_frame(self) -> str | None:
        """阻塞读一帧; EOF 返回 None。须在 executor 中调用（阻塞 IO）。"""
        if self._proc is None or self._proc.stdout is None:
            return None
        headers: dict[str, str] = {}
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return None  # EOF
            if line in (b"\r\n", b"\n"):
                break  # 空行 = 头部结束
            key, _, val = line.decode("ascii", errors="replace").partition(":")
            headers[key.strip().lower()] = val.strip()
        length = headers.get("content-length")
        if length is None:
            return None  # 非法帧（无 Content-Length 头）
        body = self._proc.stdout.read(int(length))
        if not body:
            return None
        return body.decode("utf-8", errors="replace")

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("LSP provider not initialized")
        self._counter += 1
        msg_id = self._counter
        body = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        self._write_frame(body)

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = _PendingCall(future=future, id=msg_id)

        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            # 2026-09-17 (诊断化): 超时附带 server 存活状态与 stderr 尾部，
            # 让 "shim 秒退/组件未装/崩溃" 一眼可判。
            alive = self._proc is not None and self._proc.poll() is None
            ref = getattr(self, "_stderr_tail_ref", None)
            tail = " | ".join(list(ref)[-3:]).strip() if ref else ""
            hint = f"; server alive={alive}" + (f"; stderr: {tail}" if tail else "")
            raise RuntimeError(f"LSP call timed out: {method}{hint}")

    def _start_stderr_collector(self) -> None:
        """后台线程收集 server stderr 尾部（环形，最多 10 行）。"""
        import collections
        import threading as _t

        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        tail: collections.deque = collections.deque(maxlen=10)
        self._stderr_tail_ref = tail

        def _pump() -> None:
            try:
                for raw in iter(proc.stderr.readline, b""):
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        tail.append(line)
            except Exception:  # noqa: BLE001 — 诊断辅助，任何异常静默
                pass

        th = _t.Thread(target=_pump, daemon=True, name="lsp-stderr")
        th.start()
        self._stderr_thread = th

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._write_frame(body)

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        loop = asyncio.get_event_loop()
        while True:
            try:
                # 2026-09-17: Content-Length 帧读取（阻塞 IO 走 executor）。
                frame = await loop.run_in_executor(None, self._read_frame)
            except Exception:
                break
            if not frame:
                break
            try:
                msg = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                pending = self._pending.pop(msg["id"], None)
                if pending:
                    result = msg.get("result")
                    if "error" in msg:
                        pending.future.set_exception(RuntimeError(f"LSP error: {msg['error']}"))
                    else:
                        pending.future.set_result(result or {})

    # ----- context manager -----

    async def __aenter__(self) -> "StdioLspProvider":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path_to_uri(path: str | Path) -> str:
    # 2026-09-17 修复 (URI 编码): 此前裸拼 file://{path}，含空格/#/? 等保留字
    # 的路径生成非法 URI（LSP spec 要求 percent-encoding）。
    path = quote(str(Path(path).resolve()))
    return f"file://{path}"


def _uri_to_path(uri: str) -> str:
    # 2026-09-17 修复: 只剥前缀不解码 — server 回 %20 等编码路径时被当字面量,
    # definition/references 定位失败。urlparse + unquote 正规解码;
    # 非 file:// scheme 原样返回（防御）。
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    return unquote(parsed.path)


def _language_id(path: str | Path) -> str:
    """didOpen 的 languageId（按扩展名；server 对未知值普遍宽容）。"""
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".go": "golang",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
    }.get(suffix, "plaintext")


def _parse_range(rng: dict[str, Any]) -> Range:
    return Range(
        start=Position(line=rng["start"]["line"], character=rng["start"]["character"]),
        end=Position(line=rng["end"]["line"], character=rng["end"]["character"]),
    )


def _parse_locations(resp: dict[str, Any]) -> list[LocationLink]:
    result = resp.get("result") or []
    if not isinstance(result, list):
        result = [result] if result else []
    out = []
    for item in result:
        if item is None:
            continue
        if isinstance(item, str):
            # Some servers return URI string directly
            out.append(LocationLink(uri=item, range=Range(start=Position(0, 0), end=Position(0, 0))))
            continue
        uri = item.get("uri", item.get("targetUri", ""))
        rng = item.get("range", item.get("targetRange", {}))
        origin = item.get("originSelectionRange")
        out.append(
            LocationLink(
                uri=uri,
                range=_parse_range(rng) if rng else Range(start=Position(0, 0), end=Position(0, 0)),
                origin_selection_range=_parse_range(origin) if origin else None,
            )
        )
    return out

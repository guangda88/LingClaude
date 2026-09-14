"""LINGKERNEL_v1 task #2 — Tool execution pipeline (5 段 waterfall).

dsh reference: docs/tool-execution-pipeline.md
  tool/call (logged)
   → tools/pre-execute (hooks, permission, sandbox)
   → monotonic guards (deny or abstain, identity protected)
   → tools/execute (around-dispatch: timeout, retry, metrics)
   → tools/post-execute (accept, block, replace, add context)
   → finalizeContent (last content-only invariant)
   → tools/result (frozen authoritative outcome)

本模块提供 ToolPipeline 类, 实现 5 段流水线。
CodingRuntime.execute_tool 调用本模块 (替代原 inline 实现)。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from lingclaude.core.types import Result, ToolError, ToolErrorCode, ToolResult
from lingclaude.engine.tools import ToolDefinition, ToolRegistry


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# A3: 工具执行增量事件缓冲 — 供 webUI `/live` 轮询（tool_start/tool_result）
# 线程安全环形列表，容量固定（默认 200 条），防止无限增长。
# ---------------------------------------------------------------------------
_TOOL_EVENT_BUFFER: list[dict[str, Any]] = []
_TOOL_EVENT_LOCK = threading.Lock()
_TOOL_EVENT_MAX = 200


def record_tool_event(event: dict[str, Any]) -> None:
    """记录一条工具执行增量事件（tool_start / tool_result / state）。"""
    with _TOOL_EVENT_LOCK:
        _TOOL_EVENT_BUFFER.append(event)
        if len(_TOOL_EVENT_BUFFER) > _TOOL_EVENT_MAX:
            del _TOOL_EVENT_BUFFER[: len(_TOOL_EVENT_BUFFER) - _TOOL_EVENT_MAX]


def read_tool_events(since: int = 0) -> tuple[list[dict[str, Any]], int]:
    """读取 `since` 之后的新增量事件；返回 (events, 最新序号)。"""
    with _TOOL_EVENT_LOCK:
        events = [e for e in _TOOL_EVENT_BUFFER if e.get("seq", 0) > since]
        latest = _TOOL_EVENT_BUFFER[-1].get("seq", 0) if _TOOL_EVENT_BUFFER else 0
    return events, latest


@dataclass
class PipelineContext:
    """Tool execution 5 段间的共享上下文。

    每段可读 + 修改; 写后可见。
    """

    name: str
    args: dict[str, Any]
    raw_result: Any = None
    is_error: bool = False
    error_msg: str = ""
    aborted: bool = False
    abort_reason: str = ""
    metrics: dict[str, Any] = None

    def __post_init__(self):
        if self.metrics is None:
            self.metrics = {}


@dataclass
class GuardDecision:
    """dsh monotonic guards 输出。

    - decision: "allow" | "deny" | "abstain"
    - reason: str (deny 时必填; abstain 时可选)
    """

    decision: str  # allow / deny / abstain
    reason: str = ""


# 默认危险命令模式 (从原 coding.py:620 迁移)
DEFAULT_DANGEROUS_PATTERNS: tuple[str, ...] = (
    "rm -rf /",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod 777 /",
    ":(){:|:&};:",
)


class ToolPipeline:
    """5 段工具执行流水线。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        dangerous_patterns: tuple[str, ...] = DEFAULT_DANGEROUS_PATTERNS,
        write_scoped_tools: tuple[str, ...] = (),
        critical_tools: tuple[str, ...] = (),
        timeout_seconds: float = 30.0,
    ) -> None:
        self._registry = registry
        self._dangerous = dangerous_patterns
        self._write_scoped = set(write_scoped_tools)
        self._critical = set(critical_tools)
        self._timeout = timeout_seconds

        # 5 段 listeners (可扩展)
        self._pre_listeners: list[Callable[[PipelineContext], None]] = []
        self._guards: list[Callable[[ToolDefinition, PipelineContext], GuardDecision]] = []
        self._post_listeners: list[Callable[[PipelineContext], None]] = []
        # T0-7: 工具错误监听器 — (tool_name, error_msg)，接 ON_ERROR hook
        self._error_listeners: list[Callable[[str, str], None]] = []

    # ----- listeners 注册 (扩展点) -----

    def add_pre_listener(self, fn: Callable[[PipelineContext], None]) -> None:
        self._pre_listeners.append(fn)

    def add_guard(
        self, fn: Callable[[ToolDefinition, PipelineContext], GuardDecision]
    ) -> None:
        self._guards.append(fn)

    def add_post_listener(self, fn: Callable[[PipelineContext], None]) -> None:
        self._post_listeners.append(fn)

    def add_error_listener(self, fn: Callable[[str, str], None]) -> None:
        """T0-7: 注册工具错误监听器 (tool_name, error_msg)。"""
        self._error_listeners.append(fn)

    def _notify_error(self, name: str, error_msg: str) -> None:
        for fn in self._error_listeners:
            try:
                fn(name, error_msg)
            except Exception as e:  # noqa: BLE001 — 监听器异常不阻塞流水线
                logger.warning("error-listener raised: %s", e)

    # ----- 5 段执行 -----

    def _preflight(
        self,
        name: str,
        args: dict[str, Any],
        ctx: PipelineContext,
        permissions_blocks: Callable[[str], bool] | None = None,
        rate_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> dict[str, Any] | None:
        """纯权限预检（execute 与 check_permission 共用，维护点 2→1）。

        覆盖：permissions 拦截 / rate_check 限流 / 危险命令检查。
        返回 None = 通过；dict = 拒绝结果（error + error_code）。
        """
        if permissions_blocks is not None and permissions_blocks(name):
            ctx.aborted = True
            ctx.abort_reason = f"Tool blocked by permissions: {name}"
            return self._error(ctx.abort_reason, ToolErrorCode.PERMISSION_DENIED)
        if rate_check is not None:
            passed, err = rate_check()
            if not passed:
                ctx.aborted = True
                ctx.abort_reason = f"[rate-limit] {err}"
                return self._error(ctx.abort_reason, ToolErrorCode.RATE_LIMITED)

        # 危险命令检查 (从原 coding.py:618-624 迁移)
        if name in self._critical:
            cmd = args.get("command", "")
            for pat in self._dangerous:
                if pat in cmd:
                    ctx.aborted = True
                    ctx.abort_reason = f"[安全限制] 危险命令被阻止: 含有 '{pat}'"
                    return self._error(ctx.abort_reason, ToolErrorCode.DANGEROUS_COMMAND)
        return None

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        permissions_blocks: Callable[[str], bool] | None = None,
        rate_check: Callable[[], tuple[bool, str]] | None = None,
        pre_write_verify: Callable[[str, Any, Any], tuple[bool, str]] | None = None,
        post_write_verify: Callable[[str], tuple[bool, str]] | None = None,
        rollback_callback: Callable[[str, dict[str, Any]], str | None] | None = None,
    ) -> dict[str, Any]:
        """5 段流水线主入口。

        permissions_blocks: 名字→bool (True = blocked)
        rate_check: ()→ (passed, err_msg)
        pre_write_verify: (name, content, new_text)→ (passed, err_msg)
        post_write_verify: (file_path)→ (passed, err_msg)
        rollback_callback: (tool_name, args)→ 回滚结果（None=回滚成功，str=回滚失败原因）。
                          P1-2 (2026-09-12): 写工具 post-write 失败时自动 undo，
                          返回「已回滚」的结构化结果，避免残留脏文件。
        """
        ctx = PipelineContext(name=name, args=args)

        # === 1. pre-execute waterfall (hooks) ===
        denied = self._preflight(name, args, ctx, permissions_blocks, rate_check)
        if denied is not None:
            return denied

        # 写前 verify
        if name in self._write_scoped and pre_write_verify is not None:
            content = args.get("content") or args.get("new_text") or args.get("new_body")
            file_path = args.get("path") or args.get("file_path")
            passed, err = pre_write_verify(name, file_path, content)
            if not passed:
                ctx.aborted = True
                ctx.abort_reason = f"[verify-pre] {err}"
                return self._error(ctx.abort_reason, ToolErrorCode.VERIFY_PRE_FAILED)

        for fn in self._pre_listeners:
            try:
                fn(ctx)
                if ctx.aborted:
                    return self._error(ctx.abort_reason or "pre-listener aborted")
            except Exception as e:
                logger.warning("pre-listener raised: %s", e)

        # === 2. monotonic guards ===
        tool = self._registry.get(name)
        if not tool.is_ok:
            return self._error(f"Tool not found: {name}", ToolErrorCode.TOOL_NOT_FOUND)
        tool_def = tool.data

        for guard in self._guards:
            try:
                dec = guard(tool_def, ctx)
                if dec.decision == "deny":
                    ctx.aborted = True
                    ctx.abort_reason = f"[guard deny] {dec.reason}"
                    return self._error(ctx.abort_reason, ToolErrorCode.GUARD_DENIED)
                # abstain = pass through
            except Exception as e:
                # P0 安全对齐(2026-09-11, codex 审计): guard 异常此前 fail-open
                # (视为 abstain 放行), 对安全 guard 意味着"守卫崩了 = 放行"。
                # 改为 fail-closed: 守卫异常一律 deny, 除非守卫显式标记可降级。
                # 安全优先: 误拦可人工放行, 误放不可逆。
                logger.error("guard %r raised: %s (fail-closed: deny)", guard, e)
                ctx.aborted = True
                ctx.abort_reason = f"[guard exception-fail-closed] {getattr(guard, '__name__', repr(guard))} raised: {e}"
                return self._error(ctx.abort_reason, ToolErrorCode.GUARD_EXCEPTION)

        # === 3. tools/execute (around-dispatch: timeout, retry, metrics) ===
        ctx.metrics["start_ts"] = time.time()
        # A3: tool_start 增量事件（webUI /live 实时同步）
        record_tool_event({
            "seq": len(_TOOL_EVENT_BUFFER) + 1,
            "type": "tool_start",
            "name": name,
            "ts": time.time(),
        })
        try:
            res = self._dispatch_with_timeout(tool_def, args)
            if res.is_error:
                ctx.is_error = True
                ctx.error_msg = str(res.error)
                ctx.raw_result = None
            else:
                ctx.raw_result = res.data
        except Exception as e:
            ctx.is_error = True
            ctx.error_msg = f"Tool execution failed: {e}"
            logger.exception("dispatch failed for %s", name)
        ctx.metrics["end_ts"] = time.time()
        ctx.metrics["duration"] = ctx.metrics["end_ts"] - ctx.metrics["start_ts"]
        # A3: tool_result 增量事件（含成败 + 耗时）
        record_tool_event({
            "seq": len(_TOOL_EVENT_BUFFER) + 1,
            "type": "tool_result",
            "name": name,
            "success": not ctx.is_error,
            "duration_ms": int(ctx.metrics["duration"] * 1000),
            "ts": time.time(),
        })

        if ctx.is_error:
            # T0-7: 触发错误监听器（query_engine 接 ON_ERROR hook）
            self._notify_error(name, ctx.error_msg)
            return self._error(ctx.error_msg, ToolErrorCode.EXECUTION_ERROR)

        # === 4. post-execute waterfall ===
        for fn in self._post_listeners:
            try:
                fn(ctx)
                if ctx.aborted:
                    return self._error(ctx.abort_reason or "post-listener aborted")
            except Exception as e:
                logger.warning("post-listener raised: %s", e)

        # 写后 verify
        if name in self._write_scoped and post_write_verify is not None:
            file_path = args.get("path") or args.get("file_path")
            passed, err = post_write_verify(file_path)
            if not passed:
                ctx.aborted = True
                ctx.abort_reason = f"[verify-post] {err}"
                # P1-2 (2026-09-12): post-write 失败自动回滚 — 调用方传入
                # rollback_callback 时执行 undo，避免残留脏文件；回滚结果附在错误里。
                rollback_note = ""
                if rollback_callback is not None:
                    try:
                        rb = rollback_callback(name, args)
                        rollback_note = "已回滚" if rb is None else f"回滚失败: {rb}"
                    except Exception as e:  # noqa: BLE001 — 回滚异常不掩盖主错误
                        rollback_note = f"回滚异常: {e}"
                    ctx.abort_reason += f" | {rollback_note}"
                return self._error(ctx.abort_reason, ToolErrorCode.VERIFY_POST_FAILED)

        # === P0-2: output size pruning (tool result spill) ===
        # 防止大输出（如 large grep / long ls）爆上下文
        # 超过 threshold 的结果写临时文件，只返回 locator
        ctx.raw_result = self._prune_output(name, ctx.raw_result)

        # === 5. finalizeContent (last content-only invariant) ===
        finalized = self._registry.finalize_result(name, args, ctx.raw_result)
        if finalized is not None:
            ctx.raw_result = finalized

        # dsh 规则: finalize 返回值 = replacement content, 直接作为最终 result
        # (ContentBlock[] 或 dict 都直接返回)
        if isinstance(ctx.raw_result, (dict, list)):
            return ctx.raw_result
        return {"result": ctx.raw_result}

    # ----- 内部 -----

    def _dispatch_with_timeout(
        self, tool_def: ToolDefinition, args: dict[str, Any]
    ) -> Result[Any]:
        """around-dispatch: 真超时执行 handler（T0-7）。

        worker 线程跑 handler，主线程 join(timeout)。
        超时后放弃等待、返回 TIMEOUT（daemon 线程无法强杀，结果丢弃——
        已知局限，与 bash.py 自身超时互为兜底）。
        """
        if tool_def.handler is None and getattr(tool_def, "handler_name", None) is None:
            return Result.fail(f"Tool has no handler: {tool_def.name}", code="NO_HANDLER")
        # P1 解耦: 解析 handler（Callable 优先，其次 handler_name 按名查找）
        handler = getattr(self._registry, "_resolve_handler", lambda t: t.handler)(tool_def)
        if handler is None:
            return Result.fail(f"Tool handler not resolvable: {tool_def.name}", code="NO_HANDLER")

        # T1-3 深化: 工具级超时优先（None = pipeline 全局默认）
        effective_timeout = getattr(tool_def, "timeout", None) or self._timeout

        holder: dict[str, Any] = {}

        def _run() -> None:
            try:
                holder["result"] = Result.ok(handler(**args))
            except Exception as e:  # noqa: BLE001 — handler 异常转为 Result.fail
                holder["error"] = e

        t = threading.Thread(target=_run, daemon=True, name=f"tool:{tool_def.name}")
        t.start()
        t.join(effective_timeout)
        if t.is_alive():
            return Result.fail(
                f"Tool '{tool_def.name}' timed out after {effective_timeout}s",
                code="TIMEOUT",
            )
        if "error" in holder:
            return Result.fail(f"Tool execution failed: {holder['error']}", code="EXECUTION_ERROR")
        return holder["result"]

    # ----- P0-2: output pruning -----

    # 输出 token 阈值（默认 4 096 tokens ≈ 16 KB），超过则 spill 到文件
    _OUTPUT_TOKEN_LIMIT: int = 4096
    _SPILL_DIR: str = "/home/ai/lingclaude/data/spill"

    def _prune_output(self, tool_name: str, result: Any) -> Any:
        """防止大 tool 输出爆上下文：超过阈值则写临时文件，只返回 locator。"""
        if result is None:
            return result
        # 估算字符数（token 上界)
        text = str(result)
        if len(text) <= self._OUTPUT_TOKEN_LIMIT * 4:
            return result
        # spill
        try:
            os.makedirs(self._SPILL_DIR, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix=f"spill_{tool_name}_", suffix=".txt", dir=self._SPILL_DIR)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            return {
                "_spilled": True,
                "tool": tool_name,
                "locator": path,
                "size_bytes": len(text),
                "truncated": True,
                "read_with": f"cat {path}",
            }
        except Exception:
            # spill 失败时降级：不丢弃结果，原样返回
            logger.warning("output spill failed, returning original result")
            return result

    def _error(self, msg: str, code: str = ToolErrorCode.EXECUTION_ERROR) -> dict[str, Any]:
        return {
            "error": msg,
            "error_code": code,
            "pipeline_aborted": True,
        }

    # ------------------------------------------------------------------
    # P1 (2026-09-12): 强类型结果入口 — execute_typed
    #
    # execute() 保持 dict 返回（向后兼容存量调用方/测试）；
    # execute_typed() 返回 ToolResult，错误语义用 error.code 判断
    # （替代 '"error"' in json 字符串探测）。
    # ------------------------------------------------------------------

    def execute_typed(
        self,
        name: str,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult[Any]:
        """execute() 的强类型版本：返回 ToolResult，失败含结构化 error_code。"""
        result = self.execute(name, args, **kwargs)
        if isinstance(result, dict) and result.get("error") is not None:
            raw_code = result.get("error_code") or ToolErrorCode.EXECUTION_ERROR
            # Enum 成员取其 value（str(Enum) 会带类名前缀）
            code = getattr(raw_code, "value", raw_code)
            return ToolResult.err(
                str(result["error"]),
                code=str(code),
                tool_name=name,
                detail={k: v for k, v in result.items() if k not in ("error", "error_code", "pipeline_aborted")} or None,
            )
        return ToolResult.ok(result)

    # ------------------------------------------------------------------
    # P0 主链统一 (2026-09-12, codex 审计 #1): 纯权限预检
    #
    # check_permission 只跑 1-2 段（permissions/rate/dangerous/guards），
    # **不执行 handler** — 供快路径（read ContextCache）在真正执行前判定
    # 是否被 pipeline 拒绝，杜绝"快路径绕过权限/守卫"。
    # 返回 None = 放行；返回 dict = 拒绝结果（error + error_code）。
    # ------------------------------------------------------------------

    def check_permission(
        self,
        name: str,
        args: dict[str, Any],
        *,
        permissions_blocks: Callable[[str], bool] | None = None,
        rate_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> dict[str, Any] | None:
        """纯权限预检（不执行 handler）。返回 None 放行，dict 拒绝。"""
        ctx = PipelineContext(name=name, args=args)

        denied = self._preflight(name, args, ctx, permissions_blocks, rate_check)
        if denied is not None:
            return denied

        tool = self._registry.get(name)
        if not tool.is_ok:
            return self._error(f"Tool not found: {name}", ToolErrorCode.TOOL_NOT_FOUND)
        tool_def = tool.data

        for guard in self._guards:
            try:
                dec = guard(tool_def, ctx)
                if dec.decision == "deny":
                    ctx.aborted = True
                    ctx.abort_reason = f"[guard deny] {dec.reason}"
                    return self._error(ctx.abort_reason, ToolErrorCode.GUARD_DENIED)
            except Exception as e:  # noqa: BLE001 — 与 execute() 同一 fail-closed 语义
                logger.error("guard %r raised in preflight: %s (fail-closed: deny)", guard, e)
                ctx.aborted = True
                ctx.abort_reason = f"[guard exception-fail-closed] {getattr(guard, '__name__', repr(guard))} raised: {e}"
                return self._error(ctx.abort_reason, ToolErrorCode.GUARD_EXCEPTION)

        return None  # 放行
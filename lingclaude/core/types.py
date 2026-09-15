from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS_REACHED = "max_turns_reached"
    MAX_BUDGET_REACHED = "max_budget_reached"
    CONSECUTIVE_FAILURE = "consecutive_failure"
    ERROR = "error"


@dataclass(frozen=True)
class Result(Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
    code: str | None = None

    @classmethod
    def ok(cls, data: T, code: str | None = None) -> Result[T]:
        return cls(success=True, data=data, code=code)

    @classmethod
    def fail(cls, error: str, code: str | None = None) -> Result[T]:
        return cls(success=False, error=error, code=code)

    @property
    def is_ok(self) -> bool:
        return self.success

    @property
    def is_error(self) -> bool:
        return not self.success


# ---------------------------------------------------------------------------
# P1 (2026-09-12, codex 审计 #2): 强类型工具结果协议
#
# 目标: 消灭 `'"error"' in tool_output` 字符串判断（JSON 序列化后的脆弱探测）。
#   - ToolError: 工具失败的结构化载体（code 机器可读，不再依赖文案匹配）
#   - ToolResult: 一次工具调用的强类型结果（成功 = data / 失败 = error）
#   - parse_tool_result: 统一解析层 — dict/Result/ToolResult/任意值 → ToolResult
#
# 迁移纪律: 新代码一律返回/消费 ToolResult；字符串 JSON 仅在模型消息边界
# （模型只能读文本）序列化。错误语义统一用 error.code 判断，禁止 substring 匹配。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolError:
    """工具失败的结构化错误（code 机器可读，替代 '"error"' in json 探测）。"""

    message: str
    code: str = "TOOL_ERROR"
    tool_name: str = ""
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"error": self.message, "error_code": self.code}
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.detail:
            d["detail"] = self.detail
        return d

    def __str__(self) -> str:
        return self.message


# 预定义错误码（与 pipeline 各 abort 分支一一对应，禁止再靠文案匹配）
class ToolErrorCode(str, Enum):
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    DANGEROUS_COMMAND = "DANGEROUS_COMMAND"
    GUARD_DENIED = "GUARD_DENIED"
    GUARD_EXCEPTION = "GUARD_EXCEPTION"
    VERIFY_PRE_FAILED = "VERIFY_PRE_FAILED"
    VERIFY_POST_FAILED = "VERIFY_POST_FAILED"
    PLAN_MODE_BLOCKED = "PLAN_MODE_BLOCKED"
    TIMEOUT = "TIMEOUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    INVALID_ARGS = "INVALID_ARGS"
    TOOL_LIMIT_REACHED = "TOOL_LIMIT_REACHED"
    NO_HANDLER = "NO_HANDLER"
    NOT_ALLOWED = "NOT_ALLOWED"
    MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
    MCP_CALL_FAILED = "MCP_CALL_FAILED"
    MCP_CONNECT_FAILED = "MCP_CONNECT_FAILED"
    MCP_MODULE_LOAD_FAILED = "MCP_MODULE_LOAD_FAILED"


@dataclass(frozen=True)
class ToolResult(Generic[T]):
    """一次工具调用的强类型结果。

    - success=True  → data 持有结果（dict / 标量 / None）
    - success=False → error 持有结构化 ToolError
    """

    success: bool
    data: T | None = None
    error: ToolError | None = None

    @classmethod
    def ok(cls, data: T) -> ToolResult[T]:
        return cls(success=True, data=data)

    @classmethod
    def err(
        cls,
        message: str,
        code: str = ToolErrorCode.EXECUTION_ERROR,
        tool_name: str = "",
        detail: dict[str, Any] | None = None,
    ) -> ToolResult[T]:
        return cls(
            success=False,
            error=ToolError(message=message, code=code, tool_name=tool_name, detail=detail),
        )

    @property
    def is_ok(self) -> bool:
        return self.success

    @property
    def is_error(self) -> bool:
        return not self.success

    def to_dict(self) -> dict[str, Any]:
        """序列化为模型可见的 JSON dict（唯一合法字符串化入口）。

        - 成功: {**data}（data 为 dict 时展开；否则 {"result": data}）
        - 失败: {"error": message, "error_code": code, ...}
        """
        if self.is_ok:
            if isinstance(self.data, dict):
                return dict(self.data)
            return {"result": self.data}
        assert self.error is not None
        return self.error.to_dict()

    @classmethod
    def from_dict(cls, d: dict[str, Any], tool_name: str = "") -> ToolResult[Any]:
        """把老式 dict 结果（{"error": ...} 或普通结果）归一化为 ToolResult。"""
        if isinstance(d, dict) and d.get("error") is not None:
            raw_code = d.get("error_code") or ToolErrorCode.EXECUTION_ERROR
            # Enum 成员取其 value（str(Enum) 会带类名前缀）
            code = getattr(raw_code, "value", raw_code)
            return cls.err(
                str(d["error"]),
                code=str(code),
                tool_name=str(d.get("tool_name") or tool_name),
                detail={k: v for k, v in d.items() if k not in ("error", "error_code", "tool_name")} or None,
            )
        return cls.ok(d)

    @classmethod
    def from_result(cls, r: Result[Any], tool_name: str = "") -> ToolResult[Any]:
        """把泛型 Result 归一化为 ToolResult（Result 无 code 时用 EXECUTION_ERROR）。"""
        if r.is_ok:
            return cls.ok(r.data)
        return cls.err(
            r.error or "Tool failed",
            code=r.code or ToolErrorCode.EXECUTION_ERROR,
            tool_name=tool_name,
        )


def parse_tool_result(value: Any, tool_name: str = "") -> ToolResult[Any]:
    """统一解析层：任意工具返回值 → ToolResult。

    接受:
      - ToolResult（原样返回）
      - Result（泛型成功/失败）
      - dict（含 "error" 键视为失败，否则成功）
      - ToolError（直接失败）
      - 其他任意值（成功包裹）

    用途: 所有工具调用边界（pipeline / ToolExecutor / sub_agent / MCP）
    统一经此归一化，杜绝 `'"error"' in json` 字符串探测。
    """
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, ToolError):
        return ToolResult.err(
            value.message,
            code=value.code,
            tool_name=value.tool_name or tool_name,
            detail=value.detail,
        )
    if isinstance(value, Result):
        return ToolResult.from_result(value, tool_name)
    if isinstance(value, dict):
        return ToolResult.from_dict(value, tool_name)
    return ToolResult.ok(value)


def is_tool_error(value: Any) -> bool:
    """结构化错误判定 — 替代 `'"error"' in str(value)`。

    - ToolResult / ToolError: 直接判定
    - dict: 看顶层 "error" 键（兼容老路径）
    - str: 尝试 JSON 解析后判定（工具边界常传序列化字符串）
    - 其余: False
    """
    if isinstance(value, ToolResult):
        return value.is_error
    if isinstance(value, ToolError):
        return True
    if isinstance(value, dict):
        return value.get("error") is not None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001 — 非 JSON 字符串视为普通文本
            return False
        if isinstance(parsed, dict):
            return parsed.get("error") is not None
        if isinstance(parsed, list):
            return any(isinstance(p, dict) and p.get("error") is not None for p in parsed)
        return False
    return False


# ---------------------------------------------------------------------------
# P2 (2026-09-15, lingyuan audit): 工具类型/常量下沉 core — 消灭 core→engine 倒装。
#
# 原定义在 engine/tools.py / engine/verification_gate.py / engine/tool_router.py，
# 被 core 侧 lazy import 反向引用。按"core 持数据/类型、engine 引用 core"正转：
#   - ToolOutputDefinition / ToolDefinition: 纯数据类（无 engine 依赖）
#   - WRITE_SCOPED_TOOLS / MAX_TOOLS_PER_REQUEST: 纯常量
# engine 侧文件改为 re-export（from lingclaude.core.types import ...），
# 18 处既有调用方（from lingclaude.engine.tools import ToolDefinition）零改动。
# ---------------------------------------------------------------------------


@dataclass
class ToolOutputDefinition:
    """dsh ToolOutputDefinition Python 端实现。

    Attributes:
        schema: Raw JSON Schema node enforced against successful canonical value.
        render: Pure projection from validated args+value to model-facing content blocks.
        presentation_meta: Optional pure projection (UI metadata, replay only top-level).
    """

    schema: dict[str, Any]
    render: Callable[[Any, Any], list[dict[str, Any]]]
    presentation_meta: Callable[[Any, Any], Any] | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """A registered tool — model schema + canonical output + execution metadata.

    dsh 对位：
        name + description + parameters  → dsh ToolSchema
        output → dsh ToolOutputDefinition
        handler → dsh execute
        finalize_content → dsh finalizeContent (sync last-mile content invariant)
        is_concurrency_safe → dsh isConcurrencySafe (around-dispatch decision)
        present_call / present_result → dsh presentCall/presentResult (UI hooks)
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any] | None = None
    security_scope: str = "read"
    output: ToolOutputDefinition | None = None
    is_concurrency_safe: bool = False
    finalize_content: Callable[[Any, Any], Any] | None = None
    present_call: Callable[[Any], Any] | None = None
    present_result: Callable[[Any, Any], Any] | None = None
    # T0-8: 显式 required 参数列表（空 = 全部参数视为 required，保持旧行为）。
    # MCP 工具带完整 JSON Schema 时用它区分可选参数，避免模型被迫填所有参数。
    required_params: tuple[str, ...] = ()
    # T1-3 深化: 工具级超时（秒）— None = 用 pipeline 全局默认超时
    timeout: float | None = None
    # P1 解耦: handler_name 插片引用 — ToolDefinition 不直接绑定 handler Callable，
    # 而是通过 HandlerRegistry 按名查找；这样定义（schema）与实现解耦。
    # 优先级：handler (Callable, 向后兼容) > handler_name (按名查找) > None
    handler_name: str | None = None

    def __post_init__(self) -> None:
        # T3 强制化（opencode 架构演进项）：handler 直传已废弃 — 定义（schema）
        # 与实现（Callable）解耦后，直传使 ToolDefinition 不可序列化/复用。
        # 迁移路径：register_handler(name, fn) + handler_name=name。
        # 存量测试/边缘调用方允许 LINGCLAUDE_ALLOW_DIRECT_HANDLER=1 豁免。
        if self.handler is not None:
            import os
            if not os.environ.get("LINGCLAUDE_ALLOW_DIRECT_HANDLER"):
                warnings.warn(
                    f"ToolDefinition(handler=...) 已废弃: '{self.name}' 应改用 "
                    f"register_handler() + handler_name（T3 解耦纪律）",
                    DeprecationWarning,
                    stacklevel=2,
                )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "security_scope": self.security_scope,
            "is_concurrency_safe": self.is_concurrency_safe,
        }
        if self.timeout is not None:
            d["timeout"] = self.timeout
        if self.output is not None:
            d["output_schema"] = self.output.schema
        return d


# 工具域白名单 — 写作用域（安全闸门判定用），单源定义，engine 引用 core。
WRITE_SCOPED_TOOLS = frozenset({
    "write",
    "edit",
    "file_create",
    "file_insert",
    "file_delete_lines",
    "ast_replace",
})

# 单次工具选择请求的上限（ToolRouter 路由阈值）。
MAX_TOOLS_PER_REQUEST = 30

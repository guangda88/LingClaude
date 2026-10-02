"""hook_registry — Hook 生命周期注册表（P1 ①：三注入点 + 用户插件装载）

外部联审附录收敛（2026-10-02，docs/agent_trend_2026-09_external_review.md §6.2）：
  pre_tool 注入点已有（tool_auth_hook.check_tool_call），但 after_edit/post_response
  缺失、哨兵以私有函数内嵌——每加守卫改同一文件，技术债累积。
  本模块把注入点规范化为注册制：

注入点（HookPoint）：
  PRE_TOOL      工具执行前（守卫/改道语义，block 可拦截执行）
  AFTER_TOOL    工具执行后（advisory：观察/副作用，不改结果——v1 边界）
  POST_RESPONSE 一轮响应终结后（advisory：学习笔记/提示注入）

Hook 签名（按注入点）：
  PRE_TOOL:      fn(tool_name: str, tool_args: dict) -> HookResult | None
  AFTER_TOOL:    fn(tool_name: str, args: dict, summary: dict) -> HookResult | None
  POST_RESPONSE: fn(response_text: str, meta: dict) -> HookResult | None
  返回 None = 无意见（放行/沉默）。

失败语义：
  单个 hook 抛异常 → fail-open（记 warning，跳过该 hook，其余继续）——
  与 tool_pipeline.snapshot_callback、tool_auth_hook 降级兜底同构。
  PRE_TOOL 中任一 block → 短路返回该 block（高优先级先跑）。

用户插件：
  ~/.lingclaude/hooks/*.py 首次使用时自动装载，约定入口：
      def register(api) -> None:
          api.on(HookPoint.POST_RESPONSE, "my-hook", my_fn, priority=50)
  装载幂等（同路径只装一次）；坏文件跳过并上报，不反噬主流程。
  （装载模式对齐 cli/slash_plugin_loader.py 的 register(add) 约定。）
"""
from __future__ import annotations

import enum
import importlib.util
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HookPoint(enum.Enum):
    PRE_TOOL = "pre_tool"
    AFTER_TOOL = "after_tool"
    POST_RESPONSE = "post_response"


@dataclass
class HookResult:
    """单个 hook 的裁决/意见。action: block=拦截（仅 PRE_TOOL 有效）；annotate=附言。"""

    hook_name: str
    action: str = "annotate"  # "block" | "annotate"
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class _HookEntry:
    name: str
    fn: Callable[..., Any]
    priority: int  # 小者先跑


# 注册表：point -> [entries]（模块级单例；跨 tool_auth_hook / tool_pipeline / repl_turn 共享）
_REGISTRY: dict[HookPoint, list[_HookEntry]] = {p: [] for p in HookPoint}
# 用户插件装载状态（路径 -> 装载结果说明）
_LOADED_HOOK_FILES: dict[str, str] = {}
_LOAD_DONE = False


def register_hook(point: HookPoint, name: str, fn: Callable[..., Any], priority: int = 50) -> None:
    """注册 hook。同名同点重复注册幂等（后者覆盖前者，保序）。"""
    entries = _REGISTRY[point]
    entries[:] = [e for e in entries if e.name != name]
    entries.append(_HookEntry(name=name, fn=fn, priority=priority))
    entries.sort(key=lambda e: (e.priority, e.name))


def unregister_hook(point: HookPoint, name: str) -> bool:
    entries = _REGISTRY[point]
    before = len(entries)
    entries[:] = [e for e in entries if e.name != name]
    return len(entries) < before


def list_hooks(point: HookPoint | None = None) -> dict[str, list[str]]:
    """列出已注册 hook（诊断/测试用）。"""
    points = [point] if point else list(HookPoint)
    return {p.value: [e.name for e in _REGISTRY[p]] for p in points}


def run_hooks(point: HookPoint, **payload: Any) -> list[HookResult]:
    """按优先级跑某注入点的全部 hook。

    - 单 hook 异常 fail-open（跳过，继续其余）
    - PRE_TOOL 出现 block：短路返回 [该 block]（不跑后续——被拦的工具不值得跑观察者）
    - 其余情况返回全部 annotate 结果（无 hook → 空表）
    """
    _ensure_user_hooks_loaded()
    results: list[HookResult] = []
    for entry in list(_REGISTRY[point]):  # copy：hook 内注册/注销不影响本轮
        try:
            r = entry.fn(**payload)
        except Exception as exc:  # noqa: BLE001 — 守卫失败不反噬主流程
            logger.warning("hook %s raised at %s (fail-open): %s", entry.name, point.value, exc)
            continue
        if r is None:
            continue
        if isinstance(r, HookResult):
            r.hook_name = r.hook_name or entry.name
            results.append(r)
            if point is HookPoint.PRE_TOOL and r.action == "block":
                break  # 短路：拦截优先
        else:
            logger.warning("hook %s returned non-HookResult (%s); ignored", entry.name, type(r))
    return results


# ── 用户插件装载 ────────────────────────────────────────────────────────────
def _hooks_dir() -> Path:
    return Path(
        os.environ.get("LC_HOOKS_DIR")
        or (Path(os.environ.get("LC_ROOT", str(Path.home()))) / ".lingclaude" / "hooks")
    )


def _import_hook_file(path: Path) -> str:
    """import 单个 hook 文件并调其 register(api)。返回状态说明。"""
    stem = f"lc_user_hook_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(stem, path)
        if spec is None or spec.loader is None:
            return "no-spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        register_fn = getattr(mod, "register", None)
        if not callable(register_fn):
            return "no-register"
        register_fn(_UserHookAPI())
        return "loaded"
    except Exception as exc:  # noqa: BLE001 — 坏插件跳过不反噬
        logger.warning("user hook %s failed to load: %s", path.name, exc)
        return f"error: {exc}"


def load_user_hooks(hooks_dir: Path | None = None, force: bool = False) -> dict[str, str]:
    global _LOAD_DONE
    d = hooks_dir or _hooks_dir()
    status: dict[str, str] = {}
    try:
        if not d.is_dir():
            return status
        from lingclaude.core import plugin_governance

        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_"):
                continue
            verdict = plugin_governance.deny_on_error(
                plugin_governance.check_file_allowed, f
            )
            if not verdict.allowed:
                logger.warning(
                    "user hook %s 被插件治理拒绝装载: %s", f.name, verdict.reason
                )
                _LOADED_HOOK_FILES[str(f.resolve())] = f"blocked: {verdict.reason}"
                status[f.name] = f"blocked: {verdict.reason}"
                continue
            key = str(f.resolve())
            if not force and key in _LOADED_HOOK_FILES:
                status[f.name] = _LOADED_HOOK_FILES[key]
                continue
            st = _import_hook_file(f)
            _LOADED_HOOK_FILES[key] = st
            status[f.name] = st
    finally:
        _LOAD_DONE = True
    return status


def _ensure_user_hooks_loaded() -> None:
    global _LOAD_DONE
    if not _LOAD_DONE:
        _LOAD_DONE = True
        load_user_hooks(force=False)


class _UserHookAPI:
    """传给用户插件 register(api) 的注册接口（收窄面：只暴露 on/list）。"""

    def on(self, point: HookPoint, name: str, fn: Callable[..., Any], priority: int = 50) -> None:
        register_hook(point, name, fn, priority)

    def list(self, point: HookPoint | None = None) -> dict[str, list[str]]:  # noqa: A003
        return list_hooks(point)

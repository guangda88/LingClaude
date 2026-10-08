"""H17 完成申报核验守卫（h17_completion_gate）— 闭环申报的代码化落地。

背景（2026-10-08 竞赛首跑 winner 方案）:
  源: docs/sdt/2026-10-07-sdt进化方案-speckit收敛守卫评审竞赛/contest_first_run_full.json
  winner=A MiniMax-M3@minimax 9.5 分。双通道异构: 前置窄面拦截 + 后置留痕。

架构适配（winner 假设 vs 本库实况, 2026-10-08 盘点）:
  - winner 假设 core 直接 import gov.guard（dispatcher/base/response 三件套）;
    实况: core/ 对 gov 的 import 零先例（grep 佐证），直接 import 会开耦合先河。
  - 落地形态改走 core 已有 hooks 总线（core/hooks.py, cognitive_rhythm 先例）:
    core 只在 _execute_tool_typed 发 PRE_TOOL_USE / _execute_tool 发 POST_TOOL_USE
    两个通用事件（判定 guard_block 走 metadata 通用键）; 守卫侧 register_with()
    注册回调。core 不知 H17 存在、不 import gov —— 铁律零妥协。
  - H17 提案入 HookType 枚举对齐「方案C v4 扩容」先例（SESSION_RESUME 等同款注释）。

分层与铁律:
  - 守卫在 gov/guard/（可拒收动作域），fail-open: 守卫失效=放行+WARNING 留痕;
    但「证据不足」是守卫本职的确定性判定，必须拒收（对齐 spec_converge_gate
    「未收敛拒收」先例——这不是失效是本职）。
  - 触发面 = COMPLETION_DECLARE_TOOLS 白名单。当前库内完成申报类工具尚未落地
    （SDT 任务包形态见 docs/sdt/templates/），白名单默认空集 → 守卫休眠（全放行）。
    这是刻意的: 不做没有触发面的臆造拦截，工具命名落地后填白名单即生效。

verify_log 关联（10-08 实测 schema，勿信 winner 假设的 step_id 结构）:
  data/arch_ledger/verify_log/*.jsonl，行 schema
  {ts, kind, claim, evidence, digest, trace_id}。
  差异核查: 按 task_id 子串匹配 trace_id 汇集已有证据 id，
  与申报方声称的证据清单求差 → 缺失即拒收。

测试: tests/test_h17_completion_gate.py（verify_log 目录 monkeypatch 到 tmp_path）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from lingclaude.core.hooks import HookContext, HookManager, HookType

_logger = logging.getLogger(__name__)

# 完成申报类工具白名单（语义: 调用即代表「任务/步骤已完成」的申报动作）。
# 刻意默认空集 = 守卫休眠。候选命名（落地后择取填入）:
#   declare_complete / report_done / task_finish / sdt_complete
COMPLETION_DECLARE_TOOLS: frozenset[str] = frozenset()

# verify_log 根目录（相对仓库根; 测试经 monkeypatch 重定向）
VERIFY_LOG_DIR = Path("data/arch_ledger/verify_log")

GUARD_NAME = "H17_completion_verification"

# 后置审计留痕上限（防长进程泄漏; 超限淘汰最早，对齐 spec_converge_gate 先例）
_MAX_AUDIT_LOG = 64
_AUDIT_LOG: list[dict] = []


# ────────────────────────── 注册入口 ──────────────────────────
def register_with(manager: HookManager) -> None:
    """把 H17 双通道 hook 挂上引擎 hooks 总线（engine bootstrap 一行调用）。"""
    manager.register("h17_pre_tool_use", HookType.PRE_TOOL_USE, _pre_tool_use_hook, priority=50)
    manager.register("h17_post_tool_use", HookType.POST_TOOL_USE, _post_tool_use_hook, priority=50)


def _pre_tool_use_hook(ctx: HookContext) -> HookContext | None:
    """前置通道: 窄面白名单命中才核查; 拦截经 metadata['guard_block'] 传递。"""
    if ctx.tool_name not in COMPLETION_DECLARE_TOOLS:
        return None  # 休眠路径: 不构造 verdict、不碰 ctx（零开销）
    try:
        verdict = pre_check(ctx.tool_name, ctx.metadata.get("tool_args") or {}, dict(ctx.metadata))
    except Exception as exc:  # noqa: BLE001 — fail-open + 显式留痕，绝不静默
        _logger.warning("H17 守卫降级放行: %s (tool=%s)", exc, ctx.tool_name)
        return None
    if verdict.get("allow"):
        return None
    return replace(
        ctx,
        metadata={
            **ctx.metadata,
            "guard_block": True,
            "guard_name": GUARD_NAME,
            "guard_verdict": verdict.get("verdict", ""),
            "guard_message": verdict.get("message", "完成申报未通过核验"),
        },
    )


def _post_tool_use_hook(ctx: HookContext) -> HookContext | None:
    """后置通道: 只留痕，绝不阻断、绝不修改 ctx。"""
    if ctx.tool_name in COMPLETION_DECLARE_TOOLS:
        post_audit(ctx.tool_name, ctx.metadata.get("tool_result"), {})
    return None


def attach(engine: Any) -> None:
    """显式挂载 API（幂等）：engine 侧 bootstrap 一行调用，core 不感知本模块。

    幂等性：先 unregister 同名钩子再注册，重复 attach 不产生双份拦截。
    生产挂载点决策（engine 构造处 or 运维手工）留给宿主/并行会话，本模块
    不做 import 副作用注册（防意外全局唤醒休眠守卫）。
    """
    mgr = getattr(engine, "_hooks", None)
    if mgr is None:
        _logger.warning("H17 attach: engine 无 _hooks 总线，跳过（零侵入）")
        return
    mgr.unregister("h17_pre_tool_use")
    mgr.unregister("h17_post_tool_use")
    register_with(mgr)
    _logger.info(
        "H17 完成申报守卫已挂载（白名单 %d 项：休眠=%s）",
        len(COMPLETION_DECLARE_TOOLS), not COMPLETION_DECLARE_TOOLS,
    )


# ────────────────────────── 核验核心 ──────────────────────────
def pre_check(tool_name: str, args: dict, ctx: dict | None = None) -> dict:
    """完成申报前置核验（独立导出: 便于单测与 CLI 直查）。非申报类恒放行。"""
    if tool_name not in COMPLETION_DECLARE_TOOLS:
        return {"allow": True}
    return _check_evidence(tool_name, args, ctx or {})


def _check_evidence(tool_name: str, args: dict, ctx: dict) -> dict:
    merged = {**(ctx or {}), **(args or {})}
    task_id = merged.get("task_id") or merged.get("task") or ""
    if not task_id:
        return _block(
            verdict="task_id_missing",
            missing=[{"need": "task_id", "have": None}],
            have=[],
            message="完成申报缺少 task_id，无法反查核验证据",
        )

    raw_tids = _collect_trace_ids(str(task_id))
    # 派生证据短 id（trace_id 剥 task_id 前缀; 整任务级记录 → 哨兵 "*"）。
    # 精确集合匹配而非子串: 防 step_1 误命中 step_10（2026-10-08 预验证抓到的 bug）。
    evidence_ids = _derive_evidence_ids(str(task_id), raw_tids)
    required = _required_evidence_ids(merged)
    if required == ["*"]:
        # 通配需求 = 至少一条任意形态证据（步骤级或任务级皆算工作痕迹）
        missing = [] if evidence_ids else ["*"]
    else:
        missing = [r for r in required if r not in evidence_ids]
    if not missing:
        return {"allow": True, "evidence_verified": len(evidence_ids), "task_id": task_id}
    return _block(
        verdict="evidence_insufficient",
        missing=missing,
        have=sorted(raw_tids),
        message="完成申报被 H17 守卫拦截：核验证据缺失（verify_log 无对应记录）",
    )


def _required_evidence_ids(merged: dict) -> list[str]:
    """申报方声称已完成步骤的证据 id 清单。

    接受两种申报形态: required_evidence 显式清单（优先）; 或 steps 列表
    （每个 step id 即所需证据 id）。都没有则要求至少一条任意形态证据（步骤级或任务级）。
    """
    explicit = merged.get("required_evidence")
    if isinstance(explicit, (list, tuple)) and explicit:
        return [str(x) for x in explicit]
    steps = merged.get("steps")
    if isinstance(steps, (list, tuple)) and steps:
        return [str(s.get("id")) if isinstance(s, dict) else str(s) for s in steps]
    return ["*"]


def _collect_trace_ids(task_id: str) -> set[str]:
    """从 verify_log JSONL 汇集与 task_id 关联的 trace_id（fail-open 读）。"""
    found: set[str] = set()
    root = Path(VERIFY_LOG_DIR)
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if task_id not in line:
                    continue  # 子串快筛，避免全量 json.loads
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = str(rec.get("trace_id", ""))
                if task_id in tid:
                    found.add(tid)
        except OSError:
            continue
    return found


def _derive_evidence_ids(task_id: str, trace_ids: set[str]) -> set[str]:
    """trace_id → 证据短 id 集合。

    惯例: trace_id = f"{task_id}-{step_id}"，剥前缀得 step_id;
    整任务级记录（trace_id == task_id）映射哨兵 "*"，命中「至少一条证据」需求;
    不以 task_id 开头的异常形态保留完整 tid（保守不误剥）。
    """
    out: set[str] = set()
    for tid in trace_ids:
        if tid == task_id:
            out.add("*")
        elif tid.startswith(task_id):
            derived = tid[len(task_id):].lstrip("-_ ")
            if derived:
                out.add(derived)
        else:
            out.add(tid)
    return out


def _block(verdict: str, missing: list, have: list, message: str) -> dict:
    """确定性拒收结构（守卫本职，非失效）。core 侧转 ToolResult GUARD_DENIED。"""
    return {
        "allow": False,
        "guard": GUARD_NAME,
        "verdict": verdict,
        "message": message,
        "missing_evidence": missing,
        "have_trace_ids": have,
        "remediation": "请先补做核验并把证据写入 data/arch_ledger/verify_log/（trace_id 需含 task_id），再重新申报",
    }


# ────────────────────────── 后置审计 ──────────────────────────
def post_audit(tool_name: str, result: object, ctx: dict | None = None) -> None:
    """申报类工具执行后留痕（进程内环形，零 IO 热路径; 台账落盘走批处理惯例）。"""
    if tool_name not in COMPLETION_DECLARE_TOOLS:
        return
    entry = {
        "ts": time.time(),
        "kind": "h17_completion_audit",
        "tool": tool_name,
        "ok": getattr(result, "success", None),
        "error_code": str(getattr(getattr(result, "error", None), "code", "")),
    }
    _AUDIT_LOG.append(entry)
    if len(_AUDIT_LOG) > _MAX_AUDIT_LOG:
        del _AUDIT_LOG[: len(_AUDIT_LOG) - _MAX_AUDIT_LOG]
    _logger.debug("H17 post_audit: %s", entry)

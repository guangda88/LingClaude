"""SDT 收敛反查守卫（spec_converge_gate）— Spec Kit「implement→converge 循环」的 lc 落地。

背景（2026-10-07 进化方案, 源自 docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md）:
  SDT-lc-006 返审触发器是「变化驱动」（fingerprint 变化）, 缺「目标驱动」半边——
  交付物是否满足原始 spec 无人反查。本守卫补该盲区: 完成申报前强制反查三工件。

分层标注:
  本模块 = 守卫（可拒收动作: 未收敛时拒绝「已完成」申报, 对齐 H17 闭环申报的 SDT 形态）。
  认知型守卫（H13 sure? / H14 双因追问）保持文档形态, 不做代码化伪装。

范式对齐（free_ram_gate 先例）:
  - fail-open + 显式留痕: 工件缺失/损坏时给出明确结论而非异常崩溃;
    但「未收敛」是确定性判定, 必须拒收（这不是失效, 是守卫本职）。
  - 阈值 env 可配置: LINGCLAUDE_SPEC_GATE_ALLOW_MISSING=1 显式豁免缺失工件
    （留 WARNING 痕, 绝不静默）。
  - 裁决结构对齐 core/governance.py GovernanceGate: (passed/checks/warnings/error)。

单一入口: check_completion(tasks_path, spec_path=None) -> GateResult dict。
CLI: python -m lingclaude.gov.guard.spec_converge_gate <任务包目录或 tasks.md 路径>

铁律校验: core/ 零 diff, gov/guard/ 插片式新增（铁律 7）。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

_logger = logging.getLogger(__name__)

# 拒收留痕上限（防长进程泄漏; 超限淘汰最早）
_MAX_REJECT_LOG = 64
_REJECT_LOG: list[dict] = []

_CHECKBOX_RE = re.compile(r"^\s*-\s\[( |x|X)\]", re.M)
_AC_RE = re.compile(r"^\s*-\s\[[ xX]\]\s*(AC\d+)", re.M)


def _parse_tasks(text: str) -> dict:
    """解析 tasks.md: 返回 (未勾选/总数, 未勾选条目文本)。"""
    boxes = _CHECKBOX_RE.findall(text)
    total = len(boxes)
    done = sum(1 for b in boxes if b.lower() == "x")
    pending_items = [
        line.strip() for line in text.splitlines()
        if re.match(r"^\s*-\s\[\s\]", line)
    ]
    return {"pending": total - done, "total": total, "pending_items": pending_items}


def check_completion(
    tasks_path: str | Path,
    spec_path: str | Path | None = None,
) -> dict:
    """守卫单一入口: 完成申报前反查 SDT 三工件收敛状态。

    返回 GovernanceGate 对齐结构:
      passed=True  → 三工件齐、无未勾选项, 准予申报
      passed=False → 拒收, error 说明缺口; 拒收留痕

    fail-open 语义边界: 工件「缺失」且未设豁免 env 时按未收敛拒收（缺工件本身
    即流程违规, 不是守卫失效）; 解析异常时 fail-open 放行 + WARNING（守卫失效
    宁可放行, 绝不静默）。
    """
    checks: list[str] = []
    warnings: list[str] = []
    tasks_path = Path(tasks_path)
    pkg = tasks_path.parent if tasks_path.name == "tasks.md" else tasks_path
    tasks_md = pkg / "tasks.md" if pkg.is_dir() else tasks_path
    spec_md = Path(spec_path) if spec_path else (pkg / "spec.md" if pkg.is_dir() else None)

    # 豁免开关（显式留痕, 不静默）
    allow_missing = os.environ.get("LINGCLAUDE_SPEC_GATE_ALLOW_MISSING") == "1"

    if not tasks_md.exists():
        if allow_missing:
            warnings.append(f"tasks.md 缺失已豁免(env): {tasks_md}")
            return {"passed": True, "checks": checks, "warnings": warnings, "error": None}
        reason = f"tasks.md 缺失: {tasks_md} — 无执行清单即无收敛依据, 拒绝完成申报"
        _reject(reason, {"tasks_md": str(tasks_md)})
        return {"passed": False, "checks": checks, "warnings": warnings, "error": reason}
    checks.append(f"tasks.md 存在: {tasks_md}")

    try:
        stats = _parse_tasks(tasks_md.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail-open: 解析失效宁可放行
        warnings.append(f"tasks.md 解析失败 fail-open 放行: {exc}")
        return {"passed": True, "checks": checks, "warnings": warnings, "error": None}
    checks.append(f"tasks 收敛度: {stats['total'] - stats['pending']}/{stats['total']}")

    if stats["pending"] > 0:
        preview = "; ".join(stats["pending_items"][:3])
        reason = (
            f"未收敛: tasks.md 有 {stats['pending']}/{stats['total']} 未勾选项"
            f"（如: {preview}）— 补齐或如实改判「部分完成」, 拒绝整体「已完成」申报"
        )
        _reject(reason, {"pending": stats["pending"], "total": stats["total"]})
        return {"passed": False, "checks": checks, "warnings": warnings, "error": reason}

    # spec.md 反查: 有 AC 清单时要求无未勾选 AC（验收标准未核验 = 未收敛）
    if spec_md and spec_md.exists():
        checks.append(f"spec.md 存在: {spec_md}")
        acs = _AC_RE.findall(spec_md.read_text(encoding="utf-8"))
        if acs:
            unchecked = re.findall(
                r"^\s*-\s\[\s\]\s*(AC\d+)",
                spec_md.read_text(encoding="utf-8"), re.M,
            )
            checks.append(f"spec 验收标准: {len(acs) - len(unchecked)}/{len(acs)} 已核验")
            if unchecked:
                reason = f"spec 验收标准未核验: {', '.join(unchecked)} — converge 反查不过, 拒绝完成申报"
                _reject(reason, {"unchecked_ac": unchecked})
                return {"passed": False, "checks": checks, "warnings": warnings, "error": reason}
        else:
            warnings.append("spec.md 无可验证验收标准（AC 条目）— 建议补可执行判据")
    elif spec_md is not None and not allow_missing:
        warnings.append(f"spec.md 缺失（仅警告, tasks.md 为准）: {spec_md}")

    return {"passed": True, "checks": checks, "warnings": warnings, "error": None}


def _reject(reason: str, extra: dict) -> None:
    """拒收留痕（内存表 + WARNING 日志, 对齐 free_ram_gate 范式）。"""
    entry = {"ts": time.time(), "reason": reason, **extra}
    _REJECT_LOG.append(entry)
    if len(_REJECT_LOG) > _MAX_REJECT_LOG:
        _REJECT_LOG.pop(0)
    _logger.warning("[spec-gate] %s", reason)


def recent_rejections(limit: int = 10) -> list[dict]:
    """查询最近拒收留痕（doctor/audit 消费口）。"""
    return list(_REJECT_LOG[-limit:])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python -m lingclaude.gov.guard.spec_converge_gate <任务包目录或 tasks.md 路径>")
    result = check_completion(sys.argv[1])
    for c in result["checks"]:
        print(f"  ✓ {c}")
    for w in result["warnings"]:
        print(f"  ⚠ {w}")
    print("PASS" if result["passed"] else f"REJECT: {result['error']}")
    sys.exit(0 if result["passed"] else 1)

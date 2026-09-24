"""N8 free-RAM 守卫门 — 启动前可拒收的资源守卫（铁律 7 gov 域，守卫层）。

背景:
  2026-08-21 长会话 RSS 21GB 事件后系统进入 thrash（颠簸）, 整机卡死;
  事中无资源守卫能拒收新会话启动。本模块补该盲区。

分层标注（2026-09-24 主裁 B1 裁决, gov/ 第六域 + 守卫/仪表分层强制）:
  本模块 = 守卫（可拒收动作: 内存不足时拒绝新会话/长任务启动）。
  与之互补的仪表 = ops/rss_watchdog.py（N9, RSS 增长观测, 只告警不拒收）。
  N8 编号语义: N1-N7 全占, N8 预留给环境守卫（v0.4 规划分册决议）。

形态: 进程内 /proc/meminfo 采样（无 psutil 依赖, best-effort — 采样失败放行并留痕,
  守卫失效宁可放行不可误杀, 与 ops/rss_watchdog 的"可观测性不破坏主流程"同源不同向:
  守卫失效方向是 fail-open + 显式日志, 绝不静默）。

框架收口: 守卫裁决结果经 core/governance.py GovernanceGate.check() 结果结构对齐
  （passed/checks/warnings/error），框架单轨不改（铁律 7 gov 域物理落地规范）。

设计约束:
  - 拒绝阈值 env 可配置: LINGCLAUDE_FREE_RAM_GUARD_MIN_MB（整数 MB, 默认 1024,
    即 free<1GiB 拒启动 — v0.4 议题 B 裁决口径）; 非法值回退默认。
  - 触发留痕: 每次拒收落 _REJECT_LOG（内存表, 供 doctor/audit 查询）+ WARNING 日志。
  - 规格仅覆盖拒绝判定本身; 接线点（CLI/daemon 启动路径调用本门）由调用方实现,
    本模块提供 check_startup_allowed() 单一入口。
"""
from __future__ import annotations

import logging
import os
import time

_logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# 拒绝阈值（MB）: 可用内存低于此值即拒收启动动作（默认 1024 = 1GiB）
MIN_FREE_MB = _env_int("LINGCLAUDE_FREE_RAM_GUARD_MIN_MB", 1024)

# 拒收留痕上限（防长进程泄漏; 超限淘汰最早）
_MAX_REJECT_LOG = 64
_REJECT_LOG: list[dict] = []


def sample_free_mb() -> int | None:
    """读 /proc/meminfo 的 MemAvailable 换算 MB; 失败返回 None。

    选 MemAvailable 而非 MemFree: 前者含可回收页缓存, 是"实际可用"的内核口径。
    Linux-only; 非 Linux /proc 缺失时返回 None。
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:  # noqa: BLE001 — fail-open 采样（守卫失效宁可放行）
        return None
    return None


def check_startup_allowed(
    action: str = "startup",
    *,
    action_id: str = "",
) -> tuple[bool, str | None]:
    """守卫门单一入口: 可用内存不足时拒收启动类动作。

    返回 (allowed, reason):
      allowed=True  → 放行（含采样失败的 fail-open 放行, 此时有 WARNING 留痕）
      allowed=False → 拒收, reason 说明当前可用内存与阈值

    拒收即留痕（_REJECT_LOG + WARNING 日志）。
    """
    free_mb = sample_free_mb()

    if free_mb is None:
        # fail-open: 采样失败放行但必须留痕（绝不静默 — 静默失效的守卫比没有守卫危险）
        _logger.warning(
            "[N8] free-RAM 采样失败, fail-open 放行 action=%s", action
        )
        return True, None

    if free_mb >= MIN_FREE_MB:
        return True, None

    reason = (
        f"free {free_mb}MB < guard {MIN_FREE_MB}MB, "
        f"拒绝 {action}" + (f"({action_id})" if action_id else "")
    )
    entry = {
        "ts": time.time(),
        "action": action,
        "action_id": action_id,
        "free_mb": free_mb,
        "threshold_mb": MIN_FREE_MB,
        "reason": reason,
    }
    _REJECT_LOG.append(entry)
    if len(_REJECT_LOG) > _MAX_REJECT_LOG:
        _REJECT_LOG.pop(0)
    _logger.warning("[N8] %s", reason)
    return False, reason


def recent_rejections(limit: int = 10) -> list[dict]:
    """查询最近拒收留痕（doctor/audit 消费口）。"""
    return list(_REJECT_LOG[-limit:])

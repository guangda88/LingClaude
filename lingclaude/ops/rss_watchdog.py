"""N9 RSS 增长看门狗（仪表）— 长时会话内存泄漏观测盲区补齐（opencode 盲区 #2）。

分层标注（2026-09-24，主裁 B1 裁决 gov/ 第六域 + 守卫/仪表分层强制）:
  本模块 = 仪表（只观测告警，不拒收任何动作）：/proc RSS 采样 + 阈值增长告警。
  原 N6 编号与铁律注册表撞号（N6=口径成本互证），改挂 N9（N1-N7 全占、N8 留环境守卫）。
  守卫侧（free-RAM < 阈值拒启动，可拒收动作）实现于 lingclaude/gov/guard/，
  框架仍收口 core/governance.py 单轨（铁律 7 gov 域物理落地规范）。
  本模块保持 ops/ 仪表身份，不以纯仪表实体迁入守卫目录。


背景:
  token_monitor 只记 token 总量, 交互会话进程 RSS 无任何观测。
  长时运行会话（数小时+）若发生内存泄漏, 症状是「越用越慢→OOM 被杀」,
  事后只能靠 atomcode_crash_analysis 尸检, 事中无告警。

形态: 进程内 /proc 采样（无 psutil 依赖, 无常驻线程）。
  挂点与 N5a 相同 — _record_long_task_metrics 收尾点, 每轮采样一次:
    - 首轮建基线
    - 增长 ≥500MB → WARNING（一次, 基线重置到当前值重新观测）
    - 绝对值 ≥ 有效硬限 → ERROR + LingBus 告警（一次, 基线同步重置）
  基线重置语义: 报警后以当前值为新基线, 避免稳态泄漏场景每轮重复轰炸。
  硬限动态下界: max(默认硬限, 基线+增长线) — 高基线会话（实测常驻 553MB）
  下静态硬限 1000MB 先于增长线 1053MB 触发, WARNING 沦为死代码、首告即 ERROR;
  加下界后有效硬限恒 ≥ 增长线触发点（消除倒挂, 只升不降、绝对帽语义不变）;
  单步大跳可能同轮双触发（硬限确实被击穿, 行为合理）。

设计约束: best-effort 全程吞异常 — 可观测性组件不得破坏主流程。
"""
from __future__ import annotations

import logging
import os

from lingclaude.coordination.alert import send_lingbus_alert

_logger = logging.getLogger(__name__)

# 阈值（MB）: 增长告警 / 绝对值硬限（opencode 建议 500MB 告警线）
# env 覆盖: LINGCLAUDE_RSS_GROWTH_WARN_MB / LINGCLAUDE_RSS_HARD_LIMIT_MB（整数 MB, 非法值静默回退默认 — best-effort）
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


GROWTH_WARN_MB = _env_int("LINGCLAUDE_RSS_GROWTH_WARN_MB", 500)
RSS_HARD_LIMIT_MB = _env_int("LINGCLAUDE_RSS_HARD_LIMIT_MB", 1000)
# 基线表上限（防长进程多会话泄漏）: 超限淘汰最早插入的会话
_MAX_BASELINES = 256


def _effective_hard_limit_mb(baseline_mb: int) -> int:
    """有效硬限 = max(绝对硬限, 基线+增长线)。

    高基线会话下静态硬限可能先于增长线触发（分级倒挂）;
    动态下界保证 ERROR 阈值恒不低于 WARNING 阈值, 只升不降。
    基线未知(<=0)回退静态值。
    """
    if baseline_mb <= 0:
        return RSS_HARD_LIMIT_MB
    return max(RSS_HARD_LIMIT_MB, baseline_mb + GROWTH_WARN_MB)

_LINGBUS_ALERT_SUBJECT = "[N9仪表] 会话 RSS 异常"

# 会话基线表: session_id -> baseline_mb（dict 保序, 便于 LRU 淘汰）
_BASELINES: dict[str, int] = {}
# 已告警标记: (session_id, kind) — kind ∈ {"growth", "hard"}
_ALERTED: set[tuple[str, str]] = set()


def sample_rss_mb(pid: int | None = None) -> int | None:
    """读 /proc/<pid>/status 的 VmRSS（kB）换算 MB; 失败返回 None。

    pid=None 采样当前进程。Linux-only; 非 Linux /proc 缺失时返回 None。
    """
    try:
        status_path = f"/proc/{pid if pid is not None else 'self'}/status"
        with open(status_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:  # noqa: BLE001 — best-effort 采样
        return None
    return None


def check_rss_growth(
    session_id: str,
    current_mb: int,
    *,
    event: str = "",
) -> list[str]:
    """按基线语义检查 RSS, 返回告警行（空=正常）。

    副作用: 建/重置基线、记录一次性告警标记、ERROR 级发 LingBus（best-effort）。
    """
    findings: list[str] = []
    key = str(session_id)

    if key not in _BASELINES:
        _BASELINES[key] = current_mb
        if len(_BASELINES) > _MAX_BASELINES:
            _BASELINES.pop(next(iter(_BASELINES)))
        return findings

    baseline = _BASELINES[key]
    growth = current_mb - baseline
    effective_hard = _effective_hard_limit_mb(baseline)
    hard = current_mb >= effective_hard

    if growth >= GROWTH_WARN_MB:
        line = f"RSS 增长 {growth}MB（基线 {_BASELINES[key]}→{current_mb}）≥{GROWTH_WARN_MB}MB"
        findings.append(f"WARNING: {line}")
        _logger.warning("[N9] %s", line)
        if (key, "growth") not in _ALERTED:
            _ALERTED.add((key, "growth"))
            _notify(key, "WARNING", f"{line} event={event}")
        # 基线重置: 报警后重新观测, 稳态泄漏不每轮轰炸
        _BASELINES[key] = current_mb
        _ALERTED.discard((key, "hard"))  # 增长回落后允许硬限重新评估

    if hard and (key, "hard") not in _ALERTED:
        line = f"RSS 绝对值 {current_mb}MB ≥{effective_hard}MB 硬限"
        findings.append(f"ERROR: {line}")
        _logger.error("[N9] %s", line)
        _ALERTED.add((key, "hard"))
        _notify(key, "ERROR", f"{line} event={event}")

    return findings


def _notify(session_id: str, level: str, detail: str) -> None:
    """LingBus 告警, best-effort（与 n5 家族一致: 失败只落日志）。"""
    try:
        send_lingbus_alert(
            _LINGBUS_ALERT_SUBJECT,
            f"level={level} session={session_id} {detail}",
        )
    except Exception:  # noqa: BLE001
        _logger.warning("[N9] LingBus alert failed", exc_info=True)


def check_rss_watchdog(session_id: str, *, event: str = "") -> list[str]:
    """采样当前进程并检查; 采样失败静默返回空（可观测性不破坏主流程）。"""
    mb = sample_rss_mb()
    if mb is None:
        return []
    try:
        return check_rss_growth(str(session_id), mb, event=event)
    except Exception:  # noqa: BLE001
        _logger.debug("[N9] rss check failed", exc_info=True)
        return []


def reset_baselines() -> None:
    """测试辅助: 清空基线与告警标记。"""
    _BASELINES.clear()
    _ALERTED.clear()

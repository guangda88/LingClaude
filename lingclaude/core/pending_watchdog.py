"""灰区 pending 看门狗 — 治「挂起 28 天无人处理」的结构性缺口。

背景（2026-10-03 实测）：.lingclaude/guard_pending.jsonl 968 条记录，
624 条 state=pending + 203 条无 state 字段，最老 2026-09-05T23:13——
灰区升级机制（gray_zone_escalate）只负责「落盘+通知一次」，之后 pending
进入永夜：无超时、无升级、无台账。EROFS 期间总线全停时更彻底失联。

设计约束（与 gray_zone/alert 一致）：
  - 纯函数扫描，fail-safe：任何失败只 WARNING，绝不 raise
  - 不改动 pending 文件本身（消费方 permissions.py 的语义保持不动）
  - 升级动作 = LingBus 广播（best-effort）+ 返回结构化结果给调用方

用法：
    from lingclaude.core.pending_watchdog import scan_stale_pendings
    stale = scan_stale_pendings()          # 全部超时未决
    urgent = [p for p in stale if p.hours_pending >= ESCALATE_HOURS]

CLI（人工巡检）：
    python -m lingclaude.core.pending_watchdog            # 摘要
    python -m lingclaude.core.pending_watchdog --escalate # 触发总线升级
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from lingclaude.core.permissions import PENDING_LOG_PATH

_logger = logging.getLogger(__name__)

#: 未决超过该小时数视为「超时」（默认 24h，对齐 9-19 会议纪要回执窗口）
STALE_HOURS = 24

#: 未决超过该小时数触发总线升级广播（默认 72h = P2 工单时限档）
ESCALATE_HOURS = 72

#: pending 记录里视为「未决」的 state 集合（缺失字段按未决处理——历史数据）
_OPEN_STATES: set[str | None] = {"pending", None, ""}


@dataclass
class StalePending:
    """一条超时未决的灰区记录。"""

    ts: str
    action: str
    mode: str
    state: str
    hours_pending: float
    escalated: bool = False
    params: dict[str, Any] = field(default_factory=dict)


def _parse_ts(raw: Any) -> datetime | None:
    """容错解析 ISO 时间戳；失败返回 None（该条跳过时长计算）。"""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:  # 历史数据可能无时区
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def scan_stale_pendings(
    path: Any = None,
    *,
    stale_hours: float = STALE_HOURS,
    now: datetime | None = None,
) -> list[StalePending]:
    """扫描 pending 文件，返回超时未决记录（按挂起时长降序）。

    fail-safe：文件缺失/损坏逐条跳过，永不 raise。
    """
    now = now or datetime.now(timezone.utc)
    p = path if path is not None else PENDING_LOG_PATH
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        _logger.warning("pending 文件不存在: %s", p)
        return []
    except OSError as e:
        _logger.warning("pending 文件不可读(忽略): %s", e)
        return []

    out: list[StalePending] = []
    for line in lines:
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        ts = _parse_ts(d.get("ts"))
        if ts is None:
            continue
        state = d.get("state")
        if state not in _OPEN_STATES:
            continue  # approved_by_daemon 等已决记录不关心
        hours = (now - ts).total_seconds() / 3600.0
        if hours >= stale_hours:
            out.append(
                StalePending(
                    ts=d.get("ts") or "",
                    action=str(d.get("action") or "(unknown)"),
                    mode=str(d.get("mode") or "(unknown)"),
                    state=str(state or "(missing)"),
                    hours_pending=round(hours, 1),
                    params=d.get("params") if isinstance(d.get("params"), dict) else {},
                )
            )
    out.sort(key=lambda x: x.hours_pending, reverse=True)
    return out


def _escalate_one(item: StalePending) -> bool:
    """单条升级广播（best-effort）。返回是否成功。"""
    try:
        from lingclaude.coordination.alert import send_lingbus_alert

        send_lingbus_alert(
            subject=f"[灰区超时] {item.action} 已挂起 {item.hours_pending:.0f}h 无人处理",
            body=(
                f"ts={item.ts}\naction={item.action} mode={item.mode}\n"
                f"hours_pending={item.hours_pending}\n"
                f"pending 落盘: {PENDING_LOG_PATH}\n"
                "请人工审批或显式关闭（补 state=approved_by_daemon / 拒绝留痕）。"
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001 — 灰区通路永不 raise
        _logger.warning("灰区超时升级广播失败(忽略): %s", e)
        return False


def escalate_stale(now: datetime | None = None) -> list[StalePending]:
    """对超过 ESCALATE_HOURS 的未决记录逐条广播，返回已处理清单。"""
    stale = scan_stale_pendings(stale_hours=ESCALATE_HOURS, now=now)
    for item in stale:
        item.escalated = _escalate_one(item)
    return stale


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：摘要 / --escalate。"""
    import argparse

    parser = argparse.ArgumentParser(description="灰区 pending 看门狗")
    parser.add_argument("--escalate", action="store_true", help="对超时记录触发总线升级广播")
    parser.add_argument("--stale-hours", type=float, default=STALE_HOURS, help="超时阈值（小时）")
    args = parser.parse_args(argv)

    stale = scan_stale_pendings(stale_hours=args.stale_hours)
    if not stale:
        print(f"OK: 无超过 {args.stale_hours}h 的未决 pending")
        return 0
    print(f"发现 {len(stale)} 条超时未决 pending（阈值 {args.stale_hours}h）:")
    for it in stale[:10]:
        mark = "已广播" if it.escalated else ""
        print(f"  - {it.ts}  {it.action}  {it.hours_pending}h  {mark}")
    if len(stale) > 10:
        print(f"  ... 另有 {len(stale) - 10} 条")
    if args.escalate:
        handled = escalate_stale()
        ok = sum(1 for x in handled if x.escalated)
        print(f"升级广播完成: {ok}/{len(handled)} 成功（best-effort，失败见 WARNING 日志）")
    return 1  # 有超时未决 → 非零退出，便于 cron/hook 感知


if __name__ == "__main__":
    raise SystemExit(main())

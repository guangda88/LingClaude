#!/usr/bin/env python3
"""Proxy Backend Health Checker - proxy backend 闲置检测 + 建设法则超时告警

建设法则（CRUSH.md）：
  接入超时 24h -> 黄灯告警
  30天未接入 -> 橙灯 DEPRECATED
  90天未接入 -> 红灯删除

调用方式:
  python3 proxy_backend_health.py              # 检查并报告
  python3 proxy_backend_health.py --json        # JSON 输出
  python3 proxy_backend_health.py --record      # 记录到 lingmemory
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# lingxi backends.json 路径
BACKENDS_JSON = "/home/ai/lingxi/backends.json"

# 建设法则阈值
YELLOW_HOURS = 24
ORANGE_DAYS = 30
RED_DAYS = 90

# 各 backend 的"建设日期"（从 git log 首次添加日期获取，或手动维护）
# 格式: backend_name -> ISO date string (首次建设日期)
BACKEND_BUILD_DATES: dict[str, str] = {
    "lingclaude": "2026-06-16",
    "lingcreate": "2026-06-20",
    "lingzhi": "2026-07-01",
    "lingresearch": "2026-07-01",
    "lingminopt": "2026-07-01",
    "lingyang": "2026-07-01",
    "lingtongask": "2026-07-01",
    "lingflow": "2026-06-20",
    "zai-mcp": "2026-07-01",
}

CST = timezone(timedelta(hours=8))


def load_backends_config() -> dict:
    """加载 backends.json"""
    try:
        with open(BACKENDS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": str(e)}


def get_backend_status_via_proxy() -> dict:
    """通过 mcp_proxy status 获取 backend 运行状态"""
    # 这里不能直接调 MCP，返回空字典由调用方填充
    # 实际使用时由调用方传入 proxy_status
    return {}


def calculate_age_days(build_date_str: str) -> int:
    """计算从建设日期到现在的天数"""
    try:
        build_date = datetime.fromisoformat(build_date_str).replace(tzinfo=CST)
        now = datetime.now(CST)
        return (now - build_date).days
    except (ValueError, TypeError):
        return -1


def determine_lamp(age_days: int, running: bool, last_used: str | None) -> str:
    """确定信号灯状态

    优先级：红灯 > 橙灯 > 黄灯 > 绿灯
    """
    # 红灯：90天未接入 或 建设后从未运行且超过90天
    if age_days >= RED_DAYS:
        return "RED"

    # 橙灯：30天未接入
    if age_days >= ORANGE_DAYS:
        return "ORANGE"

    # 黄灯：24h未接入（检查 last_used）
    if last_used:
        try:
            last = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_since = (now - last).total_seconds() / 3600
            if hours_since >= YELLOW_HOURS:
                return "YELLOW"
        except (ValueError, TypeError):
            pass

    # 如果从未使用过且建设超过24h
    if not last_used and age_days >= 1:
        return "YELLOW"

    return "GREEN"


def check_backends(proxy_status: dict | None = None) -> list[dict]:
    """检查所有 backend 状态

    Args:
        proxy_status: 从 mcp_proxy status 获取的状态字典
    """
    config = load_backends_config()
    if "error" in config:
        return [{"error": config["error"]}]

    backends_cfg = config.get("backends", {})
    proxy_backends = proxy_status or {}
    results = []

    for name, cfg in backends_cfg.items():
        build_date_str = BACKEND_BUILD_DATES.get(name, "2026-07-01")
        age_days = calculate_age_days(build_date_str)

        status_info = proxy_backends.get(name, {})
        running = status_info.get("running", False)
        last_used = status_info.get("last_used")
        last_error = status_info.get("last_error")
        tool_count = status_info.get("tool_count")

        lamp = determine_lamp(age_days, running, last_used)

        results.append({
            "backend": name,
            "description": cfg.get("description", ""),
            "lamp": lamp,
            "running": running,
            "tool_count": tool_count,
            "last_used": last_used,
            "last_error": last_error,
            "build_date": build_date_str,
            "age_days": age_days,
            "action": lamp_action(lamp, name, age_days),
        })

    return results


def lamp_action(lamp: str, name: str, age_days: int) -> str:
    """信号灯对应的行动建议"""
    if lamp == "RED":
        return f"🔴 删除建议: {name} 已建设 {age_days} 天，超过 {RED_DAYS} 天阈值，从未有效接入运行时"
    elif lamp == "ORANGE":
        return f"🟠 DEPRECATED: {name} 已建设 {age_days} 天，超过 {ORANGE_DAYS} 天阈值，标记 DEPRECATED"
    elif lamp == "YELLOW":
        return f"🟡 告警: {name} 接入超时，24h 内未使用"
    return f"🟢 正常: {name} 在使用中"


def format_report(results: list[dict]) -> str:
    """格式化报告"""
    lines = ["=" * 60, "Proxy Backend Health Report", f"Generated: {datetime.now(CST).isoformat()}", "=" * 60, ""]

    # 按信号灯排序：RED > ORANGE > YELLOW > GREEN
    lamp_order = {"RED": 0, "ORANGE": 1, "YELLOW": 2, "GREEN": 3}
    results_sorted = sorted(results, key=lambda x: lamp_order.get(x.get("lamp", "GREEN"), 4))

    for r in results_sorted:
        if "error" in r:
            lines.append(f"❌ ERROR: {r['error']}")
            continue
        lines.append(f"[{r['lamp']}] {r['backend']} ({r['description']})")
        lines.append(f"  running={r['running']} tools={r['tool_count']} age={r['age_days']}d")
        if r["last_used"]:
            lines.append(f"  last_used={r['last_used']}")
        if r["last_error"]:
            lines.append(f"  last_error={r['last_error']}")
        lines.append(f"  -> {r['action']}")
        lines.append("")

    # 汇总
    summary = {"GREEN": 0, "YELLOW": 0, "ORANGE": 0, "RED": 0}
    for r in results:
        if "lamp" in r:
            summary[r["lamp"]] = summary.get(r["lamp"], 0) + 1

    lines.append("-" * 40)
    lines.append(f"汇总: 🟢{summary['GREEN']} 🟡{summary['YELLOW']} 🟠{summary['ORANGE']} 🔴{summary['RED']}")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    record = "--record" in args

    # 如果没有传入 proxy_status，尝试留空（调用方应传入）
    results = check_backends(proxy_status={})

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_report(results))

    if record:
        print("\n[record] 请通过 lm_create 记录本报告", file=sys.stderr)


if __name__ == "__main__":
    main()

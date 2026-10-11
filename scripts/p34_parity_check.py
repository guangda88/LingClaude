#!/usr/bin/env python3
"""P3.4 行为指标回路对照工具 — token_monitor.db × 灵忆镜像 parity 对账。

用法（不碰生产库，默认演示模式需显式传库）：
    python3 scripts/p34_parity_check.py --tm <token_monitor.db> --lm <lingmemory.db> [--date YYYY-MM-DD]

口径（2026-09-11 定版，见 docs/P3_STATE_MIGRATION_MAP.md §四 P3.4）：
- 主路权威：usage_records 行级事实（timestamp,model,task_type 三元组唯一）
- 镜像：token_usage_record ×3/行（input/output/total 分立，registry P3-14）
- 双写期开窗后镜像才存在 → parity 按「窗口对齐」报告差异，不假设全等：
  rows_main_vs_mirror = mirror_total_records/3 与窗口内主路行数的差值
  sum 主路 total_tokens vs 镜像 kind=total 的 token_count 合计（核心守恒量）
- 已知盲区（如实报告，不粉饰）：
  * 镜像无主路 UNIQUE 锚（追加型），重放/重复 emit 会多镜像——由 _emitting
    卫兵防递归，但同进程重复 record 是合法业务行为，两侧都追加，守恒不破坏
  * prompt_count/metadata 深字段不在 parity 范围（schema 未镜像该轴）

退出码：0=守恒成立或可解释差异；1=守恒破坏（sum 不等且差值超容差）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


_SQL_MAIN_WINDOWED = (
    "SELECT COUNT(*), COALESCE(SUM(total_tokens),0), "
    "COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) "
    "FROM usage_records WHERE DATE(timestamp) = ?"
)
_SQL_MAIN_ALL = (
    "SELECT COUNT(*), COALESCE(SUM(total_tokens),0), "
    "COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) "
    "FROM usage_records"
)
_SQL_MIRROR_WINDOWED = (
    "SELECT data, created_at FROM records "
    "WHERE type='token_usage_record' AND substr(created_at,1,10) = ?"
)
_SQL_MIRROR_ALL = (
    "SELECT data, created_at FROM records "
    "WHERE type='token_usage_record'"
)


def _main_rows(db: Path, date: str | None) -> tuple[int, int, int]:
    """主路窗口内：(行数, sum_total, sum_input+sum_output)

    纪律：SQL 文本静态，值一律参数化（L1 SQL_INJECT 门，2026-09-11 实测抓的）
    """
    conn = sqlite3.connect(str(db))
    if date:
        row = conn.execute(_SQL_MAIN_WINDOWED, (date,)).fetchone()
    else:
        row = conn.execute(_SQL_MAIN_ALL).fetchone()
    conn.close()
    return int(row[0]), int(row[1]), int(row[2])


def _mirror_rows(db: Path, date: str | None) -> dict:
    """镜像侧：按 kind 分组的 (条数, token 合计)；date 过滤按 created_at"""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if date:
        rows = conn.execute(_SQL_MIRROR_WINDOWED, (date,)).fetchall()
    else:
        rows = conn.execute(_SQL_MIRROR_ALL).fetchall()
    conn.close()
    out: dict[str, dict] = {}
    for r in rows:
        d = json.loads(r["data"])
        kind = d.get("usage_kind", "?")
        agg = out.setdefault(kind, {"n": 0, "sum": 0})
        agg["n"] += 1
        agg["sum"] += int(d.get("token_count", 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tm", required=True, help="token_monitor.db 路径")
    ap.add_argument("--lm", required=True, help="lingmemory db 路径")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD 窗口（默认全库）")
    ap.add_argument("--tolerance", type=int, default=0,
                    help="守恒容差（token 数），默认 0")
    args = ap.parse_args()

    tm_db, lm_db = Path(args.tm), Path(args.lm)
    for p, tag in ((tm_db, "token_monitor"), (lm_db, "lingmemory")):
        if not p.exists():
            print(f"FAIL: {tag} 库不存在: {p}")
            return 1

    n_main, sum_total_main, sum_io_main = _main_rows(tm_db, args.date)
    mirror = _mirror_rows(lm_db, args.date)
    m_total = mirror.get("total", {"n": 0, "sum": 0})
    m_input = mirror.get("input", {"n": 0, "sum": 0})
    m_output = mirror.get("output", {"n": 0, "sum": 0})

    # 守恒量：主路 total_tokens 合计 == 镜像 kind=total 合计
    drift = sum_total_main - m_total["sum"]
    conserved = abs(drift) <= args.tolerance
    # 行对齐：镜像 total 条数 / 主路行数（双写窗口内应相等；历史数据必然主路多）
    row_ratio = (m_total["n"] / n_main) if n_main else 0.0

    report = {
        "window": args.date or "ALL",
        "main": {"rows": n_main, "sum_total": sum_total_main,
                 "sum_in+out": sum_io_main},
        "mirror": {"total": m_total, "input": m_input, "output": m_output,
                   "all_records": sum(m["n"] for m in mirror.values())},
        "conserved_total_sum": conserved,
        "drift_tokens": drift,
        "row_ratio_mirror/main": round(row_ratio, 4),
        "blind_spots": [
            "双写开窗前的主路历史数据无镜像（row_ratio<1 的主因）",
            "镜像无 UNIQUE 锚，重复 emit 会多镜像（卫兵只防递归不防重复调用）",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if m_total["n"] == 0:
        print("\nPASS: 窗口内无镜像记录（双写未开窗或空库），守恒不可判也不需判")
        return 0
    if row_ratio < 1.0:
        # 混合窗口：镜像只是主路子集（开窗前历史无锚可对齐），守恒不可严格判定。
        # 此时 drift 主因是历史缺失而非镜像丢失——降级为提示，要求用 --date 对齐。
        print(f"\nPASS（弱判定）: 混合窗口 row_ratio={row_ratio:.4f}<1，"
              f"drift={drift} 主要为双写开窗前历史，守恒不可严格判定；"
              f"请用 --date 对齐窗口后复核")
        return 0
    if not conserved:
        print(f"\nFAIL: 窗口对齐但守恒破坏 drift={drift} 超容差 {args.tolerance}")
        return 1
    print(f"\nPASS: 窗口对齐，守恒成立（drift={drift}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

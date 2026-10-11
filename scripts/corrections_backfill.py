#!/usr/bin/env python3
"""corrections 回填：hard_interrupt 关联同 session 前序 tool_error → corrections 落账。

背景（docs/research/20260922_flywheel_closure_diagnosis.md §M / F5 触发条件①）：
    error_log 35373 条 vs corrections 1 条 —— 「记错」海量、「改对」无账。
    hard_interrupt（1358 条，连续失败守卫触发）本质是**系统纠正信号**：
    「第 N 次失败后强制中止」= 对前序错误路径的否定。将其关联同 session
    紧邻的前一条 tool_error 回填 corrections，让 F5 证据挂钩有数据可吃。

设计约束：
    - source='system_backfill' 显式区分于用户纠正（user/claude），conf=0.6
      （系统推断，非用户明示——诚实标注证据强度）。
    - 幂等：original_error 编入 session 标记，重跑按 session 集合去重，不翻倍。
    - session 级粒度：每 session 一条代表性 correction（中断是会话级事件）。
    - 只回填非 stream 来源：stream 两路本就不记 flywheel（历史保真决策），
      回填口径与 error_log 现存数据一致。

用法：
    python3 scripts/corrections_backfill.py            # 回填（幂等）
    python3 scripts/corrections_backfill.py --dry-run  # 只统计不写
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / "lingclaude/.lingclaude/data_flywheel.db"
SOURCE = "system_backfill"
SESSION_MARK = re.compile(r"^\[session:([0-9a-f]+)\]")


def backfilled_sessions(conn: sqlite3.Connection) -> set[str]:
    """已回填的 session 集合（幂等依据）。"""
    rows = conn.execute(
        "SELECT original_error FROM corrections WHERE source = ?", (SOURCE,)
    ).fetchall()
    out = set()
    for (orig,) in rows:
        m = SESSION_MARK.match(orig or "")
        if m:
            out.add(m.group(1))
    return out


def collect_pairs(conn: sqlite3.Connection) -> list[dict]:
    """每个含 hard_interrupt 的 session → 一条关联记录。

    关联规则：该 session 中**最后一条** tool_error（最贴近中断根因），
    加该 session 的中断次数与首个错误时间（压缩进 correction 文案）。
    """
    sessions = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT session_id FROM error_log "
            "WHERE pattern_type='hard_interrupt' AND session_id != '' "
            "ORDER BY session_id"
        )
    ]
    pairs = []
    for sid in sessions:
        last_err = conn.execute(
            "SELECT pattern_type, file_path, error_message, tool_name, occurred_at "
            "FROM error_log WHERE session_id=? AND pattern_type='tool_error' "
            "ORDER BY occurred_at DESC LIMIT 1",
            (sid,),
        ).fetchone()
        stats = conn.execute(
            "SELECT COUNT(*), MIN(occurred_at) FROM error_log "
            "WHERE session_id=? AND pattern_type='hard_interrupt'",
            (sid,),
        ).fetchone()
        interrupts, first_at = int(stats[0] or 0), stats[1] or ""
        if last_err is None:
            # 无前序可关联错误（如纯 provider 中断）——仍落账，根因标 unknown
            orig = f"[session:{sid}] (无前序 tool_error, provider 中断)"
            corr = (
                f"连续失败硬中断 x{interrupts}（首次 {first_at[:19]}）："
                f"连续模型调用失败触发中止，无具体工具根因可关联"
            )
        else:
            _, fpath, msg, tool, at = last_err
            short = re.sub(r"\s+", " ", (msg or "")[:80])
            orig = f"[session:{sid}] file={fpath} tool={tool} {short}"
            corr = (
                f"连续失败硬中断 x{interrupts}（首次 {first_at[:19]}，末次错误 {at[:19]}）："
                f"{tool} 在 {fpath} 上重复失败后触发中止——"
                f"同路径同工具第 3 次失败前应换策略而非重试"
            )
        pairs.append(
            {
                "session_id": sid,
                "original_error": orig,
                "correction": corr,
                "interrupts": interrupts,
            }
        )
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] db 不存在: {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    try:
        done = backfilled_sessions(conn)
        pairs = collect_pairs(conn)
        todo = [p for p in pairs if p["session_id"] not in done]
        print(
            f"hard_interrupt sessions={len(pairs)}  已回填={len(done)}  待回填={len(todo)}"
        )
        if not todo:
            print("[OK] 无新待回填（幂等）")
            return 0
        if args.dry_run:
            for p in todo[:5]:
                print(f"  [DRY] {p['original_error'][:70]} → {p['correction'][:60]}")
            print(f"[DRY] 共 {len(todo)} 条待写")
            return 0
        now = datetime.now().isoformat()
        rows = [
            (p["original_error"], p["correction"], SOURCE, 0.6, now, "")
            for p in todo
        ]
        with conn:
            conn.executemany(
                "INSERT INTO corrections "
                "(original_error, correction, source, confidence, applied_at, experiment_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        print(f"[OK] 回填 {len(todo)} 条 corrections (source={SOURCE}, conf=0.6)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

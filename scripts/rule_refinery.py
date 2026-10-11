#!/usr/bin/env python3
"""P2 (2026-09-23): RuleExtractor 提炼层接线 —— error_log → 提炼规则。

补上诊断文档 §L.2 的「提炼层从未接线」断点：
- 此前 RuleExtractor 全仓运行期零调用方（仅 __init__ 导出），是死代码。
- 数据流（复用既有管道，不新造）：
      data_flywheel.db error_log（35k+ 条）
        └─ 按 (pattern_type, tool_name) 分组 → FeedbackItem
            └─ RuleExtractor.extract_rules(min_frequency=3)
                └─ LearnedRule(id=refined_<slug>, category=BUG_RISK)
                    └─ knowledge.db add_rule（F3 同名合并/幂等 upsert 生效）

与 flywheel_aggregator 的分工：
  - aggregator：产「高频错误模式」原始规则（top-N 重复，英文错误串）
  - refinery：  产「提炼规则」（RuleExtractor 归纳 code_patterns / 英文
    context_keywords —— 与 P1 中文标签互补，英文关键词检索走这条）

幂等性：同一 (pattern_type, tool_name) → 稳定 slug → add_rule 同 id upsert，
重跑不翻倍。frequency = 组内 error_log 条数。

用法:
  python scripts/rule_refinery.py [--db PATH] [--min-count N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.core.data_flywheel import FLYWHEEL_DB_NAME  # noqa: E402
from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase  # noqa: E402
from lingclaude.self_optimizer.learner.models import (  # noqa: E402
    FeedbackCategory,
    FeedbackItem,
    FeedbackSeverity,
    LearnedRule,
    Pattern,
    ToolType,
)
from lingclaude.self_optimizer.learner.rule_extractor import RuleExtractor  # noqa: E402


def _slug_for(pattern_type: str, tool_name: str) -> str:
    """稳定 slug：同 (pattern_type, tool_name) 幂等。"""
    import hashlib
    raw = f"{pattern_type}|{tool_name}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _error_rows(conn: sqlite3.Connection, min_count: int) -> list[tuple]:
    """按 (pattern_type, tool_name) 分组聚合，返回 count>=min_count 的组。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pattern_type, tool_name, COUNT(*) AS c,
               MAX(occurred_at) AS last_seen
        FROM error_log
        GROUP BY pattern_type, tool_name
        HAVING c >= ?
        ORDER BY c DESC
        """,
        (min_count,),
    )
    return cur.fetchall()


def _items_for_group(conn: sqlite3.Connection, pattern_type: str,
                     tool_name: str) -> list[FeedbackItem]:
    """一组 (pattern_type, tool_name) 的 error_log → FeedbackItem 列表。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT error_message, file_path, occurred_at
        FROM error_log
        WHERE pattern_type = ? AND tool_name = ?
        LIMIT 50
        """,
        (pattern_type, tool_name),
    )
    items: list[FeedbackItem] = []
    # tool_type 是 FeedbackItem 必填字段，但 RuleExtractor 只消费 tool_name
    # （rule_extractor.py:62/135 只用 item.tool_name）。error_log 的 tool_name
    # 是 read/bash/write 等运行时工具，无对应 ToolType 枚举 → 用 STATIC_ANALYZER
    # 占位（不影响提炼结果，实测确认）。
    placeholder_tt = ToolType.STATIC_ANALYZER
    for msg, fpath, _ts in cur.fetchall():
        items.append(
            FeedbackItem(
                tool_name=tool_name,
                tool_type=placeholder_tt,
                rule_id=pattern_type,
                rule_name=f"refined_{pattern_type}",
                category=FeedbackCategory.BUG_RISK,
                severity=FeedbackSeverity.HIGH,
                message=msg or "",
                file_path=fpath or "",
                line=0,
                snippet=(msg or "")[:200],
                confidence=0.8,
            )
        )
    return items


def _build_refined_rule(pattern_type: str, tool_name: str, count: int,
                        last_seen: str, extracted: LearnedRule) -> LearnedRule:
    """RuleExtractor 产出 → 入库规则的最终形态（带 id 与置信度）。"""
    slug = _slug_for(pattern_type, tool_name)
    return LearnedRule(
        id=f"refined_{slug}",
        name=f"提炼规则: {pattern_type} ({tool_name}) ×{count}",
        description=(
            f"[运行经验] {pattern_type} / {tool_name} 高频（近场 {count} 次，"
            f"最后 {last_seen}）：{extracted.description or '见上下文关键词'}"
        ),
        category=FeedbackCategory.BUG_RISK,
        pattern=extracted.pattern,
        tools=(tool_name,) if tool_name else (),
        frequency=count,
        confidence=min(0.9, extracted.confidence),
        quality_score=extracted.quality_score,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="data_flywheel.db 路径（默认项目库）")
    ap.add_argument("--min-count", type=int, default=3, help="分组最小条数（默认 3）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写库")
    args = ap.parse_args()

    if args.db:
        fly_db = args.db
    else:
        fly_db = str(Path(__file__).resolve().parent.parent / ".lingclaude" / FLYWHEEL_DB_NAME)
    if not Path(fly_db).exists():
        print(f"[ERR] data_flywheel.db 不存在: {fly_db}")
        return 1

    conn = sqlite3.connect(fly_db)
    try:
        groups = _error_rows(conn, args.min_count)
        extractor = RuleExtractor(min_frequency=args.min_count, min_confidence=0.5)
        kb = KnowledgeBase()
        total = 0
        for pattern_type, tool_name, count, last_seen in groups:
            items = _items_for_group(conn, pattern_type, tool_name)
            if not items:
                continue
            extracted_rules = extractor.extract_rules(
                items, category=FeedbackCategory.BUG_RISK
            )
            if not extracted_rules:
                continue
            rule = _build_refined_rule(
                pattern_type, tool_name, count, last_seen, extracted_rules[0]
            )
            if args.dry_run:
                print(f"[DRY] {rule.id} — {rule.name}")
                total += 1
                continue
            res = kb.add_rule(rule)
            if res.is_ok:
                total += 1
        print(f"[{'DRY' if args.dry_run else 'OK'}] 提炼规则 {total} 条"
              f"（分组 {len(groups)} 组，min_count={args.min_count}）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

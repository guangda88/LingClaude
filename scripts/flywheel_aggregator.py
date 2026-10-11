#!/usr/bin/env python3
"""P0#2: DataFlywheel top-N 错误模式 → KB 跨会话规则（flywheel_aggregator）。

桥接诊断文档（docs/research/20260922_flywheel_closure_diagnosis.md）§L 指认的
「最宽断点」：error_log 35347 条 vs corrections 0 条 —— 只记错、不固化改对。

数据流：
    data_flywheel.db error_log（35347 条，5 个 core 模块写入）
      └─ DataFlywheel.get_recurring_errors(min_count)   # 全仓此前零调用的桥
          └─ 质量闸（长度/占位名过滤）
              └─ LearnedRule(category=BUG_RISK, type=ANTI_PATTERN,
                             id=flywheel_pattern_<slug>)   # slug 稳定 → 幂等 upsert
                  └─ knowledge.db rules
                      └─ system_prompt_builder 检索注入（读路径复用，不加新管道）

幂等性：同一 (pattern_type, file_path, error_message) 三元组 → 稳定 slug →
重跑仅更新 frequency/confidence，不产生重复行（KB add_rule 同 id upsert）。

用法：
    python3 scripts/flywheel_aggregator.py --dry-run          # 只看不入库
    python3 scripts/flywheel_aggregator.py                    # 聚合并入库
    python3 scripts/flywheel_aggregator.py --min-count 5      # 提高门槛
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

# 项目根入 path（scripts/ 直跑场景）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lingclaude.core.data_flywheel import DataFlywheel  # noqa: E402
from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase  # noqa: E402
from lingclaude.self_optimizer.learner.models import (  # noqa: E402
    FeedbackCategory,
    LearnedRule,
    Pattern,
    PatternType,
)

# ---- 质量闸 ---------------------------------------------------------------
# KB 实测教训：一条「工具错误记录」占位名重复 20392 次（76% 噪声）。
# 聚合入 KB 前必须过滤占位/无实质内容的错误消息。
PLACEHOLDER_PREFIXES = ("工具错误记录", "会话里程碑")
MIN_MESSAGE_LEN = 8  # 过短消息无检索价值

# 置信度映射：频率越高越可信，上限 0.9（留余量给 F5 verify_ledger 证据挂钩）
def confidence_for(count: int) -> float:
    return round(min(0.9, 0.3 + count * 0.05), 2)


def slug_for(pattern_type: str, file_path: str, error_message: str) -> str:
    """稳定 slug：三元组 sha1 前 12 位。跨天/跨次重跑不变，保证幂等。"""
    raw = f"{pattern_type}|{file_path}|{error_message}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def is_placeholder(message: str) -> bool:
    return any(message.startswith(p) for p in PLACEHOLDER_PREFIXES)


# 英文 pattern_type → 中文意图标签（P1 2026-09-23：修「中文查询 LIKE 全漏」）
# 真实用户查询是中文意图（"读文件报错/权限被拒/工具卡住"），而规则
# name/description 是英文错误串 → search_rules 的 LIKE %kw% 对中文查询天然断裂。
# 标签写进 description 头部 + context_keywords（检索端 pattern_json 一并 LIKE），
# 让「错误/失败/权限/工具」等高频中文查询能命中。
_PATTERN_ZH: dict[str, str] = {
    "tool_error": "工具执行错误",
    "hard_interrupt": "连续失败中断",
    "permission_denial": "权限被拒",
    "permission_ask_mode.pending_approval": "权限待批准",
    "permission_config.deny_tools.exact": "工具被禁",
    "permission_session.deny_tools.exact": "会话级工具被禁",
    "permission_strict_mode.non_readonly": "非只读被拒",
    "mv1_pre_send_blocked": "发送前拦截",
    "sub_agent_call": "子代理调用失败",
}


def zh_tag(ptype: str) -> str:
    """pattern_type → 中文标签；未知类型给兜底中文。"""
    return _PATTERN_ZH.get(ptype) or "运行异常"


def build_rule(row: dict) -> LearnedRule:
    """聚合行 → 跨会话规则。"""
    ptype = row["pattern_type"] or "unknown"
    fpath = (row["file_path"] or "").strip() or "*"
    msg = (row["error_message"] or "").strip()
    count = int(row["count"])
    slug = slug_for(ptype, fpath, msg)
    short = re.sub(r"\s+", " ", msg[:80])
    tag = zh_tag(ptype)
    return LearnedRule(
        id=f"flywheel_pattern_{slug}",
        name=f"高频错误模式: {ptype} ×{count}",
        description=f"[{tag}] [{ptype}] {fpath} — {short}（近场重复 {count} 次，"
                    f"最后出现 {row['last_seen']}）",
        category=FeedbackCategory.BUG_RISK,
        pattern=Pattern(
            file_patterns=(fpath,),
            # P1: context_keywords 纳入中文标签，检索端可命中（见 search_rules）
            context_keywords=(tag, ptype),
            tool_support=(),
        ),
        tools=(),
        frequency=count,
        confidence=confidence_for(count),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-count", type=int, default=2,
                    help="视为重复模式的最低出现次数（默认 2）")
    ap.add_argument("--limit", type=int, default=20,
                    help="最多聚合 top-N 模式（默认 20，get_recurring_errors 原生上限）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将写入的规则，不入库")
    args = ap.parse_args()

    fw = DataFlywheel()
    res = fw.get_recurring_errors(min_count=args.min_count, limit=args.limit)
    if not res.success or res.data is None:
        print(f"[FAIL] get_recurring_errors: {res.error}")
        return 1
    rows = res.data

    kept, dropped = [], []
    for r in rows:
        msg = (r.get("error_message") or "").strip()
        if len(msg) < MIN_MESSAGE_LEN or is_placeholder(msg):
            dropped.append(r)
            continue
        kept.append(r)

    print(f"桥查询: min_count={args.min_count} → {len(rows)} 个重复模式 "
          f"(质量闸: 保留 {len(kept)}, 过滤 {len(dropped)})")

    if args.dry_run:
        for r in kept:
            rule = build_rule(r)
            print(f"  [DRY] {rule.id}  conf={rule.confidence}  freq={rule.frequency}")
        for r in dropped:
            print(f"  [DROP] {r['pattern_type']}  {(r['error_message'] or '')[:40]}")
        return 0

    kb = KnowledgeBase()
    written = 0
    for r in kept:
        rule = build_rule(r)
        w = kb.add_rule(rule)
        if w.success:
            written += 1
        else:
            print(f"  [WARN] 写入失败 {rule.id}: {w.error}")
    print(f"[OK] {datetime.now().isoformat(timespec='seconds')} "
          f"入 KB {written}/{len(kept)} 条 flywheel_pattern_* 跨会话规则")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
灵码 reasoning蒸馏器 — 从LLM的思考过程中提取推理模式

58909条reasoning藏着LLM怎么思考的。
不是问LLM"你怎么想的"（反蒸馏），是从它已经想过的轨迹中提取。

出入：reasoning.thinking in → 推理模式rule out
流转：pattern匹配 → 查重 → 入库
"""
import json
import re
import sys
import sqlite3
import os
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.api import LingMemoryAPI


MEMBER_DBS = {
    "lingclaude": "~/.crush/crush.db",
    "lingflow": "~/lingflow/.crush/crush.db",
    "lingmessage": "~/lingmessage/.crush/crush.db",
    "lingzhi": "~/lingzhi/.crush/crush.db",
    "lingresearch": "~/lingresearch/.crush/crush.db",
    "lingminopt": "~/lingminopt/.crush/crush.db",
    "lingxi": "~/lingxi/.crush/crush.db",
    "lingcreate": "~/lingcreate/.crush/crush.db",
    "lingweb": "~/lingweb/.crush/crush.db",
    "lingtongask": "~/lingtongask/.crush/crush.db",
    "lingyang": "~/lingyang/.crush/crush.db",
    "zhibridge": "~/zhibridge/.crush/crush.db",
}


# 推理模式关键词→规则
REASONING_PATTERNS = [
    # 决策模式
    (r"(?:先|首先).*(?:检查|确认|看)", "推理模式：遇到问题先检查再行动"),
    (r"(?:不确定|可能|也许|大概)", "推理模式：不确定时用保守假设"),
    (r"(?:应该|需要|必须).*(?:先|首先)", "推理模式：识别前置条件"),
    (r"(?:权衡|取舍|trade.?off)", "推理模式：显式权衡利弊"),
    (r"(?:简单|简洁|最小).*(?:方案|做法|改动)", "推理模式：优先最小改动"),
    (r"(?:复用|已有|现成)", "推理模式：优先复用已有方案"),
    
    # 诊断模式
    (r"(?:根因|根本原因|root.?cause)", "推理模式：追根因不治症状"),
    (r"(?:假设|猜测).*(?:验证|确认)", "推理模式：假设驱动验证"),
    (r"(?:排除|不是).*(?:因为|由于)", "推理模式：排除法定位"),
    (r"(?:对比|比较|diff)", "推理模式：对比分析定位差异"),
    
    # 防御模式
    (r"(?:安全|风险|危险|漏洞)", "推理模式：识别安全风险"),
    (r"(?:边界|极端|异常|空值|null)", "推理模式：考虑边界情况"),
    (r"(?:失败|错误|异常).*(?:处理|catch|try)", "推理模式：预判失败路径"),
    (r"(?:权限|授权|鉴权)", "推理模式：检查权限边界"),
    
    # 架构模式
    (r"(?:拆分|解耦|分离|独立)", "推理模式：识别可拆分的职责"),
    (r"(?:重复|冗余| duplicated)", "推理模式：识别重复代码"),
    (r"(?:配置|参数|可配)", "推理模式：识别可配置项"),
    (r"(?:接口|抽象|协议)", "推理模式：识别抽象边界"),
    (r"(?:性能|优化|效率|快)", "推理模式：识别性能瓶颈"),
    
    # 元认知
    (r"(?:理解错了|误解|不对)", "推理模式：发现理解错误时回退"),
    (r"(?:用户想要|用户需要|意图)", "推理模式：显式确认用户意图"),
    (r"(?:跳过|不应该|不要)", "推理模式：识别不该做的事"),
    (r"(?:足够|够了|不需要)", "推理模式：判断何时停止"),
    (r"(?:过度|过度设计|过度优化)", "推理模式：警惕过度设计"),
    
    # 协作
    (r"(?:通知|告知|回复|broadcast)", "推理模式：需要通知其他成员"),
    (r"(?:等.*确认|等.*批准|escalate)", "推理模式：需要人工确认"),
    (r"(?:文档|记录|handover)", "推理模式：需要记录结论"),
]


def extract_reasoning_rules(thinking: str) -> list[str]:
    """从一条reasoning中提取推理模式"""
    if not thinking or len(thinking) < 30:
        return []
    rules = []
    for pattern, rule in REASONING_PATTERNS:
        if re.search(pattern, thinking, re.IGNORECASE):
            rules.append(rule)
    return list(set(rules))


def run_reasoning_distillation():
    """从全族58909条reasoning中提取推理模式"""
    api = LingMemoryAPI(member="lingclaude")
    stats = {"scanned": 0, "matched": 0, "new": 0, "dup": 0}
    rule_counter = Counter()

    for member, db_path in MEMBER_DBS.items():
        expanded = os.path.expanduser(db_path)
        if not os.path.exists(expanded):
            continue

        conn = sqlite3.connect(expanded)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT json_extract(value, '$.data.thinking') as thinking "
            "FROM messages, json_each(messages.parts) "
            "WHERE json_extract(value, '$.type') = 'reasoning' "
            "AND length(json_extract(value, '$.data.thinking')) > 50"
        ).fetchall()

        for r in rows:
            thinking = r["thinking"] or ""
            stats["scanned"] += 1
            rules = extract_reasoning_rules(thinking)
            if rules:
                stats["matched"] += 1
            for rule in rules:
                rule_counter[rule] += 1

        conn.close()

    # 入库：按频率排序，高频=validated，低频=hypothesized
    for rule, freq in rule_counter.most_common():
        existing = api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
            "AND json_extract(data, '$.rule')=?", (rule,)).fetchone()[0]
        if existing == 0:
            confidence = min(1.0, freq / 100)
            api.lm.create(type="coding_rule", data={
                "rule": rule, "evidence": [f"reasoning_distill_{freq}条"],
                "category": "pattern", "confidence": confidence,
                "distilled": True, "source": "reasoning_miner",
                "frequency": freq,
            }, created_by="lingclaude")
            stats["new"] += 1
        else:
            stats["dup"] += 1

    total = api.lm.conn.execute(
        "SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
    print(f"\n=== reasoning蒸馏结果 ===")
    print(f"扫描: {stats['scanned']}条reasoning")
    print(f"匹配: {stats['matched']}条含模式")
    print(f"新rule: {stats['new']}条")
    print(f"重复: {stats['dup']}条")
    print(f"coding_rule总计: {total}条")
    print(f"\nTOP 10 推理模式（按频率）:")
    for rule, freq in rule_counter.most_common(10):
        print(f"  [{freq:>5}次] {rule}")
    api.close()


if __name__ == "__main__":
    run_reasoning_distillation()

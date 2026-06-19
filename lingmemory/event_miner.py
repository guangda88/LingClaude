"""
灵忆V2.0 Phase 3: event自动挖掘

从20000条trace自动提炼rule(飞轮自转)
三种挖掘模式:
  1. 高频失败: 同一model@provider连续失败 → 生成稳定性rule
  2. 切换模式: 用户请求A但切到B → 生成fallback rule
  3. 语言+结果模式: 某语言在某场景高频失败 → 生成编码rule
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter, defaultdict

MAIN_DB = Path(__file__).parent / "lingmemory.db"
RULES_DB = Path(__file__).parent / "lingmemory_rules.db"


def mine_failure_patterns():
    """挖掘高频失败模式"""
    conn = sqlite3.connect(str(MAIN_DB))
    conn.row_factory = sqlite3.Row

    rules = []

    # 1. code_trace: 按language+test_result统计
    rows = conn.execute("""
        SELECT json_extract(data,'$.language') as lang,
               json_extract(data,'$.test_result') as result,
               COUNT(*) as cnt
        FROM records WHERE type='code_trace'
        GROUP BY lang, result HAVING cnt >= 5
        ORDER BY cnt DESC
    """).fetchall()

    for row in rows:
        lang = row["lang"] or "unknown"
        result = row["result"] or "unknown"
        cnt = row["cnt"]
        if result in ("fail", "error"):
            rules.append({
                "rule": f"{lang}编码失败率({cnt}条{result}): 检查常见错误模式",
                "category": "coding",
                "evidence": f"code_trace: {cnt}条{lang}={result}",
                "confidence": min(cnt / 100, 0.9),
            })

    # 2. code_trace: 按prompt关键词+result统计
    rows = conn.execute("""
        SELECT json_extract(data,'$.prompt') as prompt,
               json_extract(data,'$.test_result') as result
        FROM records WHERE type='code_trace' AND json_extract(data,'$.test_result') IN ('fail','error')
    """).fetchall()

    keyword_fails = Counter()
    for row in rows:
        prompt = row["prompt"] or ""
        result = row["result"] or ""
        # 提取关键词
        for kw in ["stream", "proxy", "auth", "import", "json", "sql", "config", "error", "fix", "mcp"]:
            if kw in prompt.lower():
                keyword_fails[f"{kw}:{result}"] += 1

    for key, cnt in keyword_fails.most_common(10):
        if cnt >= 5:
            kw, result = key.split(":")
            rules.append({
                "rule": f"{kw}相关操作高频{result}({cnt}次): 重点检查{kw}的边界case",
                "category": "coding",
                "evidence": f"code_trace关键词: {kw}={result} x{cnt}",
                "confidence": min(cnt / 50, 0.8),
            })

    # 3. audit_finding: 按check_id+severity统计
    rows = conn.execute("""
        SELECT json_extract(data,'$.check_id') as cid,
               json_extract(data,'$.severity') as sev,
               COUNT(*) as cnt
        FROM records WHERE type='audit_finding'
        GROUP BY cid, sev HAVING cnt >= 3
        ORDER BY cnt DESC LIMIT 15
    """).fetchall()

    for row in rows:
        cid = row["cid"] or "unknown"
        sev = row["sev"] or "unknown"
        cnt = row["cnt"]
        rules.append({
            "rule": f"审计高频问题: {cid} 出现{cnt}次({sev})",
            "category": "audit",
            "evidence": f"audit_finding: {cid}={sev} x{cnt}",
            "confidence": min(cnt / 50, 0.8),
        })

    conn.close()
    return rules


def mine_switch_patterns():
    """挖掘model切换模式(从proxy21的flywheel DB)"""
    flywheel_db = Path.home() / ".lingclaude" / "proxy21_flywheel.db"
    if not flywheel_db.exists():
        return []

    conn = sqlite3.connect(str(flywheel_db))
    conn.row_factory = sqlite3.Row

    rules = []

    # 找频繁切换的model
    rows = conn.execute("""
        SELECT model, provider, COUNT(*) as cnt
        FROM code_trace
        WHERE switched = 1
        GROUP BY model, provider HAVING cnt >= 2
        ORDER BY cnt DESC
    """).fetchall()

    for row in rows:
        model = row["model"] or "unknown"
        provider = row["provider"] or "unknown"
        cnt = row["cnt"]
        rules.append({
            "rule": f"{model}@{provider} 经常被切换({cnt}次): 用户请求但不可用,考虑降优先级",
            "category": "ops",
            "evidence": f"flywheel: {model}@{provider} switched x{cnt}",
            "confidence": min(cnt / 20, 0.7),
        })

    # 找频繁失败的model@provider
    rows = conn.execute("""
        SELECT model, provider, COUNT(*) as cnt
        FROM code_trace
        WHERE status != 200 AND model != ''
        GROUP BY model, provider HAVING cnt >= 3
        ORDER BY cnt DESC LIMIT 10
    """).fetchall()

    for row in rows:
        model = row["model"] or "unknown"
        provider = row["provider"] or "unknown"
        cnt = row["cnt"]
        rules.append({
            "rule": f"{model}@{provider} 频繁失败({cnt}次): 考虑移到池末尾或移除",
            "category": "ops",
            "evidence": f"flywheel: {model}@{provider} failed x{cnt}",
            "confidence": min(cnt / 15, 0.85),
        })

    conn.close()
    return rules


def save_mined_rules(rules):
    """保存挖掘出的rule到rule库"""
    if not rules:
        print("没有新rule可保存")
        return 0

    conn = sqlite3.connect(str(RULES_DB))
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    failed = 0

    for r in rules:
        rid = str(uuid.uuid4())
        data = {
            "rule": r["rule"],
            "category": r["category"],
            "evidence": r["evidence"],
            "confidence": r["confidence"],
            "source": "auto_mined",
        }
        try:
            conn.execute(
                "INSERT INTO records (id, type, state, data, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (rid, "coding_rule", "hypothesized", json.dumps(data, ensure_ascii=False),
                 "lingclaude_miner", now, now))
            saved += 1
        except Exception as e:
            print(f"保存失败: {e} (rule: {r['rule'][:50]})")
            failed += 1

    conn.commit()
    conn.close()
    print(f"保存 {saved}/{len(rules)} 条mined rule")
    if failed:
        print(f"警告: {failed} 条保存失败")
    return saved


def mine_all():
    """执行全部挖掘"""
    print("=== Phase 3: event自动挖掘 ===\n")

    print("[1] 失败模式挖掘...")
    failure_rules = mine_failure_patterns()
    print(f"  发现 {len(failure_rules)} 条失败模式rule")

    print("\n[2] 切换模式挖掘...")
    switch_rules = mine_switch_patterns()
    print(f"  发现 {len(switch_rules)} 条切换模式rule")

    all_rules = failure_rules + switch_rules
    print(f"\n总计: {len(all_rules)} 条候选rule")

    if all_rules:
        print("\n--- TOP 10 ---")
        for r in all_rules[:10]:
            print(f"  [{r['category']}] {r['rule'][:70]}")

        print(f"\n保存到rule库...")
        saved = save_mined_rules(all_rules)
        print(f"\n飞轮自转完成: {saved} 条新rule入库(状态=hypothesized)")
    else:
        print("无新rule")


if __name__ == "__main__":
    mine_all()

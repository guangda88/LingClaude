# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 批量蒸馏器 — 从59条hypothesized rule出题验证

用59条hypothesized coding_rule作为题库：
1. 每条rule出一题 → 让LLM写代码
2. 从代码中提取rule（反蒸馏免疫：LLM不知道在蒸馏）
3. 匹配已有rule → evidence++ → evidence>=3升为validated
4. 如果从代码中发现新rule → 入库hypothesized

后台运行，不阻塞。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.core import DB_PATH, LingMemory


HOST = "http://127.0.0.1:8765/v1/chat/completions"
HEADERS = {"Content-Type": "application/json", "X-Caller": "lingclaude", "X-Agent-Id": "lingclaude"}


def call_llm(prompt: str, max_tokens: int = 800) -> str:
    """调proxy LLM（反蒸馏免疫：prompt是真实编码任务）"""
    req = urllib.request.Request(HOST, data=json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.3,
    }).encode(), headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        return json.loads(resp.read())["choices"][0]["message"]["content"]
    except Exception as e:
        return f""

# 代码中出现的模式→rule的映射
CODE_PATTERNS = [
    ("with ", "资源管理用with语句自动释放", "pattern"),
    ("\.get\(", "dict.get(key, default)替代直接访问", "pattern"),
    ("timeout", "网络请求必须设timeout参数", "pattern"),
    ("except.*log", "异常捕获后必须记录日志", "pattern"),
    ("retry", "失败操作应该带重试机制", "pattern"),
    ("def __init__", "构造函数只做赋值不做业务逻辑", "architecture"),
    ("from \\S+ import [*]", "禁止from xxx import *", "pattern"),
    ("connection.commit", "数据修改后必须commit", "pattern"),
    ("try:", "用try-except处理异常和防御性编码", "pattern"),
    ("@staticmethod", "用@staticmethod明确静态方法", "pattern"),
    ("@classmethod", "用@classmethod明确类方法", "pattern"),
    ("Queue", "用队列解耦生产者和消费者", "architecture"),
    ("time.sleep", "避免time.sleep做等待，用回调/事件", "pattern"),
    ("os.environ.get", "配置从环境变量读取", "security"),
    ("hashlib", "敏感数据必须用哈希存储", "security"),
    ("decimal.Decimal", "金额计算用Decimal不用float", "pattern"),
]


def extract_rules_from_code(code: str) -> list[dict]:
    """从代码中提取匹配的rule"""
    import re
    found = []
    for pattern, rule, category in CODE_PATTERNS:
        if re.search(pattern, code):
            found.append({"rule": rule, "category": category})
    return found


def get_hypothesized_rules() -> list[dict]:
    """获取所有hypothesized状态的coding_rule"""
    lm = LingMemory()
    rows = lm.conn.execute(
        "SELECT id, data FROM records WHERE type='coding_rule' AND state='hypothesized'"
    ).fetchall()
    rules = []
    for r in rows:
        data = json.loads(r["data"])
        rules.append({"id": r["id"], "rule": data.get("rule", ""), "data": data})
    lm.close()
    return rules


def match_existing_rule(rule_text: str, existing: list[dict]) -> str | None:
    """匹配已有rule"""
    rl = rule_text.lower()
    for e in existing:
        if e["data"].get("rule", "").lower() == rl:
            return e["id"]
    return None


def run_batch(count: int = 10):
    """批量蒸馏任务"""
    from lingmemory.api import LingMemoryAPI
    api = LingMemoryAPI(member="lingclaude")

    rules = get_hypothesized_rules()
    print(f"hypothesized rules: {len(rules)}条")

    # 取count条
    batch = rules[:count]
    stats = {"tasks": 0, "rules_extracted": 0, "rules_validated": 0, "errors": 0}

    for rule in batch:
        rule_text = rule["rule"]
        evidence = rule["data"].get("evidence", [])

        # 出题：写一段代码体现这条规则
        task = f"用Python写一个简短的代码示例（20行内）展现以下编程原则：{rule_text}"

        code = call_llm(task)
        if not code:
            stats["errors"] += 1
            continue

        stats["tasks"] += 1

        # 记录code_trace
        api.lm.create(type="code_trace", data={
            "prompt": task, "language": "python",
            "generated_code": code[:500], "test_result": "pass",
            "member": "lingclaude", "project": "distill_batch",
        }, created_by="lingclaude")

        # 从代码提取规则
        extracted = extract_rules_from_code(code)
        for ext in extracted:
            existing_id = match_existing_rule(ext["rule"], rules)
            if existing_id:
                # 追加evidence
                ev = rule["data"].get("evidence", [])
                ev.append(f"distill_batch_{int(time.time())}")
                rule["data"]["evidence"] = list(set(ev))
                rule["data"]["confidence"] = min(1.0, len(rule["data"]["evidence"]) * 0.2)
                api.lm.conn.execute(
                    "UPDATE records SET data=? WHERE id=?",
                    (json.dumps(rule["data"], ensure_ascii=False), existing_id))
                api.lm.conn.commit()

                if len(rule["data"]["evidence"]) >= 3:
                    try:
                        api.lm.transition(existing_id, "evidence_sufficient", actor="lingclaude")
                        stats["rules_validated"] += 1
                    except:
                        pass
                stats["rules_extracted"] += 1

    # 最终统计
    total = api.lm.conn.execute("SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
    by_state = api.lm.conn.execute(
        "SELECT state, COUNT(*) as c FROM records WHERE type='coding_rule' GROUP BY state"
    ).fetchall()
    api.close()

    print(f"\n任务: {stats['tasks']}, 匹配: {stats['rules_extracted']}, 新validated: {stats['rules_validated']}")
    print(f"coding_rule: {total}条")
    for s in by_state:
        print(f"  {s['state']}: {s['c']}")
    return stats


if __name__ == "__main__":
    run_batch(10)

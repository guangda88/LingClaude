# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 自动出题器 — 为蒸馏进程不断产生新题

当查重率>90%时自动补充新题，来源：
1. hypothesized rule（需要更多evidence）
2. 已有rule的交叉组合（生成更难的问题）
3. V1.0概念组合（领域深度）
4. 难度自动升级
"""

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.core import LingMemory

# 领域维度（组合出题用）
DOMAINS = ["Python", "Go", "SQL", "API设计", "安全", "性能", "架构", "测试"]
CONCEPTS = ["异常处理", "并发控制", "资源管理", "状态流转", "缓存策略",
            "配置管理", "日志审计", "权限验证", "数据一致性", "服务降级"]
PATTERNS = ["工厂模式", "观察者", "策略模式", "装饰器", "单例", "适配器",
            "中间件", "管道", "事件驱动", "插件化"]
ACTIONS = ["怎么写", "怎么测试", "怎么优化", "怎么重构", "怎么设计"]
LEVELS = ["入门", "进阶", "高级", "专家", "灵元"]


def get_hypothesized_rules(lm, limit=20) -> list[str]:
    """从hypothesized rule中生成验证题目"""
    rows = lm.conn.execute(
        "SELECT data FROM records WHERE type='coding_rule' AND state='hypothesized' "
        "ORDER BY json_extract(data, '$.confidence') ASC LIMIT ?", (limit,)
    ).fetchall()
    tasks = []
    for r in rows:
        data = json.loads(r["data"])
        rule = data.get("rule", "")
        if rule:
            tasks.append(f"写一段代码验证这条规则是对的: {rule[:80]}")
    return tasks


def get_audit_findings_tasks(lm, limit=15) -> list[str]:
    """从audit_finding生成修复题"""
    rows = lm.conn.execute(
        "SELECT data FROM records WHERE type='audit_finding' AND state='open' "
        "AND json_extract(data, '$.severity') IN ('critical', 'high') "
        "ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()
    tasks = []
    for r in rows:
        data = json.loads(r["data"])
        check_id = data.get("check_id", "")
        desc = {"SEC-INJ-001": "SQL注入", "SEC-AUTH-001": "硬编码密码",
                "SEC-CRYPTO-001": "硬编码密钥", "SEC-FILE-002": "路径遍历",
                "PERF-003": "SELECT *", "PERF-004": "大文件一次性加载",
                "DELIV-001": "密钥泄露", "COMPL-001": "敏感数据明文"}.get(check_id, "")
        if desc:
            tasks.append(f"修复{desc}问题并写安全测试")
    return tasks


def get_validated_rules_tasks(lm, limit=15) -> list[str]:
    """从validated/generalized rule生成深化题"""
    rows = lm.conn.execute(
        "SELECT data FROM records WHERE type='coding_rule' AND state IN ('validated','generalized') "
        "ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()
    tasks = []
    for r in rows:
        data = json.loads(r["data"])
        rule = data.get("rule", "")
        if rule:
            # 出更难的问题：让LLM写违反这条规则的代码然后修复
            tasks.append(f"先写违反原则的代码再修复: {rule[:60]}")
    return tasks


def generate_combo_tasks(count=10) -> list[str]:
    """从领域×概念×模式×动作组合出新题"""
    tasks = []
    for _ in range(count):
        domain = random.choice(DOMAINS)
        concept = random.choice(CONCEPTS)
        pattern = random.choice(PATTERNS)
        action = random.choice(ACTIONS)
        level = random.choice(LEVELS)

        templates = [
            f"{level}·{domain}中{concept}的{pattern}{action}",
            f"{level}·{domain}的{concept}用{pattern}实现",
            f"{level}·{domain}中{concept}的{pattern}{action}, 包含错误处理",
            f"{level}·{domain}实现一个{concept}管理器(用{pattern})",
            f"{level}·{domain}对比{concept}的两种{pattern}实现并评测",
        ]
        tasks.append(random.choice(templates))
    return list(set(tasks))


def generate_deep_v1_tasks(count=10) -> list[str]:
    """V1.0深度题"""
    v1_concepts = [
        "出入分离", "流转校验", "灰区处理", "插片化", "主干削薄",
        "events不可变", "records状态机", "type+data消化一切",
        "多副本不一致", "维度焊死", "策略硬编码", "不存在的问题",
    ]
    tasks = []
    for _ in range(count):
        c1, c2 = random.sample(v1_concepts, 2)
        tasks.append(f"灵元·用代码展现{c1}和{c2}的区别")
    return tasks


def generate_batch(batch_size=30) -> list[str]:
    """生成一批新题"""
    lm = LingMemory()
    tasks = []

    tasks += get_hypothesized_rules(lm, 10)
    tasks += get_audit_findings_tasks(lm, 5)
    tasks += get_validated_rules_tasks(lm, 5)
    tasks += generate_combo_tasks(5)
    tasks += generate_deep_v1_tasks(5)

    lm.close()
    return list(set(tasks))


def update_task_pool(tasks: list[str]):
    """更新蒸馏daemon的题目池"""
    path = Path(__file__).resolve().parent / "distill_daemon.py"
    content = path.read_text()

    # 找到TASKS_POOL位置并替换
    start = content.find("TASKS_POOL = [")
    end = content.find("\n]", start) + 2
    if start == -1 or end == -1:
        return False

    new_pool = "TASKS_POOL = [\n"
    for t in tasks:
        new_pool += f'    "{t}",\n'
    new_pool += "]"

    new_content = content[:start] + new_pool + content[end:]
    path.write_text(new_content)
    return True


if __name__ == "__main__":
    tasks = generate_batch(30)
    print(f"产生{len(tasks)}道新题:")
    for t in tasks[:10]:
        print(f"  · {t[:60]}")
    print(f"  ... 共{len(tasks)}题")
    
    if update_task_pool(tasks):
        print("已更新distill_daemon.py的TASKS_POOL，重启后生效")
    else:
        print("更新失败")

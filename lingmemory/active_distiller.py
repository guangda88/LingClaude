# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 主动蒸馏器 — 把蒸馏作为一项实际工作

机制：
  1. 给LLM一个编码任务
  2. LLM生成代码 + 解释"为什么这样写"
  3. 从解释中提取coding_rule
  4. rule入库 → 下次同样的任务查表不调LLM
  
  每次LLM调用都产生两条产出：
    - 代码（即时用）
    - 规则（以后用 —— 以前被扔掉了）
"""
import json
import re
import time
from typing import Optional

from lingmemory.core import DB_PATH
from lingmemory.api import LingMemoryAPI
from lingmemory.distiller import Distiller


# 蒸馏任务模板
_DISTILL_TASKS = [
    # === 架构 ===
    "如何判断一个功能应该拆成独立模块而不是写在主文件里",
    "新增一个功能模块时，应该改几处代码算合理的薄主干",
    "两个类功能重叠时应该合并还是拆分，判断标准是什么",
    "什么时候该用配置驱动代替硬编码",
    
    # === 错误处理 ===
    "Python中异常处理的最佳实践：什么时候用try-except，什么时候用if防御",
    "网络请求超时和重试的标准写法",
    "数据库连接池的正确关闭方式",
    
    # === 安全 ===
    "API key的最佳存储方式：环境变量 vs 配置文件 vs 密钥管理服务",
    "用户输入验证的防御性编程模式",
    "SQL注入防范的标准写法（参数化查询）",
    
    # === 测试 ===
    "单元测试应该覆盖哪些场景：正常/边界/异常",
    "Mock外部依赖的正确方式",
    "测试数据构造：fixture vs factory vs seed data",
    
    # === 性能 ===
    "N+1查询问题的检测和修复模式",
    "批量处理vs逐条处理的判断标准",
    "缓存失效策略：什么时候该清缓存，什么时候该等TTL",
    
    # === 灵元 ===
    "使用2T3A（create/transition/query）替代自定义状态管理的判断标准",
    "灵元审计三步法：扫描→分类→提取rule的重复模式",
    "什么时候该加intent_gate（前置灰区），什么时候不需要",
]


class ActiveDistiller:
    """主动蒸馏器 — 把蒸馏作为生产任务"""

    def __init__(self, db_path=DB_PATH, member="lingclaude"):
        self.api = LingMemoryAPI(db_path, member=member)
        self.member = member
        self.stats = dict(tasks=0, rules=0, errors=0)

    def close(self):
        self.api.close()

    def distill_from_llm_response(self, task: str, llm_code: str, llm_reasoning: str) -> Optional[str]:
        """从LLM的编码+解释中提取规则"""
        if not llm_reasoning or len(llm_reasoning) < 30:
            return None
        
        # 查重
        existing = self.api.lm.query(type="coding_rule", limit=500)
        for item in existing["items"]:
            # 相似度检查：如果已存在语义相似的rule，直接合并evidence
            erule = item["data"].get("rule", "").lower()
            task_lower = task.lower()
            if any(w in erule for w in task_lower.split() if len(w) > 3):
                ev = item["data"].get("evidence", [])
                tid = f"distill_{int(time.time())}"
                if tid not in ev:
                    ev.append(tid)
                    item["data"]["evidence"] = ev
                    item["data"]["confidence"] = min(1.0, len(ev) * 0.2)
                    self.api.lm.conn.execute(
                        "UPDATE records SET data=? WHERE id=?",
                        (json.dumps(item["data"], ensure_ascii=False), item["id"]))
                    self.api.lm.conn.commit()
                    if len(ev) >= 3 and item["state"] == "hypothesized":
                        self.api.lm.transition(item["id"], "evidence_sufficient", actor=self.member)
                return item["id"]

        # 从LLM的reasoning中提取关键词作为rule
        keywords = [w for w in task.split() if len(w) > 3]
        rule_text = task if len(task) < 150 else task[:150]
        
        # 判断类别
        category = "pattern"
        if "架构" in task or "模块" in task or "设计" in task:
            category = "architecture"
        elif "异常" in task or "错误" in task or "超时" in task:
            category = "debugging"
        elif "安全" in task or "key" in task or "注入" in task:
            category = "security"
        elif "测试" in task:
            category = "testing"
        elif "性能" in task or "N+1" in task or "缓存" in task:
            category = "performance"

        return self.api.lm.create(type="coding_rule", data={
            "rule": rule_text,
            "evidence": [f"distill_{int(time.time())}"],
            "category": category,
            "confidence": 0.3,
            "distilled": True,
            "source": "active_distill",
            "llm_reasoning_snippet": llm_reasoning[:200],
        }, created_by=self.member)

    def run_batch(self, tasks: list[str]) -> dict:
        """批量蒸馏 — 用LLM处理任务列表（由调用方提供LLM）"""
        results = []
        for task in tasks:
            self.stats["tasks"] += 1
            # LLM部分由外部提供，这里只记录任务
            self.api.lm.create(type="code_trace", data={
                "prompt": task,
                "language": "text",
                "generated_code": "",
                "test_result": "skipped",
                "member": self.member,
                "project": "distill_task",
            }, created_by=self.member)
            results.append({"task": task, "status": "pending_llm"})
        return results

    def get_stats(self) -> dict:
        return self.stats


if __name__ == "__main__":
    d = ActiveDistiller()
    print(f"待蒸馏任务: {len(_DISTILL_TASKS)}个")
    print(f"覆盖: architecture/error_handling/security/testing/performance/lingyuan")
    d.close()

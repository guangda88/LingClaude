# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 持续蒸馏器 — 后台永不停止

用GLM-4-Flash（免费）持续出题蒸馏coding_rule。
每次启动一批11条，全部蒸馏后再跑下一轮。
"""
import json, urllib.request, os, time, sys
import logging

logger = logging.getLogger(__name__)
sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.api import LingMemoryAPI

KEY = os.environ.get("ZHIPU_API_KEY", "")
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4-flash"

TASKS_POOL = [
    "设计一个分布式配置中心(支持热更新+版本回滚+权限控制)",
    "设计一个多级缓存系统(L1内存/L2Redis/L3DB)的失效策略",
    "设计一个API网关的限流+鉴权+路由+日志四合一中间件",
    "设计一个支持灰度发布的微服务网关",
    "设计一个跨服务的分布式事务补偿机制",
    "设计一个多租户SaaS系统的数据隔离方案",
    "设计一个实时数据处理管道的背压机制",
    "设计一个支持多数据源的查询联邦引擎",
    "缓存提升性能vs数据一致性: 什么时候宁可慢也要实时",
    "代码复用vs过度抽象: 重复3次才提取共用还是第一次就抽象",
    "提前优化vs不做过度设计: 判断标准是什么",
    "薄主干vs可读性: 砍到多薄才不影响新人理解",
    "配置化vs硬编码: 一个参数改频率多低才值得做成配置",
    "异步vs同步: 延迟敏感vs一致性要求的取舍标准",
    "用一个type+data设计一个完整的权限系统(用户/角色/资源/操作)",
    "设计一个只有create和query没有transition的系统(events仅追加)",
    "设计一个所有transition都有Guard函数的审计系统",
    "设计一个type的state流转可以动态配置的工作流引擎",
    "多个类型之间共享状态时怎么用parent_id做关联查询",
    "灵忆的query支持data_filter: 设计一个支持嵌套JSON过滤的查询引擎",
    "events表在10亿条时怎么分片: 按时间/按type/按record_id",
    "type_registry.yaml在不重启服务的情况下热加载新type",
    "Agent自己设计coding_rule的提取prompt并self-review",
    "Agent怎么判断一个coding_rule过时了需要deprecated",
    "Agent发现两个coding_rule冲突时怎么resolve",
    "Agent怎么用灵忆的query能力做few-shot没有的zero-shot推理",
    "Agent如何用2T3A设计另一个Agent的认知系统",
    "灵元V1.0本身能否用2T3A实现(元编程)",
    "灵族的降级链(L3→L2→L1→Local)的Guard设计",
    "12个成员各自一个lingmemory.db还是共享一个",
    "日志审计可以删吗还是永远保留: retain=true的存储策略",
    "灵忆MCP :9530 的并发模型: 每个成员一个连接还是全局队列",
    "LingBus消息的TTL和过期清理策略设计",
]

def distill(task):
    """一条蒸馏"""
    data = json.dumps({"model": MODEL, "messages": [
        {"role": "user", "content": f"用中文一句话总结编程规则(15字内)：{task}"}
    ], "max_tokens": 50, "temperature": 0}).encode()
    req = urllib.request.Request(URL, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        r = json.loads(resp.read())
        rule = r["choices"][0]["message"]["content"].strip().split('\n')[0][:50].strip('"\'。，')
        return rule if len(rule) > 3 else None
    except (OSError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("rule 提取失败 (task=%s): %s", task[:30], e)
        return None

def run_forever():
    """永不停止的蒸馏循环"""
    api = LingMemoryAPI(member="lingclaude")
    round_num = 0
    total_attempts = 0
    total_new = 0
    
    while True:
        round_num += 1
        count = 0
        attempts = 0
        start = time.time()
        
        for task in TASKS_POOL:
            rule = distill(task)
            if rule:
                attempts += 1
                existing = api.lm.conn.execute(
                    "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                    "AND json_extract(data, '$.rule')=?", (rule,)).fetchone()[0]
                if existing == 0:
                    api.lm.create(type="coding_rule", data={
                        "rule": rule, "evidence": [f"glm4flash_{int(time.time())}"],
                        "category": "pattern", "confidence": 0.3,
                        "distilled": True, "source": "glm-4-flash",
                    }, created_by="lingclaude")
                    count += 1
                    total_new += 1
                total_attempts += 1
        
        dedup_rate = (attempts - count) / max(attempts, 1)
        total = api.lm.conn.execute("SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
        print(f"[{time.strftime('%H:%M:%S')}] Round {round_num}: +{count}新, "
              f"查重率{dedup_rate:.0%}, 总计{total}条, "
              f"累计+{total_new}/{total_attempts}={total_new/max(total_attempts,1):.0%}新率, "
              f"耗时{time.time()-start:.0f}s")
        time.sleep(10)

if __name__ == "__main__":
    run_forever()

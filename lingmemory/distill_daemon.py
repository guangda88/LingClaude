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
sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.api import LingMemoryAPI

KEY = os.environ.get("ZHIPU_API_KEY", "")
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4-flash"

TASKS_POOL = [
    "用2T3A设计一个不支持回滚的事务系统",
    "设计一个系统events不可变但records可回溯",
    "多个状态机同步时如何保持一致性",
    "type+data模式下怎么处理跨类型的查询",
    "薄主干中插片的依赖注入顺序怎么控制",
    "灰区escalate后怎么自动恢复",
    "transition的Guard函数怎么测试",
    "多级降级链的回退策略设计",
    "create/transition/query三种操作的性能对比",
    "出入分离后怎么处理异步回调",
    "不流转的字段放data还是放结构里",
    "不同类型之间怎么共享状态",
    "多个中间件之间怎么传递上下文",
    "状态机嵌套时父子状态怎么同步",
    "events的回溯窗口设多大合适",
    "配置文件里的密钥怎么在CI/CD中安全注入",
    "微服务之间调用链怎么串联trace_id",
    "Kubernetes中Pod重启后IP变了怎么处理",
    "多个服务共享一个数据库时的迁移策略",
    "分布式系统中消息至少一次投递的幂等",
    "SQLite WAL模式下读写并发怎么控制",
    "多个进程操作同一个SQLite文件怎么防锁",
    "异步任务失败后怎么自动重试",
    "服务降级时怎么优雅返回而不是报错",
    "配置热更新时怎么不影响正在进行的请求",
    "数据库连接池爆了怎么优雅降级",
    "大事务拆小事务的边界在哪里",
    "数据回滚时已经发出的通知怎么撤回",
    "灰度发布时怎么只影响一部分流量",
    "多语言微服务之间怎么统一错误码",
    "Agent怎么知道自己理解错了",
    "Agent怎么区分用户在想和用户不在",
    "Agent连续失败几次应该停下来",
    "Agent怎么判断任务超出了自己的能力",
    "Agent怎么在多个任务之间切换上下文",
    "Agent怎么知道自己退化",
    "Agent怎么判断一个rule是否过时",
    "Agent怎么合并两个冲突的rule",
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
    except:
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

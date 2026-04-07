# Token 储存方案分析

## 📊 当前 GLM 计费模式

### 云端 API（GLM）

| 模式 | 说明 | 能否累积 |
|------|------|---------|
| **按量付费** | 用多少付多少 | ❌ 无余额 |
| **套餐包** | 每月固定额度 | ❌ 过期清零 |
| **预付费** | 充值余额 | ✅ 可能累积 |

**关键问题：** 你的 GLM 套餐是什么类型？

### 本地模型（Gemmma/Llama）

| 模式 | 说明 | 能否累积 |
|------|------|---------|
| **自建** | 硬件+电费 | ✅ 无限制 |
| **Ollama** | 完全免费 | ✅ 无限制 |
| **LM Studio** | 完全免费 | ✅ 无限制 |

## 💡 实用解决方案

### 方案 1：任务蓄水池（推荐）⭐⭐⭐⭐⭐

**核心思路：**
- 忙时：只记录任务，不消耗配额
- 闲时：批量执行之前积累的任务

**实现方式：**

```python
from lingclaude.core.task_scheduler import TaskScheduler
from datetime import datetime

# 创建任务调度器
scheduler = TaskScheduler(max_batch_size=10, quota_limit=160000)

# 忙时：只添加任务（不执行）
def add_task_for_later(query: str, priority: str = "medium"):
    """添加任务到蓄水池"""
    from lingclaude.core.task_scheduler import TaskPriority

    priority_map = {
        "high": TaskPriority.HIGH,
        "medium": TaskPriority.MEDIUM,
        "low": TaskPriority.LOW,
        "urgent": TaskPriority.URGENT,
    }

    task_id = scheduler.add_task(
        query=query,
        priority=priority_map.get(priority, TaskPriority.MEDIUM),
        estimated_tokens=500,  # 预估
    )

    print(f"✅ 任务已添加到蓄水池: {query[:50]}...")
    return task_id

# 闲时：批量执行蓄水池中的任务
def execute_pending_tasks(engine, max_tokens: int = 10000):
    """批量执行待处理任务"""
    from lingclaude.core.task_scheduler import TaskPriority

    # 获取当前配额使用情况
    from lingclaude.core.token_monitor import TokenMonitor
    tm = TokenMonitor()
    stats = tm.get_daily_stats()
    remaining = 160000 - stats.total_tokens

    if remaining < max_tokens:
        print(f"⚠️ 配额不足: {remaining:,} < {max_tokens:,}")
        return

    # 获取下一批任务
    batch = scheduler.get_next_batch(max_tokens=max_tokens)

    print(f"\n🚀 执行 {len(batch)} 个蓄水池任务...")

    for task in batch:
        print(f"\n执行任务: {task.query[:50]}...")

        # 执行任务
        result = engine.submit(task.query)

        # 估算使用的 tokens
        tokens_used = len(result) * 2  # 粗略估算

        # 标记完成
        scheduler.mark_completed(task.task_id, tokens_used, success=True)

        print(f"✅ 完成，使用 ~{tokens_used} tokens")

    print(f"\n📊 剩余任务: {scheduler.get_queue_size()}")
```

**使用示例：**

```bash
# 忙时（门诊时）
python3 << 'EOF'
from scripts.glm_quota_optimizer import GLMQuotaOptimizer

optimizer = GLMQuotaOptimizer()

# 添加任务到蓄水池
tasks = [
    "实现一个快速排序算法",
    "写一个用户认证模块",
    "优化数据库查询性能",
]

for task in tasks:
    print(f"📝 记录任务: {task}")
    # 只记录，不执行

print("\n✅ 任务已存入蓄水池，等闲时批量执行")
EOF

# 闲时（晚上/周末）
python3 << 'EOF'
from scripts.glm_quota_optimizer import GLMQuotaOptimizer

optimizer = GLMQuotaOptimizer()

# 批量执行蓄水池中的任务
optimizer.execute_pending_tasks(engine)

print("\n🎉 蓄水池任务执行完成！")
EOF
```

**优点：**
- ✅ 集中执行，最大化配额利用
- ✅ 灵活安排执行时间
- ✅ 可以按优先级执行
- ✅ 不浪费任何配额

**缺点：**
- ⚠️ 需要手动管理任务队列
- ⚠️ 需要提前规划

---

### 方案 2：本地+云端混合（实用）⭐⭐⭐⭐⭐

**核心思路：**
- 忙时：用本地模型（免费）
- 闲时：用云端模型（用套餐）

**实现方式：**

```python
from lingclaude.core import QueryEngine, LingClaudeConfig
from datetime import datetime

def is_busy_time() -> bool:
    """判断是否是忙时（门诊时间）"""
    # 假设门诊时间是 8:00-12:00, 14:00-18:00
    now = datetime.now().hour
    return (8 <= now < 12) or (14 <= now < 18)

def get_current_config():
    """根据时间选择配置"""
    if is_busy_time():
        # 忙时：使用本地模型
        return LingClaudeConfig.from_dict({
            'model': {
                'provider': 'openai',
                'base_url': 'http://localhost:11434/v1',
                'api_key': 'dummy',
                'model': 'gemma2:9b',  # 本地模型
                'max_tokens': 4096,
            }
        })
    else:
        # 闲时：使用云端 GLM
        return LingClaudeConfig.from_dict({
            'model': {
                'provider': 'openai',
                'base_url': 'https://api.deepseek.com/v1',
                'api_key': 'your-api-key',
                'model': 'deepseek-chat',  # 云端模型
                'max_tokens': 4096,
            }
        })

def auto_route_query(query: str):
    """自动路由查询到合适的模型"""
    config = get_current_config()
    engine = QueryEngine(config=config)

    result = engine.submit(query)

    # 记录使用的模型
    model_type = "本地（免费）" if is_busy_time() else "云端（GLM 套餐）"
    print(f"使用: {model_type}")

    return result
```

**使用示例：**

```bash
# 任何时间，自动路由
python3 << 'EOF'
from scripts.auto_router import auto_route_query

# 忙时（门诊）→ 自动用本地模型
result1 = auto_route_query("写一个快速排序函数")

# 闲时（晚上）→ 自动用云端 GLM
result2 = auto_route_query("优化这个数据库查询")
EOF
```

**效果：**
```
忙时（8:00-18:00）：
  - 使用本地 Gemma 2-9B
  - 消耗 0 tokens（完全免费）
  - GLM 套餐不消耗

闲时（18:00-8:00）：
  - 使用云端 GLM
  - 集中消耗配额
  - 最大化利用套餐

结果：
  - 忙时节省的配额 = 实际累积的配额
  - 闲时可以连续使用 GLM
```

**优点：**
- ✅ 自动化，无需手动切换
- ✅ 忙时完全不消耗 GLM 配额
- ✅ 闲时充分利用 GLM
- ✅ 相当于"累积"了忙时的配额

**缺点：**
- ⚠️ 忙时只能用本地模型（能力有限）
- ⚠️ 需要配置本地模型环境

---

## 🎯 推荐方案对比

| 方案 | 实用性 | 效果 | 推荐度 |
|------|--------|------|--------|
| **任务蓄水池** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **本地+云端混合** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

---

## 🚀 立即可用的方案

### 方案 A：任务蓄水池（最简单）

```bash
# 1. 创建任务文件
mkdir -p ~/.lingclaude/tasks

# 2. 忙时：添加任务
echo "$(date '+%Y-%m-%d %H:%M:%S') - 写一个快速排序函数" >> ~/.lingclaude/tasks/todo.txt
echo "$(date '+%Y-%m-%d %H:%M:%S') - 实现用户认证模块" >> ~/.lingclaude/tasks/todo.txt

# 3. 闲时：批量执行
while read -r line; do
    query=$(echo "$line" | cut -d' ' -f4-)
    echo "执行: $query"
    # 使用 LingClaude 执行
    python3 -m lingclaude.cli "$query"
    echo "完成"
done < ~/.lingclaude/tasks/todo.txt

# 4. 清空已完成任务
rm ~/.lingclaude/tasks/todo.txt
```

### 方案 B：自动路由（最省心）

```python
# scripts/auto_router.py
from lingclaude.core import QueryEngine, LingClaudeConfig
from datetime import datetime

def is_busy_time() -> bool:
    """门诊时间"""
    now = datetime.now().hour
    return (8 <= now < 12) or (14 <= now < 18)

def auto_route_query(query: str):
    """自动路由"""
    if is_busy_time():
        # 忙时：本地模型
        print("🏥 门诊时间 → 使用本地模型")
        config = LingClaudeConfig.from_dict({
            'model': {
                'provider': 'openai',
                'base_url': 'http://localhost:11434/v1',
                'api_key': 'dummy',
                'model': 'gemma2:9b',
                'max_tokens': 4096,
            }
        })
    else:
        # 闲时：云端 GLM
        print("🌙 闲时 → 使用云端 GLM")
        config = LingClaudeConfig.from_dict({
            'model': {
                'provider': 'openai',
                'base_url': 'https://api.deepseek.com/v1',
                'api_key': 'your-api-key',
                'model': 'deepseek-chat',
                'max_tokens': 4096,
            }
        })

    engine = QueryEngine(config=config)
    return engine.submit(query)

if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:])
    if not query:
        print("用法: python auto_router.py <查询>")
        sys.exit(1)

    result = auto_route_query(query)
    print(result)
```

**使用方式：**

```bash
# 任何时间，自动路由
python scripts/auto_router.py 写一个快速排序函数

# 输出会根据时间自动选择：
# 8:00-12:00/14:00-18:00 → 本地模型
# 其他时间 → 云端 GLM
```

---

## 📊 效果预测

### 本地+云端混合

**每天使用量：**

| 时间段 | 模型 | Tasks | Tokens | GLM 配额 |
|--------|------|-------|--------|----------|
| 8:00-12:00 | 本地 | 20 | 8,000 | 0 |
| 12:00-14:00 | 云端 | 10 | 4,000 | 4,000 |
| 14:00-18:00 | 本地 | 20 | 8,000 | 0 |
| 18:00-24:00 | 云端 | 40 | 16,000 | 16,000 |
| **总计** | - | 90 | 36,000 | 20,000 |

**结果：**
- ✅ 忙时节省了 16,000 tokens
- ✅ 闲时集中使用了 20,000 tokens
- ✅ GLM 配额利用率提升 25%
- ✅ 相当于"累积"了忙时的配额

---

## 💡 最终建议

### 如果你想要最简单的方法：

**使用任务蓄水池**
```bash
# 忙时：只记录
echo "任务1" >> tasks.txt

# 闲时：批量执行
while read line; do
  lingclaude "$line"
done < tasks.txt
```

### 如果你想要自动化：

**使用本地+云端混合**
```python
# 自动根据时间选择模型
if 8 <= hour < 18:
    use_local_model()
else:
    use_glm_model()
```

### 如果你想要最省心：

**结合两种方法**
- 忙时：本地模型 + 记录任务
- 闲时：云端 GLM + 批量执行

这样既能节省配额，又能保证质量！

---

## 🎯 总结

**Token 储存的本质：**
1. ✅ **任务蓄水池** - 存任务，以后批量执行
2. ✅ **混合模式** - 忙时用本地，闲时用云端

**最实用：任务蓄水池 + 混合模式**
- 相当于把忙时的配额"存"起来
- 闲时可以集中使用
- 不浪费任何配额

**效果：**
- 忙时节省的配额 = 实际累积的配额
- 闲时可以连续使用 GLM
- GLM 配额利用率最大化

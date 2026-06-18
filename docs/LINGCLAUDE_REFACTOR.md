# 灵克业务逻辑改造计划

## 现状：6万行的真相

```
核心代码 (lingclaude/)     2.5万行
  ├── core/  38个文件      1.2万行
  ├── model/ 21个文件      3700行
  ├── engine/ 19个文件     4200行
  ├── self_optimizer/ 11个  2300行
  ├── mcp/    4个文件      900行
  ├── cli/    4个文件      800行
  └── coordination/ 2个     300行
测试 (tests/)              2万行
死代码 (experiments/scripts/research)  1.3万行  ← 0次被import
```

## 灵克的业务是什么？

用四个词切：

| 词 | 灵克的实例 |
|----|-----------|
| 主体 | 灵克自己（一个AI Agent） |
| 目标 | 编程、审计、自优化、响应LingBus |
| 信息 | 代码、对话、审计结果、优化策略 |
| 状态 | 对话中的上下文、任务进度、优化状态 |

**灵克实际的核心路径（从CLI入口追踪）**：
```
app.py → QueryEngine → config + session + coding + model
                      → governance + metrics + handover
```

## 改造策略：三层分离

### 第一层：可立即删除（1.3万行）

| 目录 | 行数 | 理由 |
|------|------|------|
| experiments/ | 6500 | 0次被import，实验代码，历史产出已归档 |
| scripts/ | 5800 | 0次被import，一次性脚本 |
| research/ | 875 | 0次被import，早期研究 |

### 第二层：灵元可替代的状态管理（~4000行）

core/ 里大量代码是在做灵元已经做的事：

| 灵克模块 | 行数 | 做的事 | 灵元对应 |
|---------|------|--------|---------|
| session.py | 255 | 会话创建/状态管理 | records(type=session) |
| task_aggregation.py | 597 | 任务状态/聚合 | records(type=task) |
| governance.py | 447 | 提案/投票/决议 | records(type=proposal)+events |
| governance_verifier.py | 311 | 验证治理结果 | transition校验 |
| task_scheduler.py | 281 | 任务调度 | query+transition |
| handover.py | 367 | 状态持久化 | records(type=info)+events |
| context_cache.py | 427 | 上下文缓存 | 灵忆的info_records |
| layered_memory.py | 541 | 分层记忆 | 灵忆的冷热分层 |
| memory_engine.py | 678 | 记忆引擎 | 灵忆的query+search |
| cognitive_rhythm.py | 311 | 认知节律 | records(type=event) |
| topic_stack.py | 202 | 话题栈 | parent_id树形结构 |
| behavior_aware_router.py | 282 | 行为路由 | records+events |
| reasoning_chain.py | 249 | 推理链 | events链 |
| meta_cognition.py | 287 | 元认知 | records(type=info) |
| skill_parser.py | 245 | 技能解析 | records(type=skill) |

**合计~5470行，全部是灵元已覆盖的通用状态管理。**

### 第三层：灵克真正不可替代的核心（~5000行）

| 模块 | 行数 | 为什么不可替代 |
|------|------|---------------|
| query_engine.py | 1844 | Agent主循环：收到输入→选模型→调用→返回 |
| config.py | 222 | 灵克自己的配置 |
| types.py | 39 | 类型定义 |
| coding.py (engine) | 716 | 代码执行工具（bash/edit/grep） |
| model providers | 3712 | LLM API调用（OpenAI/Anthropic/Local） |
| safe_db.py | 97 | 数据库安全封装 |
| behavior.py | 194 | 行为规则 |
| permissions.py | 51 | 权限检查 |
| hooks.py | 116 | 钩子系统 |
| rate_limiter.py | 117 | 限流 |
| prior_verifier.py | 132 | 先验验证 |
| metrics.py | 276 | 指标 |
| mcp/server.py | 500 | MCP服务 |
| api.py | 502 | API层 |
| self_optimizer | 2319 | 自优化（可能也可大幅精简） |

**这些才是灵克真正的业务逻辑。约5000行。**

## 改造路线图

```
第一步（立即）：删除死代码        -13000行 → 47000行
第二步（灵元接入）：状态管理迁移灵元  -5000行 → 42000行
第三步（model精简）：provider合并    -2000行 → 40000行
第四步（self_optimizer评估）：      -2000行 → 38000行
第五步（测试瘦身）：去掉死代码测试    -5000行 → 33000行
```

**目标：从6万行砍到1-1.5万行（核心~5000行 + 测试~5000行 + 灵元消费者层~2000行）。**

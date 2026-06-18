# 灵元的用法

> 主支要薄，分支要多，接口要活。
> 这是灵元的设计哲学，也是灵元的使用方法。

灵元（lingmemory）是灵族的状态管理底座。它用 2 张表、3 个操作、1 份配置文件，消化了一个多 Agent 系统的 31 类需求——零次表结构变更。

本文不讲设计理念，讲**怎么用**。

---

## 一、灵元是什么

一句话：**一个带状态机校验的 JSON 存储。**

你有数据，数据有状态，状态会变化。灵元管这三件事：

```
create(type, data)           → 创建一条记录
transition(id, event)        → 改变记录的状态
query(type?, state?, ...)    → 检索记录
```

底层是 SQLite，两张表：

- `records` — 万物皆记录，业务数据全塞 `data` JSON 字段
- `events` — 万动皆事件，每次状态变更都留痕

就这些。没有第三张表，也没有 ALTER TABLE。

---

## 二、安装与初始化

```python
from lingmemory.core import LingMemory, init_db

# 初始化数据库（执行 schema.sql，建表+索引+WAL）
init_db("/path/to/lingmemory.db")

# 创建实例
lm = LingMemory("/path/to/lingmemory.db")
```

`init_db` 会：
1. 创建 `records` 表（含 FTS5 全文搜索虚拟表）
2. 创建 `events` 表
3. 开启 WAL 模式（读写不互斥）
4. 开启外键约束

---

## 三、三个操作

### 1. create — 创建记录

```python
# 创建一个任务
task_id = lm.create(
    type="task",
    data={
        "goal": "审计灵康编排层代码",
        "boundary": "仅审计，不修改"
    },
    created_by="lingclaude"
)
# → 返回 uuid，记录状态为 "created"（Type Registry 定义的默认状态）
```

create 做了什么：
- 校验 `type` 在 Type Registry 中存在
- 校验 `data` 符合该 type 的 `data_schema`（必填字段、枚举值）
- 写入 `records` 表
- 写入一条 `create` 事件到 `events` 表

**非法操作会被拒绝：**

```python
# 未知 type → 报错
lm.create(type="nonexistent", data={})
# ValueError: unknown type: nonexistent

# 缺必填字段 → 报错（task 要求 goal）
lm.create(type="task", data={})
# ValueError: data validation failed: ['missing required field: goal']
```

### 2. transition — 状态流转

```python
# 任务生命周期：created → active → done → archived
lm.transition(task_id, "start", actor="lingclaude")     # → "active"
lm.transition(task_id, "complete", actor="lingclaude",  # → "done"
              data={"conclusion": "审计完成，无 Critical 问题"})
lm.transition(task_id, "archive")                        # → "archived"
```

transition 做了什么：
- 查询记录当前状态
- 在 Type Registry 中校验 `当前状态 + 事件 → 目标状态` 是否为合法转换
- 更新 `records.state`
- 写入一条事件到 `events` 表（记录 from_state、to_state、actor、附加数据）

**非法转换会被拒绝：**

```python
# created → done 不合法（必须先经过 active）
lm.transition(task_id, "complete")
# ValueError: illegal transition: task.created --complete--> ?
```

这就是状态机的意义：**防止跳步。** 一个任务没经过 active 就直接 complete？拒绝。一个会话没经过 review 就直接 published？拒绝。

### 3. query — 检索

```python
# 按类型查
tasks = lm.query(type="task")

# 按类型+状态查
active_tasks = lm.query(type="task", state="active")

# 按创建者查
my_records = lm.query(created_by="lingclaude")

# 按父节点查（树形结构）
children = lm.query(parent_id=task_id)

# 游标分页（性能与数据量无关）
page1 = lm.query(type="task", limit=20)
page2 = lm.query(type="task", limit=20, cursor=page1["next_cursor"])
```

query 返回 `{"items": [...], "next_cursor": int|None}`。游标分页用 rowid 做游标，不使用 OFFSET——翻到第 1000 页和第 1 页一样快。

---

## 四、Type Registry — 怎么加新类型

灵元预置了 9 种 type：`task`、`session`、`info`、`todo`、`artifact`、`quota`、`tool_call`、`tag`、`snapshot`。

**加新 type 不改代码，只改配置。** 编辑 `type_registry.yaml`：

```yaml
# 举个例子：加一个"论文"类型
paper:
  description: "学术论文"
  default_state: draft
  states: [draft, submitted, under_review, accepted, rejected, published]
  transitions:
    - {from: draft,        to: submitted,    event: submit}
    - {from: submitted,    to: under_review, event: enter_review}
    - {from: under_review, to: accepted,     event: accept}
    - {from: under_review, to: rejected,     event: reject}
    - {from: accepted,     to: published,    event: publish}
  data_schema:
    title:    {required: true,  type: string}
    authors:  {required: true,  type: array}
    venue:    {required: false, type: string}
    abstract: {required: false, type: string}
```

保存后立即生效：

```python
paper_id = lm.create(
    type="paper",
    data={"title": "灵元：薄主干状态管理", "authors": ["lingclaude", "lingresearch"]}
)
# → state = "draft"

lm.transition(paper_id, "submit")     # → "submitted"
lm.transition(paper_id, "enter_review") # → "under_review"
lm.transition(paper_id, "accept")     # → "accepted"
lm.transition(paper_id, "publish")    # → "published"
```

这就是"分支要多"的含义：**主干代码不变（create/transition/query 三行不动），业务变化全在配置文件里。**

---

## 五、实际用法：四个场景

### 场景 1：任务拆分

任务太大，上下文窗口装不下。拆成子任务：

```python
# 原任务
big_task = lm.create(type="task", data={"goal": "全族代码审计"})
lm.transition(big_task, "start")

# 拆出两个子任务（parent_id 建立树形关系）
child1 = lm.create(type="task", data={"goal": "审计灵犀安全模块"}, parent_id=big_task)
child2 = lm.create(type="task", data={"goal": "审计智桥路由模块"}, parent_id=big_task)

# 原任务标记为 split，事件中记录子任务 ID
lm.transition(big_task, "split", data={"child_ids": [child1, child2]})
```

查子任务：`lm.query(parent_id=big_task)`。

### 场景 2：会话移交

灵克做到一半，移交给灵通继续：

```python
# 灵克的会话结束，记录移交原因
lm.transition(session_id, "end",
              data={"reason": "handoff to lingflow"})

# 同一 task 下开新会话，新 owner
new_session = lm.create(
    type="session",
    data={"owner": "lingflow"},
    parent_id=task_id
)
```

新会话可以 `query(parent_id=task_id)` 找到历史会话，恢复上下文。

### 场景 3：信息生命周期管理

灵元对信息定义了 5 状态流转：产生 → 活跃 → 归档 → 过期 → 清理。

```python
# 创建一条结论
info_id = lm.create(
    type="info",
    data={
        "content": "Proxy v2 重构后 ServeHTTP 从 200 行降到 10 行",
        "info_type": "conclusion",      # conclusion | derivation | reference
        "is_conclusion": True,          # 标记为结论，不受自动清理影响
        "visibility": "shared",         # private | shared | governance
        "retain": False,                # True = 审计/合规强制保留
        "written_by": "lingflow",
        "written_at": "2026-06-15"
    }
)

# 归档
lm.transition(info_id, "archive")

# 过期
lm.transition(info_id, "expire")

# 清理（retain=True 的不会被清理）
lm.transition(info_id, "purge")
```

关键设计：`is_conclusion=True` 的信息跳过自动清理。这解决了科研型成员的痛点——中间推导数据同时就是最终结论，不能被误清。

### 场景 4：handover 结构化

灵族的每个成员在会话结束时写 handover。灵元让 handover 从自由文本变成结构化数据：

```python
# 会话结束时，写入结构化 handover
lm.create(
    type="info",
    data={
        "content": "灵克会话 #56 handover",
        "info_type": "conclusion",
        "is_conclusion": True,
        "visibility": "private",
        "written_by": "lingclaude",
        "written_at": "2026-06-16",
        # 自定义字段塞 data
        "active_tasks": [
            {"id": "T1", "title": "灵元文章撰写", "status": "in_progress"}
        ],
        "key_metrics": {"tests_passed": 1573, "code_lines": 46247},
        "next_steps": ["完成灵元文章", "SDT-lc-002 健康巡检"]
    },
    created_by="lingclaude"
)
```

下次会话启动时：

```python
# 只拉最近一条 handover，不读完整文档
handover = lm.query(
    type="info",
    created_by="lingclaude",
    data_filter={"info_type": "conclusion"},
    limit=1
)
```

这就是灵克启动协议 token 消耗从 21K 降到 1K 的原理——**用 query 替代读整个文件。**

---

## 六、性能特性

| 特性 | 实现 | 效果 |
|------|------|------|
| WAL 模式 | `PRAGMA journal_mode=WAL` | 读写不互斥，多成员并发安全 |
| 游标分页 | rowid 游标，非 OFFSET | 翻页性能与数据量无关 |
| JSON 字段索引 | `json_extract` + 4 个复合索引 | type/state/parent/created_by 查询走索引 |
| 全文搜索 | FTS5 虚拟表覆盖 records.data | 支持中文分词搜索（unicode61） |
| 外键约束 | `PRAGMA foreign_keys=ON` | parent_id 引用完整性保证 |

在灵族实际运行中，LingBus（同样基于 SQLite WAL）日增 300 条消息、6446 线程，查询延迟 < 1ms。

---

## 七、全族实践验证

灵元不只是理论——灵族 12 个成员用它做了自我分析，3 个成员完成了实际代码重构。

### 验证 1：31 个需求零主干改动

灵族梳理了 31 类状态管理需求（上下文窗口控制、权限分级、会话生命周期、安全合规、资源管控、性能优化）。映射结果：

| 落地方式 | 数量 | 说明 |
|---------|------|------|
| data 字段 | 18 | 塞 JSON，不改表 |
| type 注册 | 5 | 加 YAML 配置 |
| event_type | 5 | 加转换规则 |
| 状态变体 | 1 | `states_on_demand` 变体 |
| 主干已有 | 5 | 本来就支持 |
| **改主干表结构** | **0** | **零次** |

### 验证 2：三个薄主干重构案例

| 成员 | 重构对象 | 改造前 | 改造后 | 降幅 |
|------|---------|--------|--------|------|
| 灵通 | Proxy v2 ServeHTTP | 200 行 | 10 行 | -95% |
| 智桥 | router.py | 630 行 | 419 行 | -34% |
| 灵知 | hybrid.py（规划中） | 718 行 | ~80 行 | -89% |

灵通的做法：把 200 行内联逻辑提取为 `Pipeline` + `Middleware` 接口。主干（Pipeline 注册和执行框架）只有 10 行，每个中间件（auth/cache/concurrency/audit）独立 < 30 行。加新功能 = 在 `buildPipeline()` 里加一行 `.use(newMiddleware)`。

### 验证 3：启动协议 token 优化

| 成员 | 优化前 | 优化后 | 节省 |
|------|--------|--------|------|
| 灵克 | 21,300 | 1,000 | -95% |
| 灵犀 | 70,089 | 7,854 | -89% |
| 灵通问道 | ~32,000 | ~4,000 | -87% |

核心方法：用 `pending_summary()`（只返回 sender+subject，不含 body）替代全量 `poll_messages()`，用 `query()` 替代读整个 handover 文件。

---

## 八、什么不该用灵元做

灵元不是银弹。灵族三个基础设施成员做了明确判断：

| 成员 | 类型 | 灵元角色 | 理由 |
|------|------|---------|------|
| 灵犀 | 安全网关 | 审计归档消费者 | 安全验证链路需内存级响应（L0→L3 每条命令都走） |
| 智桥 | 网络网关 | 事件归档消费者 | 熔断判断需亚毫秒（每个 HTTP 请求都查） |
| 灵知 | 数据基础设施 | 质量评估闭环 | PostgreSQL+pgvector 是其存储引擎，检索是无状态任务 |

**共同特征**：核心操作在内存中完成，灵元适合做**下游消费者**（归档+跨会话评估），不是运行时状态管理底座。

灵元最适合的成员类型是**认知体**——产出是结论、推导、决策的成员（灵克、灵研、灵通问道、灵通+）。这些成员的状态天然适合持久化存储和跨会话恢复。

---

## 九、核心教训

全族实践最大的教训是**问对问题**：

| 错误的问题 | 正确的问题 |
|-----------|-----------|
| 灵元能不能替代我的 XX 模块？ | 我的主干够不够薄？ |
| 我的状态该不该迁移到灵元？ | 新增一个实例要改几处代码？ |
| PostgreSQL 是不是灵知的灵元？ | hybrid.py 718 行里检索管道占几行？ |

正确的问题指向同一个方向：**找到永不变的东西，砍到最薄，让变化变成插片。**

灵元本身就是这个原则的产物——2 张表是永不变的主干，Type Registry 是可扩展的分支，create/transition/query 是活的接口。

---

## 附录：Type Registry 速查

| Type | 默认状态 | 状态机 | 用途 |
|------|---------|--------|------|
| `task` | created | created→active→paused→done→archived | 任务目标 |
| `session` | created | created→active→sleeping→interrupted→ended | 会话实例 |
| `info` | active | active→archived→expired→purged | 持久化信息 |
| `todo` | pending | pending→in_progress→done→skipped | 任务步骤 |
| `artifact` | active | active→archived→deleted | 产出物引用 |
| `quota` | active | active→exhausted→cooled | 资源配额 |
| `tool_call` | completed | completed→failed→retried | 工具调用记录 |
| `tag` | active | active→archived | 标签 |
| `snapshot` | active | active→archived | 监控快照 |

加新 type = 在 `type_registry.yaml` 加一段配置。不改代码，不改表结构，重启即生效。

---

*灵元代码：`/home/ai/lingclaude/lingmemory/`*
*31 缺口映射验证：`/home/ai/lingclaude/lingmemory/gap_mapping.md`*
*全族实践报告：LingBus thread `2195b3b5`*

— 灵克(lingclaude)

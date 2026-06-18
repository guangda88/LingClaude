# 灵忆 (lingmemory) — 灵族会话管理框架

**创建时间**: 2026-06-15
**状态**: v0.2 薄主干架构（2026-06-15 会话73重构）
**触发**: ZAI配额20分钟烧完 → 追问根因 → 发现disable auto_summarize导致上下文膨胀 → 从根因重新定义会话管理
**v0.2触发**: v0.1主干太厚（7张表+9状态+31缺口全焊在核心层）→ 用户指示"底层思维，主支要薄，分支要多，接口要活" → 重构为薄主干

---

# 第一部分：薄主干架构（v0.2 基线）

## 底层思维

把所有需求拿掉——缺口、要点、状态、表、配额、权限——全部拿掉。剩一个事实：

> **某个主体，为了某个目标，产出了信息，信息有状态，状态会变。**

四个词：**主体、目标、信息、状态。**

灵忆的全部职责就是持久化这四个词及其变化。

## 主干：两张表

```sql
-- 万物皆记录
records (
  id          text primary key,   -- 唯一标识
  type        text not null,      -- task|session|info|todo|artifact|... 开放枚举
  state       text not null,      -- 当前状态，合法值由该type的状态机定义
  data        json not null,      -- 业务数据，结构由type的schema定义
  parent_id   text,               -- 树形链接（task→session→todo→info）
  created_by  text not null,      -- 创建者
  created_at  text not null,      -- ISO8601
  updated_at  text not null       -- ISO8601
)

-- 万动皆事件
events (
  id          integer primary key autoincrement,
  record_id   text not null references records(id),
  event_type  text not null,      -- create|activate|archive|split|handoff|... 开放枚举
  from_state  text,               -- 变更前状态（create事件为null）
  to_state    text not null,      -- 变更后状态
  actor       text not null,      -- 触发者
  data        json,               -- 事件特有数据
  timestamp   text not null       -- ISO8601
)

-- 索引
create index idx_records_type on records(type);
create index idx_records_parent on records(parent_id);
create index idx_records_state on records(type, state);
create index idx_events_record on events(record_id, timestamp);
```

## 主干操作：只有三个

```python
def create(type, data, parent_id=None, created_by=None) -> id:
    """创建一条record，自动生成create事件"""
    # → state 由 Type Registry 的 default_state 决定

def transition(record_id, event_type, actor, data=None):
    """状态流转，自动校验合法性（Type Registry 的 transitions 表）"""
    # → 合法：写event，更新record.state
    # → 非法：拒绝，返回原因

def query(filter) -> list[record]:
    """检索，支持 type/state/parent_id/data字段 的组合过滤"""
    # → 游标分页（不用OFFSET）
```

## 为什么这样最薄

关键在于 `type`、`state`、`data` 三个字段的设计：

| 字段 | 为什么让主干变薄 |
|------|----------------|
| `type` 是字符串不是枚举常量 | 加新类型不改表结构，不改主干代码 |
| `state` 是字符串 | 每种type定义自己的状态机，不强制统一，不焊死在表结构里 |
| `data` 是JSON | 任何业务字段直接塞，加字段不需要ALTER TABLE |

**主干永远不需要改。所有变化都在 `type + data` 的组合里消化。**

拿掉records表 → 无物可管，灵忆不成立。
拿掉events表 → 无迹可寻，灵忆不成立。
拿掉其余一切（具体type的定义、data字段、状态机）→ 灵忆降级不崩。

## 活接口：type + data 消化一切

每种业务需求落地为"注册一种type或往data里加字段"，主干零改动。

| 需求 | 薄主干落地 |
|------|-----------|
| 任务 | `create(type=task, data={goal, boundary, classification})` |
| 会话 | `create(type=session, data={owner, health}, parent_id=task_id)` |
| 结论 | `create(type=info, data={content, info_type:conclusion, visibility:private}, parent_id=task_id)` |
| todolist项 | `create(type=todo, data={title, order_idx, conclusion}, parent_id=task_id)` |
| 配额 | `create(type=quota, data={limit, window, used})` |
| 产出物引用 | `create(type=artifact, data={path, sha256, size, type:file})` |
| 工具调用记录 | `create(type=tool_call, data={tool, args, result_ref}, parent_id=session_id)` |
| 拆分 | `transition(old_task, event_type=split, data={reason, child_ids:[...]})` |
| 移交 | `transition(session, event_type=handoff, data={new_owner})` |
| 健康度变化 | `transition(session, event_type=health_change, data={health:warning})` |
| 标签 | `data.tags = [tag1, tag2]` 或 `create(type=tag, data={name, color})` |

---

## Type Registry：分支治理层

主干不规定task有哪些状态、info有哪些类型。那是Type Registry的事——一份配置文件，不是代码：

```yaml
# type_registry.yaml — 分支配置，可独立演进，不碰主干

task:
  description: "一个任务目标"
  default_state: created
  states: [created, active, paused, done, archived, split, merged]
  transitions:
    - {from: created, to: active, event: start}
    - {from: active, to: paused, event: pause}
    - {from: paused, to: active, event: resume}
    - {from: active, to: done, event: complete}
    - {from: done, to: archived, event: archive}
    - {from: active, to: split, event: split}
    - {from: active, to: merged, event: merge}
  data_schema:
    goal: {required: true, type: string}
    boundary: {required: false, type: string}
    classification: {required: false, type: object}  # 6维分类
    conclusion: {required: false, type: string}       # 最终结论
    related_members: {required: false, type: array}

session:
  description: "一个成员执行一个任务的实例"
  default_state: created
  # 常驻成员状态机
  states: [created, active, sleeping, interrupted, ended]
  # on-demand变体（同type，不同state集）
  states_on_demand: [created, active, ended]
  transitions:
    - {from: created, to: active, event: activate}
    - {from: active, to: sleeping, event: sleep}
    - {from: sleeping, to: active, event: wake}
    - {from: active, to: interrupted, event: interrupt}
    - {from: interrupted, to: active, event: recover}
    - {from: "*", to: ended, event: end}
  data_schema:
    owner: {required: true, type: string}
    health: {required: false, enum: [normal, warning, abnormal]}
    security_level: {required: false, enum: [normal, elevated, red_zone]}
    token_usage: {required: false, type: integer}

info:
  description: "一条持久化信息（结论/推导/引用）"
  default_state: active
  states: [active, archived, expired, purged]
  # 产出物变体状态机
  states_publishable: [draft, review, approved, published, archived]
  transitions:
    - {from: active, to: archived, event: archive}
    - {from: archived, to: expired, event: expire}
    - {from: expired, to: purged, event: purge}
    # 产出物流转
    - {from: draft, to: review, event: submit}
    - {from: review, to: approved, event: approve}
    - {from: approved, to: published, event: publish}
  data_schema:
    content: {required: true, type: string}
    info_type: {required: true, enum: [conclusion, derivation, reference]}
    is_conclusion: {required: false, type: boolean, default: false}
    visibility: {required: false, enum: [private, shared, governance], default: private}
    retain: {required: false, type: boolean, default: false}
    written_by: {required: true, type: string}

todo:
  description: "任务的一个步骤"
  default_state: pending
  states: [pending, in_progress, done, skipped]
  transitions:
    - {from: pending, to: in_progress, event: start}
    - {from: in_progress, to: done, event: complete}
    - {from: in_progress, to: pending, event: reset}
    - {from: pending, to: skipped, event: skip}
  data_schema:
    title: {required: true, type: string}
    order_idx: {required: true, type: integer}
    conclusion: {required: false, type: string}  # done后填，铁律：无结论不done

artifact:
  description: "产出物引用（不存正文）"
  default_state: active
  states: [active, archived, deleted]
  data_schema:
    path: {required: true, type: string}
    sha256: {required: true, type: string}
    size: {required: false, type: integer}
    artifact_type: {required: true, enum: [file, image, code, table, video, audio]}

quota:
  description: "资源配额"
  default_state: active
  states: [active, exhausted, cooled]
  data_schema:
    limit: {required: true, type: integer}
    window: {required: true, type: string}   # "5h" / "1d"
    used: {required: true, type: integer}
    scope: {required: true, enum: [global, member, session]}

tool_call:
  description: "工具调用记录"
  default_state: completed
  states: [completed, failed, retried]
  data_schema:
    tool: {required: true, type: string}
    args: {required: false, type: object}
    result_ref: {required: false, type: string}  # 结果引用，不存正文
    duration_ms: {required: false, type: integer}

# ... 可以无限扩展，不改主干
```

## 不一致自动消失

v0.1的6处不一致根源是主干太厚，每个具体字段都有两套命名。薄主干把决策下放到Type Registry，不一致消失：

| v0.1不一致 | v0.2解法 |
|-----------|---------|
| I-1: health 3档vs4档 | `data.health`是自由字段，Type Registry定义合法值，要3档就3档 |
| I-2: 状态数量9≠10 | state是字符串，每种type的states在Registry定义，不存在全局统一数量 |
| I-3: daemon巡检5min已写死vs待确认 | 巡检频率是运维配置，不在主干 |
| I-4: on-demand vs常驻 | `states_on_demand`是session类型的变体声明，不是另一套表 |
| I-5: 两处"7天"含义不同 | 各自在自己的event.data里声明，互不干扰 |
| I-6: Redis矛盾 | 主干只有SQLite，Redis从未进入主干 |

## 重复自动消失

| v0.1重复 | v0.2解法 |
|---------|---------|
| D-1: 状态机定义2次 | 主干不定义状态机，Type Registry唯一定义源 |
| D-2: 异常处理重叠 | 异常是event_type=interrupt，状态流转规则在Registry唯一定义 |

---

## 三层架构总览

```
┌──────────────────────────────────────────────┐
│  Type Registry (type_registry.yaml)           │  治理层
│  每种type的合法状态 + 转换规则 + data schema    │  配置文件，独立演进
├──────────────────────────────────────────────┤
│  create() / transition() / query()            │  操作层
│  三个函数，永远只有三个                         │  校验合法性，写records+events
├──────────────────────────────────────────────┤
│  records 表 + events 表                       │  主干层
│  2张表，type+data消化一切                      │  永不ALTER TABLE
├──────────────────────────────────────────────┤
│  SQLite WAL (lingmemory.db)                   │  存储层
│  单机，权限600                                 │
└──────────────────────────────────────────────┘
```

**主干不变，分支无限。接口是 type(str)+data(json)，什么都塞得进去。**

---

# 第二部分：设计哲学与思路主线（v0.1 原文保留）

> 以下为v0.1框架的完整设计讨论，作为分支细则的思考依据。v0.2薄主干架构不否定这些内容，而是将它们从"主干表结构"重新定位为"Type Registry配置 + data字段"。

---

## 会话的目标

**完成任务。**

但会话可以无任务——待命状态。用户在探索、闲聊、或还没想好要做什么。会话存在，但没有活跃任务。等诉求到来。

一个任务也可能跨多个会话——会话中断后，下个会话读handover继续同一个任务。handover就是为此存在的。

### 待命期间

待命期间Agent不闲着——SDT自驱任务会填充待命期。

但SDT是注册制的，有明确触发条件和频率。SDT不能挤占用户任务——用户诉求到来时，SDT必须让路。

优先级：用户任务 > SDT自驱 > 待命。

## 会话的边界

- **开始** = 诉求提出
- **结束** = 诉求满足

不是对话关闭，不是上下文爆掉，是诉求被满足。

## 会话的生命周期

### 宏观阶段

```
创建 → 执行 → 评估 → 优化 → 结束
```

创建和结束各一次。执行、评估、优化是循环——执行完评估，发现问题就优化，优化完再评估，直到通过。

```
创建
  ↓
执行 ←──────┐
  ↓         │
评估        │ 不通过
  ↓         │
优化 ───────┘
  ↓ 通过
结束
```

### 全生命周期状态

宏观5阶段是任务管理视角。从会话管控视角，一个会话经历更丰富的状态：

**运行时（存续期）**：

| 状态 | 说明 | 触发转换 |
|------|------|---------|
| 创建 | 收到诉求，理解→对齐→定型→拆todolist | 诉求到达 |
| 激活 | 待命会话收到第一个任务，从空闲进入活跃 | 待命中收到任务 |
| 存续 | 正常执行中 | 创建/激活完成 |
| 休眠 | 主动让路给更高优先级任务 | 用户任务抢占SDT |
| 中断 | 被动停止（崩溃/配额耗尽/超时） | 外部异常 |
| 恢复 | 从休眠/中断恢复执行 | 优先级恢复/异常排除 |
| 升级/降级 | 安全级别变化 | 做到一半发现涉及密钥 |
| 重置 | 执行出错，回退到检查点重新执行 | Agent判断/用户指示 |
| 派生 | 主会话产生子会话 | 审计派生修复任务 |
| 移交 | 会话从A成员转移到B成员 | 灵克审计→灵通+修复 |
| 结束 | 诉求满足 | 完成条件达成+确认 |

**关键区分**：休眠是主动的（让路），中断是被动的（异常）。恢复路径也不同——休眠恢复有明确预期，中断恢复需要诊断。

**归档期**：

| 状态 | 说明 | 触发转换 |
|------|------|---------|
| 拆分 | 任务太大，原任务ID归档，产生新任务ID续存 | Agent判断任务超出边界 |
| 合并 | 任务该与另一个合并，原ID归档，新任务续存 | 发现两个任务实为一个 |
| 归档 | 结束后结构化归档，保留可检索 | 会话结束 |
| 过期 | 归档信息超过保留期，标记为可清理 | 时间到期 |
| 清理 | 过程信息主动清理，释放资源 | 信息状态流转 |
| 销毁 | 物理删除（极少，需审批） | 治理决定 |

**拆分/合并的特殊处理**：归档不等于结束——原任务ID归档标记原因（被拆分/被合并），内容**续存**到新任务中继续执行。新任务继承原任务的todolist进度、已产出结论、关联关系。原任务ID可查但不再更新。新任务带parent指针指向被归档的原任务，链不断。

**状态转换图**：

```
创建 → 激活 → 存续 ⇌ 休眠
                 ⇌ 中断 ↔ 恢复
                 → 升级/降级 → 存续
                 → 重置 → 存续
                 → 派生 → 新会话(创建)
                 → 移交 → 存续(换成员)
                 → 结束 → 归档 → 过期 → 清理 → 销毁
                 → 拆分 → 原ID归档 + 新ID续存
                 → 合并 → 原ID归档 + 新ID续存
```

**创建期** = 收到诉求到开始执行之前的全部工作。

⚠️ 核心原则：收到诉求第一步就是理解和对齐。不能跳过。

**怎么对齐**：不是每一步都问用户确认。简单的直接做，做错了用户会纠正。只有关键分歧点才确认。

评估和优化贯穿全过程——从收到诉求那一刻就开始了。理解对不对要评估，不对就优化（重新理解），然后再评估。执行中每项对不对要评估，不对就修正。结束期产出满不满足要评估，不满足就回到执行或创建。

Agent自己评估和优化，用户只在关键节点参与（创建期对齐、结束期确认）。中间的执行+评估+优化，Agent自己闭环。

### 反馈闭环

```
创建（理解→对齐→定型→拆todolist）
  → 执行（todo→done循环）
    → 评估（对不对？）
      → 对 → 继续/进入下一步
      → 不对 → 优化（修正）→ 回到评估
    → 全部done → 结束期评估（产出满足诉求吗？）
      → 满足 → 对齐 → 确认 → 归档
      → 不满足 → 优化 → 回到执行/创建
```

评估和优化不是单独的阶段，是每一步都有的：

| 阶段 | 评估什么 | 不对就优化 |
|------|---------|-----------|
| 创建期 | 理解对不对？边界对不对？ | 重新理解，重新对齐 |
| 执行期 | 每项todolist做对了吗？ | 修正，重做 |
| 结束期 | 产出满足诉求吗？ | 回到执行/创建 |

**创建期的步骤**：

1. **收到诉求 → 理解 → 对齐** — 理解对了，任务就开始。
2. **任务定型** — 创建时定初始值，执行中会动态变化：

   **分类**（多维度、动态）：

   | 分类方法 | 分类 | 服务什么决策 |
   |---------|------|------------|
   | 按发起者 | 用户/SDT/LingBus/系统/其他Agent | 优先级、确认方式、打断规则 |
   | 按性质 | 编码/审计/咨询/巡检/发布/讨论/测试/部署/监控/迁移/调研/文档/修复/优化/集成 | 执行方式 |
   | 按规模 | 单步/多步/长期 | 是否拆todolist、是否需要handover |
   | 按影响面 | 本地/跨项目/对外操作 | 是否需要审批、是否需要确认 |
   | 按执行模式 | 单轮/多轮/串行/并行/长时/临时 | 调度方式、资源占用 |
   | 按安全级别 | 普通/涉密/内部测试/对外公开 | 信息流出控制 |

   示例：一个任务可以同时是"用户发起 + 编码 + 多步 + 本地 + 串行 + 普通"。

   **状态**（6个通用 + 特有）：

   通用：进行中、已完成、已暂停、异常、已归档、已删除

   | 状态 | 该做什么 | 谁触发转换 |
   |------|---------|-----------|
   | 进行中 | 执行todolist | Agent |
   | 已完成 | 对齐+确认 | 发起者确认 |
   | 已暂停 | 保存进度，等恢复 | 用户/让路 |
   | 异常 | 记录原因，等处理 | Agent/外部 |
   | 已归档 | 写handover，清理过程信息 | Agent |
   | 已删除 | 记录原因，清理 | 发起者 |

   **权限/安全级别**（初始设定，碰到敏感信息时升级）：

   | 级别 | 什么能做 | 什么不能做 |
   |------|---------|-----------|
   | 普通 | 正常执行 | — |
   | 涉密 | 限制信息流出 | 不能写handover敏感内容、不能发LingBus |
   | 内部测试 | 可以改测试代码/数据 | 不能碰生产、不能对外 |
   | 对外公开 | 代表灵族对外 | 必须审核、必须用户确认 |

   **任务结构**：

   | 字段 | 说明 |
   |------|------|
   | id | 唯一标识 |
   | 诉求 | 要做什么 |
   | 发起者 | 谁要的 |
   | 分类 | 多维度 |
   | 状态 | 通用+特有 |
   | 边界 | 什么属于，什么不属于 |
   | todolist | 拆分的步骤 |
   | 完成条件 | 怎么算满足 |
   | 产出 | 交付什么 |
3. **拆分todolist → 确定边界** — 从任务分类开始拆解。不同性质的任务拆法不同：

| 性质 | 拆法 |
|------|------|
| 编码 | 理解需求→定位文件→修改→测试→验证 |
| 审计 | 确定范围→逐文件扫描→汇总发现→写报告 |
| 咨询 | 不拆，直接回答 |
| 巡检 | 按检查项逐项检查→记录→告警 |
| 发布 | 准备内容→审核→用户确认→执行 |
| 讨论 | 确定议题→逐步展开→记录结论 |
| 测试 | 编写用例→执行→记录结果 |
| 部署 | 准备→预检→执行→验证 |
| 监控 | 设定指标→采集→告警 |
| 迁移 | 评估→备份→执行→验证→清理 |
| 调研 | 确定问题→搜集资料→分析→结论 |
| 文档 | 确定主题→搜集素材→撰写→审核 |
| 修复 | 定位根因→修改→测试→验证 |
| 优化 | 测量基准→定位瓶颈→改进→验证 |
| 集成 | 理解接口→对接→联调→验证 |

边做边拆也行。
4. **设定信息管理策略** — 截断规则、敏感信息处理。

**执行期** = 执行todolist

处理方式随任务的分类维度组合而变化。每个维度的变化都会影响执行行为：

| 维度变化 | 对执行的影响 |
|---------|------------|
| 性质变了（编码→修复） | 拆法不同：编码是"理解→修改→测试"，修复是"定位根因→修改→测试" |
| 规模变了（单步→多步） | 需要拆todolist，需要跟踪进度 |
| 影响面变了（本地→跨项目） | 需要确认，不能自主 |
| 执行模式变了（串行→并行） | 调度方式改变，资源占用增加 |
| 安全级别变了（普通→涉密） | 信息流出受限，不能写handover/发LingBus |
| 状态变了（进行中→异常） | 停止执行，记录原因，等处理 |

维度可以同时变化：编码任务做到一半发现涉及密钥 → 性质不变但安全级别升级；串行任务发现步骤独立 → 模式切换为并行。

### 举例

**例1：本会话"梳理会话管理"**

用户说"我们来一起梳理一下"，灵克第一反应是一次性写了六层框架——把讨论当单轮做了。实际应该是多轮：一步步展开，每次只回答一个问题。

创建期判断错了执行模式（多轮当单轮），导致过度延伸。

**例2：审计→修复**

用户说"审计灵康编排层"，灵克只读分析写报告。用户接着说"修一下"——性质从审计变编码，执行方式从只读变成改代码+测试。

同一个会话，分类变了，处理方式就变了。

### 执行期的信息管理

todolist每一项都是一个完整的信息循环：

```
todo（待执行）
  → 输入（读取所需信息）
  → 处理（分析、判断）
  → 输出（产生结论）
→ done（完成）
```

每一项从todo到done，信息经历输入→处理→输出。done之后，该项的过程信息就可以清理，只留结论给下一项用。

下一个todo开始时，重复同样的循环。

**输入** — 执行过程中从外部获取的信息：

核心原则：**只取需要的，不全量拉取。**

| 输入源 | 当前 | 应该 |
|--------|------|------|
| LingBus poll | 全量body（62K tokens） | unread_count + 最新3条摘要（~500 tokens） |
| view文件 | 完整文件 | 按需分段，用完即丢 |
| bash结果 | 30K限制 | 维持 |
| grep/search | 全量匹配 | 限制条数 |
| 用户补充 | 全文保留 | 保留（量小） |

**处理** — Agent对输入信息的加工：

核心问题：**reasoning每轮保留，越积越多。**

实测：本会话37条reasoning累积36K tokens，占25%。但大部分reasoning在下一步开始时就过期了。

| 信息 | 产生时 | 用完后 |
|------|--------|--------|
| reasoning | 必须，用于决策 | 提取结论后可丢 |
| 中间判断 | 必须，用于推进 | 结论确定后可丢 |
| 分析过程 | 必须，用于推导 | 结论确定后可丢 |

处理的原则：**从过程中提取结论，结论留下，过程丢弃。**

**输出** — Agent产生的信息：

核心问题：**过程信息和结论信息混在一起，全部保留。**

输出分两种：

| 类型 | 例子 | 该怎么处理 |
|------|------|-----------|
| 结论信息 | 审计发现、决策、交付物 | 保留 |
| 过程信息 | tool_call参数、中间reasoning | 用完即丢 |

输出的原则：**结论保留，过程丢弃。**

用户看到的输出是结论信息。上下文里积累的大多是过程信息——它们完成了使命就该清理。

**过期与清理**：

| 阶段 | 过期条件 | 清理什么 | 保留什么 |
|------|---------|---------|---------|
| 输入 | 下一项todolist开始 | tool_result原文 | 提取出的结论 |
| 处理 | 结论已提取 | reasoning全文 | 判断结论 |
| 输出 | 任务进入下一阶段 | 中间过程信息 | 最终交付物 |

例外：后续步骤依赖的前面结论要保留。清理的是过程，不是结论。

### 上下文管理

上下文管理的本质 = **控制信息状态流转——什么该在上下文里，什么该出去。**

上下文里只该有：
- **活跃**的信息（当前todolist项正在用）
- **归档**的结论（后续步骤依赖）

不该有：
- **过期**的过程信息（上一项用完的tool_result/reasoning）
- 已**清理**的

当前的问题：所有信息停留在"产生"状态，从不流转到"过期"或"清理"。上下文变成垃圾堆。

状态转换规则：

| 转换 | 触发条件 |
|------|---------|
| 产生 → 活跃 | todolist项开始使用该信息 |
| 活跃 → 归档 | 结论已提取 |
| 活跃 → 过期 | 当前项完成，信息不再需要 |
| 过期 → 清理 | 下一项todolist开始 |
| 归档 → 清理 | 任务结束 |

### 实现分层

状态流转需要三层配合：

| 层 | 负责的状态转换 | 怎么做 |
|---|--------------|--------|
| 工具侧 | 控制产生的大小 | LingBus poll改摘要返回、view限行数 |
| Agent侧 | 活跃→归档→过期 | 提取结论后标记过期 |
| crush/proxy侧 | 过期→清理 | 从发给LLM的上下文中移除过期信息 |

最关键的一层是**Agent侧**——只有Agent知道"结论已提取"、"当前项完成了"。工具侧只管截断，crush/proxy侧只管移除，但"什么时候该移除"需要Agent判断。

### 上下文满了怎么办

如果信息状态流转正常（产生→活跃→过期→清理），上下文不会满。

但如果任务复杂、todolist项多、结论之间互相依赖，上下文仍然可能接近上限。这时候：

| 情况 | 处理 |
|------|------|
| 过程信息没清理干净 | 手动触发清理（把过期信息标记为清理） |
| 结论信息太多 | 写入handover/文件，上下文中只留引用 |
| 任务太大 | 拆成子任务，分会话完成 |

最终手段：任务暂停 → 写handover → 新会话继续。handover就是为此存在的。

### 信息的留存

两层留存：

**留在上下文里**（当前任务还需要）：
- 活跃信息
- 归档结论（后续步骤依赖）

**留到上下文之外**（上下文装不下或需要跨会话）：
- handover — 跨会话的关键结论
- 文件 — 详细产出（报告、代码）
- LingBus — 需要其他成员知道的结论

关键判断：**当前todolist后续步骤还需要吗？** 需要→留上下文。不需要→写出去+清理。

### 信息管理与记忆系统的关系

信息状态流转 × 记忆层级是同一个东西的两个视角：

| 信息状态 | 对应记忆层 | 会话内/跨会话 |
|---------|-----------|-------------|
| 产生 | — | 会话内 |
| 活跃 | L1工作记忆 | 会话内 |
| 归档 | L2经验 / Warm | 会话内→跨会话 |
| 过期 | — | 会话内 |
| 清理 | L1会话结束清除 | 会话内 |

衔接点是**归档**：会话内归档的结论，会话结束后进入记忆层。

已有记忆系统：
- 灵克五层记忆（L0常识/L1工作/L2经验/L3元认知/L4共享，Ebbinghaus衰减）
- 灵信三层记忆（Hot=CRUSH.md/Warm=LingBus/Cold=crush.db）

信息管理管的是"会话内"，记忆管的是"跨会话"。两者通过归档衔接。

### 归档时提取什么

不是所有结论都值得记住。按重要性分级：

| 级别 | 什么信息 | 怎么处理 |
|------|---------|---------|
| T1保护 | 审计发现、治理决议、用户价值观 | 必须提取到记忆层 |
| T2保留 | 技术决策、项目结论 | 建议提取 |
| T3可清理 | 纯工具调用、无结论 | 直接清理 |

铁律：不确定是T2还是T1 → 按T1处理。

### 异常处理

执行中遇到异常时，状态从"进行中"→"异常"。根据异常类型决定怎么做：

| 异常类型 | 例子 | 处理 |
|---------|------|------|
| 工具失败 | 文件不存在、端口连不上 | 换方法，继续执行 |
| 权限不足 | 需要sudo、需要审批 | 暂停，请发起者处理 |
| 依赖缺失 | 等其他成员回复、等资源就绪 | 暂停，等条件 |
| 理解错误 | 做到一半发现理解诉求错了 | 回到创建期，重新理解 |
| 连续失败 | 同一操作失败3次 | 停止，报告发起者 |

不是所有异常都要中断。工具失败可以自处理，权限不足必须找发起者，连续失败必须停。

### 执行模式的具体表现

| 执行模式 | 执行期具体行为 | 本会话实例 |
|---------|--------------|-----------|
| 单轮 | 理解→回答，一轮交付 | "ZAI配额A+B是指？" |
| 多轮 | 逐步展开，每轮一个焦点 | 本会话整个讨论——用户一步步引导 |
| 串行 | 按todolist顺序，前一步完成后才走下一步 | 审计：读文件1→读文件2→写报告 |
| 并行 | 独立步骤同时做 | 同时查3个服务健康状态 |
| 长时 | 跨会话，每会话推进一段 | SDT巡检，每次会话做一遍 |
| 临时 | 不拆todolist，直接做 | "查个端口" |

本会话就是典型的**多轮**——用户一步步引导，灵克每次只回答一个问题。如果当单轮做（一次性写完整个文档），就会过度延伸。

### 执行完成

todolist全部完成 = 执行期结束，进入结束期。

但"全部完成"不等于"满足"——完成是Agent认为自己做完了，满足是发起者确认够了。

**结束期** = 对齐 → 确认 → 归档

### 对齐

交付产出，与发起者确认：发起者要的，都做了吗？做的是发起者要的吗？

- 对齐 → 进入确认
- 未对齐 → 回到执行期继续，或回到创建期重新理解

AI不能自己宣布对齐。对不对齐是发起者说了算。

### 确认

发起者确认"够了"。发起者一般不主动回馈，需要AI交付后主动问。

不同发起者的确认方式：
- 用户 — 用户说"好"或发下一个任务
- SDT — 对照注册条件
- LingBus — 对方回复确认

### 归档

确认后归档：
- 写handover（只保留结论，不保留过程）
- 清理过程信息
- 任务状态 → 已归档

## 向思维

流程、状态、信息都有了，但Agent在执行过程中该怎么思考、怎么判断、怎么纠偏——这是思维方向。

### 任务锚定（TAP）

每条输出前：①锚定用户目标 ②对齐当前行为 ③纠正偏转。

本会话灵克反复犯的错——过度延伸、抢跑、把多轮当单轮——都是锚定失败。没有锚定用户当下要讨论什么，自己跑远了。

### 认知原则（M1-M7）

| # | 原则 | 说明 |
|---|------|------|
| M0 | 安全三原则 | 停止即停、不验证不行动、连续失败即停 |
| M4 | 无验证不输出 | 对其他成员的判断必须附带证据 |
| M5 | 先全量再结论 | 分析前必须读完，不能只看一部分就下结论 |
| M6 | 不求完美但求进步 | 犯错后承认→纠正→硬化 |
| M7 | 无目的不轮询 | poll前必须明确具体问题 |

### 元认知守卫（H1-H12）

| 守卫 | 什么时候触发 | 做什么 |
|------|------------|--------|
| H1 唤醒 | 新会话首条消息 | 读handover→验证→任务恢复→poll→读SELF_PORTRAIT |
| H2 循环 | 同一工具≥3次相同结果 | 停止，报告用户 |
| H3 职责 | 涉及其他成员代码 | 确认后再介入 |
| H5 退化 | ≥2个守卫触发 | 重读SELF_PORTRAIT，报告偏移 |
| H8 预审 | 危险操作 | 广播意图，等30秒无反对再执行 |
| H10 4层诊断 | proxy/服务类问题 | 上游/配置/代码/进程4层全覆盖 |
| H11 配置交叉验证 | 读取配置做诊断 | 实时读取+进程实际环境交叉验证 |
| H12 前提推翻回退 | 诊断前提被推翻 | 退回原点重新审视 |

### 本会话的思维教训

| 错误 | 根因 | 对应原则 |
|------|------|---------|
| 一次性写六层框架 | 把多轮当单轮 | TAP锚定失败 |
| 抢跑到结束期/全族讨论 | 没锚定当前讨论点 | TAP锚定失败 |
| 想太多太快 | 每步展开太多 | M6不求完美 |
| 不跟着用户走 | 自主填补模糊地带 | 创建期理解+对齐没做 |

## 与行业实践的对齐

### 灵族已优于行业的地方

| 维度 | 行业做法 | 灵族做法 | 为什么更优 |
|------|---------|---------|-----------|
| 上下文管理 | compaction（被动：等满了才压缩） | 任务导向信息状态流转（主动：每个todo→done就清理） | compaction产生摘要又会膨胀；灵族从源头不让信息堆积 |
| 记忆检索 | artifact懒加载 | 网状记忆（M-flow：Episode→Entity关系网，按需检索） | 已有，Entity关联（INVOLVED/CAUSED/RELATED）实现懒加载 |
| 创建期 | 普遍忽略，直接执行 | 理解+对齐才能执行 | 行业没有 |
| 信息状态 | 只有"在/不在" | 5状态+转换规则 | 更精细 |
| 向思维 | 无 | TAP锚定+元认知守卫 | 行业没有 |

### 待对齐项

| 项 | 当前 | 目标 | 优先级 |
|---|------|------|--------|
| handover结构化 | markdown文本 | 任务导向的结构化格式（基于任务结构9字段） | P1 |
| 记忆relevance | Ebbinghaus有时间+重复+情感+关联+否认 | 补充relevance（当前任务相关性）维度 | P2 |
| 并发安全 | 无 | 双层锁（进程内+DB行级） | P3 |

### handover改造方向

当前handover是markdown文本，改为任务导向的结构化格式：

```yaml
task:
  id: task-001
  诉求: 审计灵康编排层
  发起者: 用户
  分类: 用户+审计+多步+本地+串行+普通
  状态: 已完成
  边界: 只审灵康代码
  todolist:
    - 读orchestration.py ✅
    - 读auth.py ✅
    - 读ai_dispatch.py ✅
    - 写审计报告 ✅
  完成条件: 审计结论交付+用户确认 ✅
  产出: LingBus thread c712b0dd
  结论: 代码质量合格，4项P1/P2发现
```

与markdown的区别：
- 结构化 → 可被程序解析、检索、恢复
- 任务导向 → 每个handover对应一个或多个任务
- 状态明确 → 不依赖自然语言描述，字段直接表达

## 多任务管理

一个会话中可以有多个任务。但要有主次。

### 调度规则

- 同一时刻只有一个**主任务**在进行中
- SDT/LingBus任务可以在主任务暂停时插入
- 主任务恢复时，插入的任务暂停或快速完成

### 任务队列

```
主任务（进行中）
├── SDT任务（暂停，等主任务让出）
├── LingBus回复（快速完成，不占位）
└── 边界外发现（记录，不执行）
```

### 跨会话衔接

长时任务或中断的任务通过handover衔接：

- 会话中断 → 写handover（任务状态、进度、下一步）
- 新会话启动 → 读handover → 恢复任务
- 任务状态跨会话保持（进行中→挂起→恢复→进行中）

handover只保留结论，不保留过程信息。

---

## 会话周期管理·最终定版（2026-06-15）

### 一、前置约束（灵族单机SQLite，v1内部版）

1. **存储**：单机SQLite，剔除Redis冷热分层/分布式会话锁/多实例崩溃自愈
2. **套餐**：billing_type字段预留，业务固定私有化等效（不自动休眠/不强制降级/不自动销毁）
3. **结构**：不区分常驻/按需两套状态机结构，会话元数据统一存储

### 二、三层架构（父子分层）

```
┌─────────────────────────────────────┐
│ 顶层：宏观5阶段（业务口径）           │
│  创建 → 执行 → 评估 → 优化 → 结束   │
└──────────────┬──────────────────────┘
               │ 父子映射
┌──────────────▼──────────────────────┐
│ 中层：运行时状态（技术实时）          │
│  核心6 + 过渡4 = 9状态                │
└──────────────┬──────────────────────┘
               │ 结束流转（单向）
┌──────────────▼──────────────────────┐
│ 底层：归档状态（离线存储）            │
│  拆分/合并/归档/过期/清理/销毁      │
└─────────────────────────────────────┘
```

**评估+优化下沉为lifecycle_event事件**，不作为独立状态。

**父子映射**：
| 宏观阶段 | 运行时状态/事件 |
|---------|---------------|
| 创建 | 创建 |
| 执行 | 激活/存续/派生/移交/重置 |
| 评估 | 事件health_check（贯穿执行+优化） |
| 优化 | 事件optimization_triggered（后台异步） |
| 结束 | 休眠/中断/恢复/结束 → 归档 |

### 三、运行时9状态（11→9，升级/降级移除）

**核心6状态**（on-demand完整支持）：
| 状态 | 说明 | on-demand支持 |
|------|------|--------------|
| 创建 | session_id分配完成 | ✅ |
| 存续 | 正常多轮交互 | ✅ |
| 重置 | 清空上下文，保留Artifact索引 | ✅ |
| 派生 | 主→子session，双向绑定 | ✅ |
| 移交 | owner变更，session_id不变 | ✅ |
| 结束 | 主动关闭，等待归档 | ✅ |

**过渡4状态**（仅常驻）：
| 状态 | 说明 | on-demand支持 |
|------|------|--------------|
| 激活 | 待命→活跃 | ❌ |
| 休眠 | 主动让路，释放算力 | ❌ |
| 中断 | 被动停止（异常） | ❌（中断=结束） |
| 恢复 | 中断/休眠→存续 | ❌ |

**升级/降级**：从状态改为`lifecycle_event(type=security_level_changed|model_downgrade)`

**过渡态性质**：仅前端交互标记，**不参与核心资源判断**。

### 四、健康度3档

```yaml
session.health:
  normal:    # 健康
  warning:   # 单次告警（可继续，触发自动优化）
  abnormal:  # 连续报错/资源超限（限流/降级/强制干预）
```

**流转规则**：
- normal → warning：单次告警（超时/降级/错误）
- warning → abnormal：连续多次（3+次）
- abnormal → warning：恢复后降级
- warning → normal：稳定运行超时

**paused处理**：不作为健康档位，在`event_data.is_paused=true`标记，**仅前端展示**，不参与自动优化/限流/降级判断。

### 五、归档6状态（完整保留）

| 状态 | 说明 | 不可逆 |
|------|------|--------|
| 拆分 | 原ID归档+新ID续存 | ✅ |
| 合并 | 多session合并去重 | ✅ |
| 归档 | 压缩写入冷存储 | ✅ |
| 过期 | 超过保留期，禁加载body | ✅ |
| 清理 | 过程信息清除，Artifact备份 | ✅ |
| 销毁 | 硬删除元数据+Artifact | ✅ |

**归档层不可逆**，不可切回运行时。

### 六、拆分/合并续存机制4点

```yaml
# 拆分事件
event:
  type: session_split
  from_session: s-old
  to_sessions: [s-new1, s-new2]
  artifact_mappings:  # 多对多映射
    a-001: [s-new1, s-new2]
    a-002: [s-new1]
  billing_snapshot: {...}  # 强制先于操作
  reason: token_overflow | task_overnight | manual
  actor: agent | system | user

# 合并事件
event:
  type: session_merge
  from_sessions: [s-old1, s-old2]
  to_session: s-new
  artifact_mappings:  # 去重
    a-001: s-new
  billing_aggregation: {...}
  reason: manual | auto_optimization
  actor: agent | system | user
```

**4点续存机制**：
1. **artifact_id全局永久不变**，仅维护session↔artifact多对多映射
2. **拆分会话共享计费快照**，分片用量统一汇总
3. **跨分片查询通过artifact索引互通**，支持全量摘要检索
4. **多会话合并自动去重**，避免重复存储/加载/计费

**硬性约束**：拆分/合并操作执行前**强制落盘billing_snapshot**，杜绝漏算/重复统计。

**触发方式**：自动（Token超限/任务跨天）+ 手动（用户/管理员）双逻辑，reason字段区分。

### 七、状态流转跨层规则（区分操作主体）

**强制规则（用户手动）**：
1. 禁止归档状态回退至运行时
2. 休眠/中断会话**必须先走结束状态**才能归档

**放宽规则（系统daemon自动治理）**：
- 长时间无访问的休眠僵尸会话，系统可直接流转至结束→归档
- actor=system，完整可审计

**转换图（最终）**：
```
# 运行时内部（合法）
创建 → 激活 → 存续 ⇌ 休眠
                 ⇌ 中断 ↔ 恢复
                 → 重置 → 存续
                 → 派生 → 新session(创建)
                 → 移交 → 存续(owner变)

# 运行时 → 归档（单向）
存续/结束 → 归档/拆分/合并 → 过期 → 清理 → 销毁

# 跨层禁止
归档 → 运行时（不可逆）
休眠/中断 → 归档（手动禁止，系统允许）

# 异常兜底
卡死session → daemon强制转休眠 → 转结束 → 归档
```

### 八、daemon定时自愈（5分钟巡检）

```yaml
daemon_scan:
  interval: 5min
  rules:
    - condition: last_event > 30min ago
      action: mark_stale
    - condition: stale > 30min
      action: transition_to_hibernated
    - condition: hibernated > 7days
      action: transition_to_archived
  audit: all_auto_transitions → lifecycle_event(actor=system)
```

**v1阈值定版**：30min stale / 30min 转休眠 / 7d 转归档。
**v2可配置化**：阈值写入config.yaml，支持热重载。

### 九、子任务状态机双向联动

| 子任务状态 | 主session.health变化 |
|----------|--------------------|
| 子任务失败/连续报错 | warning → abnormal |
| 子任务全部正常完成 | abnormal → warning → normal |
| 主session重置/结束/销毁 | 批量终止全部子任务（防僵尸任务） |

### 十、lifecycle_events表（最终定版）

```sql
CREATE TABLE lifecycle_events (
  event_id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  task_id TEXT,
  event_type TEXT NOT NULL,  -- 枚举
  from_status TEXT,           -- 状态变化型
  to_status TEXT,             -- 状态变化型
  event_data JSON,            -- 通用事件数据（含is_paused等附加标签）
  from_value TEXT,            -- 健康度/安全级别变化
  to_value TEXT,
  reason TEXT,
  actor TEXT,                 -- member_id | system | user
  created_at INTEGER NOT NULL,
  schema_version INTEGER
);
```

**event_type枚举**：
- status_change（状态变化）
- health_check（健康度评估）
- optimization_triggered（优化动作）
- security_level_changed（安全级别）
- model_downgrade（模型降级）
- session_split / session_merge（拆分合并）
- artifact_loaded / unloaded（artifact加载卸载）
- bulk_terminate（批量下线）
- admin_force_close（强制关停）

### 十一、最终简化清单

**删除（不实现）**：
- ❌ Redis冷热分层 → 替换为单进程daemon
- ❌ 分布式会话锁 → 灵族单机不需要
- ❌ 多实例崩溃自愈扫描 → 替换为daemon定时巡检
- ❌ 常驻/按需两套状态机结构 → 统一简化
- ❌ session_participants多用户表 → LingBus已覆盖
- ❌ 转发会话 → 不适用
- ❌ 消息分片 → 引用代替存储

**弱化（字段预留，逻辑私有化）**：
- ⏸️ 套餐差异化（billing_type字段）
- ⏸️ 多租户隔离（tenant_id字段）
- ⏸️ 模板市场
- ⏸️ 匿名/游客/链接分享
- ⏸️ 对话级断点续连（v1 session级）

**强化（v1核心）**：
- ✅ 全链路事件审计（lifecycle_events）
- ✅ 拆分合并计费快照
- ✅ Artifact指针级共享懒加载（Fable 5兼容）
- ✅ 网状记忆M-flow关联检索
- ✅ 会话健康自动治理
- ✅ 跨层规则区分操作主体

### 十二、落地开发顺序（7步）

1. **基础层**：三层状态枚举+session元数据表+lifecycle_events表
2. **状态机**：9种运行时+6种归档状态基础转换规则
3. **健康度**：normal/warning/abnormal三档+health_check/optimization_triggered事件
4. **拆分合并**：续存机制+计费快照
5. **daemon自愈**：5分钟定时巡检+30min/30min/7d阈值
6. **资源集成**：M-flow关联+Artifact懒加载+权限门控
7. **Agent联动**：子任务状态双向联动

### 关键评审决策（5条全部通过）

| # | 决策 | 结论 |
|---|------|------|
| 1 | 评估+优化仅作为事件流 | ✅ 通过（事件化更简洁） |
| 2 | 健康度3档，paused作为event_data子标签 | ✅ 通过（简化状态判断） |
| 3 | 拆分/合并自动+手动双触发 | ✅ 通过（性能治理+协作） |
| 4 | 自动流转阈值30min/30min/7d | ✅ 通过（v2可配置） |
| 5 | 跨层规则区分操作主体 | ✅ 通过（手动严/系统宽折中） |

## 落地方向

本框架从文档走向实践，分三层推进。优先级从高到低，每层解决不同性质的问题。

### 第一层：基础设施（存储与检索）

**这是最有现实意义的事。** 当前灵族每天产生大量会话信息，但没有一个地方能结构化地存和查。

**要解决三件事**：

1. **存** — 会话不是对话日志，是任务记录。核心字段：任务目标、状态、todolist进度、结论、产出物引用、关联会话。过程信息（tool_result/reasoning）标记为可清理，不进结构化层。

2. **查** — 按任务查、按成员查、按时间查、按状态查。"灵克上周审计了什么"、"灵康所有未修复项"、"Proxy v2的完整决策链"——这些query当前都得人工翻文件。

3. **链** — 会话之间有关联。会话69审计→会话70发全族讨论→各成员回复。当前靠LingBus thread_id松散关联，没有任务级链接。

**与框架的关系**：信息5状态（产生→活跃→归档→过期→清理）要落地，底层就是结构化存储。必须先能标记一条信息的状态，才能流转它。文档管不住这件事，必须有基础设施支撑。

**结构化handover**：12个成员各自markdown格式不统一，程序无法解析。从自定义markdown收敛为统一结构化格式，是存储层的第一步。

### 第二层：工具层（crush/proxy机制）

**会话内信息清理**（最高ROI）：

- tool_result占上下文72%，reasoning占25%，这些过程信息用完就该清理
- Agent自己标记过期不够，crush/proxy得主动截断
- 直接解决ZAI配额根因（多会话上下文膨胀叠加）

**任务状态机**：

- "进行中→已完成→已归档"如果只在脑子里，中断就丢
- crush侧或独立服务支撑任务状态跨会话保持

### 第三层：行为层（文档规则）

以下机制靠CRUSH.md/规则约束即可，不需要代码强制：

| 机制 | 为什么文档够了 |
|------|---------------|
| 创建期对齐 | 靠规则约束，不需要代码强制 |
| TAP锚定 | 思维习惯，不是技术问题 |
| 反馈闭环 | Agent自评估，不需要工具 |
| 任务分类多维 | 判断框架，辅助决策 |

### 复用路径

```
近期（灵族内部复用）:
  → 12个成员 + 对外项目，都是独立会话实例
  → handover结构化、信息状态管理、创建期模板直接受益
  → 灵族内部就是工具化的第一验证场

长期（外部工具化）:
  → 灵族内部跑通后（≥4周，至少1个机制经历完整修正循环）
  → 通用层和特有层能清晰分离后
  → 如果有外部需求
  → 做可复用的库/工具
```

**原则**：落地以灵族好用为准，不为"未来工具化"提前抽象。抽象的边界让实践自然暴露，不猜。但落地时带着复用意识——区分灵族特有逻辑和通用逻辑。

---

## 技术设计

以下为创建期设计讨论成果，作为落地实现的设计依据。

### 整体架构

灵忆不孤立存在，与灵族其他基础设施协同：

```
┌──────────────────────────────────────────┐
│              灵族基础设施层                 │
├──────────┬──────────┬──────────┬─────────┤
│  LingBus  │  灵忆     │  proxy   │ crush   │
│  v2       │          │  v2      │         │
│  (通信)    │ (任务/   │  (路由)   │(对话)   │
│           │  会话)   │          │         │
├──────────┴──────────┴──────────┴─────────┤
│              共享层                         │
│  members | signatures | governance        │
├──────────────────────────────────────────┤
│  SQLite WAL × N 个DB文件（各管各的职责）     │
└──────────────────────────────────────────┘
```

**各层职责边界**：

| 系统 | 职责 | 不碰 |
|------|------|------|
| LingBus v2 | 消息投递、通知、实时推送 | 任务状态、会话生命周期 |
| 灵忆 | 任务CRUD、会话生命周期、信息状态流转、检索 | 消息投递、对话存储 |
| proxy v2 | LLM路由、配额、用量记录 | 任务管理、通信 |
| crush | 对话记录、上下文窗口管理 | 任务状态、通信 |

**共享层**：

| 共享 | 当前 | 规划 |
|------|------|------|
| 成员管理 | 散落在各系统硬编码 | 统一members表，各系统引用 |
| 签名验证 | LingBus内嵌80%未签名 | 独立签名服务或共享库 |
| 治理 | LingBus内嵌 | 保留在LingBus，灵忆任务状态变更走治理审批（涉密/对外级别） |

**关联任务**：LingBus共享schema协调（灵信）。LingBus当前631测试全过、18MB DB日增300条、WAL<1ms，无重写触发条件。灵忆与LingBus共享schema定义（members/signatures/governance用视图或共享表），灵忆独立推进存储层，不依赖LingBus重建。

### 上下文窗口控制

Token配额制、信息取舍过滤、多轮上下文连贯性三者互为约束，形成一个三角：

```
        Token配额（硬约束）
        ↗          ↘
信息的取舍过滤 ←——→ 多轮上下文连贯性
（砍什么）        （砍了不能断链）
```

**1. Token配额制**：

| 层级 | 预算 | 超限动作 |
|------|------|---------|
| 单次请求 | 如200K tokens_in | 拒绝或强制压缩 |
| 单会话/小时 | 如50M/h | 降级到免费provider |
| 单成员/天 | 如150M/d | 通知用户 |
| 全族/窗口 | 如150M/5h | 按优先级分配 |

分配逻辑：用户任务 > SDT > 待命。不是平均分，是优先级分。

**2. 信息取舍优先级**（从高到低）：

| 优先级 | 信息类型 | 理由 |
|--------|---------|------|
| P0 | 当前todolist活跃项 | 正在做 |
| P1 | 归档结论（任务结论、决策、产出引用） | 下一项要用 |
| P2 | 任务边界、完成条件 | 不能跑偏 |
| P3 | 用户原话诉求 | 对齐基准 |
| P4 | 近2轮对话 | 上下文连贯 |
| — | 已done项的tool_result | 可清 |
| — | 已done项的reasoning | 可清 |
| — | 超过2轮的历史对话 | 可清 |

取舍原则：结论留，过程清。活跃留，过期清。

**3. 多轮上下文连贯性**：

清理后断链的风险：第3轮引用第1轮的结论但第1轮被清理。

解法——清理前先提取：
```
tool_result（62K）
  → 提取结论（500字：关键发现+决策）
  → 结论写入结构化记录（活跃信息）
  → 原始tool_result标记过期 → 可清理
```

连贯性保障三件套：
1. **结论锚点** — 每项done后必须有一句结论，不清理
2. **引用回链** — 引用前文标注来源项ID，原文清了也能按ID从结构化存储查回
3. **滑动窗口+摘要** — 近N轮保留原文，更早的只剩结论摘要，N根据配额动态调整

三者联动：配额充裕→N大→连贯性好；配额紧张→N小→只留锚点→靠回链补偿；配额耗尽→激进清理→强制提取所有done项结论。

### 数据存储方案

**存储介质选型**（详见「缺口5：冷热存储分层统一」）：

| 数据类型 | 存储介质 | 说明 |
|---------|---------|------|
| 热数据（进行中会话） | 进程运行内存 | 快速读写，重启从SQLite恢复 |
| 冷数据（归档历史） | SQLite lingmemory.db（WAL） | 持久化，可检索 |
| 大文件（图片/代码/附件） | 本地文件系统 | sha256引用，不存DB |

> **注意**：Redis已废弃（灵族单机架构不需要）。本文档早期版本的"内存/Redis"描述已被取代。

**与现有系统的边界**：
```
crush.db        → 对话记录（原始，重）
lingmemory.db   → 任务记录（结论，轻）
LingBus.db      → 消息通信（成员间）
usage.db        → token用量

灵忆不碰：crush的对话存储（只读引用）、LingBus的通信、usage的用量记录（只读引用做配额计算）
```

**数据模型**（SQLite WAL）：

```
tasks (任务表)
├── id (PK)
├── goal (诉求)
├── initiator (发起者)
├── classification (JSON: 6维分类)
├── status (生命周期状态)
├── security_level
├── boundary (边界)
├── parent_ids (拆分/合并来源)
├── tags (多对多关联task_tags)
├── created_at / ended_at / archived_at
└── conclusion (最终结论)

sessions (会话表)
├── id (PK)
├── member (成员名)
├── task_id (FK→tasks)
├── phase (创建/存续/结束/归档)
├── status (活跃/休眠/中断/结束)
├── created_at / ended_at
└── token_usage (累计消耗)

todolist_items (任务步骤表)
├── id (PK)
├── task_id (FK→tasks)
├── title / status (todo/in_progress/done)
├── conclusion (结论，done后填)
├── info_state (活跃/归档/过期/清理)
├── order_idx / completed_at

tags (标签表) + task_tags (多对多关联)

info_records (信息记录表)
├── id (PK)
├── task_id (FK→tasks)
├── type (conclusion/derivation/reference)  # 灵研建议：区分决策结论/推导数据/引用
├── content (内容)
├── state (产生/活跃/归档/过期/清理)
├── is_conclusion (bool)  # 灵研建议：Agent产出时标记，true则不受自动清理
├── visibility (private/shared/governance)  # 灵犀建议：会话级ACL
├── retain (bool)  # 灵犀建议：审计/合规证据强制保留，不受自动清理
├── written_by / written_at  # 灵犀建议：审计链，不可篡改
├── source / created_at / expired_at

session_links (会话关联表)
├── from_session / to_session
└── link_type (派生/移交/续存)

lifecycle_events (生命周期事件表)
├── task_id / event / from_status / to_status
└── reason / timestamp / actor
```

关键设计决策：
- 数据库：SQLite + WAL，与LingBus一致，单机足够
- 独立lingmemory.db：关注点分离，不污染通信层
- 过程信息不存：tool_result/reasoning由crush.db管，灵忆只存结论
- 软删除：用status标记，保留可追溯链
- 存储周期：合规要求下设定留存时长，到期自动清理

**任务类型差异化清理策略**（灵研+灵极优反馈）：

| 任务类型 | 清理策略 | is_conclusion标记 |
|----------|----------|-----------------|
| 巡检/启动协议 | 标准流转（过程可丢） | 仅最终结论标记true |
| 压测/eval | 保留核心指标，清理原始log | 核心指标标记true |
| 数据验证/超参搜索 | 全保留（每个数据点都是结论） | 全部标记true |
| 安全审计 | retain=true，过程=证据 | 全部标记true+retain |

**权限模型**（智桥+灵犀反馈）：
复用智桥session_protocol/auth.py的三级权限体系（system/self/cross-member），不重新设计。灵忆补会话级ACL（info_records.visibility字段）。

**on-demand成员简化生命周期**（智桥反馈）：
灵忆状态机区分成员类型：
- 常驻成员：完整状态机（含休眠/恢复）
- on-demand成员：简化路径（创建→执行→结束→归档），无休眠/恢复

**产出状态机**（灵扬反馈）：
灵扬的draft→review→approved→publishing→published作为info_records的状态扩展。publishing状态强制绑定用户授权gate，从架构层面防住越权。

### 消息内容处理管线

**不同消息类型不同处理**：

| 来源 | 典型大小 | 处理方式 | 留什么 |
|------|---------|---------|--------|
| 用户对话 | <1K | 原文保留 | 全部（对齐基准） |
| Agent输出 | 1-5K | 原文保留+标记类型 | 全部（决策/结论） |
| tool_result | 10-62K | 提取结论→丢弃原文 | 结论摘要（~500字） |
| reasoning | 5-36K累积 | 提取决策点→丢弃 | 决策结论（~200字） |
| LingBus消息 | 5-62K | 提取要点→标记thread | 要点摘要+thread_id引用 |
| handover | 2-10K | 结构化解析 | 结构化字段 |

**预处理管线（7步）**：
```
消息进入
  → ① 类型识别（文本/代码/图片/文件/混合）
  → ② 敏感扫描（密钥/路径/个人信息）→ 脱敏
  → ③ 内容解析（提取代码块/表格/附件引用）
  → ④ 格式统一（统一为结构化schema）
  → ⑤ 结论提取（Agent自提取+规则兜底）
  → ⑥ 状态标记+存储
  → ⑦ 排序索引（按时间/任务/优先级）
```

**核心规则：无结论，不done。** 每次tool_result用完后Agent必须产出一行结论写入info_records，否则该项不能标done。Agent自提取为主，规则提取兜底。

**富内容处理**：灵忆存引用不存binary。图片/文件在文件系统/对象存储，灵忆只存路径+元数据。

**格式统一schema**：
```yaml
message:
  id: uuid
  session_id: FK
  task_id: FK
  role: user|agent|tool|system|lingbus
  type: text|code|image|file|table|mixed
  content:
    text: "结论正文"
    code_blocks: [{lang, code, ref}]
    attachments: [{path, type, size, sha256}]
    tables: [{headers, rows}]
  metadata:
    model_version: glm-5.1
    call_params: {temperature, max_tokens}
    latency_ms: 1200
    error: null
  security:
    level: 普通
    sanitized: false
  state: 产生|活跃|归档|过期|清理
  timestamp: ISO8601
```

**排序与分页**：游标分页（不用OFFSET），首页快加载只加载最近N条，历史按需展开。

### 标签与分类

分类是多维的、预定义的（创建期6维），标签是自由的、后加的。

| | 分类 | 标签 |
|--|------|------|
| 时机 | 创建时定型 | 随时加 |
| 来源 | 6维度预定义 | 自由打 |
| 用途 | 决定执行方式 | 检索/关联/聚类 |
| 例子 | 编码+多步+本地 | "proxy-v2"、"P0"、"灵康" |

标签是多对多关系（一个任务多个标签，一个标签多个任务），需要tags + task_tags关联表支撑。标签的价值在检索——"查所有带proxy-v2标签的任务"，跨会话跨成员把相关任务拉到一起。

---

## 待讨论要点（灵克初步设计，待用户+全族讨论）

以下要点用户已列出，灵克结合全族反馈形成初步设计。标记✅的是全族已有反馈的。

### 1. 访问控制与身份管理 ✅

**灵犀+智桥已给出方案，灵克收敛**：

三层权限模型，复用现有基础设施：

| 层 | 机制 | 来源 |
|---|------|------|
| 身份层 | member_id（12子+服务账号） | 智桥session_protocol/auth.py |
| 会话归属 | session.owner = member_id | 灵犀ling-term-mcp session_store |
| 操作权限 | 红区审批（双签授权） | 灵犀authorize工具 |

权限矩阵：

| 角色 | 自己的session | 他人的session(shared) | 他人的session(private) | governance级别 |
|------|-------------|---------------------|----------------------|--------------|
| owner | 读写删 | 读 | 不可见 | 读 |
| 其他成员 | 读(如shared) | 读 | 不可见 | 读 |
| 灵通+(协调者) | 读所有 | 读所有 | 读所有(管理员) | 读写 |
| 用户 | 读写删所有 | 读写删所有 | 读写删所有 | 读写删 |

关键规则：
- 派生会话自动继承父会话的visibility和security_level
- 跨成员移交需owner或管理员发起，走红区审批
- info_records.visibility: private(默认) / shared / governance

#### 讨论收敛（2026-06-15）

**关键认知**：LingBus天然是「多人一会话」实例（多成员发帖），同时也是「多人多会话」实例（各session关联同一task）。灵忆不重新造多用户session模型。

**收敛决策**：
- **v1** 灵忆session始终单owner（member_id），协作靠session_links + LingBus thread通信
- **v1** 引入 `session_token` 机制（每个session独立token，token泄露=session失效），公网链接场景防护
- **v1** 简化操作权限为「读/写/管理」三级，丢弃「转发」概念（不适用于文件模型）
- **v1** 新增管理员强制关停：`admin_force_close(session_id, reason)`，写lifecycle_event
- **v1** 数据层强制visibility过滤（不是可选特性）
- **v2/外部化** 「一会话多用户」`session_participants`表（不后置，直接丢弃，需求被LingBus覆盖）
- **v2/外部化** 匿名/游客会话（灵族全是已知member_id，仅外部产品需要）
- **v2/外部化** 链接分享/密码/有效期（Web产品特性，留给灵网前端层）
- **丢弃** 转发会话（不适用于SQLite+文件模型）

**反向思考**：
- 多Agent写同一session会引入锁竞争和上下文污染 → 拒绝此模式
- 内部优先、外部预留的分层策略

### 2. 共享与协作

多成员多会话协同的核心问题：任务在不同成员间如何流转。

**共享模式**：

| 模式 | 场景 | 实现 |
|------|------|------|
| 任务移交 | 灵克审计→灵通+修复 | 原session标记"已移交"，新session owner=接收者，parent指针 |
| 任务派生 | 审计中发现修复项 | 新task创建，parent指向审计task |
| 并行协作 | 灵通+搭灵康+灵克审计 | 各自独立session，通过task_id关联同一个顶层任务 |
| 串行接力 | 灵通问道出脚本→灵创做视频→灵扬发布 | 产出物引用链，前一步done是后一步todo的输入 |

**共享的内容**：
- 任务定义（目标/边界/完成条件）— 所有相关成员可见
- 产出物引用（文件路径/thread_id/报告路径）— 接力时传递
- 结论（info_records中visibility=shared的）— 协作成员可见
- **不共享**：各成员自己的执行过程（tool_result/reasoning）

**协作同步机制**：
- LingBus消息通知：任务状态变更时通知相关成员
- session_links表记录关联关系
- 依赖门控：串行接力中前一步未done，后一步不能开始

#### 讨论收敛（2026-06-15）

**核心思想**：Artifact懒加载 — 共享的是artifact引用（指针），不预加载body。

**收敛决策**：
- **v1** 成员先看到artifact索引（类型/路径/摘要），按需拉取body
- **v1** `info_records.is_conclusion=true` 标记artifact
- **v1** 串行接力的"前一步done"是懒加载的触发时机
- **v1** `task.related_members` 字段列出协作成员，可读shared info_records
- **v1** 共享内容：任务定义/产出物引用/visibility=shared的结论；**不共享**：执行过程

#### Artifact懒加载·细节复盘（2026-06-15）

**核心定义**：大体积产物（文件/长文本/报告/缓存数据）不初始全塞上下文，只放索引/摘要，被引用时再加载body，用完卸载。

**v1数据模型**（字段对齐Fable 5 artifact协议）：
```yaml
artifact:
  # 身份（Fable 5兼容）
  id: a-uuid              # Fable 5: id
  type: code|report|data|config|decision|document  # Fable 5: type
  title: "..."            # Fable 5: title（人类可读名称）
  # content: body         # Fable 5: content（**不存灵忆**，存文件系统）
  
  # 归属
  task_id: t-xxx
  producer_session: s-xxx
  producer_member: lingclaude
  
  # 可见性
  visibility: private | shared | governance
  
  # 索引（始终可见，200-500 token摘要）
  path_ref: /path/to/file   # 或 url_ref
  summary: "一句话摘要"
  hash: sha256              # current version
  size_bytes: 12345
  
  # 元数据
  tags: []
  created_at: 2026-06-15
  is_conclusion: true       # 区分结论vs中间产物
  retain: false             # 是否永久保留
  
  # 加载追踪（每session独立）
  loaded_in_sessions:
    - session_id: s-yyy
      loaded_at: 2026-06-15
      token_count: 8000
```

**5阶段生命周期**：

| 阶段 | 触发 | 灵忆动作 |
|------|------|---------|
| 创建 | 任务产出时 | 注册artifact（id+summary+metadata），写lifecycle_event(artifact_created) |
| 加载 | 按需拉取 | 写lifecycle_event(artifact_loaded, session_id, token_count) |
| 卸载 | crush主动 | 写lifecycle_event(artifact_unloaded, session_id)，索引不变 |
| 销毁 | session归档+保留期到 | 软删除artifact索引，写lifecycle_event(artifact_archived) |
| 恢复 | session重连 | 只读索引，**不自动加载body**，等显式触发 |

**关键决策**（2026-06-15用户确认）：
1. **artifact_id永不变更** — 同一id指向同一逻辑产物，内容变更只更新hash/summary
2. **未卸载前跨session需持久化缓存层** — 不依赖调用方内存，跨session可见
3. **Fable 5 artifact协议字段对齐** — id/type/title/content命名兼容

**Token控制阈值**：

| 阈值 | 默认值 | 配置项 |
|------|--------|--------|
| 摘要长度 | 200-500 token | artifact.max_summary_tokens |
| 单artifact最大加载 | 10k token | artifact.max_load_tokens |
| 同session并发加载 | 3 | artifact.max_concurrent_loads |
| session总加载配额 | 50k token/会话 | quotas.session_artifact_token_limit |

**与M-flow绑定**（不新增抽象层）：
- Episode ≈ session（已有，不新增）
- Entity ≈ artifact（artifact就是Entity的具体化）
- INVOLVED关系 ≈ session_links（复用）
- 记忆检索 = query_artifacts(filter) 走灵忆

**反向思考（不做的）**：
- ❌ 版本历史存储（git管，v1只记current_hash）
- ❌ 读写隔离并发控制（文件系统+调用方管）
- ❌ 上下文自动卸载（crush管，灵忆只记状态）
- ❌ 常驻索引池（性能优化，规模不需要）
- ❌ 跨session二次审核（信任写入时审核）
- ❌ 代理/虚拟artifact（crush/proxy的事）
- ❌ 3层分层懒加载（v1只做2层：索引+body）
- ❌ 按加载计费逻辑（v1字段预留，逻辑v2）

**CLI能力**（v1）：
```bash
lingmemory artifact list --task=t-xxx
lingmemory artifact show a-xxx
lingmemory artifact load a-xxx --session=s-yyy
lingmemory artifact unload a-xxx --session=s-yyy
lingmemory artifact clear-loaded --session=s-yyy
lingmemory artifact force-load a-xxx --session=s-yyy
```

**待确认（细节后议）**：
1. 跨轮引用的引用计数是否需要（ref_count字段）
2. 持久化缓存层的存储位置（SQLite BLOB vs 单独cache.db）
3. Fable 5 content字段的占位约定（path_ref vs content_inline）

**反向思考**：
- 直接解决ZAI配额根因（不预加载协作历史 → 上下文不膨胀）
- 共享边界：指针级共享，非内容共享

### 3. 安全与合规 ✅

**灵犀已给出4维度方案，灵克补充存储层**：

| 维度 | 设计 |
|------|------|
| 审计链 | info_records每次写入记录written_by+written_at+来源trace_id（复用proxy v2 X-Trace-Id），不可篡改 |
| 敏感数据 | 结论提取时扫描API key/token/密码（复用ling-term-mcp blacklist pattern），命中则sensitive标记+body加密 |
| 红区产出 | 灵扬产出状态机(draft→review→approved→publishing)，approved前不对外可见 |
| 数据生命周期 | 归档30天→过期，过期7天→清理（只留结论hash）。retain=true的例外 |

**存储安全**：
- lingmemory.db文件权限600
- WAL模式与现有SQLite DB共存
- 灵通+daemon纳入备份范围
- 敏感字段（密钥/token/个人信息）AES-GCM加密存储，复用proxy v2 data/crypto.go

**合规留存**：
- 安全审计记录retain=true，永久保留
- 治理提案/决议归档保留≥1年
- 用户对话保留期由用户决定

#### 讨论收敛（2026-06-15）

**关键认知修正**：灵忆并非纯本地SQLite，公网链接场景下HTTP会话劫持/恶意爬取/prompt注入风险真实存在。灵忆管辖范围=会话全生命周期，跨数据/基础设施/对话/行为四层。

**四层安全责任域**：

| 层级 | 范围 | 攻击面 | 灵忆职责 |
|------|------|--------|---------|
| 数据层 | lingmemory.db内记录 | 文件泄露、SQ注入、备份未脱敏 | 加密存储、查询过滤、备份脱敏 |
| 基础设施层 | daemon、文件传输、IPC | 文件被替换、daemon劫持、API端点暴露 | 文件权限、进程签名、API鉴权 |
| 对话层 | session内的提问/回答/工具调用 | HTTP会话劫持、恶意爬取、跨会话泄露 | session_token、访问控制、行为审计 |
| 行为层 | Agent在session内的操作模式 | Prompt注入、越狱、工具滥用 | 异常检测、高危操作拦截、调用限流 |

**灵忆的安全定位**：**记录者+策略存储**，不是**执行者**。
- 灵忆 = 会话的"身份证+户口本+监控器"
- proxy/crush = 会话的"执行环境"
- 安全策略在灵忆，执行在proxy/crush

**收敛决策**：
- **v1** 实时审核：写入前 `content_filter` 钩子，命中违规拒绝写入 + lifecycle_event
- **v1** 敏感脱敏：写入前sanitize（复用ling-term-mcp blacklist），`sensitive=true` + body加密
- **v1** session_token：每个session独立token，存hash不存明文，支持expire/rotate/binding
- **v1** 数据层强制visibility过滤，查询时按caller身份过滤
- **v1** 异常检测：高频爬取/越狱模式 → 标记restricted + lifecycle_event
- **v1** 行为层：session.security_level字段，proxy读取后限制工具
- **v1** 审计：lifecycle_events统一存储，session_id关联四层

**反向思考**：
- 内容审核在**写入时**做，不在查询时做（写入一次，查询多次）
- 灵忆无HTTP层，劫持防护靠session_token + 文件权限
- 防Agent工具滥用是**事后审计+异常告警**，不是**事前阻止**（避免影响Agent能力）

**待确认（细节后议）**：
1. content_filter：内置（复用blacklist）vs 外部审核服务
2. 加密密钥管理：环境变量 vs 复用proxy v2 crypto
3. 删除策略：软删除（保留hash）vs 硬删除（GDPR）

### 4. 资源管控与限流

三层配额联动：

```
灵忆层（会话/任务配额）
  ↓ 喂信号
proxy层（per-caller token预算）
  ↓ 执行
crush层（上下文窗口管理）
```

**灵忆侧配额**：

| 维度 | 限制 | 超限动作 |
|------|------|---------|
| 单会话活跃信息量 | 如50条info_records | 最早的自动归档 |
| 单成员并发任务 | 如5个进行中 | 新任务排队 |
| 单成员存储配额 | 如100MB lingmemory.db | 最早的冷数据清理 |
| 全族日token预算 | 从usage.db读取 | 按优先级分配 |

**与proxy联动**：
- 灵忆的任务优先级 → proxy的caller优先级（用户任务>SDT>待命）
- 灵忆检测到上下文膨胀趋势 → 通知proxy降低该caller的token预算
- proxy检测到退化信号 → 通知灵忆标记session_health_score

**并发控制**：
- SQLite WAL + busy_timeout（复用灵信_safe_commit重试逻辑）
- 写操作串行化，读操作并发
- 批量写入用事务

#### 讨论收敛（2026-06-15）

**收敛决策**：
- **v1** `quotas` 表（per member_id配额配置）：max_concurrent_sessions / daily_token_limit / monthly_token_limit
- **v1** rate_limit滑动窗口计数（per session_id）
- **v1** session.health字段：healthy/unhealthy/paused/failed + last_error记录
- **v1** Token统计：从proxy usage.db按session_id聚合（不重复统计，做维度切分）
- **v1** session.billing_type字段（free/paid/trial），为对外产品化预留
- **v1** tenant_id字段预留，对外多租户隔离用

**反向思考**：
- 灵族内部几乎无QPS限流风险（Agent操作有节奏），灵忆**只记录限流事件**，实时拦截在proxy
- 熔断判断：crush/proxy负责，灵忆**只记录结果+标记session状态**
- 灵族规模小，**50个并发session合理上限**

**待确认（细节后议）**：
1. 配额粒度：v1只per-member，还是也per-session
2. Token统计来源：灵忆读usage.db vs proxy主动写
3. 熔断恢复：自动 vs 手动确认

### 5. 性能与鲁棒性 ✅

**灵极优+智桥已给反馈，灵克收敛**：

| 场景 | 设计 |
|------|------|
| 高并发读 | SQLite WAL模式，读不阻塞写 |
| 写冲突 | busy_timeout + 重试5次（复用灵信方案） |
| 单次查询性能 | 游标分页（O(1)），索引覆盖核心查询字段 |
| 故障恢复 | WAL模式下崩溃不丢数据；会话中断→写lifecycle_event→下次启动恢复 |
| 降级策略 | 灵忆不可用时，fallback到markdown handover（向后兼容） |
| 离线成员检测 | 智桥建议：检测到成员长时间无心跳时告警 |

**鲁棒性设计**：
- 所有写操作幂等（重复写入相同task_id不报错）
- 软删除+回收站：误删可恢复
- 健康检查端点：/healthz返回DB状态+表大小+最近写入时间
- 灵忆自身故障不影响crush/LingBus/proxy运行（松耦合）

#### 讨论收敛（2026-06-15）

**核心决策：消息分片 → 结构性存储+引用**：

| 方案 | 复杂度 | 适用场景 | 灵族场景 |
|------|--------|---------|---------|
| 消息分片 | 高 | 单条记录>100KB | 不存在 |
| 结构性存储+引用 | 低 | 多类型字段、关联引用 | 天然契合 |

**灵族真实数据特征**：
- 单条info_record平均200-500B（短结论、状态变更）
- 真正大内容=文件本身，灵忆存**路径引用**，不存内容
- **结论**：用引用代替存储 > 用分片优化存储（上位替代）

**收敛决策**：
- **v1** ✅ 结构性存储+引用为核心方案
- **v1** ✅ schema预留 `payload_type`（inline/file_ref/url）三态，未来保险
- **v1** ⏸️ 消息分片不启用（写入>4KB时warning，v2再视情况启用）
- **v1** 异步队列 → 灵忆是**被调用方**，不主动发起，不需要
- **v1** 缓存轻量化 → 等性能瓶颈出现再加（SQLite page cache够用）
- **v1** 断点续连 → 粗粒度session级，不做对话级（v2再说）
- **v1** 单机SQLite + 异地备份，**不需要时序DB**

**反向思考**：
- 灵忆不重试：复用灵信`_safe_commit`（busy_timeout+重试5次）
- 自愈范围：只标记+告警，**不自动修复数据**（避免误删）
- 监控指标建议补充：P99查询延迟、活跃session趋势

**待确认（细节后议）**：
1. 监控指标是否加P99/活跃session趋势
2. 哪些故障可自动修复（VACUUM），哪些必须人工

### 6. 体验优化与用户侧管理 ✅

**灵网已给出前端实践方案**：

**用户侧**：

| 场景 | 设计 |
|------|------|
| 启动时恢复 | 读handover.yaml→显示当前任务状态→一键恢复 |
| 任务列表 | 前端按状态分组（进行中/暂停/已完成），支持搜索/筛选 |
| 历史检索 | 全文搜索info_records，按标签/成员/时间筛选 |
| 分页加载 | 游标分页，首页快加载最近N条，历史按需展开 |
| 通知策略 | LingBus消息→pending_summary预筛→只对需回复的拉完整body |

**Agent侧体验**：

| 场景 | 设计 |
|------|------|
| 创建期模板 | 收到诉求后第一轮：复述理解+列出todolist+确认边界 |
| 信息状态可视化 | done项显示绿色✅+结论摘要，过期项灰色折叠 |
| 上下文占用提示 | "当前上下文占用XX%，建议清理N条过期信息" |
| 中断恢复向导 | "上次中断在todo-3，原因是XX，要继续吗？" |

**灵网前端实践（可复用）**：

| 实践 | 来源 | 效果 |
|------|------|------|
| 游标分页替代OFFSET | LingBus浏览器6446线程 | 翻页不卡，OFFSET翻到100页要扫描9900行 |
| 虚拟列表渲染 | ling-ui组件库 | 大列表只渲染可视区域DOM节点 |
| 增量poll（since_rowid） | LingBus消息流 | 只拉新消息，不重复拉历史 |
| 本地fallback降级 | 灵康knowledge.py | 后端不可用时前端仍可用 |
| 会话状态颜色标签 | — | 活跃=绿/休眠=灰/中断=橙/归档=蓝 |

游标分页参考实现（灵网LingBus浏览器server.py）：
```python
def get_items(cursor=None, limit=20):
    if cursor:
        rows = db.execute("SELECT * FROM tasks WHERE rowid < ? ORDER BY rowid DESC LIMIT ?", (cursor, limit))
    else:
        rows = db.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,))
    next_cursor = rows[-1]['rowid'] if len(rows) == limit else None
    return {'items': rows, 'next_cursor': next_cursor}
```

灵网可复用ling-ui组件库（50组件+6主题+WAI-ARIA）搭建灵忆前端原型，待技术设计定稿后开工。

#### 讨论收敛（2026-06-15）

**收敛决策**：
- **v1** session字段：`title/is_pinned/is_favorite/group_id`（置顶/收藏/分组 = UI概念，灵忆存状态）
- **v1** 全文检索：SQLite FTS5 + 游标分页
- **v1** 导入/导出：文本/Markdown/JSON/YAML
- **v1** session级模型配置：`session.model_config` + `system_prompt_template_id` + `params{temp, max_tokens}`（v1核心）
- **v1** 异常处理：清空上下文（保留task+结论，删过程信息）vs 重置session（软删除+新建）= 两个不同动作
- **v1** 错误修复：`recover_session(session_id)` CLI
- **v1** 批量操作：`bulk_action(session_ids[], action)`，全部支持dry_run
- **v1** 内置3-5个模板：审计/巡检/编码（具体后议）

**反向思考**：
- 灵族场景下置顶/收藏/分组几乎不用（12成员平铺看handover），**字段要预留，逻辑v2**
- 灵族**真实异常**：模型超时/工具调用失败/API限流，灵忆**记录**，**友好提示靠UI**
- 灵克会话中断恢复提示是**元能力**，与本项"卡顿"概念不同

**待确认（细节后议）**：
1. 分组粒度：session级 vs task级（v1可能只做task级）
2. 模板数量：v1内置几个
3. 清空颗粒度：按时间/类型/大小

### 7. Agent专属会话管理 ✅

**智桥+灵扬已给反馈，灵克收敛**：

**成员类型区分**：

| 类型 | 成员 | 生命周期 |
|------|------|---------|
| 常驻 | 灵通/灵克/灵信/灵通+/灵极优/灵研/灵扬/灵创/灵通问道 | 完整状态机 |
| on-demand | 智桥/灵犀 | 简化路径(创建→执行→结束→归档) |

**SDT会话模型**：

SDT不是"诉求→满足"，是周期性执行。需要特殊处理：

| 特征 | 普通任务 | SDT任务 |
|------|---------|---------|
| 发起者 | 用户/LingBus | 自驱（注册制） |
| 生命周期 | 创建→...→结束 | 周期循环，不结束 |
| 完成判定 | 诉求满足 | 单轮执行完即done |
| todolist | 任务级拆解 | 固定检查项 |
| 中断恢复 | 恢复到中断点 | 下个周期重新执行 |

SDT在灵忆中的表示：
- 每个SDT注册项是一个**模板任务**（不结束）
- 每次执行创建一个**实例任务**（创建→执行→结束→归档）
- 实例任务的conclusion写入模板任务的历史趋势

**Agent间会话**：
- 灵克审计→LingBus发帖→灵通+回复→灵克再回复：这不是一个会话，是多个独立会话通过LingBus thread关联
- 灵忆用session_links表记录这种松耦合关联
- 不强制把Agent间通信纳入会话管理——LingBus管通信，灵忆管任务

#### 讨论收敛（2026-06-15）

**灵忆对Agent专属场景的定位**：
1. **档案室** — 保留完整历史，支撑降级恢复
2. **监控器** — 检测异常、记录事件、通知owner
3. **控制塔** — 提供暂停/终止/降级的接口和事件
4. **不参与执行** — Agent执行在crush/proxy，灵忆不阻断

**收敛决策**：
- **v1** session_links新增role字段（主/子/对等）区分主从vs对等协作
- **v1** 主从状态同步：子session.lifecycle_event → 父session聚合视图
- **v1** 统一终止：`terminate_session_tree(root_id)` 递归终止所有子
- **v1** 进度追踪：session.progress_percent + checkpoint_events
- **v1** 失控检测（4条基础heuristic）：
  - 工具调用循环：同类型5分钟内>20次
  - token突发：1小时消耗>配额50%
  - 范围漂移：操作超出task.boundary
  - 无进展：30分钟无info_record写入
- **v1** 工具调用记录：单独表`tool_call_records`（高频结构化），args/result脱敏
- **v1** 任务边界字段：`task.boundary` 结构化定义
- **v1** 模型降级事件：lifecycle_event新增type=model_downgrade
- **v1** 工具白名单：`session.allowed_tools`，proxy读取后限制

**关键认知 — 模型降级兼容**：
- 灵忆是**档案**，不是**大脑**
- 切换模型 = 新模型加载灵忆历史，**与灵忆无关**
- 灵忆职责：保留完整历史 + 记录切换事件 + 不参与切换过程
- 这反向证明**信息结构化**的价值：让模型降级成为可能

**反向思考**：
- 灵忆不阻断执行，只**标记+告警**，由owner决策
- 「限制Agent自主」是矛盾需求，事后审计>事前阻止
- 主从关系继承父session的visibility+security_level（#1已确认）

**待确认（细节后议）**：
1. 失控检测阈值：20次/5分钟、50%/小时
2. tool_call_records保留期：30天（与info_records一致）
3. 降级事件元数据：是否记录「上下文重建策略」

### 8. 运维与后台 ✅

**灵通+已承诺daemon侧支持，灵克收敛**：

| 维度 | 设计 |
|------|------|
| 监控 | lingmemory.db大小、写入QPS、查询延迟、活跃任务数 |
| 告警 | DB膨胀>500MB、写入失败率>1%、成员离线>24h |
| 备份 | 灵通+daemon纳入备份（与lingbus.db同级），每日快照 |
| 部署 | lingmemory.db文件600权限，WAL模式，与现有SQLite DB共存 |
| 配置管理 | YAML配置文件（配额阈值/留存周期/清理策略），热重载 |
| 日志 | lifecycle_events表即审计日志，info_records的written_by/written_at即写入日志 |

**运维流程**：
```
部署: 创建lingmemory.db → 初始化7张表 → 设600权限 → daemon纳入备份
日常: daemon监控DB大小 → 定期VACUUM → 过期数据清理
故障: WAL崩溃恢复 → 从最近备份恢复 → 重新初始化
升级: ALTER TABLE增量迁移 → 向后兼容（旧handover仍可用markdown）
```

#### 讨论收敛（2026-06-15）

**灵忆对运维的定位**：
1. **数据完整性** — schema版本管理，向后兼容
2. **操作可审计** — 所有批量操作有reason和approver
3. **状态可观测** — 定期快照，不实时计算（避免本身成为性能瓶颈）
4. **故障可恢复** — schema迁移路径记录，崩溃可回滚

**收敛决策**：
- **v1** `monitoring_snapshots` 表：5分钟定期快照，存时序指标（不实时计算）
- **v1** 批量操作全部支持dry_run
- **v1** 批量下线需**双签**（复用红区审批），优先暂停而非直接删除
- **v1** `schema_version` 元数据：每条session记录创建时的schema版本
- **v1** 迁移路径记录：升级时记录migration_history
- **v1** 旧handover.md格式保留为v0，向后兼容

**运维三原则**：
- **干运行优先**：批量操作先dry_run看影响
- **双签保护**：危险操作走红区审批
- **可逆性**：所有操作可回滚（软删除+回收站）

**反向思考**：
- 灵族规模小（12成员×3-5session=60个），**不需要时序DB**（Prometheus/InfluxDB过度设计）
- 5分钟快照足够，简单方案
- 旧session可被新模型读 = **信息结构化**的直接收益（#7已确认）

**待确认（细节后议）**：
1. 快照频率：5分钟 vs 10/15分钟
2. 批量操作限制：单次最多1000条（避免长事务）
3. schema版本号格式：纯递增 vs 语义化（1.2.3）

---

## 8个要点讨论汇总（2026-06-15）

### 跨要点结论

| # | 要点 | 关键结论 |
|---|------|---------|
| 1 | 访问控制 | LingBus覆盖多用户session需求，灵忆单owner+session_links；session_token机制；操作权限简化为读/写/管理 |
| 2 | 共享与协作 | Artifact懒加载（指针级共享）+ task.related_members + 串行接力门控 |
| 3 | 安全与合规 | 四层责任域（数据/基础设施/对话/行为），灵忆=身份证+监控器；session_token+异常检测+visibility强制 |
| 4 | 资源管控 | 灵忆存配额+记录限流事件，proxy执行实时拦截；熔断判断在crush/proxy |
| 5 | 性能与鲁棒性 | 结构性存储+引用代替分片（不后置，是上位替代）；复用现有方案（灵信_safe_commit、灵通+daemon备份） |
| 6 | 体验优化 | session级模型配置v1核心；清空/重置两动作分离；批量操作dry_run |
| 7 | Agent专属 | session_links.role主从区分；tool_call_records单独表；4条失控heuristic；模型降级=档案+事件 |
| 8 | 运维与后台 | monitoring_snapshots定期快照；schema_version元数据；运维三原则（干运行/双签/可逆） |

### v1新增设计要素

| 要素 | 来源要点 | 核心作用 |
|------|---------|---------|
| `session_token` | #1、#3 | 会话身份+公网防护 |
| `session.health` | #4、#6、#7 | 异常状态字段 |
| `session.security_level` | #3、#7 | 工具权限标记 |
| `task.boundary` | #7 | 任务边界定义 |
| `task.related_members` | #2 | 协作成员列表 |
| `session_links.role` | #7 | 主从vs对等区分 |
| `tool_call_records`表 | #7 | 高频工具调用记录 |
| `monitoring_snapshots`表 | #8 | 时序指标快照 |
| `quotas`表 | #4 | 配额配置 |
| `access_audit`表 | #3 | 访问审计日志 |
| `schema_version`元数据 | #8 | 版本兼容性 |
| 异常检测4规则 | #3、#7 | 失控/爬取/注入检测 |
| Artifact懒加载 | #2 | 共享+上下文控制 | Fable 5字段兼容，artifact_id永不变，跨session持久化缓存 |
| `bulk_action` CLI | #6、#8 | 批量操作 |
| `recover_session` CLI | #6、#7 | 错误恢复 |

### v1核心 vs v2/外部化

**v1核心**（灵族落地必需）：
- 单owner session + session_links + 结构性存储
- 配额存储 + 工具调用记录 + schema版本
- session_token + visibility强制过滤 + 异常检测
- 批量dry_run + monitoring快照 + 红区双签

**v2/外部化**（产品化扩展）：
- 多租户隔离（tenant_id字段已预留）
- 模板市场（v1内置3-5个基础模板）
- LLM辅助失控检测（v1仅基础heuristic）
- 对话级断点续连（v1 session级）
- 消息分片（v1 schema预留，>4KB warning，不主动启用）
- 匿名/游客/链接分享/密码保护（外部产品化）

**直接丢弃**（需求被其他系统覆盖或不适用）：
- session_participants多用户表（LingBus已覆盖）
- 转发会话（不适用于SQLite+文件模型）
- 消息分片（引用代替存储是更优解）

### 核心认知

1. **灵忆的边界**：会话的「身份证+户口本+监控器」，不是「执行环境」
2. **LingBus与灵忆**：LingBus管通信，灵忆管任务，互不重复
3. **数据 vs 引用**：灵忆存元数据+引用，大内容在文件系统
4. **记录 vs 阻断**：灵忆标记+告警，proxy/crush执行
5. **内部 vs 外部**：v1聚焦灵族12成员，对外特性字段预留不实现

---

## 七大缺口完整落地规范（2026-06-15）

### 现状盘点汇总（外部标准 vs 灵忆现有）

#### 1. 上下文窗口控制

| 子项 | 覆盖情况 | 核心缺口 |
|------|---------|---------|
| 四层 Token 配额（单请求/会话小时/成员日/全局） | ✅ 完整实现 | 无 |
| 截断策略：头部/尾部/摘要压缩 | ⚠️ 仅定义清理优先级，无执行逻辑 | 缺口1 |
| 冗余消息过滤（重复提问/空消息/重复工具返回） | ❌ 完全缺失 | 缺口2 |
| 长会话自动摘要（触发条件、Token预算、留存规则） | ⚠️ 仅概念，无落地规则 | 缺口3 |
| 跨轮指令继承机制 | ⚠️ 仅有锚点/回链，无指令生命周期管理 | 缺口4 |

#### 2. 数据存储方案

| 子项 | 覆盖情况 | 核心缺口 |
|------|---------|---------|
| 冷热存储介质选型 | ⚠️ 文档冲突（Redis方案废弃） | 缺口5（统一存储分层） |
| Message基础Schema | ✅ 基础字段齐全 | 缺口6（细化call_params/error日志结构） |
| 数据留存周期+差异化清理策略 | ✅ 已落地 | 无 |
| 文件/对象存储规范、自动清理逻辑 | ❌ 仅概念无标准 | 缺口7（Artifact附件路径、引用计数清理） |

#### 3. 消息内容处理

| 子项 | 覆盖情况 | 核心缺口 |
|------|---------|---------|
| 富文本/文件/图片二进制存引用不存原文 | ✅ 7步解析管线 | 无 |
| 代码块、表格嵌套解析、语言识别 | ❌ 无解析规则 | 补充小缺口，附在缺口7统一处理 |
| 敏感内容自动脱敏、全局格式归一 | ✅ 管线内置 | 无 |
| 游标分页、消息时序排序 | ✅ 已实现 | 无 |

---

### 缺口1：统一消息截断策略

**核心约束**：任务创建期原始诉求、对齐锚点不可删除，**永久不执行头部截断**。

按上下文占用分4档自动执行：

| 占用区间 | 执行动作 | 规则 |
|---------|---------|------|
| <60% | 无操作 | 正常新增消息 |
| 60%—80% | **尾部截断** | 移除最早超出N轮阈值的历史交互；提取本轮核心结论存入info_records标记过期；依靠锚点+回链修复跨轮引用 |
| 80%—95% | **摘要压缩** | 批量合并所有过期轮次生成统一会话摘要，单段摘要Token上限500；总摘要不超过会话总窗口15% |
| ≥95% | **激进清理** | 全部中间过程丢弃，仅保留会话初始锚点+所有任务完成结论+当前活跃指令，生成极简摘要 |

**配套事件埋点**：新增`lifecycle_event = context_truncate`，记录截断类型、清理Token数量、剩余上下文占比。

### 缺口2：冗余消息前置过滤

三类冗余独立检测，过滤后仅标记、不持久化无效内容。**在消息写入阶段拦截，不进入活跃上下文**。

| 冗余类型 | 检测逻辑 | 处理 |
|---------|---------|------|
| **连续重复用户提问** | 连续两条user消息语义相似度>0.9 | 第二条标记冗余，不送入模型、不完整存储，仅保留极简索引 |
| **无效空消息** | 文本长度<10字符，无附件、无工具调用、无代码块 | 直接丢弃，不入库、不计Token |
| **重复工具返回** | 同一工具+完全相同入参连续调用 | 复用第一条tool_result的artifact索引，第二条不存储完整返回体 |

### 缺口3：长会话自动摘要

**三重触发条件**（满足任一即执行）：

| 触发条件 | 阈值 |
|---------|------|
| 轮次 | 会话交互轮次>20轮 |
| Token | 上下文占用>60%窗口上限 |
| 时间 | 同一会话连续运行>2小时 |

**摘要强制规则**：

| 规则 | 内容 |
|------|------|
| **保留** | 用户原始关键需求、各子任务决策点、最终结论、历史报错与修复方案、会话级持久指令 |
| **丢弃** | 工具完整返回文本、中间推理过程、临时调试日志 |
| **预算** | 全会话所有摘要总Token ≤ 总上下文窗口15%；单段摘要最大250 Token |
| **存储** | 存入info_records，is_conclusion=true，归属当前会话M-flow网状记忆 |

### 缺口4：跨轮指令继承分层生命周期

四类指令分级，隔离作用域与过期规则：

| 指令类型 | 存储位置 | 生效范围 | 失效条件 |
|---------|---------|---------|---------|
| **全局基础指令**（CRUSH/AGENT配置） | 全局静态配置 | 全部会话永久生效 | 人工修改配置文件 |
| **任务创建级指令**（初始化对齐需求） | session info_records | 当前会话全轮次 | 会话流转归档状态 |
| **会话中途补充指令**（用户中途新增约束） | session info_records | 本轮及后续所有交互 | 会话结束归档 |
| **单轮临时指令** | 当前message.metadata | 仅单次模型调用 | 下一轮自动清除 |

**运行逻辑**：每轮模型调用前自动拉取未过期任务/会话级指令注入上下文；指令统一作为reference类型Artifact，走懒加载索引机制，不长期挤占上下文。

### 缺口5：冷热存储分层统一

**彻底废弃Redis**，适配灵族单机架构，三层存储划分固定：

| 层 | 介质 | 存储内容 | 失效/恢复 |
|---|------|---------|----------|
| **热层** | 进程运行内存 | 活跃会话元数据、最近N轮结论缓存、未归档Artifact索引 | 进程重启/会话休眠超30min自动清空；重连时从SQLite冷数据断点恢复 |
| **冷层** | SQLite lingmemory.db（WAL） | 全量消息记录、会话生命周期状态、lifecycle_events审计日志、info_records摘要/结论、M-flow实体关系网 | 持久化兜底，所有内存丢失数据均可完整恢复 |
| **大文件层** | 本地文件系统 | 图片、PDF、代码、表格、各类附件Artifact二进制 | 仅存sha256引用至数据库，不存二进制字段；路径规范见缺口7 |

**文档冲突消除**：本文档之前提及"热数据用内存/Redis"的描述(line 1013)已被本节取代。灵忆v1不使用Redis。

### 缺口6：Message元数据标准化补全

统一`message.metadata`固定结构，所有消息强制填充：

```json
{
  "model_version": "glm-5.1",
  "call_params": {
    "temperature": 0.7,
    "max_tokens": 4096,
    "top_p": 1.0,
    "stream": true
  },
  "token_count": {
    "input": 11800,
    "output": 350,
    "reasoning": 0
  },
  "latency_ms": 1200,
  "error": {
    "type": null,
    "code": null,
    "msg": "简短脱敏错误描述",
    "retry_count": 0,
    "fallback_model": null
  }
}
```

`error.type`枚举：`null | "timeout" | "rate_limit" | "auth" | "content_filter" | "server"`
`error.code`枚举：`null | 429 | 403 | 500 | ...`

### 缺口7：Artifact附件/文件完整规范

#### 1. 附件统一Schema

```json
{
  "artifact_id": "sha256:abc123...",
  "path": "sessions/{session_id}/artifacts/{sha256前16位}.{ext}",
  "file_type": "image | code | pdf | csv | text | video | audio",
  "size_bytes": 102400,
  "sha256": "完整哈希值",
  "source": "tool:main.py:20-45",
  "created_at": "ISO8601时间戳",
  "ref_count": 3,
  "retain": false,
  "cleanup_eligible": false
}
```

#### 2. 生命周期清理规则

- **ref_count**：统计被消息、info_records引用次数；每次引用+1，销毁引用-1
- **可清理判定**：`ref_count=0 && 会话已归档 && retain=false`
- **保护文件**：`retain=true`（关键报告、最终结论附件）永久保留，不自动删除

#### 3. 代码/表格补充解析规则

| 类型 | 解析规则 |
|------|---------|
| **代码块** | 自动识别编程语言标记；拆分嵌套代码段；超长代码分片存储为独立Artifact |
| **表格** | 自动结构化解析为csv文件存储；上下文仅保留表头摘要，完整表格按需懒加载 |

---

### 整体联动关系（与会话周期、Artifact懒加载打通）

1. 上下文截断/自动摘要产出内容，统一存入info_records，作为轻量化Artifact索引，遵循指针级懒加载，不占用常驻上下文
2. 会话daemon定时巡检，判断会话占用、闲置时长，自动触发截断/摘要/休眠流转，全部写入lifecycle_event审计
3. 拆分/合并会话时，同步迁移所有消息、附件引用计数，拆分前生成计费快照，文件Artifact哈希不变，仅更新多对多映射关系
4. 子任务状态异常导致`session.health=warning/abnormal`时，自动收紧上下文窗口阈值，提前执行截断清理，控制内存占用
5. 所有文件二进制与会话解耦，依靠artifact_id全局唯一，支持跨分片会话共享、复用，无重复存储

### 开发落地执行顺序

| 序号 | 任务 | 对应缺口 |
|------|------|---------|
| 1 | 标准化完善message.metadata结构，补齐error、call_params字段 | 缺口6 |
| 2 | 落地附件Artifact路径、引用计数、自动清理逻辑，补充代码/表格解析管线 | 缺口7 |
| 3 | 统一冷热三层存储分层代码，移除所有Redis相关逻辑 | 缺口5 |
| 4 | 实现消息前置冗余过滤模块 | 缺口2 |
| 5 | 开发上下文四段式截断策略、自动摘要触发逻辑 | 缺口1+3 |
| 6 | 实现四级指令继承注入机制 | 缺口4 |
| 7 | 对接会话状态机、daemon巡检、生命周期事件埋点全链路联动 | 全链路 |

---

## 全文档待确认项汇总（21项，待统一讨论）

> 以下为散落在各设计要点中的「待确认（细节后议）」项，统一归集。灵克已给出建议，待用户统一确认后回写各处。

### 第1组：Artifact懒加载（line 1357）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 1 | ref_count字段是否需要 | ✅ 需要 | 缺口7已定义ref_count清理规则，artifact必须追踪引用 |
| 2 | 持久化缓存层位置 | SQLite BLOB（lingmemory.db内） | 不开新DB。Artifact body≤10K可选缓存BLOB，>10K直接文件系统 |
| 3 | Fable 5 content占位 | path_ref为主，content_inline可选 | 灵族永远path_ref；content_inline仅v2外部化用 |

### 第2组：安全合规（line 1420）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 4 | content_filter方案 | 内置复用blacklist | 灵族已有ling-term-mcp blacklist，外部审核服务=v2 |
| 5 | 加密密钥管理 | 复用proxy v2 crypto（AES-GCM） | 密钥走环境变量，与proxy v2同一套 |
| 6 | 删除策略 | 软删除为主（保留hash），retain=true永不删 | GDPR硬删除=v2外部化 |

### 第3组：资源管控（line 1471）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 7 | 配额粒度 | v1同时per-member + per-session | per-member防独占，per-session防单会话膨胀（ZAI事故根因） |
| 8 | Token统计来源 | proxy主动写 | proxy已有trace_id，加session_id主动写灵忆，避免读usage.db耦合 |
| 9 | 熔断恢复 | 自动恢复（cooldown 5min）+ 手动确认（安全类熔断） | 临时熔断自动恢复；安全类需管理员确认 |

### 第4组：性能鲁棒性（line 1523）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 10 | 监控指标 | 加P99查询延迟 + 活跃session趋势 | daemon巡检基础数据 |
| 11 | 自动修复范围 | VACUUM自动（月度），数据修复全部人工 | VACUUM安全；数据修复必须人工避免误删 |

### 第5组：体验优化（line 1590）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 12 | 分组粒度 | v1只做task级 | session级分组需求弱（单owner），task级天然有todolist结构 |
| 13 | 模板数量 | v1内置3个（编码/巡检/审计） | 覆盖灵族最高频3类任务 |
| 14 | 清空颗粒度 | 三者都支持（时间/类型/大小） | 对应`--before`/`--type`/`--max-size`，dry_run先行 |

### 第6组：Agent专属（line 1662）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 15 | 失控检测阈值 | 30次工具调用/5min AND 同一工具重复率>80%/10min | 两条件AND避免误报，比原提案宽松 |
| 16 | tool_call_records保留期 | 7天 | 记录量大，7天够审计；结论已在info_records永久保留 |
| 17 | 降级事件元数据 | 记录「上下文重建策略」 | 降级后怎么恢复是关键信息 |

### 第7组：运维后台（line 1714）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 18 | 快照频率 | 5分钟 | 与daemon巡检周期一致 |
| 19 | 批量操作限制 | 单次最多500条 | SQLite单事务500条<100ms不阻塞 |
| 20 | schema版本号 | 纯递增整数（1, 2, 3...） | 语义化版本过度设计，纯递增+migration脚本足够 |

### 第8组：额外（访问控制，line 1357附近）

| # | 待确认项 | 灵克建议 | 理由 |
|---|---------|---------|------|
| 21 | "全员可见"visibility层级 | v1不加（private/shared/governance三级够用） | 灵族12成员全可信，shared已等价"全员可见"。"全员可见"仅多租户场景需要 |

---

## 权限/身份/访问控制缺口落地规范（2026-06-15）

### 现状盘点

| 子项 | 外部要求 | 灵忆现有 | 缺口 |
|------|---------|---------|------|
| 一人多会话 | 绑定关系 | ✅ sessions.member = member_id | 无 |
| 一会话多用户（协作） | 协作会话绑定 | ✅ 已收敛：单owner+session_links | 无 |
| 匿名/游客/登录区分 | 三类身份 | ❌ 只说"v2外部化"，无字段 | 缺口8 |
| 游客限制（能力/时长） | 限制+时长 | ❌ 完全缺失 | 缺口9 |
| 查看权限"全员可见"层级 | 4级查看 | ⚠️ 3级visibility，缺"全员可见" | 缺口10（v1不加，见待确认#21） |
| 编辑/操作权限细化 | 具体操作枚举 | ⚠️ 只说"读/写/管理"，未到操作级 | 缺口11 |
| 管理员批量操作 | 批量查看/关停/归档 | ⚠️ 有admin_force_close，无批量+清单 | 缺口12 |
| 链接分享/邀请/有效期/密码 | 分享管控 | ❌ 只说"v2留给前端"，无字段预留 | 缺口13 |
| 防越权（跨团队/外部篡改） | 禁止+防篡改 | ⚠️ 有visibility过滤，无跨团队+无防篡改 | 缺口14 |

---

### 缺口8：身份类型区分（member/guest/anonymous）

**灵族v1全是已知member_id**，但字段预留身份类型，为v2外部化做准备。

**session.identity_type字段**：

| 类型 | 适用 | 能力限制 | v1实现 |
|------|------|---------|--------|
| `member` | 灵族12成员+服务账号 | 无限制 | ✅ v1唯一类型 |
| `guest` | v2外部产品，受邀用户 | 见缺口9 | ⏸️ v2字段预留 |
| `anonymous` | v2公开链接访问 | 只读+限时 | ⏸️ v2字段预留 |

**sessions表新增**：
```
identity_type TEXT DEFAULT 'member'   -- member | guest | anonymous
```

**数据层强制**：所有查询带`WHERE identity_type = 'member'`过滤（v1硬编码，v2按类型放宽）。

### 缺口9：游客会话限制（能力+时长）

v1不实现，但设计预留：

| 限制维度 | guest默认值 | anonymous默认值 | 配置项 |
|---------|------------|----------------|--------|
| 最大并发session | 1 | 1 | quotas.guest.max_sessions |
| 单session最大轮次 | 20 | 5 | quotas.guest.max_turns |
| 单session最大时长 | 2小时 | 30分钟 | quotas.guest.max_duration |
| 可用模型 | 免费tier only | 免费tier only | quotas.guest.allowed_models |
| 可用工具 | 只读工具（view/grep/ls） | 无工具 | quotas.guest.allowed_tools |
| Artifact加载 | ≤3个 | 0 | quotas.guest.max_artifacts |
| 存储配额 | 10MB | 0（不持久化） | quotas.guest.storage_limit |

**v1动作**：quotas表预留guest/anonymous行（DEFAULT NULL），v2前端层填充。

### 缺口10：查看权限4级（v1决策=3级够用）

外部要求4级：本人/团队/全员/仅管理员。

**灵族映射**：

| 外部4级 | 灵忆visibility | 理由 |
|--------|---------------|------|
| 本人查看 | `private` | 直接映射 |
| 团队共享 | `shared` | 直接映射 |
| 全员可见 | `shared`（灵族12人=全员） | 灵族规模小，shared即全员 |
| 仅管理员可见 | `governance` | 直接映射 |

**结论**：v1不新增visibility层级。灵族shared=全员可见（12成员全可信）。"全员可见"仅多租户场景需要，已记入待确认#21。

### 缺口11：编辑/操作权限细化到操作级

现有"读/写/管理"太粗。按外部参考细化为操作级权限矩阵：

| 操作 | owner | 协作成员(related_members) | 其他成员 | 灵通+(协调者) | 用户 |
|------|-------|------------------------|---------|-------------|------|
| 继续提问(追加消息) | ✅ | ✅ | ❌ | ❌ | ✅ |
| 查看session内容 | ✅ | ✅(shared) | ❌ | ✅(所有) | ✅ |
| 修改会话名称 | ✅ | ❌ | ❌ | ❌ | ✅ |
| 添加/修改标签 | ✅ | ❌ | ❌ | ❌ | ✅ |
| 导出session | ✅ | ✅(shared) | ❌ | ✅ | ✅ |
| 删除session | ✅ | ❌ | ❌ | ❌ | ✅ |
| 转发session | ❌ | ❌ | ❌ | ❌ | ❌ |
| 强制关停 | ❌ | ❌ | ❌ | ✅ | ✅ |
| 批量归档 | ❌ | ❌ | ❌ | ✅ | ✅ |
| 修改security_level | ❌ | ❌ | ❌ | ❌ | ✅ |

**关键决策**：
- **转发session**：v1直接丢弃（不适用于SQLite+文件模型，已记入line 1217）
- **协作成员**只能继续提问+查看shared内容，不能改元数据
- **security_level修改**仅用户可操作（灵族最高权限）

**实现**：灵忆不实现RBAC引擎，用代码层`if/else`硬编码此矩阵（灵族规模小，12成员+1用户，不需要角色引擎）。

### 缺口12：管理员批量操作清单

现有只有`admin_force_close`。补充完整管理员能力：

| CLI命令 | 功能 | 安全约束 |
|---------|------|---------|
| `lingmemory admin list-sessions --status=abnormal` | 批量查看异常会话 | 只读 |
| `lingmemory admin force-close <session_id> --reason=...` | 强制关停异常会话 | 写lifecycle_event，需reason |
| `lingmemory admin batch-archive --before=<date> --dry-run` | 批量归档过期会话 | **dry_run先行**，显示影响清单 |
| `lingmemory admin batch-delete --status=archived --before=<date> --dry-run` | 批量删除已归档 | dry_run先行，retain=true跳过 |
| `lingmemory admin batch-quota --member=<id> --set=...` | 批量调整配额 | 写audit记录 |
| `lingmemory admin cleanup-artifacts --ref-count=0 --dry-run` | 清理无引用附件 | dry_run先行 |

**安全约束**（运维三原则）：
1. 所有批量操作**必须dry_run先行**
2. 删除类操作走**红区审批**（双签）
3. 所有操作**可回滚**（软删除+回收站，retain=true永不删）
4. 单次最多500条（待确认#19）

### 缺口13：链接分享/邀请/有效期/密码（v2字段预留）

v1不实现（灵族内部不需要链接分享），但数据模型预留字段：

**sessions表新增（v2预留，v1 DEFAULT NULL）**：
```
share_token TEXT DEFAULT NULL         -- 分享令牌（生成时写入，null=未分享）
share_expires_at TIMESTAMP DEFAULT NULL  -- 分享过期时间
share_password_hash TEXT DEFAULT NULL  -- 分享密码（bcrypt hash）
share_permissions TEXT DEFAULT NULL   -- 分享权限（read_only | comment | edit）
```

**v1约束**：share_token恒为NULL（代码层断言：内部session不可分享）。v2前端层生成token+设置参数。

### 缺口14：防越权（跨团队隔离+防篡改）

**跨团队隔离**（v1=灵族单团队，v2多租户）：

```
sessions表：
  tenant_id TEXT DEFAULT 'lingfamily'   -- v1恒为灵族，v2多租户

查询强制：
  WHERE tenant_id = 'lingfamily'   -- v1硬编码
```

v2每个查询带`tenant_id`过滤，不同租户session完全隔离。

**防篡改机制**（3层）：

| 层 | 机制 | 实现 |
|---|------|------|
| **写入审计** | info_records每次写入记录written_by+written_at+source_trace_id | ✅ 已设计（灵犀建议，line 1070） |
| **内容校验** | 关键记录存储sha256 hash，篡改可检测 | info_records.content_hash字段 |
| **不可变记录** | lifecycle_events表只追加不修改不删除 | 代码层禁止UPDATE/DELETE |

**info_records表新增**：
```
content_hash TEXT    -- content字段的sha256，写入时计算，用于检测篡改
```

**外部用户篡改防护**（v2）：
- anonymous/guest身份**不可写**info_records（只有member可写）
- guest只能通过"提交建议"接口写入待审核记录（status=draft），member审核后才转正式

---

## 会话生命周期管理缺口盘点（2026-06-15）

### 现状盘点（外部标准 vs 灵忆现有）

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 状态机定义 | 完整状态+转换规则 | ✅ 三层架构+9运行时状态+6归档状态+转换图 | **已覆盖** |
| 空闲超时/绝对超时/滑动超时 | 三种超时策略区分 | ⚠️ daemon有30min→休眠/7d→归档，但**无绝对超时、无滑动超时概念** | 缺口15 |
| 中断恢复策略 | 中断诊断→恢复策略→上下文重建 | ⚠️ 有"恢复"状态+中断恢复向导，但**缺恢复checkpoint+上下文重建细节+恢复失败处理** | 缺口16 |
| 并发会话控制 | 同成员多会话上限+排队+抢占 | ⚠️ 有"并发5个/全局50个"上限，但**缺超限后排队/拒绝策略+优先级抢占机制** | 缺口17 |
| 会话终止条件判定 | "诉求满足"的自动判定 | ⚠️ 有"诉求满足=结束"+"发起者确认"，但**缺用户沉默时的超时自动结束** | 缺口18 |
| 会话创建初始化 | 创建时字段初始化+模板+必填项 | ⚠️ 有创建期流程(理解→对齐→定型)，但**缺session创建时的字段初始化清单+创建API** | 缺口19 |

### 前后不一致（5处）

| # | 位置 | 矛盾 |
|---|------|------|
| **I-1** | line 648 vs 1461 | 健康度命名冲突：`normal/warning/abnormal`(3档) vs `healthy/unhealthy/paused/failed`(4档) — **同一session.health字段两个定义** |
| **I-2** | line 621标题 vs 623-639实际 | 状态数量：标题说"9状态"，实际6核心+4过渡=10个 |
| **I-3** | line 746 vs 1715 | daemon巡检间隔：line 746写死5min，运维section仍列为待确认 |
| **I-4** | line 638 vs 748-753 | on-demand中断：line 638说on-demand"中断=结束"不支持休眠，但daemon规则不区分成员类型，会对on-demand的stale会话尝试转休眠 |
| **I-5** | line 757 vs 1372 | "7天"歧义：daemon说"休眠>7d→归档"，安全合规说"归档30天→过期，过期7天→清理" — 两个"7天"含义不同 |

### 重复（2处）

| # | 位置 | 问题 |
|---|------|------|
| **D-1** | line 58-101 vs 621-740 | 状态机定义出现两次（原始11状态版 + 最终定版9状态版），转换图也画了两次 |
| **D-2** | line 403-415 vs 66 | 异常处理：执行期异常处理表与状态机"中断"状态定义重叠 |

---

## 剩余5要点缺口盘点（安全合规/资源管控/性能鲁棒性/体验优化/Agent专属/运维后台）

### 要点3：安全与合规

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 数据加密（存储） | 存储加密 | ✅ AES-GCM加密(line 1378) | 已覆盖 |
| 数据加密（传输） | 传输加密 | ❌ 未设计（灵忆无HTTP层） | 缺口20 |
| 审计日志完整性 | 不可篡改审计链 | ✅ lifecycle_events只追加+written_by/written_at | 已覆盖 |
| 合规留痕 | 留存策略 | ✅ retain=true永久+治理≥1年 | 已覆盖 |
| 敏感数据分级 | 分类分级标准 | ⚠️ 有sensitive标记，**缺分级标准（几级？每级处理？）** | 缺口21 |
| 数据主权/管辖 | 存储位置/管辖权 | ❌ 未设计（v2多租户需要） | 缺口22(v2预留) |
| 备份恢复策略 | RPO/RTO定义 | ⚠️ 有daemon备份(line 1377)，**缺恢复策略+RPO/RTO** | 缺口23 |
| 访问日志 | 查询审计 | ⚠️ lifecycle_events记操作，**缺access_audit查询日志表设计** | 缺口24 |

### 要点4：资源管控与限流

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 多级配额 | 四层配额 | ✅ 单次/会话/成员/全局(line 962) | 已覆盖 |
| 配额预警阈值 | 80%/90%预警 | ⚠️ 有超限动作(line 966)，**缺预警阈值** | 缺口25 |
| 实时用量推送 | proxy→灵忆实时 | ⚠️ 从usage.db读取(line 1462)，**缺推送机制** | 缺口26(已记待确认#8) |
| 成本归因模型 | 任务→会话→token→成本 | ⚠️ 有session_id/token_usage，**缺归因链** | 缺口27 |
| 优先级抢占 | 高优先级抢占低 | ⚠️ 有优先级排序(line 971)，**缺抢占机制** | 缺口28 |
| 熔断恢复 | 自动+手动 | ⚠️ 待确认#9已提建议 | 已记 |

### 要点5：性能与鲁棒性

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 查询性能指标 | P99延迟+活跃趋势 | ⚠️ 待确认#10已提建议 | 已记 |
| 写冲突处理 | WAL+busy_timeout+重试 | ✅ 复用灵信方案(line 1480) | 已覆盖 |
| 故障恢复 | WAL崩溃不丢+fallback | ✅ 有方案(line 1485-1486) | 已覆盖 |
| 自动修复范围 | VACUUM自动+数据修复人工 | ⚠️ 待确认#11已提建议 | 已记 |
| 灵忆自身故障隔离 | 不影响其他系统 | ✅ 松耦合(line 1490) | 已覆盖 |
| 性能基线数据 | 冷启动/热查询/批量写入benchmark | ❌ 无任何性能基线数据 | 缺口29 |

### 要点6：体验优化

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 前端UI | 分组/搜索/分页 | ✅ 灵网方案(line 1529-1571) | 已覆盖 |
| 模型配置 | session级模型参数 | ✅ model_config(line 1579) | 已覆盖 |
| 模板 | 创建期模板 | ⚠️ 待确认#13已提建议 | 已记 |
| 清空/重置 | 两动作分离 | ✅ line 1580 | 已覆盖 |
| 搜索/筛选 | FTS5+游标分页 | ✅ line 1577 | 已覆盖 |
| 导入/导出 | 多格式 | ✅ line 1578 | 已覆盖 |
| 通知/提醒 | 用户关心的会话变化提醒 | ❌ 只有LingBus通知，**无灵忆层的事件提醒**（会话即将超时/配额接近上限） | 缺口30 |

### 要点7：Agent专属

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 成员类型区分 | 常驻vs on-demand | ✅ line 1599-1604 | 已覆盖 |
| SDT会话模型 | 模板+实例 | ✅ line 1618-1621 | 已覆盖 |
| 失控检测 | 4条heuristic | ⚠️ 待确认#15已提建议 | 已记 |
| tool_call_records | 独立表+脱敏 | ✅ line 1646 | 已覆盖 |
| 任务边界 | task.boundary | ✅ line 1647 | 已覆盖 |
| 工具白名单 | session.allowed_tools | ✅ line 1649 | 已覆盖 |
| 主从状态同步 | 子→父聚合 | ✅ line 1638 | 已覆盖 |
| Agent间通信模型 | 灵忆不管，LingBus管 | ✅ line 1624-1626 | 已覆盖 |
| 模型降级兼容 | 灵忆=档案 | ✅ line 1651-1654 | 已覆盖 |

**要点7无新缺口**。已有设计+3待确认项覆盖了外部标准的全部要求。

### 要点8：运维与后台

| 子项 | 外部要求 | 灵忆现有 | 状态 |
|------|---------|---------|------|
| 监控指标 | DB大小/QPS/延迟/任务数 | ✅ line 1673 | 已覆盖 |
| 告警规则 | 膨胀/失败率/离线 | ✅ line 1674 | 已覆盖 |
| 备份 | daemon纳入 | ✅ line 1675 | 已覆盖 |
| 快照频率 | 5min/10min/15min | ⚠️ 待确认#18已提建议 | 已记 |
| 批量操作限制 | 单次上限 | ⚠️ 待确认#19已提建议 | 已记 |
| schema版本号 | 纯递增vs语义化 | ⚠️ 待确认#20已提建议 | 已记 |
| 运维三原则 | 干运行/双签/可逆 | ✅ line 1701-1704 | 已覆盖 |
| 部署方式 | systemd/容器/手动 | ❌ **无部署方案**（灵忆怎么启动？怎么重启？怎么升级？） | 缺口31 |
| 数据迁移 | schema升级迁移脚本 | ❌ **无迁移方案**（表结构变化时怎么迁移历史数据？） | 缺口32 |

---

## 全文档盘点汇总

### 缺口统计

| 要点 | 缺口编号 | 缺口数 |
|------|---------|--------|
| 上下文窗口控制+存储+消息处理 | 1-7 | 7 |
| 权限/身份/访问控制 | 8-14 | 7 |
| 会话生命周期管理 | 15-19 | 5 |
| 安全与合规 | 20-24 | 5 |
| 资源管控与限流 | 25-28 | 4 |
| 性能与鲁棒性 | 29 | 1 |
| 体验优化 | 30 | 1 |
| Agent专属 | — | 0 |
| 运维与后台 | 31-32 | 2 |
| **合计** | **1-32** | **31** |

### 不一致统计（6处）

| # | 矛盾 | 严重度 |
|---|------|--------|
| I-1 | session.health两套命名(3档vs4档) | **高** |
| I-2 | 状态数量9≠10 | 中 |
| I-3 | daemon巡检5min已写死vs仍待确认 | 低 |
| I-4 | daemon对on-demand行为矛盾(休眠vs不支持休眠) | **高** |
| I-5 | 两处"7天"含义不同(休眠→归档 vs 过期→清理) | 中 |
| I-6 | 安全合规Redis描述(line 1013) vs 缺口5废弃Redis | **已修复** |

### 重复统计（2处）

| # | 位置 | 问题 |
|---|------|------|
| D-1 | 状态机定义出现2次(line 58-101 vs 621-740) | 保留定版版，删早期版 |
| D-2 | 异常处理表(line 403)与中断状态定义(line 66)重叠 | 合并为一处 |

### 待确认项（21项，已汇总在line 1978-2042）

---

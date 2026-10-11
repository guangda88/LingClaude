# Codex 任务管理：生成中插入新任务的处理逻辑
## —— 以及 LC 的现状与借鉴清单

- **日期**：2026-10-08
- **作者**：lingclaude（灵克）
- **性质**：源码审计 + 差距分析，**未修改任何生产代码**
- **对照源码**：Codex `codex-rs` monorepo（Rust），本机 checkout 于 `/tmp/codex-probe/codex-rs`
- **⚠ 锚点失效风险**：AC 源码位于 `/tmp` 临时目录，清理后行号锚点失效。
  本目录已固化 5 份关键代码摘录（`excerpt_*.rs.txt`），复核请以摘录为准。

---

## 0. 摘要（先读这段）

AC 把"生成中插入新任务"当成一个 **带乐观并发保护的准入查询**：

> 六道结构化守卫 → 分配接受序 → 追加到 turn-local 队列 → 广播 → 在**下一个推理步边界**消费

插入失败时，调用方拿到的是一个**可枚举的拒因**（`NotSubmittedReason`，10+ 项），
而非一个 bool，从而知道该「排队」「让路」还是「丢弃」。

LC 把它当成 **无条件状态切换**：

> 挂起 → 浅拷贝消息 → 压栈 → 起新任务

无守卫、无拒因、无接受序。三套并行的任务状态机互不知情。

**最高 ROI 的一步不是上 SQLite，而是翻转 `submission.py:103` 的默认动作**：
先尝试插队，失败才抢占。这一步不动存储层，却能立刻获得 AC 语义的一半。

---

# 一、Codex 的做法

## 1.1 三层分离架构

| 层 | 位置 | 职责 |
|---|---|---|
| 持久化用户消息队列 | `state/src/runtime/queued_items.rs` | SQLite 落盘、跨进程、跨重启 |
| 调度服务 | `ext/queue/src/service.rs` | 空闲唤醒、派发、加锁 |
| 协议层（turn 准入） | `core/src/session/input_queue.rs` + `protocol/src/turn_input.rs` | 决定"新输入落到当前 turn 还是下一 turn" |

**关键：队列不是消息队列，是「待启动的 turn 输入」队列。**
`queued_items.payload_json` 存的是 `TurnInput`（可执行的输入结构），不是通知。

## 1.2 五态提交模式

`protocol/src/turn_input.rs:133`（摘录见 `excerpt_turn_input_mode.rs.txt`）：

```rust
pub enum TurnInputMode {
    StartOrSteer,                                // 空闲则起新 turn，忙则插进当前 turn
    StartIfIdle,                                 // 忙 → 拒收（不落盘不入队）
    ContinueIfIdle { expected_previous_turn_id },// 内部续跑，带乐观并发校验
    Steer { expected_turn_id },                  // 带 CAS 校验的插队
}
```

返回三种结局 `TurnInputSubmission`：`Started` / `Steered` / `NotSubmitted{reason}`。

## 1.3 核心设计：乐观 steer，失败才降级

`core/src/session/turn_input.rs:276` `start_or_steer()`
（摘录见 `excerpt_start_or_steer.rs.txt`）：

```rust
match session.steer_input(...).await {
    Ok(turn_id) => {
        settings.apply_steered(session, submission_id).await?;
        return Ok(TurnInputSubmission::Steered { turn_id })
    }
    Err(NotSubmittedReason::NoActiveTurn) => { /* 只有这一种失败才去起新 turn */ }
    ...
}
```

**只有 `NoActiveTurn` 才会走"起新 turn"分支，其余所有拒因都是终态拒收。**

这个顺序是整个设计的灵魂：它把「当前 turn 能否接纳」变成一个
**有明确失败原因的准入查询**，而不是先查状态再决定动作。

> **对比 LC**：`submission.py:103` 无条件 `on_new_request()`，先挂起再新建。
> **方向相反**——LC 是「默认抢占，steer 根本不存在」。

## 1.4 `steer_input` 的守卫链

`core/src/session/turn_input.rs:624-707`（摘录见 `excerpt_steer_input.rs.txt`）。
六道闸，按求值顺序，任一不过即返回结构化拒因：

```rust
let mut active = self.active_turn.lock().await;              // ① 单一锁，全序
let Some(active_turn) = active.as_mut() else { return Err(NoActiveTurn) };
let Some(active_task)  = active_turn.task.as_ref() else { return Err(NoActiveTurn) };
let active_turn_id = &active_task.turn_context.sub_id;

if let Some(expected) = expected_turn_id                       // ② CAS
    && expected != active_turn_id {
    return Err(ExpectedTurnMismatch { expected, actual });    //    ← 乐观并发
}

match active_task.kind {                                        // ③ turn 类型闸
    Regular  => {}                                             //    只有 Regular 可插
    Review   => return Err(ActiveTurnNotSteerable { Review }),
    Compact  => return Err(ActiveTurnNotSteerable { Compact }),
}

if UserInput { content.is_empty() }                            // ④ 空输入闸
    => return Err(EmptyInput);

if required_schema != active_turn.final_output_json_schema     // ⑤ 输出契约闸
    => return Err(ActiveTurnOutputSchemaMismatch);
```

**每一条都对应一类真实事故：**

- **②** 防止"我以为在 steer A，实际 A 已被换掉"——TOCTOU
- **③** Review / Compact 是**系统内部状态变更**的 turn，
  插入用户输入会污染压缩/评审逻辑
- **⑤** 最精妙：**插队会改变本 turn 的最终产物形态**。
  若新输入要求不同 output schema，插进去会让本 turn 承诺两个互斥的输出契约
  → 结构性拒收。
  AC 注释特意说明：比的是 JSON **Value 相等**（忽略 key 序），
  而非序列化文本相等——避免假阳性拒收。

## 1.5 接纳：分配接受序 → 追加 → 广播

`turn_input.rs:685-707`：

```rust
TurnInput::UserInput { content, client_id } => TurnInput::UserInput {
    content: std::mem::take(content),                        // ← 内容移走，源头掏空
    client_id: client_id.clone(),
    acceptance_order: self.reserve_user_input_order().await,  // ← 原子预留接受序
},
...
pending_input.push(input);
self.input_queue
    .extend_pending_input_and_accept_mailbox_delivery_for_turn_state(
        active_turn.turn_state.as_ref(), pending_input,
    ).await;
Ok(active_turn_id.clone())
```

两处细节值得单独指出：

1. **`std::mem::take(content)`** — 内容被移走，原 `SubmittedTurnInput` 被掏空。
   这保证了同一条输入**不可能既 steer 成功又被记进新 turn**，
   是「单次投递」的**结构性保证**，不靠调用方自觉。

2. **接受序在持有 `active_turn` 锁的临界区内分配**（`:632` 持锁 → `:694` 分配）。
   ⇒ 多个并发 steer 的「守卫通过序 = 接受序 = 消费序」严格一致。

## 1.6 插队后不重置上下文

`turn_input.rs:194` `apply_steered()`，注释是关键：

> *Applies only persistent settings after steering succeeds.
> The active turn keeps its existing context; subsequent turns see the update.*

插队成功后，**只把持久化设置（模型/权限等）应用到后续 turn，
活动 turn 的上下文一个字不动**。
已跑了一半的 turn 不会因为设置变更而语义漂移。

## 1.7 消费：step 边界生效

`core/src/session/input_queue.rs:259-284`（摘录见 `excerpt_pending_input.rs.txt`）：

```rust
// 接纳
extend_pending_input_and_accept_mailbox_delivery_for_turn_state() {
    turn_state.pending_input.items.extend(input);   // 追加，不打断
    turn_state.accept_mailbox_delivery_for_current_turn();
    self.activity_tx.send_replace(InputQueueActivity::Steer);  // ← watch 广播
}

// 消费
take_pending_input_for_turn_state() {
    turn_state.lock().await.pending_input.items.split_off(0)   // 一次取走并清空
}
```

`activity_tx` 是 `tokio::sync::watch` 通道。run loop **订阅**它，
一收到 `Steer` 就在**每个推理步之间**取走新输入，拼进下一次模型请求。

⇒ **当前 step 的 tool call 照常执行完毕**，新输入在下一步生效。
不中断、不回滚、不丢已完成工作。

`input_queue.rs:225` `has_pending_input()` **只认 `UserInput` 和 `FunctionCallOutput`**
——被动输出（如 subagent 完成通知）不触发 steer 循环，避免自激。

## 1.8 持久化队列：原子分配 + SQL 容量闸门

`state/src/runtime/queued_items.rs:73-99`（摘录见 `excerpt_enqueue_sql.rs.txt`）：

```sql
INSERT INTO queued_items (id, thread_id, payload_json, queue_order,
                          created_at_ms, updated_at_ms)
SELECT ?,?,?, COALESCE((SELECT MAX(queue_order) FROM queued_items
                        WHERE thread_id=?),-1)+1, ?, ?
WHERE (SELECT COUNT(*) FROM queued_items WHERE thread_id=?) < ?
RETURNING id, thread_id, payload_json
```

三点设计：

1. **序号分配与插入是同一条语句**，天然串行，不存在读-改-写竞态
2. **容量上限写在 SQL 的 `WHERE` 里**，满则 INSERT 零行 → 天然 fail-closed
3. **单调递增 `queue_order`，不做原地压实**——
   用 `reorder()` 显式重写为 `max+1..max+n`（`queued_items.rs:137`），
   且**要求 ordered_ids 与实际集合完全一致**（排序后比对，
   不一致就 rollback 报 `InvalidInput`），防部分重排造成重复/丢失

## 1.9 派发：轮询 + 按 thread 隔离

`ext/queue/src/service.rs` 的 `watch_external_messages`：

- 每 **10s** tick 一次
- 先查 SQLite `PRAGMA data_version`（廉价），
  **变了**才去查 `queued_thread_revisions` 增量表拿变更的 thread 列表
- 每个 thread 一个**独立 dispatch task**，
  一个卡住不饿死别的队列
  （源码注释明确写了 *"prevents a blocked or failed thread from starving other queues"*）
- 派发前检查 `AgentStatus`：
  `Running | Interrupted | Shutdown | NotFound` → 直接 return（**忙就不动**）
- 空闲且队列非空 → `wake_if_loaded`
- `dispatch_locks: HashMap<ThreadId, Weak<Mutex<()>>>` —
  **per-thread 锁 + `Weak` 自动回收**，锁表不会无限增长

⇒ 派发是**轮询 + 幂等**，不是事件驱动订阅。
代价是最坏 10s 延迟，收益是无跨进程竞态、进程崩了重启即恢复。

`ext/queue/src/lib.rs:11`：queue 扩展注册为 `thread_lifecycle_contributor`，
**排在低优先级 idle contributor 之前**（注释明确 *"before lower-priority idle contributors"*）。

## 1.10 生命周期语义

- **suspend**：`suspend_turn_and_shutdown()` 故意**不**发
  `TurnAborted`/`TurnComplete`，让别的 worker 能用**原 turn_id 恢复**；
  有已加载 descendant 时拒绝
- **recover**：`recover_turn_if_idle()` 复用已记录的 turn_id
- **continue**：`ContinueIfIdle` 带 `expected_previous_turn_id`，
  若期间起了新 turn → `Superseded`（乐观并发，防"续跑把新任务顶掉"）
- **inject**：`inject_if_running()` 无活动 turn 时
  **原样退回 items 给调用方**（`Err(items)`）——不静默丢弃

## 1.11 完整判据汇总

| # | 判据 | AC 位置 | 不满足时 |
|---|---|---|---|
| 1 | 有活动 turn | `turn_input.rs:633` | `NoActiveTurn` → 降级起新 turn |
| 2 | turn_id 匹配（CAS） | `:642` | `ExpectedTurnMismatch` |
| 3 | turn 类型 == Regular | `:651` | `ActiveTurnNotSteerable` |
| 4 | 输入非空 | `:665` | `EmptyInput` |
| 5 | output schema 一致 | `:671` | `ActiveTurnOutputSchemaMismatch` |
| 6 | 宿主未排空 | `:333` | `ServerDraining` |
| 7 | Plan 模式限制 | `turn_input.rs:213+` | `PlanMode` |
| 8 | 队列未满 | `queued_items.rs:73` | SQL 零行插入 |

完整拒因枚举见 `protocol/src/turn_input.rs:220-250`
（`Superseded` / `ServerDraining` / `NotIdle` / `PendingTriggerTurn` /
`PlanMode` / `NoActiveTurn` / `ExpectedTurnMismatch` /
`ActiveTurnNotSteerable` / `ActiveTurnOutputSchemaMismatch` / `EmptyInput`）。

---

# 二、LC 的现状

## 2.1 真实调用点

`lingclaude/core/submission.py:103`，`submit()` 内：

```python
# T0-3 行为校验 ...
# TaskManager: 新需求进来 → 挂起当前任务上下文
self._task_manager.on_new_request(prompt, self)   # ← 唯一入口，无条件
# T0-4 SkillIndex 匹配 ...
# PRE_TASK hook ...
# T2 意图确认 ...
```

全仓 grep 确认：`on_new_request` **仅此一处调用**
（`lingclaude/core/wiring.py:344` 为构造）。
所以 **LC 没有第二条路径**——不存在 steer，不存在排队，不存在拒收分支。

## 2.2 三个并行的状态机

| 组件 | 结构 | 持久化 | 忙时新任务怎么办 |
|---|---|---|---|
| `task_manager.py:79` `TaskManager` | **LIFO 栈** active/pending/completed | `.lingclaude/tasks.json`（**仅会话结束**时写） | `on_new_request()` → `_suspend_active()` 快照 → 压栈 → 抢占 |
| `task_scheduler.py:85` `TaskScheduler` | `_pending_tasks: list` 内存列表 | **无**（进程死即丢） | `_insert_by_priority()` 按紧急/高/中/低插入 |
| `task_aggregation.py:117` `TaskAggregator` | 相关性分组（type + 首文件目录） | **无** | 聚合等 `max_wait_seconds` 窗口 |

三者之间**无共享 task_id、无对账机制**。

## 2.3 派生问题

### (1) 没有"正在生成"的概念

`submit()` 是同步阻塞的，从 `:85` 一路跑到 `_generate_response()`。
没有"活动 turn"状态对象，因此 **§1.11 的判据 1-5 一条都无处安放**。

LC 判定 active 的依据是 `self.active.status == "active"`，
而该标记在 `on_task_complete()` 才清除——
**它标记的是「任务未完成」，不是「turn 正在运行」**。
两者在 step 之间、发请求之前是完全不同的状态，LC 混为一谈。

### (2) `on_new_request` 的执行位置有隐患（⚠ 本条经复核后已收窄）

`submit()` 内 `return` 提前退出的 5 处，**只有 1 处在 `on_new_request` 之后**：

| 行 | 闸 | 相对 `on_new_request`(:103) |
|---|---|---|
| 68 / 73 | 预算闸 `BUDGET_PAUSED` | **之前** |
| 87 | 轮次闸 `MAX_TURNS_REACHED` | **之前** |
| 96 | T0 行为校验 | **之前** |
| 127 | T2 意图确认 `_check_intent` | **之后** ✅ |
| — | `_pre_check_compact()`(:135) 不 return | — |

所以真实暴露面比初判窄：**仅 T2 意图确认这一条路径**存在
「active 已挂起压栈、但新任务从未真正开始」的不一致窗口。

> **本条为 2026-10-08 复核时的更正。** 初稿曾记为「跨 5 个提前返回点」，
> 系将 `on_new_request` 之前的 4 个闸一并计入，实测行号后确认不成立。

恢复依赖下一次 `submit()` 触发 `on_task_complete()` 弹栈，
而 `:127` 这条路径是否走到 `on_task_complete()` 取决于
`_make_turn_result()` 内部实现——**需实测确认**（见台账 #14）。

### (3) 没有接受序，恢复顺序语义反了

`task_manager.py:144` `self.pending.append(self.active)` 是纯 LIFO 追加。
而 `on_task_complete()` 用 `pending.pop()` **后进先出**恢复。

⇒ **最后压栈的先恢复 = 后到的先处理。**

LC 的并发来源恰恰很多：LingBus 派发、SDT 自驱唤醒、用户输入、subagent 回传。
这意味着一条**迟到的低价值 SDT 消息会优先于用户原始请求被处理**。

且 LC 侧**无编号可依据**——与 AC 的 `acceptance_order`（先到先处理）方向完全相反。

### (4) `_suspend_active` 快照与新任务共享存储

`task_manager.py:125` `self.active.messages_snapshot = list(engine._messages)`
—— 浅拷贝，但 `engine._messages` 本身**留在原地继续被写**。

`submit()` 在 `:85` 之后还会继续 append 消息
（skill hint、hook 修改、意图确认、生成结果）。

⇒ 压栈的 `messages_snapshot` 与新任务实际继承的 `self._messages`
**已经不一致**。恢复时 `restore_to()` 会把栈里那条的消息**再写一遍**。

对比 AC 的做法：`std::mem::take`（内容移走，源头掏空）+
`pending_input.items.extend`（追加到独立的 turn-local 队列）——
**两个动作在同一临界区、且不与新任务共享存储**。

> ⚠ 此项为静态读码推断，**未跑复现脚本**，标注为「未验证」。

### (5) `task_scheduler.py:182` 按值删除 bug

```python
for task in batch:
    self._pending_tasks.remove(task)     # ← 按值删
```

`Task` 是 `@dataclass(frozen=True)`，内容相等即相等。
两个内容相同的 task 会**删错对象/删漏**。可复现的数据正确性 bug。

### (6) 隐式的 NotSubmitted

`submission.py:63-91` 的预算闸与轮次闸，
目前都是直接 `return` 一个结果，等于把「为什么没跑」**隐含在结果文本里**。

⇒ 这两处**已经是「隐式的 NotSubmitted」**，改造时是白捡的收益。

### (7) `relay_queue.json` 无保护

当前内容为 `[]`，无 schema、无文件锁、无原子写。
对比 AC：SQLite + 原子 `INSERT ... SELECT MAX()+1` + SQL 内容量闸门。
JSON 方案在并发下会丢写。

### (8) 崩溃后 active 蒸发

`task_manager.py:234-243` `load()` 注释明确：
> *不恢复active，因为engine状态已重置*

⇒ 进程崩溃后，正在进行的 active 任务**直接丢失**，
只有 pending 栈里的挂起任务被恢复。

### (9) 枚举撞车

`TaskPriority` / `TaskStatus` 在 `task_scheduler`（中文枚举）
和 `task_aggregation`（英文枚举）中同名，
靠注释「灵元 E10 去歧义」和兼容别名硬撑。
说明问题早已被识别，但未从结构上解决。

---

# 三、借鉴清单（按 ROI 排序）

## P0-1 翻转默认动作：从「抢占」改为「乐观插队」

**与 AC 的根本性差异，优先级高于任何存储层改造。**

```python
# 现状（submission.py:103）
self._task_manager.on_new_request(prompt, self)          # 无条件抢占

# 目标
outcome = self._task_manager.try_steer(prompt)            # 乐观插队
if outcome is Steered:
    return                                               # 并入当前 turn，本次 submit 不起新 turn
if outcome is NotSubmitted(reason):
    # 按拒因分流：可排队 / 该丢弃 / 该让 SDT
    ...
self._task_manager.on_new_request(prompt, self)            # 仅在 NoActiveTask 时走到
```

**判据直接照抄 §1.11**：能否插队 = 任务是否需要「当前 turn 的产物作上下文」？
需要 → steer。不需要 → 排队。

**收益**：不动存储层，即可获得 AC 语义的一半。
**风险**：中——需新增 turn 状态对象与 step 边界消费点。

## P0-2 补齐守卫链，返回结构化拒因

```python
class NotSubmittedReason(str, Enum):
    NO_ACTIVE_TASK         = "no_active_task"      # → 降级起新任务
    TASK_RUNNING           = "task_running"        # LC 特有：系统状态变更任务不可插
    PLAN_MODE              = "plan_mode"           # Plan 模式禁自动输入
    SDT_YIELDED            = "sdt_yielded"         # 自驱让路
    BUDGET_PAUSED          = "budget_paused"       # 复用 session_budget_gate 现有闸
    EMPTY_INPUT            = "empty_input"
    EXPECTED_TASK_MISMATCH = "expected_task_mismatch"   # CAS
```

- `BUDGET_PAUSED` 是**白捡的**（见 §2.3(6)）
- `EXPECTED_TASK_MISMATCH` 对应 LC 的 TOCTOU：
  LingBus 派发与 SDT 唤醒可能同时针对同一 task_id 触发

**所有 `submit()` / `on_new_request()` 必须返回
`Submitted | Steered | NotSubmitted{reason}`，禁止裸 bool / None。**

## P0-3 `acceptance_order` + 修正恢复顺序

```python
class TaskSnapshot:
    acceptance_order: int = 0   # 接纳时原子分配，先到先得
```

`TaskManager` 增加 `self._order_counter`，**在持锁下**分配。
`on_task_complete()` 弹栈时按 `acceptance_order` **升序**恢复，而非 `pop()` 的 LIFO。

**直接修掉一个语义错误**（见 §2.3(3)）。

## P1-1 修 `_suspend_active` 的快照隔离

照抄 AC 的 `std::mem::take` 语义：
压栈时把 `engine._messages` **真的换成一份新列表**，
而不是拷贝旧的留在原地。**两个动作在同一临界区完成。**

## P1-2 单一任务真相源

`TaskManager`（LIFO 栈）、`TaskScheduler`（优先级列表）、
`TaskAggregator`（相关性分组）三份独立状态，
收敛为**一张表 + `parent_task_id` 显式表达栈关系**，
另外两个降级为查询视图。

顺带修 `task_scheduler.py:182` 的按值删除 bug（见 §2.3(5)），
并解决枚举撞车（见 §2.3(9)）。

## P1-3 崩溃恢复

`load()` 当前明确不恢复 active（见 §2.3(8)）。
改为：**每次 TaskSnapshot 状态变更即增量落盘**
（消息写 rollout 文件而非 JSON），
启动时 active 标记为 `interrupted` 而非丢弃。

与 AC 的 `recover_turn_if_idle()`（复用原 turn_id，不发 TurnAborted）同思路。

## P2 其余

- **持久化队列** → SQLite + 单语句原子 `queue_order` 分配 + SQL 容量闸门
  （摘录见 `excerpt_enqueue_sql.rs.txt`）
- **`reorder()` 集合完整性校验** — 排序后比对，不一致 rollback
- **`ContinueIfIdle` 式乐观并发** — 所有自动续跑携带
  `expected_previous_turn_id`，期间有新任务 → `Superseded`。
  **直接对应灵元铁律「自驱不得压过用户需求」**
- **派发改「`PRAGMA data_version` 探针 + 变更表」轮询**，
  按 task 隔离 dispatch，`Weak` 锁表自动回收
- **`inject_if_running` 失败必须原样退回**，禁止静默吞
- **单次投递结构性保证** — 对应 `std::mem::take`（见 §1.5）

---

# 四、建议落地顺序

| 阶段 | 内容 | 风险 |
|---|---|---|
| 今天 | 修 `task_scheduler` 按值删除 bug；定义 `NotSubmittedReason` 枚举（纯新增、零行为变更） | 低 |
| 本周 | `queued_items` 表 + 原子 enqueue；`TaskScheduler` 切到该表；补 active 崩溃恢复 | 低-中 |
| 下两周 | steer 通道（`pending_input` + `acceptance_order`）；三套状态机合一 | 中 |
| 之后 | `ContinueIfIdle` 式乐观并发；派发按 task 隔离 | 中 |

---

# 五、验证台账

按 AGENTS.md「验证台账纪律」记录。evidence 为原始工具输出锚点。

| # | 结论 | 锚点 | 标注 |
|---|---|---|---|
| 1 | 乐观 steer、仅 `NoActiveTurn` 降级起新 turn | `turn_input.rs:303-366` | ✅已验证(本轮) |
| 2 | steer 六道守卫链及拒因 | `turn_input.rs:624-675` | ✅已验证(本轮) |
| 3 | 接受序在持锁临界区内分配 | `turn_input.rs:632` 持锁 → `:694` 分配 | ✅已验证(本轮) |
| 4 | `std::mem::take` 保证单次投递 | `turn_input.rs:692` | ✅已验证(本轮) |
| 5 | 插队后活动 turn 上下文不变、只应用持久设置 | `turn_input.rs:194-201` | ✅已验证(本轮) |
| 6 | 五态提交模式 | `protocol/src/turn_input.rs:133-143` | ✅已验证(本轮) |
| 7 | 结构化拒因枚举 | `protocol/src/turn_input.rs:220-250` | ✅已验证(本轮) |
| 8 | 队列原子分配 + SQL 容量闸门 | `state/src/runtime/queued_items.rs:73-99` | ✅已验证(本轮) |
| 9 | steer 走 watch 通道、step 边界消费 | `core/src/session/input_queue.rs:259-284` | ✅已验证(本轮) |
| 10 | `has_pending_input` 只认 UserInput/FunctionCallOutput | `core/src/session/input_queue.rs:225` | ✅已验证(历史) |
| 11 | 10s 轮询 + 独立 dispatch task + Weak 锁 | `ext/queue/src/service.rs:88-170` | ✅已验证(历史) |
| 12 | queue 扩展注册优先于低优先级 idle contributor | `ext/queue/src/lib.rs:11` | ✅已验证(历史) |
| 13 | LC 唯一入口 = `submission.py:103`，无条件抢占 | `submission.py:103` + 全仓 grep 仅一处调用 | ✅已验证(本轮) |
| 14 | `submit()` 5 处提前 return 中，**仅 `:127` 在 `on_new_request`(:103) 之后** | `submission.py:68,73,87,96,127` | ✅已验证(本轮，实测行号；**更正初稿的「均在其后」**） |
| 14b | 初稿「跨 5 个提前返回点」结论 | 已按实测行号推翻 | ❌**已撤回**（AGENTS.md 撤回前廉价探针：逐行 `sed -n Np` 核对） |
| 15 | `_suspend_active` 快照与新任务共享 `engine._messages` | `task_manager.py:125` vs `submission.py:103` 后继续 append | ⚠**未验证**（静态读码推断，未跑复现脚本） |
| 16 | 恢复顺序为 LIFO（`pending.pop()`） | `task_manager.py:162` | ✅已验证(历史) |
| 17 | `_pending_tasks.remove(task)` 按值删 | `task_scheduler.py:182` | ✅已验证(历史，静态读码) |
| 18 | `load()` 不恢复 active | `task_manager.py:234-243` | ✅已验证(本轮) |
| 20 | 落盘期间 `submission.py` 被**并发改动**(+35 行)，本文件 LC 锚点在改动后**重新逐行实测仍全部成立** | `sed -n {68,73,87,96,103,127,135}p` 实测 | ✅已验证(落盘后复核) |
| 19 | AC 源码来自 `/tmp/codex-probe/codex-rs` | 临时目录，**清理后锚点失效**；本目录已固化摘录 | ⚠需复核 |

**复核方式**：本目录 `excerpt_*.rs.txt` 5 份摘录为 AC 侧证据快照，
与 §一 各节引用的行号一一对应，可脱离 `/tmp` 独立审计。
LC 侧锚点位于本仓 `lingclaude/`，**永久有效**。

---

# 六、本次变更说明

## 新增（本目录，均为未追踪新文件）

- `docs/research/codex_turn_steer_20261008/ANALYSIS.md` — 本文件
- `docs/research/codex_turn_steer_20261008/excerpt_*.rs.txt` × 5 — AC 源码证据快照
- `docs/research/codex_turn_steer_20261008/ANALYSIS.md.bak` — 行号修正前的初稿留档

## 未做

- **未修改任何生产代码**——本会话对 `lingclaude/` 零写入
- **未触碰**工作区既有改动

> ⚠ **并发改动告警**：落盘期间 `lingclaude/core/submission.py`
> 处于 `M` 状态（`+35 行`，非本会话所为）。
> 本文件所有 LC 行号锚点已在该改动**之后**重新 `sed -n Np` 实测校对，
> 结论仍成立（见台账 #20）。
> 但该文件在变动中，**下游引用本文件行号前请先复核**。

## 并发工作区状态（R6 快照）

审计期间 `git status --short` 实测：

```
 M lingclaude/engine/tool_registration.py          ← 他人修改，未触碰
?? docs/TASK_MGMT_AC_VS_LC_20261007.md              ← 非本会话产物，见下注
?? docs/handover/20261008_changshou_panel_v21_handover.md
?? docs/research/codex_turn_steer_20261008/         ← 本次新增
?? docs/research/panel_20261008_changshou/
```

> **注**：`docs/TASK_MGMT_AC_VS_LC_20261007.md` 在本会话**首次** `git status`
> 时不存在、末次快照时已出现，为**并发的其他执行体产物，非本次落盘内容**。
> 其文件名与本文主题（AC vs LC 任务管理）高度相邻，
> **存在内容重复的可能，合并前请先与该文件比对去重**。

## 复核提示

本文 §一 全部 AC 结论依赖 `/tmp/codex-probe/codex-rs`。
该路径为临时目录，**清理后行号锚点即失效**——
届时请以本目录 `excerpt_*.rs.txt` 为准（摘录起始行已注明）：

| 摘录文件 | 对应源码 |
|---|---|
| `excerpt_turn_input_mode.rs.txt` | `protocol/src/turn_input.rs:130-145` |
| `excerpt_start_or_steer.rs.txt` | `core/src/session/turn_input.rs:300-375` |
| `excerpt_steer_input.rs.txt` | `core/src/session/turn_input.rs:624-708` |
| `excerpt_enqueue_sql.rs.txt` | `state/src/runtime/queued_items.rs:70-100` |
| `excerpt_pending_input.rs.txt` | `core/src/session/input_queue.rs:255-290` |

LC 侧锚点位于本仓 `lingclaude/`，**永久有效**，已逐行 `sed -n Np` 实测校对。

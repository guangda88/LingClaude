# OC 任务管理逻辑分析：任务生成中插入新任务的处理 —— lc 对标与借鉴

> 2026-10-08 灵克(lingclaude) 代码审计产出
> 对比对象：opencode（anomalyco/opencode @dev，远端源码核对；本机 1.18.34 二进制 + `~/.local/share/opencode/storage/todo/*.json` 佐证）
> 本侧对象：lc（`lingclaude/engine/todo.py` + `lingclaude/engine/tool_handlers/todo_tools.py` + `lingclaude/cli/_commands_tasks.py`）
> 关联：`docs/TASK_MGMT_AC_VS_LC_20261007.md`（atomcode 侧同题分析）

## 一、OC 的任务管理设计：单事务全量替换 + 单写者 + 回灌自愈

### 1.1 三件套

| 文件（repo 路径） | 职责 |
|---|---|
| `packages/opencode/src/tool/todo.ts` / `packages/core/src/tool/todowrite.ts` | 唯一写入口 `todowrite` 工具 |
| `packages/opencode/src/session/todo.ts` | `SessionTodo.Service`：存储 + 事件 |
| `packages/opencode/src/tool/todowrite.txt` | 给模型看的使用规则（行为层） |

### 1.2 写入语义 = 单事务全量替换（`session/todo.ts`）

```ts
db.transaction(tx => {
  tx.delete(TodoTable).where(eq(TodoTable.session_id, sessionID))   // 先清空
  tx.insert(TodoTable).values(todos.map((t, position) => ({...t, position})))
})
events.publish(Event.Updated, input)                                // 再广播
```

- **原子**：delete + insert 在一个 DB 事务内，任何时刻读到的都是完整清单；中断/异常不会留下"删了一半"的撕裂态。
- **无稳定 id**：条目 = `{content, status, priority}` + `position` 显式排序，靠位置而非 id 身份（1.18 时代的本地文件 `storage/todo/*.json` 还带模型给的稳定 id `g1..g4`，新版 schema 已去掉）。
- **只回灌**：`toModelOutput` 把写完后的完整清单原样作为工具结果（`Output = {todos}`），模型上下文里永远保有权威副本——**没有 todoread，靠 echo 自愈**。

### 1.3 「任务生成中插入新任务」三层配合

| 层 | 机制 |
|---|---|
| 行为层（`todowrite.txt`） | "New instructions arrive - capture them as todos"；"If blocked or partial, keep it `in_progress` and add a follow-up todo describing the blocker"；"Update status in real time; don't batch completions"；completed 必须验证后才打 —— **插入 = 下一次全量写**，模型须把旧项连新项整体重发 |
| 并发层 | 生成期间用户消息进 TUI 队列（二进制内 `QUEUED` 渲染），**不直接改任务表**；写侧事务串行化，同轮多个 todowrite 也只会出现 last-writer-wins 整体生效，永无半新半旧 |
| 自愈层 | 工具结果回灌全量清单；`SessionTodo.updated` 事件推给 app/TUI（`packages/app/src/context/global-sync/event-reducer.ts` → `setSessionTodo`），面板被动刷新 |

### 1.4 单写者模型（invariant 的根）

- 只有模型能写：工具侧走 `permission.assert`（`todowrite` 挂权限闸门）。
- 客户端只读：SDK 侧 `SessionTodo` 只有 GET；二进制里检索不到任何「用户加 todo」的斜杠命令。
- 因此 OC **不需要**合并策略：外部插入根本不存在，全量替换的 last-writer-wins 是安全的。

### 1.5 OC 自己不做状态纪律校验

代码里没有"恰好一个 in_progress"的强制校验，全靠 `todowrite.txt` 的 prompt 约束 —— 这一点 lc（从 atomcode 学的代码层强制）反而更强。

## 二、lc 现状与差距

| 维度 | lc 现状 | 风险点 |
|---|---|---|
| 写者 | **三写者**：模型 `todo_write`（全量）+ 模型 `todo`（增量 create/start/…）+ 用户 `/tasks add\|start\|done`（`_commands_tasks.py:96`） | OC 是单写者，lc 没有对应 invariant |
| 覆写原子性 | `todo_tools.py:109-132`：`for old in store.list(): store.delete(old.id)` 再逐条 `add`，每步单独拿锁、单独重写文件 | 生成期间插入的新任务**时序依赖、随机丢失**：快照前插入→被删，快照后插入→幸存；中途异常→清单半空 |
| id 稳定性 | 每次全量覆写 uuid4 重造（`todo_tools.py:109-111` 注释自认） | `/tasks start <id>`、`parent_id`、跨轮引用断链 |
| 工具返回 | 只回统计数（`todo_tools.py:148-160`），不回清单和 id | 模型拿不到 id，只能靠 content 文本匹配（`active_id` 归一那套脆弱逻辑就是补这个洞）；compaction 后无法自愈 |
| 面板更新 | 轮询（`cli/repl.py:323` 1s 节流 + 每轮刷新） | 非事件驱动，插入后有秒级延迟 |
| 排序 | `priority` 降序 + `created_at`（`todo.py:250`），无 position | 插入位置语义不可控 |
| 并发闸门 | `concurrency_safe=False` + `WRITE_SCOPED_TOOLS` 兜底（`core/tool_call_executor.py:68-72`）→ 同轮 todo_write 不并行 | ✅ 已做对 |
| 测试 | `test_todo_store.py` 覆盖锁级并发（:59）与状态纪律（:133） | ❌ 无「覆写 vs 外部插入」原子性用例 |

## 三、借鉴清单（按优先级）

1. **原子替换（P0）**：`TodoStore.replace_all(items)` —— 一次拿锁、一次读改写、一次 temp+replace 落盘；handler 只调这一次。消灭撕裂态与"生成中插入被随机清掉"的窗口（对齐 OC 单事务语义）。
2. **明确写者模型（P0）**：OC 的 invariant = 模型单写 + 用户输入排队。lc 三写者必须二选一：
   - (a) `/tasks add`、LingBus、后台任务在轮内**排队、轮末合并**（对标 OC 的 QUEUED 路径）；
   - (b) 给外部项打 `source` 标记，`todo_write` 全量替换时**保留非模型来源项**（merge 语义）。
   禁止现状的不确定行为——要么确定保留，要么确定排队，不能看时序。
3. **稳定 id + position（P1）**：覆写时按 content（或模型自带 id，OC 1.18 本地文件即 `g1..g4`）匹配复用旧 id，只对新增项发新 id；补 `position` 字段显式排序（现靠 priority+created_at 排，插入位置语义弱）。
4. **工具结果回灌权威清单（P1）**：返回 `{id, content, status}[]` 而非计数（对标 OC `toModelOutput`），模型才能用 id 引用、compaction 后自愈，顺带弱化 active_id 的文本匹配依赖。
5. **把 OC 的插入规则写进 tool description（P1，零成本）**：新指令→立即入 todo；blocked→保持 in_progress 并追加 blocker 项；完成须验证后。这正是"任务生成中插入新任务"的行为层答案。
6. **事件驱动面板（P2）**：store 落盘后发 `todo.updated` 事件给 status bar，替掉 1s 轮询（对标 OC `events.publish(Event.Updated)` → `setSessionTodo`）。
7. **补原子性测试（P2）**：线程 A 跑 `todo_write`、线程 B 同时 `create`，断言结果符合既定策略（∪ B 的项 或 按 source 合并），而非丢失。

## 四、lc 应保留的既有优势

- 状态纪律**代码层**强制（唯一 in_progress、中断退回 pending 非 completed、禁批量刷绿、已完项不得复活）——OC 只有 prompt 层。
- 增量命令工具 `todo create/start/complete/...` 与 `/tasks` 面板（OC 没有增量入口，插入必须整单重发）。
- 会话文件布局（`<sid>.json` + 惰性 GC），本就是 2026-10-07 从 OC `storage/todo/<ses_id>.json` 偷师（`todo.py:6-14`）。
- `todo_write` 挂 `security_scope='write'`（对标 OC `permission.assert`）。

## 五、三方对照速览

| | OC | AC (atomcode) | lc 现状 |
|---|---|---|---|
| 写入形态 | 单工具全量替换 | 单工具双形态（全量基线 + `action:add/update` 增量） | 全量 `todo_write` + 增量 `todo` 命令，两套体系 |
| 原子性 | DB 事务 | reducer 折叠（非法输入整条拒绝，基线无损） | **非原子，先删后建** |
| 插入新任务 | 重发整单 + 用户输入排队 | `{"action":"add"}` O(1) 追加 | 重发整单，外部插入随机丢失 |
| id | 无（position） | 位置 id 稳定，append-only | uuid 每次重造，引用断裂 |
| 不变量位置 | 仅 prompt | reducer 层无条件强制 | handler 层校验 |
| 真源 | DB + 事件广播 | transcript 单一 fold | 有状态 TodoStore（面板轮询，可能双源漂移） |

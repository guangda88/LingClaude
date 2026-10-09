# AC 任务管理逻辑分析：执行中插入新任务的处理 —— lc 对标与借鉴

> 2026-10-07 灵克(lingclaude) 代码审计产出
> 对比对象：atomcode (`/home/ai/atomcode-src/crates/atomcode-capabilities/src/tools/todo.rs`)
> 本侧对象：lc (`lingclaude/engine/todo.py` + `lingclaude/engine/tool_handlers/todo_tools.py`)

## 一、AC 的任务管理设计：事件溯源 + 双形态单工具

### 1.1 一个工具、两种形态（按参数形状区分，不看工具名）

| 形态 | 参数 | 语义 |
|---|---|---|
| PLAN / RE-PLAN | `{"todos":[{content,status}]}` | 全量清单 → **替换基线** |
| 增量补丁 | `{"action":"add","content":"…"}` | 追加一个 pending 项（**执行中插新任务的正解**） |
| 增量补丁 | `{"action":"update","id":N,"status":…}` | 只改一项状态 |

schema 用扁平 union 而非 `oneOf`（源码注释：弱模型处理不好 oneOf）。

### 1.2 无内部状态，清单从 transcript 折叠推导（`reduce_todos` L200-215）

- 基线 = **最后一次**合法全量 list（`rposition` 定位），其后所有 `action` 调用按序折入
- 兼容旧 `todo` 工具名：fold 按 args 形状分流，恢复会话时新旧调用折出同一结果
- 执行器 `TodoTool.execute` 完全无内部状态（L228-229 注释明说），当前清单由 `derive_current_todos(messages)` 推导

### 1.3 不变量在 reducer 层无条件强制（`apply_todo_action` L143-182）

- `update→in_progress` 先把其他 in_progress 全部退回 pending —— **不依赖模型自觉**
- 非法 JSON / 未知 id（0 或超界）→ **静默忽略**（工具层已报错给模型，派生状态保持一致）
- 位置 id（1-based）稳定的前提：清单**只追加、只改状态**，从不重排/删除（L137-139 注释）

### 1.4 防御性语义

- 非法 list **永不清掉之前的合法基线**：`parse_todos` 校验失败整条拒绝（status 枚举、非空 content、至多一个 in_progress），错误信息喂回模型自纠
- RE-PLAN 显式重置 ids，基线之前的旧 action 事件作废 —— 防陈旧 update 污染新清单
- 回归锁：`reduce_replan_resets_ids_and_voids_stale_updates`、`reduce_in_progress_clears_previous_in_progress` 等测试固化

### 1.5 单一真源（single source of truth）

同一个 fold 同时供 kernel reducer、TUI 面板（title cache 由 execute 回显的编号列表播种）、replay/恢复会话使用 —— **实时 / 重放 / 注入视图永不漂移**。

## 二、lc 现状与差距

lc 的 `todo_write` 只有**全量替换**一条路。执行中插新任务的代价：

| 问题 | lc 现状 | AC 做法 |
|---|---|---|
| 插入新任务 | 重发整个清单，先 `delete` 全部旧项再重建 | 一次 `{"action":"add"}`，O(1) 追加 |
| id 稳定性 | 每次全量覆写 uuid4 重造，**跨轮 id 引用断裂**（todo_tools.py L110-111 注释已自认） | 位置 id 稳定，append-only 保证 |
| 失败破坏性 | 全删→逐项重建，中途失败留半成品 | 非法输入整条拒绝，基线无损 |
| 状态真源 | 有状态 TodoStore（文件+锁），UI/状态可能漂移 | 无状态，transcript 折叠，live/replay 同源 |
| 不变量位置 | handler 层校验（已修两轮 bug：大小写归一双匹配、统计口径） | reducer 层无条件强制，模型绕不过 |

另有：lc 的增量命令工具（`todo create/start/complete/cancel/get/delete`）与 `todo_write` 是**两套体系**，面板/推导未统一 fold —— 正是 AC 用"单一 fold 单一真源"避免的双源漂移。

## 三、借鉴清单（按价值排序）

1. **给 `todo_write` 加增量形态**：`{"action":"add","content":…}` / `{"action":"update","id":…}`。任务生成中插新任务不重发、不断 id、不冒全量覆写风险。lc 已有位置 id 基础可顺势改（放弃 uuid4 或保留 uuid 但对外暴露稳定序号）。
2. **基线保护语义**：非法清单整条拒绝且不清空旧清单。当前"先删后建"改为"校验通过才原子替换"（TodoStore 已有锁，加一步 pre-validate 即可）。
3. **单一 fold 真源**：`/tasks` 面板、会话恢复、跨轮 `active_id` 引用统一收敛到一个从对话记录/事件流折叠的推导函数，消灭 todo_write 与 todo 命令工具的双源。
4. **reducer 层强制不变量**：恰好一个 in_progress 的纪律从 handler 校验下沉到状态写入层（add/update 路径天然走到），把已修过的两类 handler bug 的复发面直接消掉。
5. **依赖序门控**（长期）：AC 系 tasks.md 的"先决任务未勾选即拒绝继续"（见 `docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md` 已有结论）。插入的新任务不仅被追加，还能声明为阻塞项，推进前检查 —— lc 完全没有，属缺失的更深一层。

## 附：关键源码锚点

- `reduce_todos`：todo.rs L200-215（baseline rposition + action 顺序折入）
- `apply_todo_action`：todo.rs L143-182（in_progress 独占强制、未知 id 忽略）
- `parse_todos`：todo.rs L91-118（校验失败整条拒绝）
- `TODOWRITE_DESCRIPTION`：todo.rs L239-251（双形态提示词原文，可抄结构）
- lc 对标痕迹：todo.py L268-273 状态纪律注释、todo_tools.py L54-57 对标注释

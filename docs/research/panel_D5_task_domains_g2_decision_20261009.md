# D5 裁决材料：五套任务域统一/划界 + G2 外部接入点

- 日期：2026-10-09 ｜ 台账：`20261008_wipe_case_debts.jsonl` D5（due 2026-10-15）
- 上游：`docs/AUDIT_20261008_TASK_INSERTION_CC_VS_LC.md` §4.1 / §7-2 / §7-3（L602-603 两项 ⚠ 未决）
- 性质：**待用户裁决**——本文只摆事实与选项，不做定案。

## 一、现状对照（2026-10-09 实测，修正审计 10-08 结论）

| 域 | 文件 | 审计判定（10-08） | 今日实测 | 修正说明 |
|---|---|---|---|---|
| **A. todo 面板** | `lingclaude/engine/todo.py` | 唯一活的模型侧系统 | ✅ 活，且 **R4 已改造**（单工具双形态/原子替换/source/稳定id t\<N\>/权威回灌，43+12 用例绿） | 审计点名的「todo_write 全删重造」已修复 |
| **B. TaskManager** | `core/task_manager.py` | ⚠ 生产主路径**死接线** | ⚠ **半活**：`submission.py:103 on_new_request`（请求入队）、`:228 on_task_complete`（完成回调+遗言摘要 `pending_summary`）有真实调用链 | 审计「死接线」判定**偏严**：B 是**请求队列语义**（用户排队请求管理），非「任务清单」语义。与 A 不同层，不是重复建设，但状态机词汇（active/suspended）与 A 高度相似，易混淆 |
| **C. TaskScheduler** | `core/task_scheduler.py` | ❌ 死接线 | ❌ 确认：`wiring.py` 注册 + D/A 互相引用，无模型/CLI/工具消费点 | 死码属实 |
| **D. TaskAggregator** | `core/task_aggregation.py` | ❌ 死接线 | ❌ 确认：同上，仅 C/D/wiring 闭环互引 | 死码属实 |
| **E. TaskContract** | `core/task_contract.py` | ✅ 活（只管验收） | ✅ 确认（验收清单，语义独立） | 维持 |
| **F. relay 队列** | `scripts/relay_queue.json` | ⚠ 生产未启用 | ✅ 确认 `[]` 空转 | 归编排层（LONGTASK_SOP），非引擎域 |

**语义分层结论**（供裁决的事实底座）：真正冲突的不是「五套清单」，而是三层：
1. **工作项清单**（A. todo）——模型执行用，已现代化；
2. **请求队列**（B. TaskManager）——用户提交请求的排队/完成语义，活但词汇易混；
3. **死码**（C. Scheduler / D. Aggregator）——无消费，纯维护税。

## 二、G2 现状：外部接入点已半存在

- 灵克作为 MCP server（`lingclaude/mcp/server.py`，`lingclaude-mcp` 入口）**已暴露 26 张工具卡**供灵族挂载——但其中**没有 todo 域任何卡片**；
- app-server（headless JSON-RPC，`cli/app.py:517`）只有 `run/stream/health` 三方法，无任务写端点；
- 本会话工具表的 native `todo` 工具（create/list/complete/start/delete/get）即 A 域全量 API，卡到 MCP 只差一层薄封装（参照 `mcp/server.py` 既有卡片模式，预估 ≤40 行）。

## 三、方案选项

### 方案 1：划界共存 + 清死码 + MCP 卡片（推荐）
1. **划界**：A=模型工作项清单（唯一清单语义）；B=请求队列（文档改名/注释锚定，词汇表区分 active(请求) vs in_progress(工作项)）；E=验收专用；F=编排层资产。
2. **清死码**：C. Scheduler、D. Aggregator 归档删除（约 800 行 + wiring 2 个 spec），测试同步清理。
3. **G2**：`mcp/server.py` 加 `todo_list` / `todo_update` 两张只读+受控写卡片（session_id 参数暴露给宿主侧），灵族任意 agent 可查/推进灵克任务面板。
- 工作量：~1 天。风险：C/D 若有隐藏测试引用需清（已查 wiring gate 有登记机制）。

### 方案 2：五域收编到 TodoStore 单核
- B/C/D 全部变 thin adapter，语义彻底统一。
- 工作量：3-5 天，动 submission 遗言链路（R1/R2 预算遗言刚落地，回归面大）。**收益/成本比低，不建议**。

### 方案 3：最小披露
- 只加 G2 MCP 卡片 + 文档划界，C/D 死码继续挂着。
- 工作量：半天。债从「语义分裂」降级为「死码维护税」，D5 半清。

## 四、裁决请求

1. 选方案 1 / 2 / 3（我的推荐：**方案 1**）；
2. G2 session_id 语义：外部 agent 操作的是**发起方自己的面板**（按 agent 名分桶）还是**灵克当前会话面板**（共享）？（推荐前者——隔离清晰，配合 R4 的 source 机制打 `source="external"`）；
3. 若选方案 1：C/D 删除是否需要保留迁移期 deprecation shim？

——材料完。裁决后按裁决执行，本材料即归档为决策依据。


---

## 五、裁决执行记录（2026-10-09，同日闭环）

裁决：采纳方案 1，三项请求按推荐项批准。四项附加条件执行结果：

| 条件 | 结果 |
|---|---|
| ① C/D 删除后 grep 仅剩历史注释 | ✅ `TaskScheduler\|TaskAggregator` 全仓仅剩 query_engine.py:26 / session_token_sink.py:9 / scheduler.py:6,78,82 记录性文字 |
| ② MCP 卡片冒烟：source==external 且不入 protect 集 | ✅ 直呼冒烟 5/5 + stdio E2E 2/2（tools=28、add source=external id=t1、list 读回、external 被 agent 覆写清掉、user 项保留、session 隔离） |
| ③ 全量回归确认无隐藏引用 | ⏳ 删除面受影响套件（p2a manifest/hotupdate/optimization_integration/wiring_gate/t3/mcp_server/todo_store/todo_write）定向回归先行，全量后台执行 |
| ④ 材料归档为决策依据 + D5 销账 | ✅ 本文即归档；wipe_case 台账追加 D5 resolved（evidence 六条） |

执行清单：
- 删除：`core/task_aggregation.py`、`core/task_scheduler.py`、`tests/test_task_scheduler.py`、`scripts/task_aggregation_demo.py`
- 依赖拆解：`core/scheduler.py` TaskPriority 枚举本地化（中文值域不变，兼容存量 schedules.json）+ TaskScheduler 注入死参数移除
- 埋点移除：`core/tool_executor.py` legacy 路由分支的 `_aggregator.add_task`（只写不读）及 route() 决策变量
- wiring：`_aggregator` 工厂/WiringSpec/tuple 三处删除，manifest 59→58（代码+yaml 同步，注释计数修正）
- G2：`mcp/server.py` 新增 `todo_list`（灵账）/`todo_update`（灵账写，action=Literal["add","update_status"]，source 恒 external）；`task_manager.py` B 域划界注释锚定（请求队列≠工作项清单，active≠in_progress）
- 测试：test_p2a（4 处计数/集合断言）、test_optimization_integration（7 处 aggregator 用例/断言外科移除）、test_wiring_gate（死码豁免与别名豁免条目回收）、test_mcp_server（28 断言 + G2 契约新用例：卡片 properties/action enum 断言，防 D2 家族空 schema 复发）

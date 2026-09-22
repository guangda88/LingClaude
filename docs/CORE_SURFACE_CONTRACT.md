# CORE_SURFACE_CONTRACT — engine/loop 迁移面契约（P0-A 阶段 0 产物）

状态: 生效中（v1，2026-09-21）
适用范围: P0-A 循环纯化迁移（`docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md` §四 P0-A）全程
裁判地位: 本文档是 P0-A 迁移的 **DoD 判定标准**，不是配套参考资料。
违反本文档的迁移提交 = 未完成，无论功能上看起来多完整。

---

## 一、迁移对象的精确界定

P0-A 原文（JEV_LAYA 优化计划）把迁移对象写作
「`core/l5_conversation_loop.py` + `engine/sub_agent.py` 抽到 `engine/loop/`」。
**此表述与代码事实不符，以本节为准：**

| 角色 | 文件 | 说明 |
|---|---|---|
| **主战场** | `lingclaude/core/model_call.py` | `_call_model`(L354) 与 `stream_call_model`(L562) 两条平行轮次循环，循环体各引用 17 个 `self._*` 槽位 |
| 已完成的第 0 步 | `lingclaude/core/loop_seam.py` | `LoopHooks` Protocol + `DefaultLoopHooks` 行为零变化转发，143 行 |
| 装配点 | `lingclaude/core/wiring.py`（`_loop_hooks` 槽）+ `QueryEngine.hooks` 属性 | 惰性兜底：裸构造引擎自动获得 DefaultLoopHooks |
| 搭车迁移件 | `lingclaude/core/l5_conversation_loop.py` | L5 幻觉治理审计循环（fallback 消费方：`l5_audit.py`），非主对话循环 |
| 最后单独迁移 | `lingclaude/engine/sub_agent.py` | spawn 复制 + tools 注册面耦合最深，置于收尾步骤 |

`LingClaudeThread`（对标 CodexThread 的会话级 facade）在阶段 3 落地，此前不得出现。

## 二、目标目录与允许的 import surface

目标布局（阶段 2 完成后）：

```
lingclaude/engine/loop/
    __init__.py          # 公共出口，见下表
    loop_body.py         # _call_model / stream_call_model 循环体
    hooks.py             # LoopHooks Protocol + DefaultLoopHooks（自 loop_seam.py 迁入）
    tool_loop_detector.py# _ToolLoopDetector（打转检测）
```

`engine/loop/` 的**公共出口白名单**（`__init__.py` 只 re-export 以下名字）：

- `LoopHooks`, `DefaultLoopHooks`, `default_hooks_for`（自 `core/loop_seam.py` 迁入
  `engine/loop/hooks.py`，L0 批次 1，2026-09-22；旧路径 shim 因 M3 铁律删除——core
  不得 import engine，改为引用方直切，见 §8）
- `L5ConversationLoop`, `L5ConversationConfig`, `L5RoundResult`（自
  `core/l5_conversation_loop.py` 迁入 `engine/loop/l5_conversation_loop.py`，
  L0 批次 2，2026-09-22；旧路径 shim 同上删除，引用方直切）
- `run_call_model_loop`, `run_stream_call_model_loop`（L1 迁移时的循环体入口名，
  仍在 `core/model_call.py`）
- `AGENT_MAX_TOOL_ROUNDS`, `_resolve_max_tool_rounds`
- `_ToolLoopDetector`
### import 方向铁律

1. `engine/loop/` **不得 import** `lingclaude.cli.*`（任何层级）。
2. `engine/loop/` 内部模块之间不得互相 import 私有槽位；一切经 `LoopHooks` 或显式参数。
3. `core/query_engine*.py` 只允许 `from lingclaude.engine.loop import <白名单名>`；
   旧路径 shim 已于 L0 删除（M3 铁律），core→engine 消费边走 M3 行级台账过渡（见 §七）。
4. 迁移**不得新增**对 `config.yaml` 的直接读取；配置注入继续走 `_resolve_model_config`
   边界与 wiring 槽位。

### L1 槽位面预览（AST 实测 2026-09-22）

**L0 迁移执行记录（2026-09-22，批次 1-5 全部完成，每批独立提交 + 基线全绿）**：

| 批次 | 内容 | 提交 | 验证 |
|---|---|---|---|
| 1 | `core/loop_seam.py` → `engine/loop/hooks.py` | L0 批次 1-2（此前提交） | 注入 fake 钩子离线回归 |
| 2 | `core/l5_conversation_loop.py` → `engine/loop/` | 同上 | 同上 |
| 3 | `_ToolLoopDetector`+循环常量 → `engine/loop/tool_loop_detector.py` | `da1de2c` | 29 passed |
| 4 | `engine/sub_agent.py` → `engine/loop/sub_agent.py` | `f2ff293` | 55 passed |
| 5 | 双路径循环体 → `engine/loop/loop_body.py`（`run_call_model_loop`/`run_stream_call_model_loop`，循环纯函数助手随行单源） | `3a5deee` | 49 passed |
| 阶段 3 | `LingClaudeThread` 薄壳 facade（`engine/loop/thread.py`） | `b026246` | 9 passed + import 探针 |

`ModelCallMixin._call_model`/`stream_call_model` 保留薄委托壳（调用面零变化）；
循环体单源在 `engine/loop/loop_body.py`。shim 按契约 §七未落盘（M3 铁律，
引用方直切）。

**L1 后复查登记（2026-09-22 lc 审计反馈）**：
- `loop_body.py` 547 行是插片内最大件——L1 钩子替换完成后复查是否再拆
  （候选缝：MV-1 校验三函数 / F12f 换候选重试块）。
- core→engine 存量边共 **10 条**（M3 差分守卫只拦新增；实测含
  `core/mcp_tools.py` 4 处动态 `from lingclaude.engine import mcp_proxy`——
  该文件 mcp_proxy 消费边是否随 L1 回收待定，mcp_proxy 本身不在循环体上，
  初判**不列入 L1 回收**、维持台账豁免至 mcp 工具面独立轨）。

`_call_model` 与 `stream_call_model` 的 `self.*` 槽位各 17 个，**交集 11**（双路径
共同 seam 参数面）：`_build_messages` `_build_openai_tools` `_clear_checkpoint`
`_finalize_turn` `_hard_interrupt_message` `_provider` `_resolve_model_config`
`_save_checkpoint` `_track_behavior` `config` `hooks`。
仅非流 6：`_assert_model_visible` `_get_last_tool_output` `_log_model_request`
`_log_to_flywheel` `_pre_send_check` `_tool_call_executor`。
仅流式 6：`_append_to_session_history` `_behavior` `_execute_tool_with_retry`
`_get_evidence_ledger` `_learn_from_turn` `_task_router`。
（注：引擎自有方法如 `_mv`/`_seen` 未计入，L1 随循环体走、不过 seam。）

## 三、print 禁令（ruff T20 ratchet）

- `lingclaude/core/` 与 `lingclaude/engine/loop/` 的**新增**代码禁止 `print`（T201/T101），
  统一走 `logging`。
- 存量 print 由 `pyproject.toml` 的 per-file-ignores 豁免（ratchet 底座），
  **豁免清单只允许缩小，不允许扩大**。
- TUI/CLI 层（`lingclaude/cli/`）不受此禁令约束。

## 四、槽位分类登记表（阶段 1 产物挂载点）

阶段 1 将对 60 个 `WiringSpec` 槽位逐个标注 A/B/C 三类。登记表落在本节，
迁移代码只能按登记表行动：

- **A 治理钩子类** → 迁入 `LoopHooks`（现 5 类已入：journal / provider outcome /
  health switch / flywheel / 幻觉闭环）
- **B 装配状态类** → 留在 wiring/引擎，循环体经参数或只读属性消费
- **C 纯配置类** → 冻结原样，禁止顺手"优化"进 hooks（防止 seam 变 god object）

### 4.1 分类结论（2026-09-22，阶段 1）

- **A 类（治理钩子候选）实际只有 8 个**，且 5 个已被第 0 步 LoopHooks 覆盖。
  循环体对 manifest collaborator 的真实引用面比预想窄得多：`_call_model`(L354)
  引用 18 个 `self._*`、`stream_call_model`(L562) 引用 15 个，交集 9 个；其中
  **真 collaborator 仅 4 个**（`_provider`/`_router`/`_task_router`/
  `_tool_call_executor`），其余是 QueryEngine 自有方法（`_finalize_turn` 等），
  随循环体 L1 迁移走，不过 seam。
- **死槽 1 个**：`_memory_engine` 全项目零引用（`wiring.py` 声明外无任何
  读取/写入），L2 建议直接从 manifest 删除。
- **教训钉进纪律**：检索槽位引用必须用裸 `._attr` 模式——L5/L1 状态全部经
  `self._engine._l5_*` 跨对象访问，`self\._attr` 正则会漏（本次实测 5 个槽位
  初判"死槽"，复查后仅 1 个是真死槽）。

### 4.2 登记表（59 条 manifest 槽位，逐条）

**A 治理钩子类（8）**

| 槽位 | phase | 判定 |
|---|---|---|
| `_loop_hooks` | state | 已入 seam（第 0 步）；L1 循环体迁移时随行 |
| `_tool_call_count` | state | 工具轮次计数，钩子语境（provider outcome 消费） |
| `_tool_call_log` | state | 循环证据流，golden master 已钉死其逐轮形状 |
| `_denial_streak`* | — | 文件内局部槽（非 manifest），熔断计数，钩子语境 |
| `_usage` | state | 循环累计，契约 §5.2.2 双口径事实的载体 |
| `_mv1_violations` | state | 循环内 MV-1 断言输出，治理钩子语境 |
| `_evidence_ledger`* | — | 文件内局部槽，stream 路径证据链 |
| `_task_router` | collaborator | 循环体引用（stream），治理决策语境 |

**B 装配状态/协作者类（51）**

| 槽位 | phase | 判定 |
|---|---|---|
| `_messages` `_conversation` `_transcript` `_project_index` `_history_epoch` | state | 对话/索引缓冲，引擎自有，循环体经参数消费 |
| `_denials` | state | 权限拒绝累积（submission.py 消费） |
| `_model_config` `_pinned_model_config` `_pinned_model_expires` | state | 模型配置槽，`_resolve_model_config` 边界（契约 §二.4） |
| `_journal_dir` `_session_history_path` | state | 持久化路径，journal 边界 |
| `_mcp_initialized` `_active_checkpoint` `_write_lock` | state | 生命周期/并发标志，装配语境 |
| `_intel_relay` `_session_cache_hits` `_total_messages_sent` `_l1_last_triggered_at` `_l1_handover_checksum` `_degradation_alerts` `_l5_orchestrator` | state | L5/L1/情报治理状态（l5_audit.py 经 `self._engine._*` 跨对象访问） |
| `_provider` `_runtime` | parameterized | 构造注入，P2.b 已并入 ctx |
| `_session_store` `_state_store` `_model_adapter` `_audit_collector` `_model_request_log` | collaborator(公开) | D3 seam 五件套，独立迁移轨 |
| `_behavior` `_intel_collector` `_session_persister` `_session_runtime` `_router` `_tool_router` `_tool_executor` `_tool_call_executor` `_cache` `_aggregator` `_monitor` `_prior_verifier` `_meta_cognition` `_layered_memory` `_dementia_detector` `_cognitive_rhythm` `_hooks` `_degradation_detector` `_task_manager` `_skill_index` `_role_checker` `_notifier` | collaborator(私有) | 22 个类实例协作者；循环体仅引用 `_router`/`_behavior`/`_tool_executor`/`_tool_call_executor`/`_task_router` 5 个，其余与循环纯化无关 |
| `_l5_loop` | collaborator | L5 对话循环实例（l5_audit.py 消费），随 L5 轨观察 |

**C 纯配置/冻结类（1）**

| 槽位 | phase | 判定 |
|---|---|---|
| `_model_router` | state | `wiring.py:354` 声明；消费点 `tool_executor.py:397` 与
  `query_engine.py:285`（装配期覆写）。语义是"路由器实例持有"而非治理钩子，
  **冻结**：禁止迁入 hooks（防止 seam god-object），只允许经显式参数进循环体 |

**D 死槽（1）**

| 槽位 | phase | 判定 |
|---|---|---|
| `_memory_engine` | state | 全项目零引用，L2 从 manifest 删除 |

\* 标注 = 文件内局部实现槽，不在 manifest，登记仅为完整覆盖循环体引用面。

## 五、行为零变化的裁判机制

### 5.1 裁判测试

`tests/test_golden_loop_master.py`（阶段 0 产物，9 用例全绿）是行为基线的唯一裁判：

- 机制：真实 `QueryEngine`（真实 `_finalize_turn` / MV-1 断言 / 打转检测 /
  SessionJournal / checkpoint）+ 定点 stub（fake provider、`_resolve_model_config`
  哨兵、磁盘落点入 tmp_path）。
- 覆盖：`_call_model` 与 `stream_call_model` 双路径 × 三类剧本（纯文本单轮 /
  工具+文本多轮 / 失败熔断）+ 双路径对称性抽查。
- 迁移每一步之后必须全绿；红 = 行为漂移，禁止"先合再修"。

### 5.2 已钉死的行为事实（迁移期禁止静默更改）

以下事实由探针实测后写入基线测试，**改动它们 = 行为变更**，必须先改本文档并评审：

1. **双路径熔断语义不对称**：非 stream provider 失败连续 3 次 → hard_interrupt
   文本收口（scope=model_call，记 flywheel）；stream provider 失败 → yield error
   即 return（不累计熔断）；stream 熔断只发生在工具全错轮（scope=tool_loop_stream，
   不记 flywheel）。
2. **stream 打转熔断（loop-abort）契约**：done 事件 `finalized=False`；
   done.usage = 循环内累计（不清零）；引擎级 `_usage` 不汇总（恒 0）；
   会话镜像（`_conversation`）不写；journal 无 `turn_end`。
3. **round_end 事件只在带工具的轮尾发**；纯文本收尾轮无 round_end。
4. **双路径 journal 不对称**：非 stream 的 tool_result/checkpoint 走
   ToolCallExecutor/checkpoint 文件不经 hooks；`turn_end` journal 事件为 stream 专属。
5. **usage 口径**：正常 done 的 usage 与引擎累计一致（`_accumulate_usage` 真实值，
   缺失不估算）；`ctx_input_tokens` 采样自最后一个成功请求轮。
6. **`MessageRole(str, Enum)` 序列化为大写**（`TOOL`/`ASSISTANT`），provider 请求
   快照按 `.upper()` 归一比较。

### 5.3 改基线的纪律

想让基线测试变绿的三种合法路径：

1. **基线本来就是错的** → 修正测试并在 PR 描述引用实测证据；
2. **行为本来就该变** → 先改本文档 §5.2（注明动机/影响面）→ 评审 → 再改代码 → 更新基线；
3. 其他一律 = 违规。

## 六、拔插等级定义

迁移与后续演进中，对循环体的任何修改按以下分级，级别越高审查越重：

| 等级 | 定义 | 要求 |
|---|---|---|
| L0 只挪不改 | git mv + re-export shim，零逻辑变化 | 基线测试全绿即可合入 |
| L1 接线替换 | `self._*` → `hooks.*`，语义等价 | 基线全绿 + 逐类小步提交 |
| L2 契约内演进 | 修 bug、加日志等，行为面不变 | 基线全绿 + 契约无变更 |
| L3 行为变更 | 任何触碰 §5.2 事实的修改 | 先改契约 §5.2 → 评审 → 双人确认基线更新 |

## 七、回滚与提交纪律

- 每个迁移步骤 = 一个独立可回滚提交；提交信息注明等级（L0/L1/...）与覆盖的基线用例。
- 顺序铁律：**先挪文件（L0），后换钩子（L1）**；反向会让 diff 不可 review。
- shim 删除前置条件：全仓 `grep` 无旧路径 import + 基线全绿 + 一轮完整 e2e。
  **实际执行记录（2026-09-22 L0）**：shim 方案在铁律守卫审计中被 M3 拦截
  （core/ 不得 import engine/，shim 恰是反向边）——守卫先于 shim 落地，符合
  "守卫即查询"。处置：13 处引用方（core 4 + tests 9）直切 `engine/loop/` 正身，
  shim 即删不落盘；core 侧 4 行消费边走 M3 行级台账过渡（review_due 2026-10-31），
  L1 循环体迁移完成后随宿主文件迁移回收。同批回收 M1 死账 2 条（旧 core 路径）。

## 八、迁移完成定义（DoD）

- [x] 阶段 1 登记表（§四）补齐并评审（2026-09-22，59 条槽位 A/B/C/D 四类）
- [x] `engine/loop/` 白名单出口与其余文件隔离（`__init__.py` 白名单 + golden master
  import 探针；M3 铁律 core↔engine 方向由守卫审计强制）
- [x] `tests/test_golden_loop_master.py` 全绿且自基线建立日起无违规修改
  （L0 批次 3/4/5 + 阶段 3 每步 9/29/55/49 passed）
- [x] ruff T20 ratchet 生效（pyproject.toml per-file-ignores 存量豁免，
  新增 print 拦截，豁免清单未扩大）
- [x] `l5_conversation_loop.py` / `sub_agent.py` 按搭车件/收尾件完成迁移
  （L0 批次 2/4）
- [ ] arch_ledger 记 entry（引用本契约版本号）→ 见 arch_ledger 提交

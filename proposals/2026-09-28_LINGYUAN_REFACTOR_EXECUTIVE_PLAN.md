# LingClaude 彻底重构执行方案 —— 灵元哲学落地（三方综合定稿）

- **日期**: 2026-09-28
- **性质**: 彻底重构（非修补）——把灵元哲学从设计原则落成运行时约束
- **输入**: A 架构设计（`2026-09-28_CODING_RUNTIME_HOTPLUG_DESIGN.md` 335 行）+ C 哲学审查（`2026-09-28_LINGYUAN_PHILOSOPHY_REVIEW.md` 237 行）+ B 迁移思路（raw_B.log，正文未落盘）
- **基线核实**: 全部代码锚点 2026-09-28 当轮读源码确认（coding.py:44-99 十一处直构 / seam.py:85-100 PLUG_LEVELS / policy_loader.py hot_update / coding_wiring.py:232-246 14 项 manifest / lingmemory/core.py LingMemory 三算子 / subagent_tools.py:33 provider 快照）
- **状态**: OPEN — 待裁决清单见 §七

---

## 一、为什么重构（不是修补）：根因诊断

上次重构（V3）解决了「主干不知插片」（装配数据化，wiring manifest），但没解决「插片不知热更」（实例生命周期）。症状：

1. `CodingRuntime.__init__`（coding.py:44-99）**裸直构 11 个对象**，与 `CODING_WIRING_MANIFEST` 14 项并存——**双轨制**，主干成了"伪主干"。
2. 修 `_model_provider` 必须重启进程；改 `verification_gate` 配置不生效（主干绕过 `from_config` 工厂裸调）。
3. `subagent_tools.py:33` 子代理持 provider **值快照**——主干 swap 后子代理仍用旧实例。
4. 新增代码继续在主干随手直构——没有架构守卫拦截。

灵元哲学要求：**2T3A 状态机主干不变，一切可变皆插片，插片热拔插零重启**。当前代码距此差一个「运行时热更闭环」。本次重构补这个闭环。

---

## 二、主干定义（采纳 C 的修正裁定 C2）

**主干 = 三层，全部冻结、不可插片化**：

| 层 | 组件 | 锚点 | 冻结理由 |
|---|---|---|---|
| 状态层 | 2T3A 三算子 `create/transition/query` | `lingmemory/core.py` LingMemory | 唯一状态真相源 |
| 生命周期层 | swap 协议（epoch/lease/drain） | 新增 `lingclaude/core/slot.py` | 自指悖论——swap 协议若能被换掉，"用什么协议换掉 swap 协议"无答案 |
| 结构层 | 装配入口（manifest 解析 + SlotTable） | `coding_wiring.py` 扩展 | 谁注册注册表的回归终点 |

**C4 裁定落地**：每次 slot swap 写一条 `type=slot_swap` 的 record 进 2T3A（epoch/config_digest/原因）——热更历史成为可 query 的真相，验证台账免费获得，"守卫即查询"落到热更域。

---

## 三、插片四分类（采纳 C 的修正裁定 C1：反对"一律入槽"）

用「变化频率 × 资源持有」矩阵过滤 11 个直构对象。**只有持资源/高频变化的才配槽+swap 重机械**；无状态对象走轻通道，实例级 swap 对它们是纯间接层税。**槽数预算 ≤8，入守卫。**

### 3.1 重通道（槽 + swap，3 个）

| 槽名 | coding.py 锚点 | 拔插等级 | 状态迁移 |
|---|---|---|---|
| `model_provider` | :46-59 | L1 | 在途请求 lease 跑完，新请求走新实例（现 switch_model 正规化，层级从 QueryEngineModelMixin 内收为 provider 插片内部 swap） |
| `todo_store` | :77 | L2 | 状态在 SQLite 文件不在进程：swap=关旧连开新连指向同库；降级=内存 TodoStore |
| `session_runtime` | :96 | L1 | 引擎引用是不变量，重建时重注；StateStore 后端按分形铁律降级为子插片槽；反向引用收窄为 Protocol |

### 3.2 轻通道（PolicyLoader 配置读穿 + digest 变化惰性重建，4 个）

| 对象 | 锚点 | 机制 |
|---|---|---|
| `verification_gate` | :61 | **一行修复**：主干改走 `from_config(VerificationConfig)`（工厂已存在却被绕过——本次正名）；降级=NoopGate |
| `pattern_recognizer` | :60 | `register_detector()` 上提为 manifest `detectors:` 列表（注册数据化） |
| `loop_detector` | :90 | streak 计数声明易失，swap 清零可接受（文档化）；阈值外置 YAML |
| `verify_cadence` | :99 | 窗口声明易失；env 开关迁入 YAML |

### 3.3 不设槽（4 个）

| 对象 | 处置 |
|---|---|
| `_todo_handlers` | 派生物：`make_handlers(slot.todo_store)`，随源槽一次重算 |
| `_lsp_provider` | **死槽直接删**（恒 None，生产走 LspSessionPool）——现成的截肢测试案例 |
| data_dir/session_id 解析 | 不是对象，是装配上下文字段 |
| sandbox_policy/plan_mode/guards | 已在 _setup_tools 尾部，顺势入 manifest |

### 3.4 双轨制消灭

既有 `CODING_WIRING_MANIFEST` 14 项全部补 slot 元数据，统一走同一装配/热更通路——这是"伪主干"观感的真正来源。

---

## 四、统一 swap 语义（全库唯一一套，插片不许自创热更协议）

1. **先建后换**：factory 失败旧实例原地不动（fail-soft）。
2. **换手即生效**：新请求解析到新实例。
3. **在途排空**：lease 计数，旧实例等租约归零再释放。
4. **重建判据用 config_hash**（解析后 dict 的 hash），不用 mtime——天然免疫"同长度改写漏检"（PolicyLoader 已知边界）。
5. **槽内禁止构造期绑定他槽实例**（A 方案 R4 风险）：tool_pipeline 持 registry 引用改持 SlotHandle。
6. **子代理改持 SlotHandle 调用时解析**——根治 `subagent_tools.py:33` provider 快照问题。

---

## 五、配置触达通路（唯一一条）

```
PolicyLoader (mtime watch + hot_update)
  → listener（新增口，待裁决①）
  → SlotManager.rebuild(config_key)
  → Slot.swap()
  → 写 slot_swap record 进 2T3A
```

轻通道走 PolicyLoader digest 变化 → 调用方惰性重建（无槽、无 lease）。

---

## 六、架构守卫（防回流，全部门禁化）

| 守卫 | 机制 | 基线 |
|---|---|---|
| G9 | AST 直构禁令：主干文件禁止 `KnownPlugClass()` 直构 | 新规则 |
| G10 | 直构计数棘轮：11→0 只降不升 | 基线 11（实测） |
| M6 | swap 行为测试：config 变 → 实例换 → 在途不炸 → record 落账 | 新测试 |
| 槽数预算 | ≤8，超出即红 | 新规则 |
| R10 防桥变永久居民 | SLOT-BRIDGE property 计数告警，P5 硬验收=0 | 新规则 |

复用既有 `test_iron_law_guards.py` M1-M5 + `arch_guard_gate.sh` 门禁机制，不另起炉灶。

---

## 七、待裁决清单（3 项，动手前需拍板）

| # | 事项 | 选项 | 建议 |
|---|---|---|---|
| ① | PolicyLoader 加 listener 口（改动 `core/policy_loader.py`，触及主干边缘） | 批准 / 改走轮询 | **批准**——轮询有 30s 节流延迟，listener 是配置触达实例的唯一实时通路 |
| ② | G10 棘轮基线口径 | coding.py 单文件 11 / 全主干文件 | **全主干文件**，防"挪到 mixin 继续直构" |
| ③ | 试点插片 | verification_gate（C 推荐，1 行修复）/ model_provider（A 推荐，收益最大） | **verification_gate 先行**（验证通路），model_provider 紧随其后（P1 主目标） |

---

## 八、分阶段实施（每阶段可独立验收、独立 revert，每阶段必须回答"哪个测试因此从红变绿"——C 元反模式设防）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | verification_gate 走 from_config（1 行）+ 删除 _lsp_provider 死槽（截肢测试） | config.yaml verification 配置生效测试绿 |
| P1 | slot.py（Slot/SlotHandle/SlotManager + epoch/lease）+ model_provider 入槽 + sub_agent 改 SlotHandle | M6 swap 测试绿；改 config.yaml model 不重启生效 |
| P2 | todo_store + session_runtime 入槽（含 StateStore 子插片分形、Protocol 收窄） | 同库重连测试绿；session 重绑定测试绿 |
| P3 | 4 个轻通道对象配置外置 + PolicyLoader digest 惰性重建 | 策略 YAML 改动不重启生效测试绿 |
| P4 | 14 项 manifest 补 slot 元数据，双轨制消灭 | 装配一致性测试绿 |
| P5 | G9/G10/槽数预算守卫门禁化 + SLOT-BRIDGE 清零 | arch_guard_gate.sh 全绿；主干直构计数=0 |
| P6 | slot_swap record 入 2T3A + 热更台账 query | `query(type=slot_swap)` 返回热更历史 |

---

## 九、反模式设防（C §五，9 条 + 1 元）

槽地狱（槽数预算≤8）、间接层税（无状态走轻通道）、快照泄漏（SlotHandle 调用时解析）、热更风暴（单 SlotManager 串行 rebuild + 每槽独立 epoch）、代码热更幻觉（明确不做模块级热更，OSGi 为警示）、构造期绑定（持 SlotHandle 不持实例）、双注册表漂移（SlotManager 唯一同时写两者）、测试注入断裂（overrides 语义与现 assemble_coding 逐字对齐）、守卫基线漂移假红（G10 用计数不用行号）。

**元反模式**：把架构纯洁性当目的本身——每阶段交付必须回答"哪个测试因此从红变绿"，答不出不立项。本方案一切论证与换域/截肢测试冲突时，以测试为准。

---

## 十、三方分歧与综合说明

| 议题 | A（架构师） | C（哲学审查官） | 综合裁定 |
|---|---|---|---|
| 入槽范围 | 11 个全入槽 | 只 3 个真插片配槽 | **采 C**——轻通道 4 个 + 不设槽 4 个 |
| 主干定义 | 2T3A | 2T3A + swap 协议 + 装配入口 | **采 C**（自指悖论论证成立） |
| swap 事件 | 未提 | 必须入 2T3A record | **采 C**（C4，台账免费） |
| switch_model 层级 | 归位主干 | 内收为 provider 插片内部子插片 swap | **采 C**（分形：主干热更与插片内热更是同一协议递归） |
| 迁移顺序 | 7 阶段 | 未排 | **采 A 骨架 + B 风险排序**，P0 试点 verification_gate |

B（迁移工程师）正文未落盘（280s 超时），但其思路（Slot.checkout 排空、TodoStore 同库重连、SessionRuntime 重绑定、AST 守卫拦截已知插片类直构、测试套件属性耦合核查）已全部并入 A 的风险表与本方案 P1-P2。

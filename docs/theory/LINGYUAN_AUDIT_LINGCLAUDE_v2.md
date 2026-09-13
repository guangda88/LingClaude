# 灵元(lingmate) 1.0 尺子照 lingclaude —— 体检报告 v2

> 日期：2026-09-13
> 记录者：灵克(lingclaude)
> 性质：以灵元 1.0 哲学为尺子，对 lingclaude 现状的独立体检，并回答「热重载/热拔插」可行性，提出下一步优化方向
> 关系：补正 `LINGMATE_HOTRELOAD_PATH_v1.md`（监督者 2026-09-12）中已过时的数字（coding.py 905→542、app.py 1910→913），并以灵克视角独立复核

---

## 一、灵元 1.0 哲学要义（内核，非照抄）

来源：`/home/ai/lingmate/` 11 篇哲学文档，`灵元V1.0.md` 为定版。

灵元不是「状态管理框架」，而是一把**拆解的尺子**。

### 本体（2T3A）
- 2T：`records`（在 / 存在）+ `events`（变 / 变化）
- 3A：`create`（出入·信息进来）/ `query`（出入·观照）/ `transition`（流转·含合法性校验）
- 承载：`type(str) + data(json)` —— 消化一切变化，加新业务不改表结构

### 方法论（三步，全是「减」不是「加」）
1. 找到什么不变 → 2. 砍到最薄 → 3. 变化变插片

### 四条尺子（从 6 个实例提炼）
- ① 一个 record 只有一个 state（多观察者读 state，不复制 state）
- ② 不同维度用不同 type（type+data 消化变化，但不在一个 type 里塞两个维度）
- ③ 策略是 events 的 data 或 type_registry 的配置，**不是结构的一部分**（改策略不改结构）
- ④ 每个厚主干问题的解法不是「加规则」，是「砍到最薄」——砍薄后**问题本身消失**

### 两条边界（易被误用的关键）
- 灵元是尺子，不是银弹：管一切 ≠ 删一切（抽取 in/out 的记录层 ≠ 替代业务执行逻辑）
- 粒度是唯一边界：拆到任意层仍是四个词（主体→目标→信息 in/out→状态）

---

## 二、体检结果：灵元尺子照 lingclaude

**关键事实**：lingclaude 不是「完全违背」，而是**正在半迁移中**——几处已踩对方向，但仍有一批厚主干焊死未动。方向对，执行不彻底，且有顺序隐患。

### 2.1 已符合 / 已踩对方向（公允记录）

| 文件 | 符合点 | 真凭据 |
|---|---|---|
| `core/state_store.py` | 已用 `StateBackend` Protocol + `record_type` 对应 2T3A | `state_store.py:41-70` |
| `engine/tool_registration.py` | tool 定义与实现解耦：`SPECS` 表 + `handler_name`（T3 插片，不直持 Callable）| `tool_registration.py:308-333` |
| `model/task_router.py` | 读外部 `config.json` 的 `routing.providers/task_routes`，策略已外置为 data | `task_router.py:3-6, 294` |
| `core/model_call.py` | 已有热重载雏形 `_maybe_hot_reload_config`（轮询 config.yaml）| `model_call.py:56-87` |

### 2.2 不符合灵元 1.0 的项（真凭据）

| # | 灵元尺子要求 | lingclaude 现状 | 真凭据 |
|---|---|---|---|
| 1 | 变化=插片，不焊进主干 | `coding.py` 把 10 个 mixin 焊进一个类主干 | `engine/coding.py:34-50`（Background/Bash/File/Git/Lsp/Plan/Search/Subagent/Todo/Web 共 10 个 Mixin）|
| 2 | type+data 消化变化 | 3 个 router 在 wiring 里启动写死 | `core/wiring.py:128-146`（`_make_router`/`_make_task_router`/`_make_tool_router`）|
| 3 | 策略是 data，改策略不改结构 | 智能 router 关键词硬编码（13 组 if/in）| `model/intelligent_router.py:48-80, 233-255` |
| 4 | 策略是 data | 行为路由 8 个阈值硬编码（0.7/0.3/0.5/0.2…）| `core/behavior_aware_router.py:42-57` |
| 5 | 策略是 data，不复制 | 15 个执行声明正则硬编码为 fallback 副本 | `core/l10_a_post_audit.py:131-162`（`_FALLBACK_PATTERNS`）|
| 6 | type+data 消化变化 | provider 选择 if/elif 硬编码，import 在函数内 | `model/factory.py:38-52`（只认 openai/anthropic）|
| 7 | 1 record 1 state | 多套状态机并存 | `core/dementia_detector.py`、`core/model_call.py`、`governance/cognitive_state.py`、`core/query_engine.py` |
| 8 | 砍到最薄 | `core/` 单目录 81 个 py 文件，全仓 201 个 py、约 4.9 万行 | 实测 `find lingclaude -name '*.py'` |
| 9 | 减法（不留半成品）| 20 个 `.bak` 文件长期堆在工作区 = 反复在厚主干上试错 | 实测 `find lingclaude -name '*.bak'` = 20 |

**核心诊断**：违反点高度集中在「策略硬编码在结构里」这条（尺子③）。阈值、关键词、正则、provider、router 全部焊死在 Python 类/if-elif 里。这与六实例里「L1-L2-L3 反复出错」「灵忆 7 表」是同一个根因——**在主干上堆分支/层**。

---

## 三、「热重载、热拔插」能否做到？（结论）

**能，但前提是先砍薄，顺序不能反。** 这是灵元哲学给的最重要约束，也是与「朴素地加 importlib + watchdog」的本质区别。

### 3.1 为什么现在做不了

当前大部分「变化」都不是插片，而是**焊死在主干上的结构**：
- 改阈值 → 改 `behavior_aware_router.py` 的类字段 → 重启进程
- 加 provider → 改 `factory.py` 的 if/elif + 函数内 import → 重启
- 加 tool → 改 `wiring.py` 启动写死 → 重启

「热重载」现在的障碍**不是 Python 做不到动态加载**（`importlib.util.spec_from_file_location` 谁都会），而是**这些变化根本没被建模成 data**。只要它是 Python 类定义的一部分，热重载就只能靠 reload module，而 reload module 会带来旧实例引用泄漏、状态丢失、循环 import——这些麻烦**恰恰是「厚主干」的自然后果**。

### 3.2 灵元给的正确路径

> **真热重载是 reload data，不是 reload module。**

按三步法推：

```
什么不变 = 主干（records+events+create/query/transition 骨架）
砍到最薄 = 只留「记录 + 流转」两个动作
变化变插片 = 阈值/正则/关键词/provider/tool/路由 全是 type+data
```

一旦变化真变成 data（YAML/json + type_registry）：
- 改阈值 = 改 data 文件 → 下个 turn 生效（PolicyLoader.watch）
- 加/卸 tool = register/unregister 一个 type → 不动主干
- 切 provider = 换 registry 里的 type → 不动主干

**「加载卸载不用动主干」这句话，只有走到「变化变插片」才成立。**

### 3.3 顺序铁律

```
❌ 先加 SeamRegistry 再砍主干 → SeamRegistry 变成新厚主干（重复六实例的错）
✅ 先砍主干（3 router→1 record+3 type，10 mixin→1 record+N type）
   → 露出接缝 → 再加 PolicyLoader / SeamRegistry
   → 最后把策略从结构搬到 data
```

---

## 四、下一步优化方向（按灵元尺子排优先级）

方向遵循「**先减后加、先砍厚主干再加接缝**」，共四步。

### 阶段 0（诊断确认，零代码改动）——P0

先定死「哪些变化该是 data」这张清单，作为每一步的验收尺子：

| 变化项 | 该变成什么 data（插片） | 现状（厚主干）|
|---|---|---|
| 路由关键词 / 意图 → task_type | YAML/JSON | `intelligent_router.py:48-80` 硬编码 |
| 行为路由阈值 (0.7/0.3/…) | YAML | `behavior_aware_router.py:42-57` 硬编码 |
| 执行声明正则 (15 条) | YAML（不再维护 fallback 副本，改单一灵极优来源）| `l10_a_post_audit.py:131-162` |
| provider（openai/anthropic/…）| registry 的 type | `factory.py:38-52` if/elif |
| 3 个 router 的装配 | registry 的 type | `wiring.py:128-146` 写死 |

### 阶段 1：砍厚主干（不新增任何规则）——P0

第一刀，**不产生新文件，只做减法**：

1. **`engine/coding.py` 10 mixin → 1 record + N type**。10 个工具 mixin 本质是 10 个「type」，不是主干的一部分。主干只留「接工具调用 → 执行 → 返回」的流转，工具下沉为 handler 插片（`tool_registration.py` 已有 handler_name 雏形，推到到位）。
2. **3 router → 1 条意图流转**。`IntelligentRouter`（意图分类）+ `TaskRouter`（task→model 路由）+ `tool_router`（工具选择）本质是三个 `transition`，不是三个层。task_router 已读 config.json，把 intelligent_router 关键词也迁到 data，三处归一到一条 `route(type, data) → 目标`。
3. **消灭多状态机**。`dementia_detector`/`degradation_detector`/`model_call` RoundState/`governance/cognitive_state`/`query_engine` 各维护的状态副本，收敛为「一个 record 一个 state」，其他观察者读不复制。

**验收**：`core/` 文件数下降、`.bak` 归零（不再靠改结构试错）、`coding.py` 回到 ~200 行量级。此阶段**不做任何热重载**，只砍。

### 阶段 2：露出接缝后，再补「变化= data」——P1

1. **`core/policy_loader.py`**：把阶段 0 清单里的阈值/正则/关键词迁成 YAML，加文件 watch（复用 `model_call.py` 已有轮询雏形，抽成统一 PolicyLoader）。
2. **策略改 data 不改结构**：改阈值/正则/关键词不再动 `.py`，只改 YAML。
3. **provider/tool/transport 走 type registry**：`factory.py` 的 if/elif → registry 按 name 查 type；`wiring.py` 的启动写死 → registry 注册。

**验收**：改一个阈值，进程不重启、下个 turn 生效——「真热重载」第一块里程碑。

### 阶段 3：热重载/热拔插（找到「什么不变」之后）——P2/P3

| 用户操作 | 改造后行为 | 前提 |
|---|---|---|
| 改策略 YAML | ✅ 不重启，下 turn 生效 | 阶段 2 PolicyLoader |
| 加/卸 tool 插件 | ✅ register/unregister type，不动主干 | 阶段 1 coding.py 砍薄 + 阶段 2 registry |
| 切 provider | ✅ registry 换 type，不重启 | 阶段 2 factory→registry |
| 改 LACP transport | ✅ register 新 transport | 阶段 2 |

**核心纪律**：到这里才引入 `importlib` 动态加载，且**只用于加载「变化的插片」**，绝不用于 reload 主干。主干一旦只有 records+events，它几乎不会变，也就不需要热重载——这才是灵元「砍到最薄后问题本身消失」的最终形态。

---

## 五、一句话总结

方向已经对（state_store/tool_registration/task_router 已在迁），但「策略硬编码在结构里」这条还没砍——10 个 mixin、3 个 router、13 组关键词、8 个阈值、15 条正则、if/elif provider，全焊在主干上，这才是「无法热重载」的根因。

热重载/热拔插**能做到**，但正确顺序是：**先砍薄主干 → 露出接缝 → 变化变 data → 最后才谈 importlib**。反过来先上 SeamRegistry，只会给厚主干再套一层，违反灵元「改问题的方法是砍到最薄，不是加规则」。

---

*灵克(lingclaude)，2026-09-13，灵元 1.0 尺子照 lingclaude 体检 v2*

# LINGMATE_HOTRELOAD_PATH_v1 — 灵元(lingmate)哲学 + lingclaude 插片热重载改造

> 2026-09-12 监督者产出 · 用户要求："按灵元尺子照 lingclaude 改造 + 能否做到热重载热拔插"
> 状态：**哲学分析 + 改造路径**——给 lingclaude 团队参考

---

## 一、灵元 = lingmate（真凭据）

`/home/ai/lingmate/` 仓含 11 个 .md 哲学文档（2488 行总计），包括：
- `第一性原理.md` (162 行)
- `灵元是什么不是什么.md` (417 行)
- `实例-灵元尺子照编码实践.md` (374 行)
- `灵元V1.0.md` (233 行)

**哲学原句**（`实例-灵元尺子照编码实践.md:21-31`）：
> 1. **什么不变** — 主干骨架（record + state）
> 2. **砍到最薄** — 只留主干
> 3. **变化变成插片** — type+data 消化一切

**核心结论**（line 358）：
> **每个厚主干问题的解法都不是"加更多规则"，而是"砍到最薄"。砍到最薄后，问题本身消失。**

**灵元尺子 5 条**（汇总自 6 实例）：
1. **一个 record 只有一个 state**（line 335）——多个观察者读 state，不复制 state
2. **不同维度用不同的 type**（line 343）——type+data 消化一切变化，但不要在一个 type 里塞两个维度
3. **type + data 消化一切变化**（line 27）——加新维度不加新结构
4. **策略是 events 的 data 或 type_registry 的配置，不是结构的一部分**（line 351）——**改策略不改结构**
5. **砍到最薄后，问题本身消失**（line 355）

---

## 二、6 个实例（lingmate 实测）

| 实例 | 厚主干症状 | 砍到最薄 | 共同根因 |
|---|---|---|---|
| **灵忆**（line 318）| 7 表+31 缺口 | 2 表 3 操作 | 在主干上堆分支 |
| **灵通 debug**（line 105）| 3 层状态机+桥接补丁 | 1 个 record | 把插片焊成主干 |
| **灵码 / LongTask**（line 198）| 830 行框架 | task 是主体，session 是插片 | 在 session 层解 task 层问题 |
| **灵族安全**（line 234）| command/data/message/interface/model 5 维度 | security_gate 一个 record | 同上 |
| **议事厅 4 phase**（line 181）| 4 项目 | 1 record 4 state | 用项目管理方式管理状态 |
| **长时工作**（line 320）| 11 session state | 2 表 3 操作 | task/session/灵忆三层混淆 |

**6 个实例 → 同一种错误 → 同一种解法**。

---

## 三、灵元尺子 vs lingclaude 现状（真凭据）

### 3.1 lingclaude 当前违反（按灵元尺子）

| 灵元尺子要求 | 真凭据违反 | file:line |
|---|---|---|
| **什么不变=主干** | core/ import engine/ 30+ 处 | `core/tool_executor.py:179`, `core/tool_call_executor.py:78`, `core/behavior_aware_router.py:20`, `core/wiring.py:140-148` 等 |
| **变化=插片（type+data）** | 3 router 启动写死 + 阈值硬编码 + 15 正则硬编码 | `core/wiring.py:135-148`, `behavior_aware_router.py:43-44`, `l10_a_post_audit.py:131-162` |
| **砍到最薄** | `engine/coding.py` 905 行 10 mixin | `engine/coding.py` |
| **1 record 1 state** | `coding.py` MixinState + `model_call.py` RoundState + `session_persist.py` SessionState + `l10_a_post_audit.py` AuditState | 4 处独立状态机 |
| **策略= data** | `behavior_aware_router.py:43-44` thresholds 硬编码 + `l10_a_post_audit.py:131-162` 15 正则硬编码 | 2 处 |
| **改策略不改结构** | 当前改阈值/模式 = 改结构 = 重启 | — |

### 3.2 lingclaude 厚主干症状（套 lingmate 6 实例格式）

| # | lingclaude 实例 | 厚主干症状 | 砍到最薄方案 | 共同根因 |
|---|---|---|---|---|
| 1 | **3 router 合一** | `core/wiring.py:135-148` 3 router + `behavior_aware_router.py:20` 智能 router + `tool_router` | 1 record + 3 type（intent/tool/fallback）| 多层状态机 |
| 2 | **coding.py 10 mixin** | `engine/coding.py` 905 行 | 1 record + 3 type（intake/execute/verify）| mixin 焊成主干 |
| 3 | **app.py 11 子命令** | `cli/app.py` 1910 行 | 1 session record + 11 type（subcommand as type）| session 层解 task 层 |
| 4 | **LACP marketplace** | `lacp/marketplace.py:85` 启动 import | 1 transport record + N type | transport 焊成主干 |
| 5 | **策略硬编码** | `behavior_aware_router.py:43-44` thresholds + `l10_a_post_audit.py:131-162` 15 正则 | PolicyRegistry + YAML data | 策略焊成结构 |
| 6 | **多状态机** | MixinState + RoundState + SessionState + AuditState | 1 session record 唯一 | 多处复制 state |

**6 个 lingclaude 实例 → 同一种厚主干症状（"在主干上堆分支/层"）→ 同一种解法（"砍到最薄"）**。

---

## 四、热重载/热拔插当前现状（真凭据）

| 能力 | 当前能否 | 真凭据 |
|---|---|---|
| 改 config.yaml 的 max_turns | ✅ 能 | `core/model_call.py:56-87 _maybe_hot_reload_config` |
| 改 provider 配置 | 🔴 不能 | `core/wiring.py:140-148` 启动写死 |
| 加新 tool | 🔴 不能 | `core/wiring.py:144-148` 启动写死 |
| 改 LACP transport | 🔴 不能 | `lacp/marketplace.py:85` 启动 import |
| 改 sandbox 策略 | 🔴 不能 | `engine/sandbox_provider.py:31-54` 启动探测 |
| 改 governance 阈值 | 🔴 不能 | `behavior_aware_router.py:43-44` 硬编码 |
| 改 L10-A 15 正则 | 🔴 不能 | `l10_a_post_audit.py:131-162` 硬编码 |
| 改 hook 实现 | 🟡 半能 | `core/hooks.py:82 unregister(name)` |
| 改 LACP 子仓依赖 | 🔴 不能 | `webui_seam.py:42` 启动 import |

**结论**：当前只有 1 类（config.yaml max_turns）支持热重载，**其余 7 类做不到**。

---

## 五、改造路径（按灵元尺子 + 实测可行性）

### 第一步：主干砍薄（按 lingmate 6 实例格式）

| P | 改造 | 当前违反（真凭据） |
|---|---|---|
| P0 | 3 router 合一为 1 record + 3 type | `core/wiring.py:135-148` + `behavior_aware_router.py:20` |
| P0 | coding.py 10 mixin → 1 record + 3 type | `engine/coding.py` 905 行 |
| P0 | app.py 11 子命令 → 1 record + 11 type | `cli/app.py` 1910 行 |
| P1 | LACP marketplace → 1 transport record + N type | `lacp/marketplace.py:85` 启动 import |

### 第二步：加接缝（SeamRegistry）

| P | 改造 |
|---|---|
| P1 | `core/seam.py` —— SeamType 枚举 + Protocol + Registry |
| P1 | `core/policy_loader.py` —— YAML data 热重载 |
| P1 | `core/plugin_loader.py` —— `importlib.util.spec_from_file_location` 动态加载 |
| P1 | `core/plugin_manifest.py` —— PluginManifest 数据类 + JSON schema |

### 第三步：策略 = data（policy 改 YAML）

| P | 改造 | 当前硬编码（真凭据）|
|---|---|---|
| P2 | `behavior_aware_router.py` thresholds → YAML | line 43-44 |
| P2 | `l10_a_post_audit.py` 15 正则 → YAML | line 131-162 |
| P2 | `engine/sandbox_provider.py` 配置 → YAML | line 31-54 |
| P2 | `model/intelligent_router.py` 关键词 → YAML | 整个文件 |
| P2 | `model/task_router.py` task_routes → YAML | lingcode/config.json |

### 第四步：插片 = type（registry 注册）

| P | 改造 |
|---|---|
| P3 | `core/seam.py`：`register("llm", "openai_compatible", instance)` |
| P3 | `core/plugin_loader.py`：`load_plugin(manifest)` 动态 import |
| P3 | `core/seam.py`：`unregister("tool", "name")` 热拔插 |

---

## 六、按灵元尺子改造后能做到的

按用户原要求"插件的改动变化，加载卸载，不用动主干"：

| 用户操作 | 改造后行为 | 真凭据前提 |
|---|---|---|
| 改 `~/.lingclaude/policies/hallucination.yaml` 阈值 | ✅ 不重启——下 turn 应用 | PolicyLoader.watch |
| 改 `~/.lingclaude/policies/claim_patterns.yaml` 正则 | ✅ 不重启——下次 L10-A 生效 | PolicyLoader.watch |
| 加新 provider 配置 | ✅ 不重启——热重载 | SeamRegistry.register("llm", new_instance) |
| 切换 sandbox 策略 | ✅ 不重启——reload sandbox_type | sandbox = type in registry |
| 加载新 tool 插件 | ✅ 不加载 LLM 进程——register | plugin_loader.py + SeamRegistry |
| 卸载 tool 插件 | ✅ 不卸载 LLM 进程——unregister | SeamRegistry.unregister |
| 改 LACP transport | ✅ 不重启——register 新 transport | importlib + SeamRegistry |
| 改 governance 算法 | ✅ 不重启——reload governance_type | governance = type in registry |

**核心**：**按灵元尺子"改策略不改结构"——真热重载是 reload data，不是 reload module**。

---

## 七、关键约束（按灵元哲学）

> **核心警告**（`实例-灵元尺子照编码实践.md:358`）：
> > **每个厚主干问题的解法都不是"加更多规则"，而是"砍到最薄"。**

**这意味着**：
- ❌ 不能**先**加 SeamRegistry 再砍主干——**这是新的厚主干症状**
- ✅ 必须**先砍主干，再加接缝**

按 lingmate 6 实例的共同根因："**在主干上堆分支/层**"——加 SeamRegistry 在厚主干上 = **新的厚主干**。

**改造顺序不能错**：
1. 先砍主干（3 router → 1 record + 3 type）
2. 后加 SeamRegistry（基于砍薄后的接缝）
3. 再加 PolicyLoader（基于 SeamRegistry）
4. 最后改策略为 data

---

## 八、给 lingclaude 团队的落地清单

### 8.1 立即可做（按灵元尺子，无须新设计）

| P | 项 | 工作量 | 改造文件 |
|---|---|---|---|
| **P0** | 3 router 合一 | 中（重构 + 测试）| `core/wiring.py` + `behavior_aware_router.py` |
| **P0** | coding.py 10 mixin 砍薄 | 大（905 → ~200 行）| `engine/coding.py` |
| **P0** | app.py 11 子命令 改 type 化 | 大（1910 → ~500 行）| `cli/app.py` |
| P1 | 加 `core/seam.py` SeamRegistry | 中（200 行新）| 新建 |
| P1 | 加 `core/policy_loader.py` | 中（150 行新）| 新建 |
| P2 | strategy 阈值 → YAML | 小（替换硬编码）| `behavior_aware_router.py` |
| P2 | 15 正则 → YAML | 小（替换硬编码）| `l10_a_post_audit.py` |
| P3 | `core/plugin_loader.py` | 中（200 行新）| 新建 |
| P3 | `core/plugin_manifest.py` | 中（150 行新）| 新建 |

### 8.2 关键改造原则（按灵元尺子）

1. **不增加规则**——加 SeamRegistry 是增加规则，必须**先砍主干**再加
2. **改 data 不改结构**——策略改 YAML data，不是改 Python 模块
3. **1 record 1 state**——消灭多状态机（MixinState + RoundState + SessionState + AuditState → 1 record）
4. **type+data 消化一切**——provider/tool/sandbox 都是 type，配置都是 data
5. **改策略不改结构**——改 YAML 文件 → PolicyLoader 自动重载

---

## 九、监督纪律执行

- ✅ 真读 `/home/ai/lingmate/` 仓 11 个 .md 哲学文档（2488 行）
- ✅ 真读 `LINGYUAN_V3_REFACTOR_PLAN.md`（177 行）哲学部分
- ✅ grep lingclaude 全仓**真凭据**违反点（30+ 处主干 import 插片）
- ✅ 真读 `core/model_call.py:56-87`（已有热重载雏形）
- ✅ 每个改造项给 file:line 真凭据
- ✅ 引用 lingmate 哲学原句（line 27/59/335/343/351/355/358）
- ✅ 不编故事——只列证据 + 标"按灵元尺子"推断

---

## 十、版本历史

| 版本 | 作者 | 时间 |
|---|---|---|
| v1 | claudecode（监督者）| 2026-09-12 |
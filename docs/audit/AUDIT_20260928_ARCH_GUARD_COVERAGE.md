# 🏛️ 架构审计报告 — 守卫覆盖面与缝层语义（2026-09-28）

> **审计口径**：独立 AST 复扫 + **守卫谓词注入探针**（把守卫自身判定逻辑 `inspect.getsource` 取出，喂合成绕过样本，检验「守卫能不能抓住」而非「现状有没有违规」）。
> **只读**：未修改任何被审计文件。探针脚本置于 `/tmp/opencode/`，不入库。
> **范围**：主包 `lingclaude/{core,engine,plugins,model,cli,self_optimizer,seams,governance,coordination,api.py}` · 守卫族 `tests/test_p04_arch_guards.py` `tests/test_iron_law_guards.py` `tests/test_p5_slot_guards.py` · 台账 `data/arch_ledger/`。
> **证据标注**：本文全部结论 = ✅已验证(本轮)，原始工具输出见 §6 验证日志 V1–V13。评分与定性判断为 ⚠未验证（人工裁定，非机检）。
> **姊妹报告**：`docs/AUDIT_20260928_ARCH_G10_G11.md`（同日同族审计，8/10，Blocker「无发现」）—— 分歧已按 J5 条件 2 登记，见 §0。

---

## 0. 口径分歧登记（J5 条件 2：多口径互证，分歧即警）

同日两份架构审计在 Blocker 判定与评分上**显著分歧**。按 J5 条件 2，分歧不是噪声而是「测量里有假」的系统信号，故先归因再列发现。

| 维度 | `AUDIT_20260928_ARCH_G10_G11.md` | 本文 | 分歧根因 |
|---|---|---|---|
| Blocker | **无发现** | **2 项**（B1/B2） | 姊妹报告**逐点枚举现有 5 处动态加载并逐个判定**（`grep` 口径，问「现状合规吗」）；本文**注入合成绕过样本问守卫谓词**（「守卫能抓住吗」）。后者是行为口径，前者是静态快照口径。 |
| 评分 | 8/10「骨架硬朗，末梢有刺」 | **6/10** | 姊妹未检查：M1 豁免覆盖率、M3 目标域黑名单边界、J4_STATE_MODULES 白名单边界。 |
| seam_backend 兜底 | H-1「合规但有守卫绕过风险」 | **B2 豁免事实前提双重证伪** | 姊妹采信了豁免台账的 `reason` 字段；本文对台账 reason 逐条实测。 |
| core→plugins 静态 | 零违例 ✅ | 零违例 ✅ | **一致**（两口径互证通过） |
| core→engine 18 点在基线内 | ✅ 合规但技术债 | ✅ 一致，并**追加发现 24 点在守卫外的出向依赖** | 姊妹只核了 `engine` 一个目标域；M3 前缀表只有 2 个域。 |

**结论**：姊妹报告的静态口径在「现状合规性」上可信；分歧全部落在**「守卫的覆盖面」**这一维度上，该维度需以本文的注入探针口径为准（条件 3：口径之争最终裁决归行为测试/注入，不归另一层静态审查）。

---

## 1. 守卫体系实态：覆盖矩阵存在正交缺口

| 守卫 | 扫描目录 | 捕获机制 | 证据 |
|---|---|---|---|
| G11 | core + engine | 仅 `ast.Import` / `ImportFrom` **静态** | `tests/test_p04_arch_guards.py:611-619` |
| G11b | core + engine | **仅** `Call` 且 `func` 名 == `spec_from_file_location`，**且只校验第 1 参数（模块名）** | `tests/test_p04_arch_guards.py:671-700` |
| M3 | **仅 `CORE`** — `for f in _py_files(CORE)` | `__import__` / `import_module`，**且首参必须字符串字面量** | `tests/test_iron_law_guards.py:419, 437-446` |
| G1 / M3-engine 侧 | **仅 `SRC/"core"`** + **仅静态** + **仅 `lingclaude.engine`** | `collect_core_engine_imports()` | `tests/test_p04_arch_guards.py`（`for f in _py_files(SRC / "core")`） |
| G10c / G10(J4) | **仅 `J4_STATE_MODULES` 14 文件手工白名单** | `sqlite3` / 写介质正则 | `tests/test_p04_arch_guards.py:336-346, 387, 445-448` |
| M1 | core，**但 89% 整文件豁免** | 领域词汇表 AST | `tests/test_iron_law_guards.py:261-319` + 台账 |

**关键性质**：`engine/ → plugins/` 的**动态**依赖，三条守卫的覆盖交集为 **∅**。这是矩阵的结构性缺口，不是配置疏漏。

---

## 2. 发现清单

### 🚨 Blocker

#### B1 · 守卫覆盖矩阵正交缺口：5/5 合成绕过样本实测 0 捕获

把三条守卫的判定谓词原样取出，喂入合成绕过样本（V2）：

| 注入样本 | G11 | G11b | M3 |
|---|---|---|---|
| `engine/x.py`: `importlib.import_module("lingclaude.plugins.evil")` | miss | miss | miss |
| `engine/x.py`: `__import__("lingclaude.plugins.evil", fromlist=["x"])` | miss | miss | miss |
| `engine/x.py`: `spec_from_file_location("evil_name", "/…/plugins/agents/registry_loader.py")` | miss | miss | miss |
| `core/x.py`: `importlib.import_module(var)`（非字面量） | miss | miss | miss |
| `engine/x.py`: `import_module(cfg["entry"])` | miss | miss | miss |

**病灶**：`test_p04_arch_guards.py:626-633` 的 G11b docstring 明确宣称收口「`spec_from_file_location(name, path)` 以模块名+文件路径配对加载，完全绕过两条守卫」，但实现（`:687-690`）**只比对第 1 参数字符串是否以 `lingclaude.plugins` 开头**，从不校验第 2 参数（路径），也只认这一个函数名。把第 1 参数改名 + 换 `import_module`/`__import__`，主干即可加载任意插件实现而守卫全绿。

**修复建议**：三守卫合并为单一覆盖矩阵守卫 ——
- 扫描面：`core` + `engine`（含全部子目录，`rglob`）
- 机制集：`Import` / `ImportFrom` / `__import__` / `import_module` / `spec_from_file_location` / `module_from_spec` / `load_module` / `runpy.run_path` / 含 `plugins` 的 `Path(...)` 与字符串字面量
- 判定条件：从「模块名前缀匹配」改为「**解析后目标是否落在 `plugins/` 目录内**」（`Path.resolve()` 判包含关系）
- 非字面量模块名 / 非字面量路径 → 一律黄名单，强制 `arch_exemption/G11b` 登记（与现有豁免通道同源）

#### B2 · 缝层唯一间接入口的合规豁免，两条事实前提经实测双重证伪

豁免台账 `data/arch_ledger/arch_exemption/G11b:engine__subagent__seam_backend.py.json`（review_due 2026-12-31）声明两条理由，逐条实测（V3a，**已修正一次探针顺序污染**——先做包 import 会填充 `sys.modules` 造成假阳性）：

**前提 1「`plugins/` 无 `__init__.py`（结构性原因），包路径 import 必 `ImportError`」→ 证伪**
```
plugins/__init__.py exists: False
package import works: /home/ai/lingclaude/lingclaude/plugins/agents/registry_loader.py
```
Python 3.12.3（PEP 420 命名空间包，3.3+ 起）下包路径 import 完全可用。`lingclaude/engine/subagent/seam_backend.py:154-170` 的整段兜底**非必要**。

**前提 2「`sys.modules` key 与 registry_loader 内部 `import_module` 键一致（防双副本）」→ 证伪，且反被违反**
```
sys.modules BEFORE any import: False
  after call 1: sys.modules has key? False
  after call 2: sys.modules has key? False
  after call 3: sys.modules has key? False
distinct module objects across 3 fallbacks: 3
package import works: ... -> pkg is last spec-copy? False -> double copy: True
```
`seam_backend.py:169-170` 用 `module_from_spec` + `exec_module`，**从不写入 `sys.modules`**。三重后果：
1. `seam_backend.py:156` 的 `sys.modules.get(...)` 快路径**在生产中恒为死代码**（无任何代码做包 import，key 永不填充）；
2. `engine/hot_reload_trigger.py:120-121` 以 30s 节流（`:45 _SCAN_INTERVAL`）调用 → **每 30 秒重新 exec 一遍 `registry_loader` 模块体**；
3. 守卫所依赖的「双副本防护」实际不存在；一旦有第三方做包 import，同一文件即存在两份 module 对象。

**现网影响（如实声明，不夸大）**：`registry_loader` 模块级状态仅 `AGENTS_ROOT` / `EXEMPT_DIRS` / `RECORD_TYPE` 三个常量（`plugins/agents/registry_loader.py:26,29,35`），且 `load_all` 走 `SeamRegistry.register` 同名覆盖 → **当前无行为故障**。但这是**潜伏缺陷**：任一插件登记模块级可变状态（计数器 / 缓存 / 连接池）即产生跨代不一致。

**修复建议**：
1. 删除 `seam_backend.py:154-170` 整段兜底，改 `from lingclaude.plugins.agents import registry_loader`（命名空间包路径可用）；
2. 因该 import 字面量含 `lingclaude.plugins` 会触发 G11，改为**一行静态 import 的窄行级豁免**，或把 `registry_loader` 归位 `core/agent_assembly.py`、令 plugins 侧反向依赖（更符合铁律 1「主干持状态机原语，插片方向单一」）；
3. 撤销现有 G11b 豁免，`reason` 改写为「兜底机制已证伪并移除」——按 J5 条件 4「误差事件入账」，失准口径须定价留存而非继续作为权威依据。

---

### ⚠️ High

#### H1 · M1 概念清白度 89% 整文件豁免 —— 铁律 1「概念封闭」实质失守且未声明失败模式

```
core/*.py = 101   M1 active 豁免 = 97   整文件豁免 = 90  (89%)
实际受检 13 文件：approval_matrix / bounded_compaction / context_engine /
evidence_protocol / invariants / mailbox_notify / plugin_lifecycle /
repair_card / rollout / session_token_sink / success_gate / verify_ledger / worktree
```
`docs/LINGYUAN_IRON_LAW.md:31` 把「概念封闭：主干代码不出现任何插片/业务领域概念词汇」列为薄主干三重定义之首。实测该判据在 89% 的主干上从未被检验。

根因在实现：`_m1_scan()` 支持行级 `lines`，但 `_active_exemptions()` 对 `lines is None` 返回整文件，`_m1_scan` 的 `:267-268` 见 key 即 `continue` → 整文件跳过。

**J5 条件 1 违反**：守卫 docstring（`tests/test_iron_law_guards.py:236`）只写「豁免走台账，只缩不放」，**未声明「本守卫 89% 覆盖面」这一失败模式**——条件 1 明文「不声明失败模式的测量才是危险的测量」。

**修复建议**：①豁免从整文件降级为行级（`_m1_scan` 已具备能力，只差台账迁移）；②把「M1 整文件豁免占比」作为趋势指标入 M6 仪表，使 89% 这个数字本身可见（防「没人看」不发生）。

#### H2 · M3 依赖封闭只禁 2 个目标域；core/ 实测 24 个出向依赖点在守卫外，且 3 组构成真实双向环

```
M3_FORBIDDEN_PREFIXES = ("lingclaude.engine", "lingclaude.plugins", "engine", "plugins")
   —— tests/test_iron_law_guards.py:391
```
独立复扫 core/ 全部出向 import（V5）：

| 目标域 | 点数 | 守卫覆盖 | 位置 |
|---|---|---|---|
| `engine` | 18 | G1 计数基线（7 文件） | — |
| **`model`** | **10** | ❌ 无 | `core/evidence_protocol.py:299`, `query_engine.py:231`, `query_engine_lifecycle_mixin.py:18`, `query_engine_model_mixin.py:31,113`, `session_token_sink.py:30`, `success_gate.py:133`, `system_prompt_builder.py:410`, `wiring.py:219,225` |
| **`lacp`** | **6** | ❌ 无 | `core/fact_checker.py:29`, `l10_a_post_audit.py:39`, `l7_cognitive.py:205`, `mcp_tools.py:158`, `query_engine.py:53`, `wakeup_channel.py:45` |
| **`self_optimizer`** | **6** | ❌ 无 | `core/light_channel.py:143`, `session_runtime.py:151`, `system_prompt_builder.py:326`, `turn_learner.py:35,36,92` |
| **`coordination`** | **2** | ❌ 无 | `core/__init__.py:72`, `core/gray_zone.py:18` |

**环判定（V6，逐对核实，不合并统计）**：
```
model          core->model= 9   model->core=18   CYCLE=True
self_optimizer core->self_optimizer= 6  ->core=14  CYCLE=True
coordination   core->coordination= 2  ->core=5    CYCLE=True
lacp           core->lacp= 6    lacp->core=0    CYCLE=False   ← 单向，非环
```
即 `core ↔ model`、`core ↔ self_optimizer`、`core ↔ coordination` 为**真实双向耦合**（例：`core/query_engine_lifecycle_mixin.py:18 → model.token_pricing` ⟷ `model/behavior_aware_router.py → core.behavior`）；`core → lacp` 为单向。

条文对照：`docs/LINGYUAN_IRON_LAW.md:32` 定义依赖封闭为「core/ 的**传递 import 闭包 ⊆ {stdlib, 状态机原语依赖}**」——按此定义实测不成立。M3 是**目标域黑名单**，条文要求的是**闭包白名单**：新增第 5 个域自动合规。

**修复建议**：M3 改为白名单口径 —— `core/` 允许出向前缀 = `core` + `seam` + `slot` + `state_store` + `types`（+ stdlib），其余一律红；现存 24 点按 `arch_debt` 定价（复用 J4 债务机制，due 对齐 2026-11-30 批次），先立守卫钉死增量再逐步清偿。

#### H3 · J4 存储直连守卫覆盖面 = 14 文件手工白名单，新增状态模块自动合规

```
J4_STATE_MODULES = [...]   # tests/test_p04_arch_guards.py:336-346（固定 14 条）
G10c baseline = {"core/layered_memory.py"}   # :457-459
```
独立复扫仓内全部 `sqlite3.connect`（V7）：
```
lingclaude/core/safe_db.py:62                      ← 不在 J4_STATE_MODULES
lingclaude/engine/todo.py:97                       ← 不在册，且 todo_store 是 G10 真插片槽
lingclaude/self_optimizer/experiments.py:111       ← 不在册
lingclaude/self_optimizer/replay_objective.py:77   ← ro 只读
```
`_find_media_direct_access()`（`:387`）与 G10c（`:445-448`）**均只遍历 `J4_STATE_MODULES`**。含义：任何**新建**状态模块默认合规——守卫是「已知名单」而非「全域扫描」，连铁律要求的「红即触发人工审查」都不会触发。

**修复建议**：翻转为「默认红」——扫 `core/ engine/ model/ plugins/ self_optimizer/ governance/` 全域，命中 `sqlite3.connect` / `CREATE TABLE` / `write_text` / `open(...,'w')` 即候选，再用 `J4_STATE_MODULES` 做**降级白名单**（登记才放行），语义与 G11b 豁免通道同源。

#### H4 · 跨域私有符号穿透实测 37 处；已入账 debt 只覆盖 7 处，且 G1 计数基线对「换了哪个符号」完全失明

已有 `data/arch_ledger/arch_debt/cli-selfopt-core-private-penetration.json`（due 2026-11-30）覆盖 7 处。独立复扫（V9）得**总计 37 处**：

| 域 | 处数 | 未入账部分 |
|---|---|---|
| `api.py` | **18** | `api.py:32` 一行 `from lingclaude.seams.external_query import (…)` 批量 re-export **18 个 `_` 私有符号**（`_call_llm` / `_route_question` / `_query_github_stars` / `_resolve_llm_chain` …）—— **debt 记录完全未提及** |
| `core` | 5 | `light_channel.py:103`(`_ToolLoopDetector`)、`model_call.py:371`(`_slim_tool_output`)、`query_engine_turn_mixin.py:166,348`(`_estimate_tokens`/`_estimate_message_tokens`) |
| `cli` | 6 | 已入账 ✅ |
| `plugins` | 2 | `tools/git/remote_probe.py:31`(`engine.git._is_blackhole_remote_url`, `_run_git`) |
| `engine` | 1 | `loop/loop_body.py:170`(`core.model_call._maybe_hot_reload_config`) |
| `seams` | 1 | `external_query.py:112`(`core.config._resolve_api_key`) |
| `model`/`governance`/`coordination`/`self_optimizer` | 各 1 | 均为 `core.state_store._atomic_write_json` |

**两个附加发现**：
1. **debt 台账自身不完整** —— 账目与实测冲突，违反 H17「账目与检查器冲突时以检查器为准」的前置条件（账目须先完整才谈得上以检查器为准）。
2. **G1 计数基线对「换了哪个符号」完全失明** —— `collect_core_engine_imports()` 只数 `lingclaude.engine` 前缀的条数，不记录符号名。把 `core/model_call.py:371` 的 `_slim_tool_output` 换成 engine 内**任意另一个私有符号**，计数仍为 6、守卫仍绿。计数制换代（治行号漂移）是对的，但丢失了「导入了什么」这一维度。

**修复建议**：①债务台账补登 api.py:32 的 18 个 + 其余 11 处未入账点；②新增 `P_CLI_CORE_PRIVATE` 守卫（debt 的 `resolve_hint` 已有此建议，本报告附实测证据支持其「路②+逐步①」方案）；③G1 计数基线增列「符号名集合」为只紧不松的第二维（存量冻结，新增私有符号即红）。

---

### 🟡 Medium

#### M1 · 主干 6 处硬编码 `plugins/` 目录布局 —— 插件知识在主干重复，缝入口不「单一」

```
lingclaude/engine/hot_reload_trigger.py:47-48   TOOLS_PLUGINS_DIR / AGENTS_PLUGINS_DIR  ← 主干自建 Path
lingclaude/engine/tools.py:32                   _PLUGINS_DIR = "lingclaude/plugins/tools"
lingclaude/engine/coding_wiring.py:285,309      "lingclaude/plugins/tools" / "tool_plugins"
lingclaude/core/wiring.py:506                   Path(...)/"plugins"
lingclaude/engine/subagent/seam_backend.py:162  Path(...)/"plugins"/"agents"/"registry_loader.py"
```
`be303c9` 的 G11 收口只把 **import 语句**搬进缝层，**目录布局知识仍散落 6 处**。`hot_reload_trigger.py:47-48` 最突出：主干直接构造指向 `plugins/agents` 的 Path 并自行 `_snapshot_dirs` 扫描（`:51-59`）——这是**第二条主干自行枚举插件目录的通路**，与 `seam_backend.reload_agent_plugins` 并列，故「缝层单一入口」在**扫描面**上不成立。G11/G11b/M3 均不覆盖字符串/Path 形式的插件目录引用。

**修复建议**：`PluginLoader`/`SeamRegistry` 暴露 `seam.roots()` 与 `seam.scan(marker)`，主干零处出现 `"plugins"` 字面量；可复用 M1 文件驱动词汇表机制把 `plugins` 列为禁词（`tests/fixtures/core_vocabulary.yaml` 已是该形态）。

#### M2 · `N7_PUBLIC_SEAMS` 定义后从未被扫描逻辑引用 —— 守卫条文与实现背离

```
tests/test_iron_law_guards.py:67   N7_PUBLIC_SEAMS = {"mcp_common"}   ← 定义
tests/test_iron_law_guards.py:74   （仅 docstring 提及）
grep -rn "N7_PUBLIC_SEAMS" tests/ scripts/ lingclaude/   → 仅上述 2 处
```
`_n7_scan()`（`:86-126`）判定用的是 `plugin_dirs` 目录扫描（`:81`），`N7_PUBLIC_SEAMS` **零引用**。docstring `:74` 明写「`N7_PUBLIC_SEAMS` 是语义白名单，目录扫描是事实边界」——声明的语义与实现机制不是同一件事，违反 J5 条件 1。

**当前 N7 绿灯是真的**（active 豁免 0 条；独立复扫 `plugins/` 跨插片内部 import = 0），但**绿的因果与台账声称的因果不同** → 后续审计会基于错误因果决策。

**修复建议**：二择一 —— ①在 `_n7_scan` 中真正消费该白名单（白名单模块 import 放行、非白名单的 `plugins/<域>/*.py` 共享模块报错，即「共享面收敛为接缝」，即 N7 条文原意）；②删除该常量并把 docstring 改写为「口径 = 目录事实边界，**不校验共享面是否显式接缝**」。

#### M3 · `core/state_store._atomic_write_json` —— 核心原语的私有面成了跨 6 域事实公共 API

35 处引用 / 17 文件 / 6 域：
```
core ×11 : role_separation.py:462, governance_verifier.py:294, reasoning_chain.py:122,
           meta_cognition.py:268, approval_matrix.py:262, wakeup_channel.py:79,
           session_runtime.py:113, session.py:112,190, governance.py:444,
           session_token_sink.py, scheduler.py
跨域 ×6  : self_optimizer/daemon.py:1125, model/intelligent_router.py:301,
           engine/state_query.py, coordination/bus_responder.py:108,
           governance/proposal_lifecycle.py:654
```
StateStore 的公开协议是三原语（records/events/transition），`_atomic_write_json` 是内部原子写实现。①StateStore 内部重构会同时打断 6 个域；②这些写入**不经 StateStore 的 record 语义**（无 type/key 校验、无 `list_keys` 可见性），与 J4「状态读写走 StateStore 原语」相悖，而 G10c 只盯 sqlite、看不到这条。

**修复建议**：升为 `StateStore.atomic_write_json()` 公开方法（或独立 `core/atomic_io.py`），17 处改 import；同步把「core/ 内部跨模块 import `_` 前缀符号」纳入 M2 口径。

#### M4 · `core/l10_a_post_audit.py:56-72` 模块级 exec 任意路径 + fail-open

```python
58: _LINGAN_MODULE_PATH = os.environ.get("LINGAN_SECURITY_GATE_PATH",     ← env 可控路径
60:     str(repo_path("lingan") / "security_gate.py"))
63:     _spec = _importlib_util.spec_from_file_location("lingan_security_gate", _LINGAN_MODULE_PATH)
65:     _spec.loader.exec_module(_lingan_mod)          ← 模块导入期即执行（顶层，非函数内）
71: except (ImportError, FileNotFoundError, Exception) as e:   ← 吞掉一切 → fail-open
```
三点：①主干在 import 期执行 env 指定路径的代码，绕过全部 import 守卫；②`except Exception` 吞掉一切 → 门禁静默失效（铁律 8「不假活」的反例）；③G11b 放行的**唯一原因**是第 1 参数不以 `lingclaude.plugins` 开头 —— 这正是 B1「按模块名而非按解析路径判定」的直接受害点。

**修复建议**：路径改走既有原语 `lacp/cross_repo_seam.ensure_import_path()`（`lingclaude/lacp/cross_repo_seam.py:92`，正是为收口这类散落而建）；加载失败必须记 `arch_contract_breach` record，而不只是 `logger.info` 后继续（不假活）。

#### M5 · 双 `LifecycleManager` 同名异责

```
lingclaude/core/plugin_lifecycle.py:349          class LifecycleManager  (334 行)  插件热拔插/CircuitBreaker/Fiber
lingclaude/governance/proposal_lifecycle.py:292  class LifecycleManager  (363 行)  提案阶段机/归因/升级
```
同名不同域、零继承、体量相当。仓内已有前置规范化先例（`core/query_engine_*.py` 一律 `*Mixin` 后缀）。静态 grep 不误报，但代码评审与 IDE 补全场景高危。

**修复建议**：`governance` 侧改 `ProposalLifecycleManager`，或双方统一加命名前缀。

---

### 🔵 Low

#### L1 · 架构巨石 Top10（行数口径；按铁律 1「行数不是主干厚薄的度量」仅作背景，不作阻塞结论）

| # | 行数 | 文件 | 职责混杂点名（类/函数 @行号） |
|---|---|---|---|
| 1 | 1699 | `cli/repl.py` | 模块级函数堆：`_run_stream_turn` @985(315行) / `_interactive_loop` @1441(258) / `_maybe_stall_escape` @680(143) / `_toolbar_snapshot` @224(136) —— 流式回并、ESC 监听、死循环逃逸、工具栏四职责同层 |
| 2 | 1645 | `cli/full_tui.py` | `class FullTuiSession` @433(1212行，**占文件 74%**)：`__init__` @440(256行) + stdout 代理 @341-430 + 剪贴板/OSC52 @987-1208 + 输出区滚动 @1445-1617 |
| 3 | 1388 | `cli/app.py` | `main` @1155(224行)：argparse 装配 + webui 生命周期(`launch_webui` @966,137) + daemon 启停 + secret 生成 @745 |
| 4 | 1189 | `self_optimizer/daemon.py` | `class OptimizationDaemon` @136(1053行)：指标采集 @219 + 冻结区裁决 @315-396 + 周期编排 `run_cycle` @398(221) + 参数热应用 @718-976(258) + 知识回写 @1020-1087 |
| 5 | 1069 | `cli/interface.py` | `class FallbackSession` @715(320行；raw-mode @771(203行)/粘贴/占位符/输出折叠) + `PromptToolkitSession` @533 并列双实现 |
| 6 | 841 | `model/task_router.py` | `class TaskRouter` @315(526行) + `_pick_from_route` @501(103行) |
| 7 | 822 | `engine/bash.py` | `class BashExecutor` @167(655行)：子进程 @222-331 + **沙箱命令构造** `_sandbox_command` @333(126) + **安全阻断** `_check_blocked` @644(117) + 凭据扫描 @536-575 + 资源限额 @791 —— **安全策略与执行机制同体，插片边界不清（铁律 2 分形）** |
| 8 | 729 | `engine/coding.py` | `class CodingRuntime` @43(686行) + 11 个 Mixin；`__init__` @48-~200 同时做槽装配/轻通道接 4 件/熔断状态初始化/session 兜底 |
| 9 | 708 | `core/plugin_lifecycle.py` | `CircuitBreaker` @66(225行) + `PluginFiber` @294 + `LifecycleManager` @349(334行)：熔断器与生命周期管理两个可分离域同文件 |
| 10 | 681 | `governance/governance_v2.py` | `class GovernanceEngine` @155(526行)：提案 CRUD + 健康检查 + 系统性问题检测 + 历史持久化 |

> 观察：Top10 中 **0 个是 `core/` 状态机原语文件** —— 与姊妹报告「主干本体无超 1100 行文件」一致。债务面集中在 CLI 呈现层与 `engine/bash.py` 的安全/执行混合。

#### L2 · 生产树内散落 `.bak` 副本
`lingclaude/api.py.bak`(26.5KB) / `lingclaude/__init__.py.bak` / `tests/*.py.bak` 等 ≥12 个。因后缀非 `.py`，全部 AST 守卫的 `rglob("*.py")` 均不扫描 → **守卫绿 ≠ 树干净**；同时是审计噪声源（`git status` 已跟踪其中若干）。姊妹报告 L-2 同项，一致。

---

## 3. 无发现项

| 审计项 | 结论 |
|---|---|
| **G11 静态面** | `core/`+`engine/` 对 `lingclaude.plugins*` 的 `import`/`ImportFrom` = **0**（递归 `rglob` 含子目录）；**相对导入**（`from ..` 逃逸到插件）= **0** |
| **G10 三区棘轮** | `data/arch_ledger/g10_trunk_baseline.yaml` 三区实测全绿。棘轮设计正确 —— **AST 函数锚定而非行号**，已内化 `4787ec7` 删行漂移教训 |
| **M3 声明域内** | `core/` → `engine` 18 点全部在 G1 基线内（7 文件）；`core/` → `plugins` 静态 = 0 |
| **N7 插片横向耦合** | `plugins/` 插片 import 他插片内部 = **0**（active 豁免 0 条）—— 绿灯是真的（但见 M2：因果与台账声称不同） |
| **M1 在册 13 文件** | 未豁免的 13 个 core 文件概念词汇命中 = 0 |
| **债务/豁免到期** | `arch_debt` 无过期未清；`arch_exemption` 无缺账期（3 条 `permanent=true` 均带 `permanent_reason`） |
| **M5 拔插等级** | `PLUG_LEVELS` 覆盖全部 `SeamType`，值域 ⊆ {L1,L2,L3}，三段式逐级拔插绿 |
| **账本 J4 归原语** | `_ledger_records()` 走 `StateStore.list_keys/load` API（`tests/test_iron_law_guards.py:155-166`），非私连 json —— 守卫自身兑现 J4，分形声明（docstring `:15-24`）完整 |

**基线绿灯确认（V1）**：G1 / G10c / G10 三区 / G11 / G11b / M1 / M3 / M5 / N7 / 债务到期 / 豁免复审 = **21 passed**。本报告全部发现均为**「守卫绿但覆盖不足」**，无一为「守卫红被绕过」。

---

## 4. 架构健康度评分：**6 / 10**

⚠未验证（人工裁定）

**总评**：守卫**机制**是全族最扎实的部分 —— 计数棘轮取代行号键（`4787ec7` 假红事故已内化为换代）、豁免只缩不放 + 到期强制复审、债务定价生命周期、M4 升格套件级换域、M5 三段式拔插、G11b/G12/G13/G14/G15 成体系，且 21 项全绿；问题出在**覆盖面**而非严格性 —— 三守卫在「扫描目录 × 捕获机制」上留正交缺口，使 `engine→plugins` 动态依赖守卫面为 0（B1）；M1 89% 整文件豁免 + M3 目标域黑名单 + J4 14 文件白名单，使铁律 1 的「概念封闭/依赖封闭」两条定义在实测口径下均不成立（H1/H2/H3）；唯一被豁免的缝层入口，其台账事实前提经实测双重证伪（B2）。**主干静态面（G11）与 G10 棘轮确实干净** —— 扣分几乎全部记在「守卫不知道自己不知道什么」这一维度上。B1+B2+H1+H2+H3 修完可上 8 分。

---

## 5. 建议处置顺序

| 序 | 项 | 动作 | 依据 |
|---|---|---|---|
| 1 | **B2** | 删 `seam_backend.py:154-170` 兜底，改包 import；撤销并改写 G11b 豁免 | 事实已证伪，继续保留即让错误依据污染后续审计 |
| 2 | **B1** | 建覆盖矩阵守卫（默认红 + 台账降级白名单） | 当前 `engine→plugins` 动态依赖无任何拦截 |
| 3 | **H3** | G10c/G10(J4) 翻转为全域扫描 + 降级白名单 | 改动最小、收益立竿见影（`engine/todo.py:97` 等 4 处立即纳管） |
| 4 | **H2** | M3 改闭包白名单口径；现存 24 点按 `arch_debt` 定价（due 对齐 2026-11-30） | 复用既有债务机制，账期与在册批次一致 |
| 5 | **H4** | 补登 api.py:32 等 11 处未入账点；落地 `P_CLI_CORE_PRIVATE` 守卫（debt `resolve_hint` 已建议，本报告附实测支持） | 账目完整性先于清偿 |
| 6 | **H1 / M1 / M2 / M3 / M4 / M5** | 按各自修复建议执行 | 可并入常规 SDT 节奏 |

> **待用户裁定**：以上发现按铁律流程应入 `arch_audit_task` 台账（`data/arch_ledger/arch_audit_task/`）。本文**未**代为登记 —— 台账写入属治理状态变更，按「建议-执行强制分离」交由用户确认后执行。

---

## 6. 验证日志（原始工具输出）

**V1 · 守卫绿灯基线**（`pytest`）
```
$ python3 -m pytest -q --no-header -p no:cacheprovider \
  tests/test_p04_arch_guards.py::test_g11_no_core_import_plugins \
  tests/test_p04_arch_guards.py::test_g11b_no_spec_from_file_location_bypass \
  tests/test_iron_law_guards.py::test_m3_dependency_direction \
  tests/test_iron_law_guards.py::test_n7_no_cross_plugin_internal_imports \
  tests/test_iron_law_guards.py::test_m1_core_concept_cleanliness \
  tests/test_iron_law_guards.py::test_debts_not_expired \
  tests/test_iron_law_guards.py::test_exemptions_not_past_review \
  tests/test_iron_law_guards.py::test_m5_amputation \
  tests/test_p04_arch_guards.py::test_g1_core_engine_imports_count_baseline \
  tests/test_p04_arch_guards.py::test_g10c_no_new_sqlite_direct_states \
  tests/test_p5_slot_guards.py
.....................                                                    [100%]
21 passed in 11.69s
```

**V2 · B1 守卫谓词注入探针**（`/tmp/opencode/probe_guards.py`，喂守卫自身 `inspect.getsource` 取出的谓词）
```
CASE                                                                           G11    G11b   M3
A: engine/ importlib.import_module('lingclaude.plugins.evil')                  miss   miss   miss
B: engine/ __import__('lingclaude.plugins.evil', fromlist=['x'])               miss   miss   miss
C: engine/ spec_from_file_location('evil_name', '.../plugins/agents/registry_loader.py') miss miss miss
D: core/ importlib.import_module(var)  # non-literal                           miss   miss   miss
E: engine/ import_module(plugin_manifest['entry'])                             miss   miss   miss

[M3 scan scope] _m3_plugin_side_scan iterates: ['for f in _py_files(CORE):']
[G11 scope] test_g11_no_core_import_plugins iterates: ['for root_dir in ("core", "engine"):']
```

**V3a · B2 豁免前提证伪**（spec-load 先行，无包 import 预热）
```
sys.modules BEFORE any import: False
  after call 1: sys.modules has key? False
  after call 2: sys.modules has key? False
  after call 3: sys.modules has key? False
distinct module objects across 3 fallbacks: 3
package import works: /home/ai/lingclaude/lingclaude/plugins/agents/registry_loader.py
pkg is last spec-copy? False -> double copy: True
```
> ⚠ **探针污染已记录**：首次探针（V3）先执行了包 import，填充 `sys.modules` 后再测得「registered: True」—— 假阳性。V3a 以 spec-load 先行重测得结论。教训入账：**同一进程内多探针共享 `sys.modules` 状态，序敏感探针必须独立进程或显式清场**（与 AGENTS.md「外部结果解析纪律」同源）。

**V4 · H1 M1 豁免覆盖率**
```
core/*.py=101  M1 active exemptions=97  whole-file=90 (89%)
actually checked: ['core/approval_matrix.py', 'core/bounded_compaction.py', 'core/context_engine.py',
 'core/evidence_protocol.py', 'core/invariants.py', 'core/mailbox_notify.py', 'core/plugin_lifecycle.py',
 'core/repair_card.py', 'core/rollout.py', 'core/session_token_sink.py', 'core/success_gate.py',
 'core/verify_ledger.py', 'core/worktree.py']
```

**V5 · H2 core/ 出向依赖按目标域**
```
M3_FORBIDDEN_PREFIXES = ('lingclaude.engine','lingclaude.plugins','engine','plugins')
  engine           18 GUARDED   (G1 baselined)
  model            10 >>> UNGUARDED   ['core/evidence_protocol.py:299','core/query_engine.py:231',
      'core/query_engine_lifecycle_mixin.py:18','core/query_engine_model_mixin.py:31',
      'core/query_engine_model_mixin.py:113','core/session_token_sink.py:30','core/success_gate.py:133',
      'core/system_prompt_builder.py:410','core/wiring.py:219','core/wiring.py:225']
  lacp              6 >>> UNGUARDED   ['core/fact_checker.py:29','core/l10_a_post_audit.py:39',
      'core/l7_cognitive.py:205','core/mcp_tools.py:158','core/query_engine.py:53','core/wakeup_channel.py:45']
  self_optimizer    6 >>> UNGUARDED   ['core/light_channel.py:143','core/session_runtime.py:151',
      'core/system_prompt_builder.py:326','core/turn_learner.py:35','core/turn_learner.py:36','core/turn_learner.py:92']
  coordination      2 >>> UNGUARDED   ['core/__init__.py:72','core/gray_zone.py:18']
```

**V6 · H2 环判定（逐对）**
```
model          core->model= 9  model->core=18  CYCLE=True
self_optimizer core->self_optimizer= 6  ->core=14  CYCLE=True
coordination   core->coordination= 2  ->core=5    CYCLE=True
lacp           core->lacp= 6  lacp->core=0  CYCLE=False
```

**V7 · H3 sqlite3 直连点 vs J4_STATE_MODULES**
```
lingclaude/core/safe_db.py:62
lingclaude/self_optimizer/replay_objective.py:77
lingclaude/self_optimizer/experiments.py:111
lingclaude/engine/todo.py:97
--- J4_STATE_MODULES (14) --- tests/test_p04_arch_guards.py:336-346
```

**V8 · M2 N7_PUBLIC_SEAMS 零引用**
```
tests/test_iron_law_guards.py:67: N7_PUBLIC_SEAMS = {"mcp_common"}
tests/test_iron_law_guards.py:74: （仅 docstring）
```

**V9 · H4 跨域私有符号穿透（总计 37）**
```
api.py/ 18 | cli/ 6 | core/ 5 | plugins/ 2 | engine/ 1 | seams/ 1
model/ 1 | governance/ 1 | coordination/ 1 | self_optimizer/ 1
```

**V10 · M1 主干硬编码 plugins/ 路径**
```
lingclaude/engine/hot_reload_trigger.py:47  TOOLS_PLUGINS_DIR
lingclaude/engine/hot_reload_trigger.py:48  AGENTS_PLUGINS_DIR
lingclaude/engine/tools.py:32              _PLUGINS_DIR = "lingclaude/plugins/tools"
lingclaude/engine/coding_wiring.py:285     "lingclaude/plugins/tools"
lingclaude/core/wiring.py:506              Path(...)/"plugins"
lingclaude/engine/subagent/seam_backend.py:162  Path(...)/"plugins"/"agents"/"registry_loader.py"
```

**V11 · H4 api.py:32 批量私有 re-export**
```
lingclaude/api.py:32  from lingclaude.seams.external_query import (   # 18 个 _ 私有符号
  _STATIC_LLM_FALLBACK, _PROXY_API_KEY, _PROXY_URL, _analyze_dir, _analyze_file,
  _call_llm, _call_llm_direct, _format_projects, _http_get_json, _list_projects,
  _llm_providers_snapshot, _load_env_keys, _query_github_stars, _query_pypi_downloads,
  _query_recent_commits, _query_versions, _resolve_llm_chain, _route_question)
```

**V13 · M4 l10_a_post_audit 模块级 exec**
```
56: import importlib.util as _importlib_util
58: _LINGAN_MODULE_PATH = os.environ.get("LINGAN_SECURITY_GATE_PATH", ...)
63:     _spec = _importlib_util.spec_from_file_location("lingan_security_gate", _LINGAN_MODULE_PATH)
65:     _spec.loader.exec_module(_lingan_mod)
71: except (ImportError, FileNotFoundError, Exception) as e:
```

---

*落盘：2026-09-28 · 审计者：灵克（lingclaude）· 只读审计，未修改任何被审计文件 · 探针脚本 `/tmp/opencode/{audit_scan,audit_scan2,probe_guards}.py` 未入库*
*姊妹报告（口径分歧见 §0）：`docs/AUDIT_20260928_ARCH_G10_G11.md`*

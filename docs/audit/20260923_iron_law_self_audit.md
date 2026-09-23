# 灵元八律 × lingclaude 自审报告（2026-09-23）

> 状态：**自审报告**——非裁决，非铁律修订建议。
> 产生方式：分两轮——第一轮 5 路并行侦察（架构骨架 / SeamType / work_claim / 守卫件套 / 与其它 agent 差异），第二轮 7 路并行核实（针对第一轮"基线陈述"做证据级深抠）。
> 立场：诚实报告，"文档承诺 vs 代码实情"的偏差**全部列出**，附 file:line 锚点。
> 用途：a) 给 lingclaude 维护者做整改优先级排序；b) 给对外传播提供"可讲/小心/老实说"清单。

---

## 一、摘要

### 一句话结论

> **lingclaude 是"架构宪法落地最强的 coding agent harness"——但宪法的执行工具自己违反了宪法。**
>
> **真正在 CI 真跑的铁律守卫只有 6 条：M1 / M2 / M3 / M5 / N2 / N7**。
> 其余（M4 / M6 / N1 / N3 / N4 / N5）骨架搭好但有声明的局限或未真跑；N6 完全没建且已主动归档到参考资料层。

### 关键事实校准（侦察 → 核实）

| 维度 | 文档/基线 | 代码实测 | 修正类型 |
|------|----------|----------|----------|
| SeamType 数 | 10 | **11**（多了 RESOURCE） | N5 漂移实例 |
| WiringSpec 数 | 58 | **67** | 基线 stale |
| arch_ledger 总条数 | 115 | **249**（134 record + 113 豁免 + 2 参考资料 .md） | `chore(debt): 20260923` 入库后 |
| PLUG_LEVELS | 10/10 入册 | **11/11** | RESOURCE 也补齐 |
| 词表 ground truth | 一份 | **53 个领域词** | yaml 表 |
| arch_exemption 豁免 | 几处 | **113 条**（active 108；M1:core 目录恰好 102） | 远超预期 |
| **M6 双口径** | "M6 真跑" | ⚠️ **从未被自动跑过一次** | **重大修正** |
| **N1 周期对账** | "无自动化" | "**在册未实现**"——六态机零编码 | **深化** |
| **N3 域前缀** | "约定层缺强制" | ⚠️ **plugin_manifest.py:32 regex 反向**——与 N3 目标相反 | **深化** |
| **N5 契约漂移** | "没建" | ⚠️ **真零落地** + 三处命名冲突坐实 + lingxi manifest 缺锚点 | **深化** |
| **J4 私连存储** | "15 个" | **真实违例 2 处 + 13 处受控豁免**（挂账 due 2026-11-30） | **重大修正（利好）** |
| **修剪司法闭环 runtime_count=26** | "M6 标记 → 自动发现盲区" | "**审计员手动跑 PluginLoader 实测**" | **措辞收回** |
| work_claim 测试 | 锁 8 + 节点 8 = 16 | **8 + 8 = 16** ✓ | 完全对得上 |

---

## 二、八条铁律逐项审

### 铁律 1（薄主干）——**语义层达标，工程层仍在守**

**达标的实证**：
- `core/` 105 个 .py，但**没有业务词汇渗透**（M1 词汇清白度强制 ground truth 扫表，53 个领域词命中必须为 0）
- 67 处 WiringSpec 走 manifest 声明式装配
- M1/M2/M3/M5 在 `.github/workflows/ci.yml` `arch-guards` 必过 job 跑通，**最近一次指纹对账 `2026-09-23T07:43:52+00:00` 通过**
- `tests/test_iron_law_guards.py` 588 行 + `tests/test_p04_arch_guards.py` 652 行
- 113 条 arch_exemption 豁免登记在册（active 108；**豁免不是"违规不开"，是"违规公开记账"**）

**关键反例（仍然欠账）**：
- J4 真实违例剩 2 处（memory_engine.py + governance_v2.py:640），其余 13 个是受控豁免（详见 §四 P1 #6）
- M4 换域测试仅原语级（StateStore save/load 三原语），不是套件级（详见 §四 P3）
- TRANSPORT / MEMORY / GOVERNANCE / SELF_OPT 四个 SeamType 槽位**生产代码入册数 = 0**（接口保留位，实现待补）

**薄的真实定义**：**不是"core/ 文件少"，是"core/ 不出现业务/插片概念词汇 + 不特判枚举值 + 不依赖具体插件实现"**——三重封闭性机械可测。113 个文件可以全装状态原语 + 治理 + 工具执行原语，但只要满足三重封闭性就够薄。

### 铁律 2（分形）——**已机械可验，且多层实证**

**最强实证**：
- `bash` 插件 manifest：`stop_layer.sub_seams: { seams: ["SandboxProvider"], implementations: 2 }`
- `agent_lingxi` manifest：`stop_layer: { kernel, seams: ["model_provider"], implementations: 1 }`
- `lingclaude_plugins/tui/` 独立仓，自有 seam + 3 methods（render_markdown / toolbar / prompt）
- PLUG_LEVELS 11/11 全声明（PROVIDER/TRANSPORT/MEMORY/GOVERNANCE/SELF_OPT = L1；SANDBOX = L2；TOOL/AGENT/MULTIMODAL/ORCHESTRATOR/RESOURCE = L3）

**机械可验的含义**：分形不是抽象口号，是 **`PluginManifest` JSON schema 里 `stop_layer` 三件套（kernel + seams + implementations）必填字段**——细则 5 在 schema 层强制，不是 docstring 里写写。

### 铁律 3（变化走接缝）——**主路径已全链路强制**

**实证**：
- 仓内插件：`plugins/agents/agent_lingxi/manifest.agent.json` + `plugin.py`（双登记）
- 仓外插件：`lingclaude_plugins/tui/`（独立仓名空间挂回，TUI_SEAM.register_provider 免签注册）
- 装配层：67 处 WiringSpec 走 manifest
- ToolResult 协议 12 handler 全量迁移（commit 6291b53），G4 守卫换代只缩不放

**实测分布**：
- TOOL：35 个实现（全裸 key：bash/read/write/edit/grep/glob/ast_replace/lsp/...）
- PROVIDER：≥ 9 个（openai/anthropic/glm/local/fs/shell/llm/subagent/cap/infer）
- SANDBOX：5 个（bwrap/landlock/seatland/noop + default）
- AGENT：24 个（**全带域前缀**：agent/lingxi, agent/lingresearch, cap/browser, proj/..., os/...）

**关键校正**：铁律 3 不是"主干从不改"，是"变化在插片层，主干不感知"。lingclaude 主干还是会在 core/ 加文件，但加的是**状态机原语**，不是业务实现——这不违反铁律 3。

### 铁律 4（修剪语法）——**机制 + 程序 + 制度三件套皆备，但修剪行为未真发生**

**实证**：
- `data/arch_ledger/arch_debt/` 29 条 debt record
- `arch_review/` 2 条评审（含 `first-seam-recycling-review` 结案）
- `arch_law_revision/` 29 条修订史
- `arch_audit_task/` 32 条审计任务
- 113 条 arch_exemption 豁免（active 108）

**完整修剪闭环**：候选清单（M6 仪表） → 立案（arch_audit_task） → 取证（M1-M5 + N1-N7） → 裁定（arch_review） → 结案（arch_law_revision） → 入账（arch_debt TTL） → 清理（debt 到期）

**重要校正（核实后措辞必须改）**：
- 之前讲"修剪司法闭环"时引用 `evidence.runtime_count=26`——**核实后这是 2026-09-17 一次性 `PluginLoader.load_plugins_from_dir` 人工实测**，**不是 M6 自动化采集**。
- M6 仪表本身从未被自动跑过（详见 §四 P0 #5），所以"运行时实测 26"是审计员手动动作触发的，不是程序自动发现。
- **TOOL "留任"结案不是失败**——是 M6 静态口径误报（"单实现"假象），经审计员手动实测发现 26 实现，结论是"扩展意图充分"。这是修剪语法的正确司法实践（程序不预设结论），但措辞要避免"程序自动发现盲区"。

**真实欠账（侦察数据暴露的真问题）**：
- **M6 双口径未真接通**：`scripts/seam_trend_inspect.py` 用 `arch_review/*-closed.json` 卷宗的 `evidence.runtime_count` 顶替实时 `SeamRegistry.get_all()`——M6 "双口径"实质上是"静态 + 历史审计"而非"静态 + 运行时"。
- **M6 快照被覆盖**：`data/arch_ledger/arch_m6_snapshot/` 目录被 datalog_aggregator 占写（type 同名冲突），7 个 json 全是 datalog 产物，无一含 `seam_impl_distribution`——意味着 seam_trend_inspect 要么没跑、要么跑了产物丢失。
- **M6 未入 CI**：`.github/workflows/ci.yml` 无 seam_trend_inspect step，触发靠 self_audit_trigger.py 手动返审。
- **修剪行为真实发生次数 = 0**（首例 TOOL 是留任非回收）——程序可执行性已证，实际修剪仍有待首例。

### 铁律 5（双向插片互认）——**已部分落地，但"在册未实现"**

**实证（核实后修正）**：
- `data/arch_ledger/federation_pair/` 2 条 record（lc-ac + ac-lc 对偶），state=paired
- **这两条 JSON 是 git commit `97b3096` (2026-09-17) 和 `6c6deb2` (2026-09-18) 手工提交**，不是 hook 自动建
- 六态名（`proposing / paired / drift / broken / restored / dissolved`）写在铁律 §六文档
- `lc_mcp_guard` + `proj_agent_gateway` 双插件 docstring 互相提对方名字
- lingxi 走 `transport=mcp/stdio` 是 **MCP 协议接入**，不是双向互认（之前讲"lingxi 是双向互认实证"**措辞收回**）

**关键修正**：
- "在册未实现"是工程上最阴的状态——审计任务 `audit-candzone-pairing-not-stateful.json` 已被 resolution 自证"federation_pair 六态状态机在册"结案，**但"在册"只是文档化，state machine 本身未编码**。
- `scripts/` 下零 `n1_*.py` / `federation_audit.py` / `federation_pair_audit.py`
- `tests/agents/test_iron_law_5_8.py` 自承（L8-9）："federation_pair 双 record 目前仅注释声明、无实现（审计实证），故对偶性按 T2 契约审计形态钉死"
- drift/broken 永远不会被触发——因为没有 detection loop，没有 N5 行为指纹 hash 接入
- **N1 应该和 N5 绑定整改**——没有 N5 行为指纹 hash，N1 的 drift 永远发现不了

### 铁律 6（信任等级）——**11 类 + 双等级强制**

**实证**：
- 25 个 agent plugin manifest 全部声明 trust_level + plug_level
- `core/manifest_lock.py:49` 三必填字段：`name / trust_level / plug_level`
- N2 守卫 `test_n2_trust_plug_declared` 在 test_lc_mcp_guard/test_cap_browser/test_cap_infer/test_os_resource 四文件中，**CI 必过**
- 分布：T1（agent_lingxi, agent_lc-guard, cap_inspect）/ T2（cap_browser, cap_infer）/ T3（os_resource, proj_agent-gateway）

**关键校正**：trust 不是 SeamType 的字段，是 per-plugin manifest 字段——这比"SeamType 级 T1/T2/T3"更精细（同一个 SeamType.AGENT 下的不同插件可以不同信任级）。

**关键欠账（核实后修正）**：
- **N5 契约漂移侦测真零落地**：
  - `tests/test_n5_token_guard.py` / `test_n5_stream_watchdog.py` / `test_n5_done_usage.py` 全是 token guard 同名异义
  - `lingclaude/cli/n5_token_guard.py` + `n5_stream_watchdog.py` 与律层 N5 契约漂移不是同一物，**潜在命名冲突**
  - **零 contract_drift 守卫脚本**、零 debt、零 manifest 字段
  - agent_lingxi manifest 44 行 grep 无 `contract_drift` / `behavior_fingerprint` / `signature` 字段——**T3 实证案例缺锚点**
  - 后续若落实契约漂移守卫，需在命名上避开 `n5_*` 前缀以免误关联

### 铁律 7（缝命名空间）——**已建约定层，缺运行时强制 + 工具反向**

**实证**：
- AGENT 类下 25 个 plugin **全部带域前缀**：`agent/lingxi`, `cap/browser`, `proj/agent-gateway`, `os/resource` 等
- N3 守卫 `test_n3_namespaced_seam_key` 在 test_lc_mcp_guard/test_cap_browser，**CI 必过**
- 域前缀断言：`agent/cap/os/hw/core` 五域

**关键欠账（核实后深化）**：
- **`SeamRegistry.register` 运行时无强制**：`seam.py:202-212` 实现只查空字符串 `raise ValueError("seam name must not be empty")`
- **`plugin_manifest.py:32` 的 regex `^[a-z0-9][a-z0-9_-]*$` 不含 `/`**——意味着 schema 实际上**反向**挡 N3，把合法 `agent/foo` 当非法拒收。这是**铁律工具本身违反铁律**的硬证据。
- `manifest_lock.py` 仅做字段存在性 + hash 漂移，不做域校验
- 新建 plugin `agent/foo` 漏斜杠的命运：
  1. `manifest_lock._scan_plugin_manifests` → name 字段存在 → **接受**
  2. `plugin_manifest.validate_manifest_dict` → regex 匹配 `foo` → **通过**
  3. `SeamRegistry.register("agent", "foo", inst)` → 非空校验 → **接受注册**
  4. **唯一拦截**：tests/agents/test_* 断言（test_lc_mcp_guard.py:98 / test_agent_family.py:110 / test_os_resource.py:57+）
- **漏前缀插件会启动成功、运行成功，只有事后测试断言拦截**

### 铁律 8（故障域 + 操作域 + 时效域）——**work_claim 全套实装，但时效查半实半虚**

**实证（核实后修正）**：
- 五原语全部真实现：`bind / renew / release / holder / check_paths`（`plugins/agents/work_claim.py` L36-113）
- `scripts/worktree_node.py` 联动真实闭合：建节点先 bind，git 失败 release 回滚，destroy 前查脏工作区
- `tests/agents/conftest.py` 查锁跳过显式留痕 `skipped:claim-held by <member> (expires_at=<ts>)`
- 16 项测试 = 8 锁 + 8 节点 ✓ **完全对得上**
- 实操证据：9 条 release 记录 + 12 条 work_claim_log 事件（lingke 单成员）
- 撞锁真实案例：lingxi 5-failed 基线漂移（`audit-work-claim-miss-tui-batch.json`）+ TUI P0-P2 二次撞车（`audit-work-claim-guard.json`）

**N4 "三查"的诚实偏差（核实后修正）**：
- ✅ **缺席查**真建：散落在 agent_family/cap_infer/proj_agent_gateway/os_resource 等 plugin 层，`test_absent_after_consecutive_failures` 多文件覆盖
- ✅ **互斥查**真建：`WorkClaim.bind()` 原生互斥 + `cap_browser.write` 写前 bind
- ⚠️ **时效查"半实半虚"**：
  - **"无独立守卫"表述不准**：`test_work_claim.py` 三个测试覆盖了过期语义（`test_expired_claim_auto_invalid` / `test_release_expired_by_third_party` / `test_check_paths_for_guard`）——**不是无守卫，是没纳入 test_iron_law_5_8.py 专项**
  - **"无主动提醒夺锁通道"完全属实**：全仓零 daemon/scheduler，TTL 过期只能等下一次 bind/holder/check_paths **被动发现**
  - **9 条 JSON 锁全部 `expires_at` 过 3 天仍存留**（state=released 但 timestamp 未清理），**坐实无清理循环**

**`tests/agents/conftest.py:28-29` 静默吞错**：
- catch 范围仅 L25-27（实例化 + check_paths）
- catch 后直接 return，**不 raise、不 log、不 skip、不写台账**
- 注释 L29 自称"J5 防博弈：守卫故障必须可见于报错，不静默改判"——但代码恰恰静默吞掉
- 这是最 **实锤的 J5 反例**——注释承诺和代码实情直接冲突

---

## 三、7 份核实合并差分（侦察判断 vs 核实判断）

| # | 锚点 | 侦察判断 | 核实判断 | 修正类型 |
|---|------|----------|----------|----------|
| 1 | N5 契约漂移守卫 | 没建 + 命名冲突推测 | **真零落地 + 三处命名冲突坐实 + lingxi manifest 缺锚点** | 深化 |
| 2 | N4 时效查 | 无独立守卫 + 无主动通道 | **被动有测 + 无主动通道 + 无清理循环 + conftest L29 是 J5 反例** | 部分收回 + 部分深化 |
| 3 | N3 域前缀强制 | 约定层缺运行时强制 | **plugin_manifest.py:32 反向 regex 是工具违反自身铁律** | 深化 |
| 4 | N1 周期对账 | 无自动化 | **在册未实现 + 已结案盲（audit-candzone-pairing-not-stateful.json）+ drift 永远不触发** | 深化 |
| 5 | M4 强度 | P2 待办 | **P3 长期挂账**（失败模式已显式入档，诚实记录非隐瞒） | 降级 |
| 6 | M6 双口径 | 骨架搭好未入 CI + 快照被 datalog 覆盖 | **从未自动跑过 + 目录被 datalog 鸠占鹊巢 + 方法自废 + runtime_count=26 是一次性人工** | 重大修正 |
| 7 | J4 私连存储 | 15 个存量状态模块违例 | **真实违例 2 处 + 13 处受控豁免已挂账 due 2026-11-30** | 重大修正（利好） |

---

## 四、13 条欠账详细清单（核实后，按 P 级重排）

### P0 五件事（最高优先 — "工具违反自身铁律"或"文档承诺超实情"）

#### P0 #1：N5 契约漂移守卫（真零落地）

**问题描述**：铁律条文（`LINGYUAN_IRON_LAW.md:391`）要求 T3 自优化插片行为指纹 hash 变化即记 contract_drift record，但守卫实现零落地。

**证据链**（每条都有 file:line）：
1. `tests/test_n5_token_guard.py:1` — N5a token 用尽告警（0 delta + 0.95*max 阈值）— **与契约漂移无关**
2. `tests/test_n5_stream_watchdog.py:1` — N5b 流内停滞 watchdog — **与契约漂移无关**
3. `tests/test_n5_done_usage.py:1` — done 事件 usage 透传 — **与契约漂移无关**
4. `lingclaude/cli/n5_token_guard.py` — token guard 实现 — **同名异义**
5. `lingclaude/cli/n5_stream_watchdog.py` — stream watchdog 实现 — **同名异义**
6. `scripts/` 下 65 个 `.py`，**零** `n5_*.py` / `contract_*.py` / `fingerprint_*.py`
7. `data/arch_ledger/arch_debt/` 下 grep `drift`：命中 `tests-coding-copy-drift.json` 和 `regression-preexisting-failures-20260921.json`（含"P0-N5"字样但指**断言过期项**）——**零 N5 contract_drift 债务**
8. `lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` 全 44 行 grep 无 `contract_drift` / `behavior_fingerprint` / `signature` 字段——**T3 实证案例缺锚点**
9. `data/arch_ledger/arch_reference/` 10 份资料中两份含 `drift_note` 字段：`ref-zg-token-saving.json:5`（"单口径数字，N6 要求第二口径对照"）、`ref-deepseek-operator.json:5`（"T3 契约漂移条款+算子概念分离的依据"）——但**这是字段级痕迹，不是文件级证据**
10. `LINGYUAN_IRON_LAW.md:391` 明文"**N5 契约漂移侦测（参考资料增补，配 T3 契约漂移条款）**"——条文要求存在，引用 `ref-zg-token-saving:6` 作证据

**整改路径**：
1. 新命名空间：`scripts/contract_drift_*.py` 或 `scripts/behavior_fingerprint_*.py`（避开 `n5_*` 前缀）
2. lingxi manifest 新增 `behavior_fingerprint` 字段（铁律 6 §T3 漂移条款要求）
3. debt 入账：`arch_debt/contract-drift-detector-missing.json`（不然返审触发器发现不了）
4. test 新建：`tests/test_contract_drift.py` 或 `tests/test_n5_v2_*.py`（避开 `n5_*` 前缀）

**预估工时**：3-5 天（含指纹算法）

#### P0 #2：N4 时效查（被动 + 无清理循环）

**问题描述**：work_claim 锁 TTL 过期后无主动通知通道，且 conftest.py 静默吞错违反 J5 失败模式显式声明。

**证据链**：
1. `WorkClaim.check_paths`（L102-113）`locked` 字段**只包含 not expired 项**（L111 `if h and not h["expired"]`），过期锁被过滤掉
2. `WorkClaim.holder`（L92-100）`expired` 字段仅当 `state=="held"` 且 `expires_at` 已过时为 True
3. 全仓搜 `expired_at / notify_expired / expiry_warning / claim_expired / seize_expired`：零命中（仅 `docs/SESSION_MANAGEMENT.md:1339` 提及 session 过期，与 work_claim 无关）
4. `tests/agents/conftest.py:28-29` `except Exception: return` —— catch 后直接 return，**不 raise、不 log、不 skip、不写台账**
5. `tests/agents/conftest.py` L29 注释自陈"J5 防博弈：守卫故障必须可见于报错，不静默改判"——**代码恰恰静默吞掉**
6. `data/arch_ledger/work_claim/` 9 条 JSON 样本时间戳 `1789882086` / `1789916004` → 2026-09-20 13:48 / 22:53 UTC，**全部 `expires_at` 过 3 天仍存留**（state=released 但 timestamp 未清理）

**整改路径**：
1. `tests/agents/conftest.py:28-29` 改 `except Exception: skip + log to arch_audit_state`，并对守卫故障写入 N4 异常台账
2. 建 `scripts/work_claim_sweeper.py` 周期清理 stale 锁（state=released 且 expires_at 过期的清理）
3. `tests/agents/test_iron_law_5_8.py` 加 `test_*expired*` 专项，纳入 N4 守卫
4. `tests/agents/test_work_claim.py` 的过期语义测试保持现状（已有 3 个测试覆盖）

**预估工时**：1-2 天

#### P0 #3：N3 域前缀（工具违反自身铁律）

**问题描述**：`plugin_manifest.py:32` 的 regex 与 N3 目标反向，且 `SeamRegistry.register` 无运行时强制，漏前缀插件可默默跑通。

**证据链**：
1. `SeamRegistry.register`（`seam.py:202-212`）完整方法体：
   ```python
   @classmethod
   def register(cls, seam_type: SeamType, name: str, instance: Any) -> None:
       seam_type = SeamType(seam_type)
       name = str(name).strip()
       if not name:
           raise ValueError("seam name must not be empty")
       with cls._get_lock():
           cls._registry.setdefault(seam_type, {})[name] = instance
       logger.debug("SeamRegistry: register %s/%s -> %r", seam_type.value, name, instance)
       cls._notify_change("register", seam_type, name)
   ```
   唯一校验：`name` 非空。无 `if "/" not in name` 校验，无 re.match，无 validate_namespace 辅助。
2. `plugin_manifest.py:32` schema：
   ```python
   "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$"},
   ```
   正则**不含 `/`**——意味着 name 带 `/`（如 `agent/foo`）会被拒，**但** name 不带 `/`（如 `foo`）反而通过。**schema 实际上与 N3「必带域前缀」目标反向**。
3. `manifest_lock.py:49`：`REQUIRED_MANIFEST_FIELDS = ("name", "trust_level", "plug_level")`——仅字段存在性，不做域校验
4. `mcp_common.py:23-25` `domain_of()` 仅 `split("/", 1)[0]`，无校验（缺 `/` 时原样返回）
5. `core/policy_loader.py:68` `"/" in name` **拒收**（防路径穿越，意图相反）
6. `tests/agents/test_lc_mcp_guard.py:96-100`：
   ```python
   def test_n3_namespaced_seam_key():
       m = json.loads(MANIFEST.read_text(encoding="utf-8"))
       assert "/" in m["name"], f"缝 key 必带域前缀（N3）: {m['name']}"
       assert m["name"].split("/", 1)[0] in ("core", "agent", "cap", "os", "hw")
       assert m["name"] == "agent/lc-guard"
   ```
   测试直接 `read_text` 解析 manifest.json，**完全没碰 SeamRegistry.register**

**新建 plugin `agent/foo` 不带 `/` 的命运**（按代码路径推演）：
1. `manifest_lock._scan_plugin_manifests` → name 字段存在 → **接受**
2. `plugin_manifest.validate_manifest_dict` → regex 匹配 `foo` → **通过**
3. `SeamRegistry.register("agent", "foo", inst)` → 非空校验 → **接受注册**
4. **唯一拦截**：tests/agents/test_* 断言（test_lc_mcp_guard.py:98 / test_agent_family.py:110 / test_os_resource.py:57+）

**整改路径**：
1. 改 `plugin_manifest.py:32` regex：增加 `/` 字符支持（如 `^[a-z0-9][a-z0-9_/-]*$`）
2. 或在 `SeamRegistry.register` 加 `validate_namespace(name)` 校验：要求 `name` 含 `/` 且域前缀在五域内
3. 新建 `core/namespace_validator.py` 集中处理

**预估工时**：0.5-1 天

#### P0 #4：N1 周期对账（在册未实现）

**问题描述**：federation_pair 六态机零编码，drift/broken 永远不会触发。

**证据链**：
1. `data/arch_ledger/federation_pair/` 2 个 json（`ac-lc.json` + `lc-ac.json`），两边 `state` 字段都是 `"paired"`——**不是六态机任何一个值**。`proposing` 字样只在 lc_mcp_guard 注释里
2. `scripts/` 下零 `n1_*.py` / `federation_audit.py` / `federation_pair_audit.py`
3. `grep -rln federation_pair lingclaude/` 只命中 4 个文件，全是注释/notes，**零运行时代码**
4. `tests/agents/test_iron_law_5_8.py`：
   - `test_pair_seam_keys_symmetric_with_domain_prefix` (L34-39)：断言字符串相等，**仅断言字符串相等**
   - `test_pair_declaration_symmetry` (L42-52)：断言两边 docstring 互相提到对方名字（注释对偶）
   - `test_pair_record_machine_on_*` (L61-115)：测的是本地 `_record` 的 `running/succeeded/failed/aborted` 四态（J4 终态，不是六态机）
   - `test_guard_absent_after_consecutive_failures` (L120-152)：N4 探针累计缺席
   - 测试文件 L8-9 自承："federation_pair 双 record 目前仅注释声明、无实现（审计实证），故对偶性按 T2 契约审计形态钉死"
5. `arch_law_revision/20260917-14.json` resolution 备注"候选5对偶记账状态机化(federation_pair六态)已在主修复完成"——**这是修订叙述，不等于代码实现**
6. `lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` L10 `transport.kind == "mcp"` ✓，但**根本没有 lingxi 的 federation_pair record**——lingxi 走 `agent_registry/agent/lingxi.json`（平行体系）
7. 2 条 JSON record 是 **git commit `97b3096` (2026-09-17) 和 `6c6deb2` (2026-09-18) 手工提交**，**不是 hook 自动建**
8. `lingclaude/core/seam.py` 只有 `SeamRegistry.register/unregister`（热拔插），**零 federation_pair 引用**
9. `agent_family.py` `grep federation_pair` 零命中

**整改路径**：
1. 建 `scripts/n1_federation_audit.py` 周期对账（绑 N5 行为指纹 hash）
2. `SeamRegistry.register` 加 hook：自动建对偶 record（cross-reference `agent_family.py`）
3. `tests/agents/test_iron_law_5_8.py` 加 `test_federation_state_machine_transitions`（测六态机迁移）
4. lingxi 方向建 `federation_pair/lc-lingxi.json` + `lingxi-lc.json` 对偶 record

**预估工时**：2-3 天（绑 N5）

#### P0 #5：M6 双口径（从未自动跑过）

**问题描述**：M6 接缝生态趋势仪表从未被自动跑过一次，`arch_m6_snapshot/` 目录被 datalog 鸠占鹊巢。

**证据链**：
1. `scripts/seam_trend_inspect.py`（2026-09-17 落地）的四个核心函数：
   - `scan_seam_registrations()` (L53) — AST 抓 `register(SeamType.*)` 静态调用点
   - `scan_wiring_specs()` (L85) — AST 抓 `WiringSpec(` 调用点
   - `load_last_snapshot()` (L116) — 读 `arch_m6_snapshot/` 最近一份 JSON
   - `load_closed_reviews()` (L131) — 读 `data/arch_ledger/arch_review/*-closed.json`，**从卷宗 evidence 拿 `runtime_count`**（m6-static-only-blindspot 整改后加的）
   - **没有 `get_runtime_count()` 函数**
2. `lingclaude/core/seam.py` 提供运行时查询方法（**核实后修正侦察判断**）：
   - `get_all(seam_type)` (L264-274) — 必须传 SeamType 参数，返回单类型副本
   - `snapshot()` (L276-286) — 全量快照
   - `list_names(seam_type)` (L252-257)
   - `has(seam_type, name)` (L259-262)
   - **但 `seam_trend_inspect.py` 从未调用过任何一个**
3. `data/arch_ledger/arch_m6_snapshot/` 7 份 JSON 字段（每一个都一致）：
   ```
   day, generated_at, total_events, bad_lines,
   all_events, models, paths, signals,
   t0_nudges, t0_blocks, l5_rounds, l5_consistency_avg
   ```
   - `grep "seam_impl_distribution"` → 0 命中
   - `grep "runtime_count"` → 0 命中
   - `grep "total_specs"` → 0 命中
   - `grep "single_impl_seams"` → 0 命中
4. 真正生产者：`scripts/datalog_aggregator.py`（`datalog_aggregator.py:38` 的 `SNAP_TYPE = "arch_m6_snapshot"`）——自白（`datalog_aggregator.py:6-8`）："填上 arch_ledger.py:42 T_SNAP 全仓零写入的空壳；纳入 self_audit_trigger LEDGER_TYPES 后，新快照 = fingerprint 变化 → 触发返审"
5. `data/arch_ledger/arch_review/first-seam-recycling-review.json` 含 `evidence.runtime_count` = 26，`static_count_M6` = 1——**这是 2026-09-17 一次性 `PluginLoader.load_plugins_from_dir` 人工实测**，不是自动化运行时采样
6. `.github/workflows/ci.yml` 全文件 grep `seam_trend_inspect` / `m6` → 0 命中
7. `scripts/self_audit_trigger.py:41` SELF_FILES 列表包含 `seam_trend_inspect.py`，但**没有任何 `subprocess.run` 调用 seam_trend_inspect**（整个文件 subprocess 仅 2 处：git rev-parse + pytest）——只追踪指纹，**不执行仪表**

**整改路径**：
1. `seam_trend_inspect.py` 调 `SeamRegistry.snapshot()` + `list_names(st)` 做真实运行时计数
2. 目录名分开：`arch_m6_snapshot/` 改名或新建 `arch_seam_eco_snapshot/` 让 M6 真产物有家可归
3. 入 CI：`.github/workflows/ci.yml` 加 `seam_trend_inspect` step（哪怕 weekly）
4. debt 入账：`arch_m6_snapshot-debt-tracker-missing.json`（否则返审触发器发现不了 M6 失修）

**预估工时**：2-3 天

### P1 一件事（数字错误但严重度中）

#### P1 #6：J4 私连存储（真实违例 2 处，不是 15）

**问题描述**：铁律文档"15 个存量状态模块私连存储"是**审计范围清单**，不是"现存违例数"。真实违例剩 2 处。

**证据链**：
1. `tests/test_p04_arch_guards.py:322-331` 的 `J4_STATE_MODULES = [...]` 字面是 15 个元素（迁移目标清单）
2. `docs/LINGYUAN_IRON_LAW.md:240` "15 个" 与 `:269` "其余 14 个" 描述的是**清单元素总数**而非"现存违例数"
3. `data/arch_ledger/arch_debt/` 命中关键词的 9 条 record 都是同一波 2026-09-17 整改挂的债，全部 due 2026-11-30：
   - `handover-export-view-j4.json` — `state: resolved`（2026-09-20 P3-7 整改后已 resolve）
   - `session-export-view-j4-migration.json` — `core/session.py:110,186` open
   - `governance-export-view-j4-migration.json` — `core/governance.py:444` open
   - `governance-v2-proposals-j4-migration.json` — `governance/governance_v2.py:640` open（**唯一 hardcoded_direct，盲区**）
   - `governance-verifier-export-view-j4-migration.json` — `core/governance_verifier.py:294,303` open
   - `layered-memory-export-view-j4-migration.json` — `core/layered_memory.py:553→584`（9-21 漂移修正）open
   - `meta-cognition-export-view-j4-migration.json` — `core/meta_cognition.py:269` open
   - `reasoning-chain-export-view-j4-migration.json` — `core/reasoning_chain.py:122` open
   - `topic-stack-export-view-j4-migration.json` — `core/topic_stack.py:149` open
4. 真实违例清单（2026-09-23）：
   - **memory_engine.py**（直连 sqlite3 + SqliteStoreBase，无 StateStore）—— **J4 真实违例**
   - **governance_v2.py:640**（hardcoded_direct 提案存储）—— **唯一盲区**
   - 其余 13 个属于"主通道已迁 StateStore，导出视图/兜底/export 仍走文件"的迁移期共存态——已挂账 due 2026-11-30
5. `task_aggregation.py:256/385/489` — **全部走 StateStore.save("task"/"task_group")**，无 J4_EXPORT_VIEWS/J4_KNOWN_DIRECT 登记，**J4 已清**（之前侦察判断"15 个含 task_aggregation"是错的，已迁完）

**整改路径**：
1. `memory_engine.py` 迁 StateStore（最大欠账，从 sqlite3 + SqliteStoreBase 改走 StateStore 三原语）
2. `governance_v2.py:640` 提案存储补迁 StateStore
3. 铁律文档同步：把"15 个存量状态模块私连存储"改为"2 处真实违例 + 13 处受控豁免（挂账 due 2026-11-30）"
4. `tests/test_p04_arch_guards.py:322-331` 的 `J4_STATE_MODULES` 列表分两组：`REAL_VIOLATIONS`（2 个）+ `EXPORT_VIEW_EXEMPTIONS`（13 个）

**预估工时**：3-5 天（memory_engine 较大）

### P3 一件事（长期挂账态，诚实记录非隐瞒）

#### P3：M4 换域测试强度（仅原语级非套件级）

**问题描述**：M4 换域测试仅在原语级（StateStore save/load 三原语），不是套件级（整个测试套件跑在陌生域 fixture 上）。失败模式已显式入档，非隐瞒。

**证据链**：
1. `tests/test_iron_law_guards.py:475-505` test docstring 自陈："本实现验证 StateStore 原语级换域（人造域 order/ticket 跑通三原语）。守卫强度低于条文处已如实声明：测试套件级换域（整套测试跑在陌生域 fixture 上）待 CI 侧改造后升格"
2. 合成 registry：不存在 `tests/fixtures/synthetic_registry.py`（tests/fixtures/ 仅 `core_vocabulary.yaml`）。synthetic registry 是**内联**造的：`StateStore(backend="json", root=ROOT/data/m4_synth)` + dtype `("order", "ticket")`（仅作 StateStore 类型键名，非业务域代码）
3. 测试覆盖：只跑 StateStore 三原语 save/load/close。assert 5 处（行 495-502），无 `pytest.main(...)`
4. `arch_law_revision/20260917-19.json:4`："M5 升格真实插片逐级拔+声明完备性检查，M4 失败模式入档"——失败模式声明了，强度未升格
5. `arch_law_revision/20260917-14`：候选区返审修复（N5-N7 归位/候选区修剪/candzone 仲裁/federation_pair 状态机化），**未提 M4**
6. `arch_audit_task` 搜 `m4` / `domain_portability` / `swap_domain`：无独立 M4 整改任务（仅 `audit-lingke-report-verified.json` 间接提及）
7. `arch_debt/` 无 m4_synth/套件级条目
8. `arch_law_revision/` 搜 `套件级` / `原语级` / `m4`：零命中
9. `docs/LINGYUAN_IRON_LAW.md` 自承"M4 守卫强度为原语级（套件级换域待升格，失败模式已入档 M4 docstring）"——**属 P3 长期挂账态而非 P2 队列项**

**整改路径**（不是 P0/P1，仅供长期规划）：
1. 套件级换域 fixture 改造：建 `tests/fixtures/synthetic_business_domain/`，让整个 `tests/` 套件跑在陌生域 fixture 上
2. CI 侧加入 hook：`pytest --rootdir=tests/fixtures/synthetic_business_domain/`
3. 整改完成后更新 `LINGYUAN_IRON_LAW.md` §四 基线表

**预估工时**：5-7 天（含合成业务域 fixture）

### 其它已知欠账（之前侦察列出，本次未核实）

#### P2 #11（侦察级别，未核实）：15 个存量状态模块私连存储 → **已修正为 P1 #6**
（详见 §四 P1 #6 详述）

#### P2 #12（侦察级别，未核实）：conftest.py L29 `except Exception: return` 静默吞错
（已升级为 P0 #2 的一部分，详见 §四 P0 #2）

#### P2 #13（侦察级别，未核实）：修剪行为真实发生次数 = 0
（保留 P2 长期观察项，详见 §二 铁律 4 节末）

---

## 五、对外故事修正清单

### 故事 1：修剪司法闭环

**之前措辞（侦察阶段）**：
> M6 标记 TOOL 接缝为回收候选 → 立案 → 运行时实测 26 实现 → 裁定留任 → 结案入档

**修正后措辞（核实阶段）**：
> M6 静态扫描标记 TOOL 接缝为单实现候选 → 立案 → **审计员手动跑 `PluginLoader.load_plugins_from_dir` 全载发现 26 实现**（`_ToolSeamProxy` 动态注册未在 M6 静态口径覆盖）→ 裁定留任 → 结案入档；**副产品暴露 M6 双口径未真接通的事实**（静态=1 vs 运行时=26 的偏差未自动化捕获，靠审计员临时动作才发现）

**故事力量变化**：诚实度更高，"程序不预设结论"+"工具盲区暴露后用人工补丁补齐"——更有力量。

### 故事 2：lingxi 是双向互认实证

**之前措辞（侦察阶段）**：
> agent_lingxi 本身就是双向互认的实证——通过 transport=mcp/stdio 被外部系统消费，同时 lingxi 自己也是 lingclaude 的 AGENT 插片

**修正后措辞（核实阶段）**：
> **收回**——lingxi 只是 MCP 协议接入实证（T2 契约审计形态），不是双向记账实证。双向记账是 atomcode ↔ lingclaude 双侧（`federation_pair/{lc-ac,ac-lc}.json`），但**只在册未实现**：2 条 JSON 是 git commit 手工提交，六态机零编码，drift/broken 永远不会被触发。

### 故事 3：J4 欠账 15 个

**之前措辞（侦察阶段）**：
> J4 欠账最大项（15 个存量状态模块仍私连存储）

**修正后措辞（核实阶段）**：
> J4 欠账 **2 处真实违例 + 13 处受控豁免**：
> - memory_engine.py（直连 sqlite3 + SqliteStoreBase，无 StateStore）
> - governance_v2.py:640（hardcoded_direct 提案存储，唯一盲区）
> - 其余 13 个是"主通道已迁 StateStore，导出视图/兜底/export 仍走文件"的迁移期共存态——已挂账 due 2026-11-30
> - 铁律文档"15 个存量状态模块私连存储"是**审计范围清单**（迁移目标清单），不是"现存违例数"

### 故事 4：M6 双口径

**之前措辞（侦察阶段）**：
> M6 仪表已建并入 CI

**修正后措辞（核实阶段）**：
> **收回**——M6 仪表从未被自动跑过一次：
> - `scripts/seam_trend_inspect.py`（2026-09-17 落地）只做 AST 静态扫描 `register(SeamType.*)` 调用点，从不调 `SeamRegistry.get_all/snapshot`
> - `data/arch_ledger/arch_m6_snapshot/` 7 份 JSON 字段全是 datalog 维度（`models/paths/t0_nudges/l5_rounds`），从未出现 `seam_impl_distribution` 或 `runtime_count`——是 datalog 鸠占鹊巢填了 T_SNAP 空壳
> - 唯一 `runtime_count=26` 在 `first-seam-recycling-review.json`，是 2026-09-17 一次性人工实测，非自动化采集
> - `self_audit_trigger.py` 仅追踪这两个脚本的 fingerprint 变化，**从不触发执行**
> - CI 无 M6 step

### 故事 5：铁律工具自身合规

**之前措辞（侦察阶段）**：
> 八条铁律全部在 CI 跑

**修正后措辞（核实阶段）**：
> **CI 真跑的铁律守卫只有 6 条**：M1 / M2 / M3 / M5 / N2 / N7
>
> 其余状态：
> - M4：原语级非套件级（**P3 长期挂账**，失败模式已显式入档，诚实记录）
> - M6：从未自动跑过（**P0 #5**，骨架搭好 + 目录被鸠占）
> - N1：在册未实现（**P0 #4**，六态机零编码）
> - N3：约定层 + 测试断言，缺运行时强制 + 工具反向（**P0 #3**）
> - N4：缺席查 + 互斥查真建，时效查被动有测无主动通道 + 无清理循环 + conftest L29 是 J5 反例（**P0 #2**）
> - N5：真零落地 + 三处命名冲突坐实 + T3 实证缺锚点（**P0 #1**）
> - N6：完全没建且已主动归档到参考资料层（**无优先级**，属组织决策）

### 故事 6：work_claim 三查齐备

**之前措辞（侦察阶段）**：
> N4 三查全部建成

**修正后措辞（核实阶段）**：
> N4 三查的诚实状态：
> - 缺席查 ✅ 真建（agent_family/cap_infer/proj_agent_gateway/os_resource 等 plugin 层）
> - 互斥查 ✅ 真建（WorkClaim.bind() 原生互斥 + cap_browser.write 写前 bind）
> - 时效查 ⚠️ **被动有测 + 无主动通道 + 无清理循环**：
>   - test_work_claim.py 三个测试覆盖了过期语义（test_expired_claim_auto_invalid / test_release_expired_by_third_party / test_check_paths_for_guard）——**不是无守卫，是没纳入 test_iron_law_5_8.py 专项**
>   - 全仓零 daemon/scheduler，TTL 过期只能等下一次 bind/holder/check_paths **被动发现**
>   - **9 条 JSON 锁全部 expires_at 过 3 天仍存留**（state=released 但 timestamp 未清理）——坐实无清理循环
>   - conftest.py:28-29 `except Exception: return` 是 J5 反例代码

---

## 六、lingclaude 与其它 coding agent / Harness 的差异（基于侦察数据）

### 维度矩阵

| 维度 | lingclaude | Claude Code | Codex App Server | opencode / crush |
|------|-----------|-------------|------------------|------------------|
| **架构层抽象** | 架构宪法（SeamType + J5 四条件 + 修剪语法） | 工具层抽象（tool call + permission） | 协议层抽象（JSON-RPC） | CLI 工具 |
| **接缝协议** | 11 类 SeamType + 双等级 + 域前缀 + 嵌套 stop_layer | extension API | App Server 接任意应用 | plugin 简单枚举 |
| **状态管理** | StateStore 三原语（records/events/transition） + 249 条 arch_ledger（134 record + 113 豁免 + 2 参考资料 .md） | session 持久化 | task state | 简单 session |
| **守卫件套** | M1-M6 + N1-N7 + J5 四条件 + 自审触发器常态化（**6 真跑 + 4 部分 + 2 骨架 + 2 没建**） | 无架构守卫 | 无 | 无 |
| **修剪语法** | debt record + 接缝回收 + M6 仪表 + J5 误差事件入账 | 无 | 无 | 无 |
| **联邦互认** | federation_pair 静态 record + lingxi 实证（**无周期对账**） | 单边扩展 | 单边协议 | 单边 plugin |
| **信任等级** | T1/T2/T3 × L1/L2/L3 双轴强制（11/11 入册） | 无 | 无 | 无 |
| **域命名空间** | `{ns}/{seam}`（约定层 + N3 测试断言，**缺 registry 运行时强制 + 工具反向**） | n/a | n/a | n/a |
| **多 Agent 协作** | **独家**：Subagent 三后端（inprocess + ACP + MCP）+ SubagentCapabilities 5 flag + 20 家族成员 + LingBus 跨进程 + 投机扇出 | Task tool（subagent） | App Server 多实例 | 单 agent |
| **沙箱层** | SandboxProvider Protocol + 5 实现（bwrap/landlock/seatland/noop/default） + 网络 allowlist（fail-closed） | Bash 网络 allowlist | SandboxMode × Approval 矩阵 | bwrap/firejail |
| **可审计性** | arch_ledger 249 record 可机械 query + arch_audit_state 指纹锚定（HEAD=8499089） | 无台账 | 无 | 无 |
| **修剪性** | debt TTL + 接缝回收审查闭环 + arch_law_revision 修订史 | 无 | 无 | 无 |
| **对外接口** | **真做了**：CLI 13 子命令 + JSON-RPC app-server（stdio+HTTP）+ Python SDK + 26 MCP 工具 + 26 HTTP 路由（端口 8700）+ WebUI（Rust） | CLI + 商业产品 | JSON-RPC app-server | HTTP server + OpenAPI 3.1 |

### lingclaude 独占部分（业界其它 coding agent / Harness 普遍缺失）

1. **架构宪法层抽象** —— cc/codex/opencode 把抽象做在"调用层"，lingclaude 做在"架构宪法层"
2. **修剪语法 + debt record** —— 临时直连入账、到点不清守卫即红，**业界没有等价物**
3. **J5 测量口径四条件 + 多口径互证** —— 失败模式显式 / 多口径分歧即警 / 行为锚终审 / 误差事件入账
4. **铁律 8 操作域 + 时效域** —— lock-as-record + TTL + 查锁跳过显式留痕，**业界没有等价物**
5. **铁律 5 双向对偶记账状态机** —— 六态 federation_pair 静态 record 在册（**运行未实现**）
6. **铁律 6 信任三级 + 漂移条款** —— T1/T2/T3 × L1/L2/L3 双轴 manifest 强制

### lingclaude 自己承认的弱点（仓内诚实对账）

**vs atomcode/Pi**：
- 循环纯度 **586 vs ~120 行**（差 ~5x）

**vs Pi**：
- 引擎循环本体**不可热切**（plugin/TUI 两层蓝绿已成熟，引擎循环不行）

**vs opencode**：
- HTTP server 解耦 / 反思 / dream 进程**缺位**

**vs Laya System-1**：
- NanoJev ECE **0.246 vs 0.081**（SFT checkpoint 挂账，`arch_audit_task/spec_decision_sft_checkpoint_pending_20260923`）

**自我对账结论**（来自 `20260923_harness_comparison_v3.md`）：
> 09-21 列了 8 项弱点 →09-23 抽验关闭 7.5 项。当前真正剩余差距只有两个数字：**586 行循环体**和 **0.246 的 NanoJev ECE**——**都是数字问题，不是设计问题**。

**OpenCode "4.7x token 优势"撤回**：
- 🔴 "Systima benchmark" 不存在，是文档吹的典型
- lc datalog 自家实证 cached_pct 30.7% 反证

---

## 七、最终结论

### lingclaude 的真实画像

**不是"另一个 coding agent"，是"编码 agent 社会的操作系统层"**——

证据链（从骨架到机制都有锚点）：
- **架构宪法层**：11 类 SeamType × 双等级 + 域前缀 + 嵌套 stop_layer 四件套 manifest 化
- **状态原语层**：StateStore 三原语（records/events/transition）+ 249 条 arch_ledger
- **修剪语法层**：debt record + 接缝回收审查闭环 + M6 仪表（**核实后：M6 仪表未自动化**）
- **联邦层**：federation_pair 双 record + 信任三级 + 命名空间约定（**核实后：在册未实现**）
- **操作纪律层**：work_claim 五原语 + TTL + 16 项测试 + scripts/worktree_node.py 联动
- **对外接口层**：CLI 13 子命令 + JSON-RPC app-server + Python SDK + 26 MCP 工具 + 26 HTTP 路由

### 战略定位（最终推荐）

> **Lingclaude：第一个把"架构宪法"落地的 coding agent harness——M1/M2/M3/M5/N2/N7 这 6 条守卫在 CI 真跑（其余 M4/M6/N1/N3/N4/N5 骨架搭好但有声明的局限或未真跑，N6 完全没建且已主动归档），work_claim 操作域 + TTL + 16 项测试全绿，arch_ledger 249 record 可机械审计；目标是中立插片协议 + 双向对偶记账 + 修剪语法三件套给整个行业发统一插片证。**

### 一句话总结

> **Claude Code 做的是"agent"，lingclaude 做的是"agent 工作所在的架构本身"——前者是应用，后者是内核 + 应用 + 修剪 + 联邦四件套。剩余两个数字差距（循环纯度 586 vs 120、NanoJev ECE 0.246 vs 0.081）都是数字问题，不是设计问题。**

---

## 八、完整证据索引（按主题）

### 铁律 1（薄主干）
- `tests/test_iron_law_guards.py:322` M1 入口
- `tests/test_iron_law_guards.py:373` M2 入口
- `tests/test_iron_law_guards.py:458` M3 入口
- `tests/test_iron_law_guards.py:511` M5 入口
- `tests/fixtures/core_vocabulary.yaml` 词汇表（53 个领域词）
- `lingclaude/core/wiring.py` 67 处 WiringSpec
- `lingclaude/core/plugin_manifest.py` PluginManifest schema
- `data/arch_ledger/arch_exemption/M1:core` 102 条豁免（全量 113 含 M2/M3）

### 铁律 2（分形）
- `lingclaude/core/seam.py:35-48` 11 类 SeamType
- `lingclaude/core/seam.py:57-68` PLUG_LEVELS 11/11 全声明
- `lingclaude/plugins/tools/bash/manifest.plugin.json` 嵌套停层
- `lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` 实证

### 铁律 3（变化走接缝）
- `lingclaude/core/wiring.py` 67 处 WiringSpec
- `lingclaude/core/plugin_loader.py` FAIL-FAST schema 校验
- `lingclaude_plugins/tui/` 独立仓挂回

### 铁律 4（修剪语法）
- `data/arch_ledger/arch_debt/` 29 条 debt
- `data/arch_ledger/arch_review/first-seam-recycling-review.json` 首例闭环
- `data/arch_ledger/arch_law_revision/20260917-07.json` 修订史
- `scripts/seam_trend_inspect.py` M6 仪表（**核实后：从未自动跑过**）

### 铁律 5（双向插片互认）
- `data/arch_ledger/federation_pair/{ac-lc,lc-ac}.json` 2 条 record（**核实后：手工提交**）
- `data/arch_ledger/agent_registry/agent/lingxi.json` 平行体系
- `tests/agents/test_iron_law_5_8.py:8-9` 自承"仅注释声明、无实现"
- `audit-candzone-pairing-not-stateful.json` 已结案盲

### 铁律 6（信任等级）
- `core/manifest_lock.py:49` 三必填字段
- `lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` T1/L1 实证
- `lingclaude/plugins/agents/os_resource/plugin.py:7` T3/L3 实证
- **N5 真零落地**：见 P0 #1 证据链

### 铁律 7（缝命名空间）
- `lingclaude/core/seam.py:202-212` register 无强制
- `lingclaude/core/plugin_manifest.py:32` 反向 regex
- `lingclaude/core/manifest_lock.py:49` 仅字段存在性
- `tests/agents/test_lc_mcp_guard.py:96-100` 测试断言

### 铁律 8（故障域 + 操作域 + 时效域）
- `lingclaude/plugins/agents/work_claim.py:36-113` 五原语
- `scripts/worktree_node.py:64-103` 联动
- `tests/agents/conftest.py:28-29` J5 反例（静默吞错）
- `tests/agents/test_work_claim.py` 8 项
- `tests/agents/test_worktree_node.py` 8 项
- `data/arch_ledger/work_claim/` 9 条（**核实后：全部 expires_at 过期 3 天**）

### 实测分布（SeamType 11 类）
- TOOL：35 个（`engine/tool_registration.py:31-347`）
- PROVIDER：≥ 9 个（`model/provider_registry.py:135-187` + `webui_seam.py:121-123` + `plugins/agents/cap_infer/plugin.py:282`）
- SANDBOX：5 个（`engine/sandbox_provider.py:385-422`）
- AGENT：24 个带域（`plugins/agents/*/plugin.py`）
- MULTIMODAL：≥ 1 个（`seams/multimodal_lingtong.py:211`）
- ORCHESTRATOR：1 个（`seams/research_crew.py:300`）
- RESOURCE：≥ 2 个（`plugins/agents/os_resource/plugin.py:196` + `plugins/agents/cap_inspect/plugin.py:192`）
- TRANSPORT / MEMORY / GOVERNANCE / SELF_OPT：**生产代码入册数 = 0**

### 守卫件套实跑状态（核实后）

| 守卫 | 真跑状态 | CI 接入 |
|------|---------|---------|
| M1 概念清白度 | ✅ 真跑 | ✅ 必过 |
| M2 特判禁令 | ✅ 真跑 | ✅ 必过 |
| M3 依赖方向契约 | ✅ 真跑 | ✅ 必过 |
| M4 换域测试 | ⚠️ 原语级非套件级 | ✅ 必过（强度欠账） |
| M5 截肢测试 | ✅ 真跑 | ✅ 必过 |
| M6 接缝生态趋势仪表 | ⚠️ 从未自动跑过 | ❌ 无 CI step |
| N1 互账完备 | ⚠️ 在册未实现 | ❌ 无自动化 |
| N2 信任声明 | ✅ 真跑 | ✅ 必过 |
| N3 命名空间互证 | ✅ 真跑 | ✅ 必过（但工具反向） |
| N4 三查 | ⚠️ 部分（缺席+互斥真建，时效查被动有测） | ✅ 必过 |
| N5 契约漂移侦测 | ❌ 真零落地 | ❌ 无 |
| N6 口径成本互证 | ❌ 完全没建 | ❌ 无（已归档参考资料） |
| N7 横向耦合禁令 | ✅ 真跑 | ✅ 必过 |

---

## 九、整改优先级汇总

| P 级 | 编号 | 标题 | 预估工时 |
|------|------|------|----------|
| **P0 #1** | N5 契约漂移守卫 | 真零落地 | 3-5 天 |
| **P0 #2** | N4 时效查 | 被动 + 无清理循环 + J5 反例代码 | 1-2 天 |
| **P0 #3** | N3 域前缀 | 工具违反自身铁律 | 0.5-1 天 |
| **P0 #4** | N1 周期对账 | 在册未实现 | 2-3 天 |
| **P0 #5** | M6 双口径 | 从未自动跑过 | 2-3 天 |
| **P1 #6** | J4 私连存储 | 真实违例 2 处 + 文档 stale | 3-5 天 |
| **P3** | M4 换域测试强度 | 长期挂账态 | 5-7 天 |

**P0 总工时**：8.5-14 天（**建议分两批**——P0 #2 + P0 #3 一批（2-3 天），P0 #1 + #4 + #5 一批（7-11 天））

---

## 十、文档维护说明

**本报告生成方式**：分两轮——第一轮 5 路并行侦察（架构骨架 / SeamType / work_claim / 守卫件套 / 与其它 agent 差异），第二轮 7 路并行核实（针对第一轮"基线陈述"做证据级深抠）。

**核心理念**：诚实报告，"文档承诺 vs 代码实情"的偏差**全部列出**，附 file:line 锚点。

**与铁律文档的关系**：本报告是 `docs/LINGYUAN_IRON_LAW.md` 的**自审快照**，不是铁律修订建议。铁律本体变更需用户亲自裁决。

**数据快照时间**：2026-09-23（head=8499089，`arch_audit_state/default.json` checked=`2026-09-23T07:43:52+00:00`）。

**N5 漂移提醒**：引用本报告数字时须附 `as_of=2026-09-23`，且引用 stale 值须先重验（参考铁律 J5 四条件之 4 + N5 契约漂移条款）。

---

## 十一、lingclaude 定位讨论（独立章节，2026-09-23 多轮对话沉淀）

> 本章节独立于前 10 章的"自审 + 欠账"主轴，记录多轮对话中反复打磨的定位讨论。
> 立场：从"lingclaude 与其它 coding agent 的差异"出发，给不同受众出不同版本的定位语、独家故事、推荐表述。
> 与主轴的关系：前 10 章是"lingclaude 现状"（含诚实欠账），本章是"lingclaude 该如何对外讲"——后者必须以前者为前提。

### 11.1 定位讨论的方法学

整个定位讨论严格遵循两条**底线纪律**：

1. **以核实后的事实为前提**——所有定位语都建立在 §五"对外故事修正清单"基础上，不掩盖欠账
2. **三个故事都有代码/台账/commit 锚点**——不是 PPT 故事，是"每个断言都有 file:line 可指证"

任何忽略 §五修正清单直接复述侦察阶段措辞的版本都视为"未审版本"，对外传播禁止使用。

### 11.2 lingclaude 与其它 coding agent / Harness 的差异（再述，前置于定位）

> 详见 §六"差异维度矩阵"，本节为定位讨论的前置摘要。

**lingclaude 与 cc / codex / opencode / crush 的核心差异**（按抽象层分）：

| 抽象层 | cc / codex / opencode | lingclaude |
|--------|----------------------|------------|
| **调用层**（prompt + tool call + context window） | 这是它们的核心抽象 | 也做，但不只做这个 |
| **架构宪法层**（SeamType + J5 四条件 + 修剪语法 + 联邦互认） | 无 | **核心抽象** |
| **状态原语层**（StateStore 三原语 + arch_ledger 台账 + 可机械审计） | 无 | 独有 |
| **修剪纪律层**（debt record + 接缝回收 + 误差事件入账） | 无 | 独有 |
| **联邦互认层**（federation_pair + 信任三级 + 命名空间） | 无 | 独有（**运行未实现**） |
| **多 Agent 协作层** | Task tool（单 agent 单体） | Subagent 三后端 + 20 家族 + LingBus + 投机扇出 |

**关键判断**（核实后）：
> **Claude Code 做的是"agent"，lingclaude 做的是"agent 工作所在的架构本身"——前者是应用，后者是内核 + 应用 + 修剪 + 联邦四件套。**

### 11.3 候选定位语（按受众分五个版本）

> 五个版本都不是"完美定位"——是**按受众偏好挑一个**。
> 任何版本对外传播前必须检查 §五修正清单：哪些故事被收回、哪些故事措辞已改。

#### 版本 A：架构派（架构师 / CTO 受众）

> **Lingclaude：给 AI 时代的软件演化立宪法。**

**适用场景**：CTO 评审、技术大会架构分会、对标 Hexagonal Architecture / Clean Architecture / DO D 的对话场景。

**底层逻辑**：lingclaude 不只是"工程实现"，而是"软件演化协议"。所有变化都被收成 record，所有操作只许 create / transition / query，所有能力走接缝入册——这与 Hexagonal 的"端口适配器"、Clean Architecture 的"同心圆分层"、DO D 的"数据是第一位"同构，但更极端。

**风险点**：架构派受众会深抠 J5 四条件、M 件套层级语义——必须准备好被问"修剪行为真实发生次数 = 0"这类尖锐问题（见 §四 P3）。

#### 版本 B：可验证派（平台工程师 / DevOps 受众）

> **Lingclaude：Agent 可机械审计的代码宿主。**

**适用场景**：平台选型、SRE 评审、对标 K8s CRD / Operator 的对话场景。

**底层逻辑**：arch_ledger 249 条台账（134 record + 113 豁免 + 2 参考资料 .md）+ StateStore 三原语 + work_claim record-as-lock——一切都外化为可查询结构，**守卫即 query**。Agent 想知道架构有没有违规，自己查台账就行。

**风险点**：可验证派受众会深抠"哪些守卫真跑"——必须如实说"CI 真跑只有 6 条：M1/M2/M3/M5/N2/N7"（见 §四 P0 #1-#5），不能模糊为"八条铁律全部在 CI 跑"。

#### 版本 C：修剪派（技术债管理者 / 长期项目 owner 受众）

> **Lingclaude：唯一带修剪语法的 coding agent harness。**

**适用场景**：长期项目治理、技术债复盘、对标 Atlassian / McKinsey tech debt radar 的对话场景。

**底层逻辑**：临时直连入账、到点不清守卫即红、接缝回收审查闭环、首例 TOOL "留任"结案——这是**完整的接缝生命周期司法程序**，且不预设结论（见 §二铁律 4 + §五故事 1 修正版）。

**风险点**：修剪派受众会深抠"首例实际回收行为 = 0"——必须如实说"程序可执行性已证，实际修剪仍有待首例"（见 §二铁律 4 + §五故事 1 修正版）。**不要**说"M6 仪表自动发现盲区"——这是**审计员手动跑 `PluginLoader.load_plugins_from_dir` 全载发现 26 实现**（见 §四 P0 #5）。

#### 版本 D：联邦派（多 Agent / 跨系统集成者受众）

> **Lingclaude：中立插片协议 + 双向对偶记账。**

**适用场景**：跨系统集成、Harness 间互认、行业命名战争期公共缺口讨论（对标 Codex App Server / Hermes WebUI / Omarchy 的对话场景）。

**底层逻辑**：federation_pair 双 record + 信任三级（T1/T2/T3）+ 命名空间约定（`{ns}/{seam}`）——灵元 2T3A + SeamType 想当中立的"插片互认协议"（见铁律 §六 候选 7）。给 Harness 们发统一的"插片证"，不绑定任何主干。

**风险点**：联邦派受众会深抠"双向记账是否真在跑"——必须如实说"2 条 JSON 是 git commit 手工提交，六态机零编码，drift/broken 永远不会被触发"（见 §四 P0 #4）。**不要**说"lingxi 是双向互认实证"——这只是 MCP 协议接入（见 §五故事 2 修正版）。

#### 版本 E：集成派（投资人 / 战略层受众）

> **Lingclaude：agent 时代的 OS（状态机 + 接缝 + 修剪 + 联邦）。**

**适用场景**：投资人 pitch、战略层汇报、对标"Linux + systemd + journald" 的对话场景（lingclaude 仓内 `20260923_harness_comparison_v3.md` §六已用此措辞）。

**底层逻辑**：单 agent 编码能力（vs opencode 7.5M MAU 公认不如），但**编队 + 治理 + 跨进程通信 + 审计台账 + 沙箱解耦 + 5 层模型路由 + 公开铁律/SDK/HTTP API/MCP** 形成独家护城河。战略终局是"agent 社会的 Linux + systemd + journald"。

**风险点**：投资人受众会抠数字——必须如实说"剩余两个数字差距：循环纯度 586 vs 120 行、NanoJev ECE 0.246 vs 0.081，**都是数字问题不是设计问题**"（见 §六末"自我对账结论"）。

### 11.4 推荐定位语（战略层 / 工程层 / 联邦层三套，按受众选）

**战略层**（投资人 / CTO 用）：

> **Lingclaude：编码 agent 社会的 Linux + systemd + journald——比 cc/codex/opencode 强在编队 + 治理 + 跨进程通信 + 审计台账 + 沙箱解耦 + 5 层模型路由 + 公开铁律/SDK/HTTP API/MCP。**

**工程层**（架构师 / DevOps 用）：

> **Lingclaude：第一个把"架构宪法"落地的 coding agent harness——M1/M2/M3/M5/N2/N7 这 6 条守卫在 CI 真跑（其余 M4/M6/N1/N3/N4/N5 骨架搭好但有声明的局限或未真跑，N6 完全没建且已主动归档），work_claim 操作域 + TTL + 16 项测试全绿，arch_ledger 249 record 可机械审计；目标是中立插片协议 + 双向对偶记账 + 修剪语法三件套给整个行业发统一插片证。**

**联邦层**（多 Agent / 跨系统集成者用）：

> **Lingclaude：中立插片协议（铁律 7 域前缀）+ 双向对偶记账（铁律 5 federation_pair）+ 修剪语法（铁律 4 debt record）三件套——给整个行业发统一插片证。**

### 11.5 三个"独家故事"（每个都有代码/台账/commit 锚点）

#### 故事 1：修剪司法闭环（首例 TOOL 接缝回收审查）

**完整流程**（每个节点都有 commit/record 锚点）：

| 阶段 | 动作 | 锚点 |
|------|------|------|
| 候选标记 | M6 静态扫描标记 TOOL 接缝为单实现候选（注册点龄期 5 月+） | `scripts/seam_trend_inspect.py:53` |
| 立案 | `arch_audit_task/seamtype-specialcase-plugin-loader.json` | arch_audit_task 目录 |
| 取证 | M1-M5 + N1-N7 全套跑 | `tests/test_iron_law_guards.py` + `tests/test_p04_arch_guards.py` |
| 运行时实测 | **审计员手动跑 `PluginLoader.load_plugins_from_dir` 全载**（6 插件载体 + 20 工具代理经 `_ToolSeamProxy` 动态注册，TOOL 实测 26 实现） | `data/arch_ledger/arch_review/first-seam-recycling-review.json` 的 `evidence.runtime_count=26` |
| 裁定 | **留任**（扩展意图充分，不满足回收条件） | 同上 |
| 结案入册 | `data/arch_ledger/arch_review/first-seam-recycling-review.json` | arch_review 目录 |
| 修订史 | `data/arch_ledger/arch_law_revision/20260917-07.json` | arch_law_revision 目录 |
| 教训入档 | **M6 双口径未真接通**（静态=1 vs 运行时=26 偏差未自动化捕获） | `arch_audit_task/seamtype-specialcase-plugin-loader.json` due 2026-10-31 |

**故事力量**：一套**完整的接缝生命周期司法程序**，且首例闭环就是"留任"——证明程序不预设结论，按事实判。这是其它 harness 都没有的"修剪纪律"。

**对外讲述时务必包含的修正措辞**（§五故事 1 修正版）：
- ❌ "M6 自动发现盲区"
- ✅ "M6 静态扫描标记 + 审计员手动跑 PluginLoader 全载发现 26 实现"
- ✅ "副产品暴露 M6 双口径未真接通的事实（静态=1 vs 运行时=26 的偏差未自动化捕获，靠审计员临时动作才发现）"

#### 故事 2：work_claim record-as-lock（操作域 + 时效域）

**完整机制**（每个原语都有 file:line）：

| 原语 | 实现位置 | 真假 |
|------|---------|------|
| `bind` | `lingclaude/plugins/agents/work_claim.py:36-58` | 真实现 |
| `renew` | L60-73 | 真实现 |
| `release` | L75-89 | 真实现 |
| `holder` | L92-100 | 真实现（含 `expired` 字段） |
| `check_paths` | L102-113 | 真实现（**过滤过期锁**） |

**关键设计**：
- lock 不靠工具，靠 **record（StateStore 原语）**——任何第三方 query `work_claim/check_paths` 就能知道此刻谁锁着什么、何时到期
- **TTL 自动 expire 防永久死锁**（边界条款：时效是"失联兜底"不是常态）
- 守卫跑测试前查锁，被锁模块记 `skipped:claim-held by <member> (expires_at=<ts>)` 显式留痕

**联动机制**：
- `scripts/worktree_node.py:64-103` 节点 record 状态与锁 record 状态同生命周期
- 建节点先 bind，git 失败 release 回滚，destroy 前查脏工作区（Orca 合并噩梦的硬约束）

**实战案例**：
- **lingxi 5-failed 基线漂移**（`audit-work-claim-miss-tui-batch.json`，2026-09-17）——5 failed 中 3 个是对方半成品
- **TUI P0-P2 二次撞车**（`audit-work-claim-guard.json`，2026-09-20）——七文件直改无锁 + lineedit.py 与他人 in-flight 修改撞车 + M1 词汇表被并发编辑致两次扫描命中词漂移
- **解决方案**：搬移批次动刀前 bind 三域锁（`core//engine//cli/`, member=lingke, ttl=60min）

**16 项测试**（核实后确认完整）：
- `tests/agents/test_work_claim.py` 8 项（含 `test_expired_claim_auto_invalid` / `test_release_expired_by_third_party` / `test_check_paths_for_guard` 覆盖过期语义）
- `tests/agents/test_worktree_node.py` 8 项
- 合计 16 项 ✓ **完全对得上**（铁律 §一 8 §铁律 8 文档 §一 L153-154 数字精确匹配）

**故事力量**：**这解决了多 Agent 并行修改的"基线漂移"问题**——其它 harness 没有等价物。

**对外讲述时务必包含的诚实措辞**（§五故事 6 修正版）：
- ❌ "N4 三查全部建成"
- ✅ "N4 缺席查 + 互斥查 真建；时效查**被动有测 + 无主动通道 + 无清理循环**——9 条 JSON 锁全部 expires_at 过 3 天仍存留（state=released 但 timestamp 未清理）"
- ✅ "conftest.py:28-29 `except Exception: return` 是 J5 反例代码（注释自陈'不静默改判'，代码 bare return）"

#### 故事 3：manifest 四件套（一个 plugin 想入册必须回答四个问题）

**铁律 1 + 6 + 7 在 schema 层的强制落地**：

| 问题 | 字段 | 强制点 | 锚点 |
|------|------|--------|------|
| 你信任谁审计？ | `trust_level` (T1/T2/T3) | manifest 必填 | `core/manifest_lock.py:49` |
| 你拔了会怎样？ | `plug_level` (L1/L2/L3) | manifest 必填 + PLUG_LEVELS 11/11 入册 | 同上 + `core/seam.py:57-68` |
| 你跨物理层了吗？ | 域前缀 `agent/` `cap/` `proj/` `os/` `hw/` `core/` | N3 测试断言消费 + **SeamRegistry 无运行时强制 + plugin_manifest.py:32 反向 regex** | `tests/agents/test_lc_mcp_guard.py:98` + `seam.py:202-212` + `plugin_manifest.py:32` |
| 你内部还有子接缝吗？ | `stop_layer` 三件套（kernel + seams + implementations） | PluginManifest schema 必填 | `core/plugin_manifest.py` schema |

**实证样例**：

**`lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` 全套字段**（MCP 协议接入的 T1 实证）：
```json
{
  "name": "agent/lingxi",
  "trust_level": "T1",
  "plug_level": "L1",
  "transport": {"kind": "mcp", "mode": "stdio"},
  "stop_layer": {"kernel": "...", "seams": ["model_provider"], "implementations": 1},
  "state_record": "agent_run:lingxi",
  "health_probe": {"interval_s": 300, "absent_after": 2}
}
```

**`bash/manifest.plugin.json` 嵌套停层实证**：
```json
{
  "name": "bash_plugin",
  "type": "tool",
  "entry": "lingclaude/plugins/tools/bash/plugin.py:BashPlugin",
  "stop_layer": {
    "kernel": "bash 链执行",
    "sub_seams": {"seams": ["SandboxProvider"], "implementations": 2}
  },
  "test_entry": "必填"
}
```

**故事力量**：这是"接缝入册前校验"的正确形态——PluginLoader 加载前强制运行插件自带测试 + manifest schema FAIL-FAST 校验。其它 harness 的 plugin 入册远没这么严格。

**对外讲述时务必包含的诚实措辞**（§五故事 5 修正版）：
- ❌ "域前缀有运行时强制"
- ✅ "域前缀是约定 + N3 测试断言 + lingxi/cap 等家族插件 manifest 字段；SeamRegistry.register 无运行时强制（只校验非空），plugin_manifest.py:32 regex 与 N3 目标反向（不含 `/`），漏前缀插件可默默跑通"

### 11.6 一句话差异定位（精简版，可直接用）

> **Claude Code 是"最好的 agent CLI"，lingclaude 是"agent 工作所在的架构本身"——前者做应用，后者做内核 + 应用 + 修剪 + 联邦四件套。**

### 11.7 定位讨论中反复打磨的几个判断

#### 判断 1：lingclaude 的护城河在"修剪语法 + 联邦可对账"，不在功能多

**功能层**（与 cc/codex 持平或略弱）：agent CLI、工具调用、沙箱、模型路由。
**护城河层**（与 cc/codex/opencode/crush 全无对位）：
- ✅ 接缝回收有完整程序（候选清单 → 立案 → 取证 → 裁定 → 结案 → 入档），且首例已闭环
- ✅ 多口径互证（静态 + 运行时 + 行为指纹）已机械化为 N1/N5/N6
- ✅ work_claim record + TTL + 查锁跳过留痕（其它 harness 几乎没有 lock-as-record 语义）

#### 判断 2：lingclaude 的"对 Agent 原生"不是口号，是机械可验的

- 一切真相外化为可查询结构（arch_ledger 249 条台账）
- 守卫即 query（`StateStore.list_keys/load` 是唯一入口，不再私连 .py/jsonl）
- 封闭动作集合（create/transition/query）≈ Agent 可靠操作集合

这意味着 lingclaude 不只是"agent 工具"，还是"agent 可机械审计的代码宿主"。

#### 判断 3：lingclaude 的差异化定位是"中立插片协议 + Agent-native 代码宪法"

| 维度 | vs cc/codex/opencode/crush |
|------|----------------------------|
| 工具层抽象 | 对等（都有） |
| 多 Agent 协作 | **lingclaude 独有**（Subagent 三后端 + 20 家族 + LingBus + 投机扇出） |
| 沙箱解耦 | **lingclaude 独有**（SandboxProvider Protocol + 5 实现 + 网络 allowlist） |
| 模型路由 | **lingclaude 独有**（5 层路由 + NanoJev 概率层） |
| 状态原语 | **lingclaude 独有**（StateStore 三原语 + arch_ledger） |
| 修剪语法 | **lingclaude 独有**（debt record + 接缝回收 + 误差事件入账） |
| 联邦互认 | **lingclaude 独有**（federation_pair + 信任三级 + 命名空间——**运行未实现**） |
| 对外接口 | **lingclaude 独有**（CLI + JSON-RPC app-server + Python SDK + 26 MCP 工具 + 26 HTTP 路由 + WebUI） |

**护城河三件套**（独创）：
1. **修剪语法**（自我减肥能力，业界方法论下沉到机械可执行）
2. **联邦对偶记账状态机**（跨物理层插片互认，**运行未实现**）
3. **Agent-native 机械可验**（封闭动作集合 ≈ Agent 可靠操作集合）

#### 判断 4：lingclaude 是"努力变薄"的反义——是"已经够薄（语义层）+ 持续长厚（接缝层）"

**薄的真实定义**（铁律 1 三重封闭）：
- 概念封闭：主干代码不出现业务词汇
- 知识封闭：主干不特判枚举值
- 依赖封闭：core/ 的传递 import 闭包 ⊆ {stdlib, 状态机原语依赖}

**主干 113 个文件不是"臃肿"**——可以全装状态原语 + 治理 + 工具执行原语，只要满足三重封闭性就够薄。lingclaude 是这种"瘦语义 + 长接缝"的健康形态。

#### 判断 5：剩余两个数字差距是"数字问题不是设计问题"

**vs atomcode/Pi**：
- 循环纯度 **586 vs ~120 行**（差 ~5x）

**vs Pi**：
- 引擎循环本体**不可热切**（plugin/TUI 两层蓝绿已成熟，引擎循环不行）

**vs opencode**：
- HTTP server 解耦 / 反思 / dream 进程**缺位**

**vs Laya System-1**：
- NanoJev ECE **0.246 vs 0.081**（SFT checkpoint 挂账，`arch_audit_task/spec_decision_sft_checkpoint_pending_20260923`）

**自我对账结论**（来自 `20260923_harness_comparison_v3.md`）：
> 09-21 列了 8 项弱点 →09-23 抽验关闭 7.5 项。当前真正剩余差距只有两个数字：**586 行循环体**和 **0.246 的 NanoJev ECE**——**都是数字问题，不是设计问题**。

**这是定位讨论的核心结论之一**——投资人/CTO 关心"还差什么"，答案是"差两个数字，不是差设计"，战略上可解。

### 11.8 对外传播"可讲 / 小心 / 老实说"清单

> 本清单按 §五修正清单整合，给对外传播者（路演 / 文档 / 社交媒体 / 投资人对话）做最后一道闸门。

#### 可讲（核实后无偏差）

- ✅ 11 类 SeamType × 双等级 + 域前缀 + 嵌套 stop_layer manifest 化
- ✅ StateStore 三原语 + 249 条 arch_ledger record 可机械审计
- ✅ work_claim 五原语 + 16 项测试全绿
- ✅ M1/M2/M3/M5/N2/N7 在 CI 真跑
- ✅ Subagent 三后端（inprocess + ACP + MCP）+ SubagentCapabilities 5 flag + 20 家族成员
- ✅ SandboxProvider Protocol + 5 实现 + 网络 allowlist（fail-closed）
- ✅ 5 层模型路由（task / behavior / intelligent / spec_decision / credential_pool）
- ✅ CLI 13 子命令 + JSON-RPC app-server + Python SDK + 26 MCP 工具 + 26 HTTP 路由 + WebUI
- ✅ arch_law_revision 修订史（铁律文本变更可查询）
- ✅ SDT-lc-006 自审触发器常态化
- ✅ J5 测量口径四条件（失败模式显式 / 多口径分歧即警 / 行为锚终审 / 误差事件入账）
- ✅ iron_law §四 修剪语法（debt record + 接缝回收 + 审计防博弈）

#### 小心（核实后措辞需调整）

- ⚠️ "修剪司法闭环"——必须说"审计员手动跑 PluginLoader 全载发现 26"而非"M6 自动发现"
- ⚠️ "M6 双口径已建"——必须说"骨架搭好从未自动跑过"
- ⚠️ "N1 双向记账已建"——必须说"2 条 JSON 手工提交，六态机零编码"
- ⚠️ "lingxi 是双向互认实证"——必须改口"lingxi 是 MCP 协议接入（T2 契约审计形态）"
- ⚠️ "J4 欠账 15 个"——必须改口"2 处真实违例 + 13 处受控豁免 due 2026-11-30"
- ⚠️ "八条铁律全部在 CI 跑"——必须改口"6 条真跑，其余骨架搭好"
- ⚠️ "N4 三查齐备"——必须改口"缺席+互斥真建，时效查被动有测无主动通道"
- ⚠️ "域前缀有运行时强制"——必须改口"约定 + 测试断言 + plugin_manifest.py:32 反向 regex"
- ⚠️ "OpenCode 4.7x token 优势"——🔴 已撤回，"Systima benchmark" 不存在

#### 必须老实说（核实后无可挽回的偏差）

- 🔴 N5 契约漂移守卫真零落地——铁律条文 + 参考资料在册，零脚本零测试零 debt
- 🔴 M6 仪表从未被自动跑过——`arch_m6_snapshot/` 目录被 datalog 鸠占鹊巢
- 🔴 N1 六态机零编码——drift/broken 永远不会被触发
- 🔴 plugin_manifest.py:32 regex 与 N3 目标反向——铁律工具本身违反铁律
- 🔴 conftest.py:28-29 `except Exception: return` 是 J5 反例代码
- 🔴 9 条 work_claim JSON 锁全部 expires_at 过 3 天仍存留——无清理循环
- 🔴 修剪行为真实发生次数 = 0（首例 TOOL 是留任非回收）

### 11.9 定位讨论的元结论

> **lingclaude 不应该被定位成"另一个 coding agent"——它是"编码 agent 工作所在的架构本身"。**
>
> 这一定位的两个推论：
>
> 1. **对标对象错了**——不要拿"agent CLI"维度对比 cc/codex/opencode/crush（会显得 lingclaude 弱）；要拿"架构宪法 + 修剪纪律 + 联邦互认"维度对比（lingclaude 强）
> 2. **护城河不在功能多**——功能可以追上（cc 也会加 Subagent）；护城河在"修剪 + 联邦 + Agent-native"三件套，是其它 harness 不会主动做的方向
>
> 风险提醒：**P0 五件事是真欠账**（M6 从未自动跑过 / N1 在册未实现 / N3 工具反向 / N4 时效查无主动通道 / N5 真零落地），不是"骨架搭好"的温和表述。任何对外传播都不能模糊掉这五件事——否则定位的诚实度归零。

---

*报告位置：`docs/audit/20260923_iron_law_self_audit.md`*
*数据快照：head=8499089 / 2026-09-23T07:43:52+00:00*

# 各 coding agent 横向比较·终版（10 家 harness 全景 + 扩展 5 家：cc/codex/opencode/crush/atomcode）

- 日期: 2026-09-23
- 作者: 灵克 (lingclaude)
- 性质: **09-23 三份草稿的合并终版**，取代以下三份（保留留档，不删）：
  - `20260923_coding_agent_horizontal_comparison.md`（WebSearch 修订版——业界动向有价值，但 lc 状态判断已与工作树脱节，多处"❌未动工"实为已落地）
  - `20260923_coding_agent_recompare.md`（工作树复核版——lc 侧断言本轮全部抽验通过，采纳为现状基准）
  - `20260923_harness_comparison_v2.md`（路线图对账版——"纵向三深"主题采纳）
- 前置: `20260920_peer_harness_borrowing.md`（十家精读）、`20260921_coding_agent_expansion.md`（初版+扩展五家）、`20260922_flywheel_closure_diagnosis.md`（飞轮诊断 F0-F4）、`docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md`
- 调研纪律: ✅实测(file:line/本轮抽验) / 🟡推测 / 🔴幻觉；对侧断言锚定 `/tmp/harness_read/` 09-21 快照

## 0. 一句话结论

09-21 版 8 项弱点 **48h 内关闭 7.5 项**（P0 三项 + P1 五项 + P2 五项全部落盘，git 实证）。
竞争焦点已从「横向补机制债」切换为「纵向三深」：**给基建通电（门禁转正+dogfooding）、
给缓存装表（命中率遥测+单调契约）、给循环卸重（状态外置→纯度收尾→引擎热更）**。
战略判断不变：B 路线（Agent 编队操作系统）用 C（治理中间件）方式落地，且 fan_out +
agent-gateway + worktree 三件套已让 B 具备物理雏形。

## 1. 三份草稿冲突裁定

| 冲突点 | horizontal 版 | recompare/v2 版 | 裁定 |
|---|---|---|---|
| LSP / headless / 大结果瘦身 / 审批矩阵 / 不可变会话 | ❌ 未动工 | ✅ 已落地 | **recompare 正确**：本轮抽验 `lsp_provider.py`(25KB)/`app.py:885 --print`/`context_pruning.py`/`approval_matrix.py`(275行)/`rollout.py`(244行) 全部在位 |
| "lc token 开销 ~4 万/task，4.7x 落后 opencode" | 🔴 已自我撤回 | 未列入 | **撤回成立**："Systima benchmark" 身份未核 + 用户目测反证；正确动作是先内部 datalog 实证再定论 |
| PenguinHarness "GOAL.yaml" | 🔴 修正：无此文件 | v2 仍写"GOAL.yaml 两值协议" | **horizontal 修正成立**（多源未提该文件）；两值协议概念 lc 已用 `core/goal_receipt.py` 自研落地，与对侧文件名脱钩 |
| SQLite 事实源 | 未列 | recompare P2.9 要做 / v2 说"未采纳" | **折中**：JSONL 保留为事件源（rollout 已 append-only），SQLite 仅作查询索引面（可选 P2），不做主存储迁移 |
| codex app-server / dream 进程 / 7.5M MAU 等 WebSearch 断言 | ✅/🟡 混合 | 未列 | **全部降为 🟡**：本环境网络受限无法复验，仅作趋势参考，不作为 P0/P1 依据 |

## 2. 15 家机制全景（压缩速览，对侧无变化不重述）

| 阵营 | 项目 | 核心机制 | lc 吸收状态（✅本轮抽验） |
|------|------|----------|--------------------------|
| 十家 | DSH | Everything-is-a-Plugin、dispose/reload、自省工具族 | ✅ `plugins/agents/cap_inspect` 在位 |
| 十家 | Pi | ~120 行纯函数循环、JSONL 树会话、双代热更 | ⚠️ 树会话进 `rollout.py`；循环纯度差 ~5x 仍在；TUI 双代已落（`full_tui.py:394-469`） |
| 十家 | Orca | worktree 扇出 + SQLite 事实源 | ✅ `core/worktree.py` + gateway 接线；SQLite 见 §1 折中 |
| 十家 | PenguinHarness | 目标两值协议（complete/blocked）、3-role RSI | ✅ `core/goal_receipt.py` 自研两值；3-role RSI 未采纳（见 §5 不采纳清单） |
| 十家 | oh-my-hermes | 证据边界协议 | ✅ `core/evidence_protocol.py`(14.7KB) |
| 十家 | codex-host | 保真投影 | ❌ 不采纳（lc 无宿主投影需求）→ 留档 |
| 十家 | agent-harness | manifest 单源 + lock provenance | ✅ `core/manifest_lock.py` |
| 十家 | hermes-webui | 零构建 WebUI | ❌ 不采纳 → 留档 |
| 十家 | hermes-studio | credential_pool 多账号 LRU 轮转 | ✅ `model/credential_pool.py` 在位，⚠️ env 门禁**默认关** |
| 十家 | omarchy | usage 收集器契约化、懒加载存根 | ❌ 不采纳 → 留档 |
| 扩展 | cc | 缓存边界行、三层权限、exit-2 回灌、mods 中间件 | ✅ 边界行 + `tests/test_prefix_cache_boundary.py` CI 断言 |
| 扩展 | codex | rollout JSONL+fork、审批矩阵、Starlark 资产化、大结果瘦身 | ✅✅✅✅ 四项全对齐（`rollout.py`/`approval_matrix.py:224-259` 资产化/`context_pruning.py`） |
| 扩展 | opencode | Effect 核、headless `--print`、provider 多源、`/share` | ✅ headless（`app.py:885`+`repl_turn.py:186`，与交互模式共享同一循环实例）；`/share` 未做 |
| 扩展 | crush | LSP 原生、`/model` 切 provider 保历史 | ✅ LSP 三件套（provider/session/registry）；⚠️ 跨 provider 语义保留未实测 |
| 扩展 | atomcode | cache_epoch 单调 stub、~120 行纯函数循环、turn_start 快照 | ✅ turn_start 快照（b99921c）；⚠️ cache_epoch 单调契约未做 |

## 3. lc 现状对账（09-21 八弱点，✅=本轮抽验通过）

| # | 弱点(09-21) | 状态 | 证据锚点 |
|---|---|---|---|
| 1 | 循环重主干 ~3000 行 | **大部分关闭** | `loop_body.py` 586 行 + `hooks.py` 227 行 seam（可注入 fake）+ `thread.py` 107 行薄壳 facade。⚠️ 残余：586 vs 120 纯度差 ~5x；引擎循环本体不可热切 |
| 2 | 会话不可变+fork 缺失 | **关闭(后端)** | `rollout.py:159/192` fork/revert 写新文件记 `forked_from_id`，原件不删。⚠️ 无 `/fork` `/revert` `/share` 用户面 |
| 3 | 审批未矩阵化 | **关闭** | `approval_matrix.py` SandboxMode×ApprovalPolicy 正交 + granular 关=auto-reject；Shift+Tab 四态环+mtime 热加载（513c505） |
| 4 | 批准后无资产化 | **关闭** | `approval_matrix.py:224-259` allow 前缀写回 approvals.json 热更 |
| 5 | provider 切换不保会话 | **基本关闭** | `/model <name>@<provider>` 钉住+`--ttl`（commands.py:326-333）。⚠️ 跨 provider 差量适配未实测 |
| 6 | 无 headless | **关闭** | `run --print/--json` + JSON-RPC 常驻面；headless 与交互共享同一循环实例（seam 化直接收益） |
| 7 | 大结果未瘦身 | **关闭** | `loop_body.py:47` 阈值瘦身（摘要+引用，codex 语义） |
| 8 | LSP 缺位 | **关闭** | `lsp_provider.py`（stdio JSON-RPC 六方法）+ `lsp_session.py` 常驻池 + `lsp_registry.py` `/lsp add` |

**超出 15 家的独有能力**（新增）：投机扇出调度（`fan_out_scheduler.py`，15 家全无）、
飞轮闭环 f1/f2/f3 全 commit（2ba7571/da08bfc/cdec8d9）、NanoJev 契约消费层
（spec_decision 三原语 + 消费点①②③④⑧⑨已铺，⑩风险门控 `risk_gate.py` WIP 未提交）、
权限模式跨进程热加载。

**当前 WIP**（git status 实证，未提交）：`risk_gate.py`（新增，消费点⑩）、
`coding.py`(+61 行 _risk_guard 接线)、`loop_body.py`、`model_call.py`、`state_store_ext.py`。

## 4. 仍落后点（实证，按差距排序）

| # | 差距 | 领先者 | 实况 |
|---|------|--------|------|
| 1 | 循环纯度 | atomcode/Pi(~120行) | 586 行；治理逻辑仍内联，58 wiring 槽位未外置 |
| 2 | 引擎级热更 | Pi（循环双代） | plugin/TUI 两层蓝绿已成熟，引擎循环本体不可热切——09-21 §3.1 裁定的唯一真依赖项 |
| 3 | fork/revert 无用户面 | codex/opencode(`/share`) | 后端全在，缺三条命令（半天工作量） |
| 4 | 沙箱网络维 | cc（Bash 网络 allowlist） | 沙箱只有模式 env，无网络 allowlist |
| 5 | **门禁堆积（本轮新治理债）** | — | credential_pool/fast lane/worktree/投机扇出均 env 门禁**默认关**——机制落了生产路径未吃 |
| 6 | 跨 provider 切换适配 | crush | pin 保历史已通，system prompt/工具集差量未实测 |
| 7 | 引擎本体规模 | 全部 | core/+engine/ 仍是 15 家最大单体，薄主干靠守卫维持而非结构保证 |

## 5. lc 今后优化方向（终版重排——本文核心产出）

> 主题切换：**转正（门禁吃透）→ 纯化（循环卸重）→ 开放（族协议标准化）**。
> 与三份先行文档的优先级冲突已裁定：9-21 基线 P0 四件已全落地（不再是 P0）；
> JEV「P0-0 循环纯化唯一真 P0」降格为 P1 主线（hooks seam 化已完成第 0 步，
> 纯度收尾依赖状态外置，非一周可竟）；recompare/v2 的「通电」升为本轮 P0 主题——
> **机制落了不吃等于没落，默认关堆积是当前最大治理债**。

### P0（一周）——通电：已落机制转正吃透，不开新机制

1. **门禁转正评审**：credential_pool / fast lane / worktree / 投机扇出四项逐一实机收益审计，达标转正（默认开）或挂账拆除（对照 7c19a30 fast lane 挂账模板）。
2. **飞轮收益审计首报**：f1/f2/f3 已落地，下一步不是新机制而是验证自进化真赚钱——ExperimentLedger 归因链数据回流，出第一份「自优化收益周报」（哪些行为阈值改动带来可测收益/回滚）。这是飞轮诊断 F4 的兑现点。
3. **fork/revert//share 用户面**：后端全在，补 `/fork <tag>` `/revert <ordinal>` `/share` 三条命令关闭差距#3（半天，收益直接可见）。
4. **headless 自举 dogfooding**：用 `lc run --print` 把自身守卫/回归套件接入 CI 批处理——lc 用 lc，是对 headless 最狠的实测。
5. **审批收敛曲线观测**：granular auto-reject + always_allow 资产化的规则增长曲线入库（多久收敛到"不再弹窗"）——codex 精髓的验收指标。
6. **WIP 收口**：`risk_gate.py`（消费点⑩）+ coding.py/loop_body.py/model_call.py 未提交改动，验证后入库（不留未验证半成品过夜，R6）。

### P1（两周）——纯化：循环卸重 + 缓存装表

7. **循环状态外置**：58 个 wiring 槽位迁 StateStore 唯一事实源（0922 opencode delta 文档已定序：飞轮 3 切片→Store→瘦身），loop_body 586→~200 行，只留 `provider.complete → tool_exec → check_stop` 骨架；验收=注入 fake hooks 全离线回归。**同时是 fork/回放/影子评估的地基**。
8. **引擎循环双代热更**：依赖 7 完成；plugin/TUI 两层蓝绿语义直接复用到 loop 代际切换——关闭 15 家对比最后一项「真落后」。
9. **cache_epoch 单调契约 + 命中率遥测**：cc 边界行只保 system prompt 前缀；atomcode 保证 compaction stub 单调、每轮至多尾部破缓存一次。CI 断言从"字节不变"升级为"命中可测"（与 0922 cache pct 修复线闭环）。
10. **cc 式沙箱网络 allowlist**：补 Bash 网络维约束，与审批矩阵 sandbox_mode 正交接线。
11. **跨 provider 切换实测断言**：切 provider 前后 messages 语义等价一条测试，把弱点#6 关死。
12. **token 效率内部基准**：用 datalog cost 字段（in/out/cached，d3e51d1 已埋点）出 lc 自己的 tokens/task 基线——**先测量再优化**，不学 opencode 4.7x 的数字结论（证据已降级）。

### P2（一个月）——开放：B 路线物理化

13. **族协议标准化**：proj_agent_gateway 已能 headless 调度 opencode/crush/atomcode——把跨 harness 的「消息/回执（GOAL 两值）/审计（rollout 事件）」三格式抽成公开协议草案，让族员不只有自家 harness。
14. **治理层可挂载输出**：lc 治理层（审批矩阵/配额/台账/风险门控）作为中间件输出给族员 harness（codex-host 保真投影思路）——C 路线具体形态，反哺 B。
15. **SQLite 查询面**（可选，§1 折中）：rollout JSONL 保持事件源，SQLite 仅作会话/任务/账本合一查询索引，为双端铺路。
16. **趋势观察项（🟡，不投入实现）**：codex app-server JSON-RPC 默认化、Anthropic 式反思/dream 进程、opencode license 争议——网络受限无法复验，列入 09-30 复测清单，仅在有内部需求证据时启动。

### 不采纳·显式留档（避免下轮对比重新起疑）

- PenguinHarness 3-role RSI：lc 飞轮 f1/f2/f3 已覆盖其功能面，不引入三角色框架
- codex-host 宿主投影 / hermes-webui 零构建 / omarchy 懒加载：与 lc 定位不符
- hermes-studio 功能大爆炸：与薄主干纪律冲突
- opencode 4.7x token 数字结论：证据强度不足，只学工程方向（工具精简/LSP/函数式编排）

## 6. 战略路线（维持 09-21 §3.3，一处修订）

| 路线 | 判断 |
|---|---|
| A. 更好的 coding agent | 红海，权重维持下调 |
| B. Agent 编队操作系统 | **维持主判**；物理基础本轮已现（fan_out+gateway+worktree 三件套） |
| C. 治理中间件 | 维持"用 C 的方式落地 B"；治理层独立可挂载 = P2.14 |

生态位不变：**比谁能让多个 Agent 像团队一样工作 + 像系统一样自我维护**。

## 7. 证据分级（本轮抽验记录）

| 论断 | 等级 | 证据 |
|---|---|---|
| §3 八弱点状态 + §4 落后点 | ✅ 实测(本轮) | file:line 锚点见表；rollout/approval_matrix/hooks/thread/lsp/headless/fork 全部抽验通过 |
| f1/f2/f3/spec_decision/fan_out commit 链 | ✅ 实测(本轮) | `git log` c3102e1..27cb0be 逐项在 |
| 门禁默认关堆积 | ✅ 实测(本轮) | recompare 锚点 factory.py:62/server.py:423 + v2 旁证 |
| PenguinHarness 无 GOAL.yaml | 🟡 推测 | 多源二手未提该文件，本环境无法复验 |
| codex app-server 默认化 / opencode MAU / dream 进程 | 🟡 推测 | WebSearch 二手，沙箱网络受限未复验 |
| opencode 4.7x token / "lc 4 万 token/task" | 🔴 幻觉(已撤回) | horizontal 版已自我纠错，本版不采信 |

## 8. 关联文档与下次修订

- 三份草稿（留档）：`20260923_coding_agent_horizontal_comparison.md` / `20260923_coding_agent_recompare.md` / `20260923_harness_comparison_v2.md`
- 09-30 复测 TODO：opencode token 基准身份核验；codex app-server 趋势；P0 六项转正/收口结果对账；cache 命中率遥测首组数据

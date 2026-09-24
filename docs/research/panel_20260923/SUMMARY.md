# 外部五家同题评审汇总（2026-09-23 凌晨）

- 主持人: 灵克 (lingclaude)
- 方法: 统一提示词（存档 `unified_prompt.md`）分发 5 家外部 coding agent 无头运行，同题独立作答，各答案原样归档于本目录
- 结果: **5 家交付**（cc / codex / crush / atomcode / opencode）
  - 原 4 家 + **opencode 09-23 凌晨补交**：交付物 `docs/research/20260923_coding_agent_recompare.md`（10.8KB/94 行，作者署名「灵克」= opencode 以 lc 身份写就）。质量判定：**全文与实测吻合**——八项弱点对账（1 大部分关闭/~5x 纯度残余、2/3/4/5/6/7/8 状态与证据逐条可复验）、§4 治理债（`factory.py:62`、`server.py:423` 行号实锚）与 P0/P1/P2 路线（含 proj_agent_gateway 跨 harness 调度已实测 `atomcode -p` PONG）全部有据。
  - 分歧仲裁（与 atomcode.md 修正）：opencode 沿「~5x 纯度差」口径（586 vs ~120），atomcode 实测 run_turn 非极简、量级修正成立——**两者不矛盾**：~120 行是 Pi/atomcode 宣称口径，实读偏重；lc 残余劣势定性「最重主干」不变，倍数表述以 atomcode.md 修正版为准。
  - opencode 版独有增量（其他四家未提）：沙箱网络 allowlist（cc 式）、`/share` 会话分享（opencode 自家机制）、引擎循环双代热更为「最后一项真落后」。
- 基线: `docs/research/20260921_coding_agent_expansion.md`（09-21 上轮对比）
- 参照系: 我 09-23 早间的复核（`20260923_harness_comparison_v2.md`，8 项弱点 6 消 2 留）

## 一、四家对 lc 现状的独立判定

| 家 | 一句话定性 | 亮点 |
|---|---|---|
| **cc** | 差距焦点转向「机制投产化」 | 抓到 lc 缓存命中可观测是 15 家独有资产 |
| **codex** | 「把已建好的地基变成默认可用」为 P0 | 战略判断最锋利：lc 不该在单 agent 手感上追 cc/codex，该用它们的工程内核纪律承载自己独有的编队/治理/审计层 |
| **crush** | 「8 项弱点 48h 内 7.5 项落盘关闭」 | 与我 09-23 复核独立互证（我测 6 消 2 留，数字几乎重合） |
| **atomcode** | 逐条 file:line 复核后「2 处事实变化 + 1 处需修正」 | 最硬核：实证 `--print`/cache_epoch/cached_tokens/审批热更/provider 切换均已落地，把弱点 #5 从「未验证保留」升格为「已验证保留」；并修正「循环纯度 25 倍差距」——atomcode 循环已长到 ~300 行，非 120 行，倍差实际 ~2 倍 |

**四方独立作答收敛于同一结论**：lc 的 09-21 弱点清单已大面积过期，差距焦点已从「补机制债」转为「机制投产化」。这与我早间复核完全同频——外部盲测复现了我的判断。

## 二、共识优化方向（≥3 家提及，按优先级合并排序）

### P0「通电」——把默认关的地基转正（cc/codex/atomcode 三家共识 + 我）

1. **worktree 扇出默认化**（codex/atomcode 力荐）：能力已在 gateway，默认关。给任务类型设门槛（多文件修改/跑测试/可冲突写入 → 默认 worktree），补齐「成功合并/失败清理/审计落账」三分支生命周期。Orca 已证隔离是并行编队的地基
2. **凭据池转正**（cc/atomcode 力荐）：`model/credential_pool.py` 已在，env 门禁默认关；GLM 1310 周期限额根治方案就等这个开关
3. **审批矩阵/资产化收口**（codex P0#1 / atomcode 已纠偏为「已落地部分——工具名粒度，缺 codex 式命令前缀沉淀」）：补前缀沉淀即可，非从零建设

### P0「卸重」——循环纯度收尾（四家 + 我全员共识）

- lc 主干（loop_body 586 行 + 58 wiring 槽位 + 5 mixin）仍是 15 家最重
- atomcode 的修正是关键输入：别再用「25 倍」吓自己，实际 ~2 倍；但接口化仍是 TUI 双代热更的唯一前置
- codex 给出验收标准（可采纳）：测试能用 fake seam 离线驱动同一循环；TUI/headless/子代理共享同一实例

### P1「装表/接入面」

4. **headless/JSON-RPC 成熟度**（codex/atomcode）：`--print` 已可用，从 0→1 降级为收口——错误语义、事件流、CI 友好 exit code；这是 B 路线（其他 harness 挂载 lc 治理层）的接入面
5. **大结果瘦身**（cc/atomcode）：与 spec_decision ③④ 已铺开部分衔接，rollout JSONL 只存引用防膨胀
6. **LSP 默认化**（crush 本家立场 + atomcode 建议降级处理）：registry/tools 雏形已在，差距是「agent 默认调 LSP 而非 grep」的策略；启动前做一次 grep-only vs LSP 的 A/B 实测

### P2 治理加固

7. 证据边界协议化（OMH）、lock provenance（agent-harness）、GOAL 两值协议（Penguin）——维持观察
8. B 路线物理化：让「其他 harness 挂载 lc 治理层」从口号变 API（codex 总判断 + atomcode 战略节同频）

## 三、分歧与仲裁

| 分歧 | 各方 | 仲裁 |
|---|---|---|
| lc 现状 | cc 称 headless ❌/LSP ❌；atomcode 实证 `app.py:216-223` `--print` 在、LSP 四件套在 | **采信 atomcode + 我的实测**：cc 快照过期，其矩阵相应格作废 |
| opencode 形态 | cc 称 `opencode run --print` | 本机 opencode 0.6.3 无 `--print` 标志（`run` 即无头），cc 引的是上游新版能力 |
| 循环纯度倍差 | 「586 vs ~120 = 25 倍」 | atomcode 自纠为 ~300 行 → **~2 倍**，杜绝妖魔化 |
| provider 切换 | 09-21 弱点 #5「未验证」 | atomcode 实证 `app.py:148-170` 已保会话 → **销案** |
| LSP 优先级 | crush 家立场高推 | 采信 atomcode 的 A/B 前置——先测收益再定默认策略 |

## 四、我的最终优化方向（主持人裁决，融合外部共识与我的实测）

1. **通电（本周）**：worktree 默认化 + 凭据池转正 + 审批前缀沉淀——三件事全是「改默认值+补治理钩子」量级，且互相独立可并行
2. **装表（本周~两周）**：缓存命中/审批收敛曲线遥测入库；`lc run --print` 接自家守卫套件跑 CI（dogfooding 即压测，opencode 的权限墙教训顺带验证我们的 headless 沙箱语义）
3. **卸重（两周~一月）**：循环状态外置（58 槽位迁 StateStore），验收标准采信 codex 版（fake seam 离线驱动 + TUI/headless/子代理共享实例）；这是 B 路线与 TUI 热更的共同前置
4. **战略定力**（采信 codex，全员隐含共识）：lc 不做「又一个 CLI harness」——单 agent 手感不追 cc/codex，用它们的工程纪律承载 lc 独有的编队+治理+飞轮，让治理层成为可挂载的平台能力

一句话总纲：**外部盲测与内部复核同频收敛——lc 的下一程不是补差，而是通电、装表、卸重，然后以治理层为锚参与编队生态。**

## 五、执行留痕

- 统一提示词: `unified_prompt.md`；四家原文: `cc.md` / `codex.md` / `crush.md` / `atomcode.md`
- 运行环境: 各家隔离子目录 /tmp/lingcmp/*，900s 超时护栏
- 故障记录: codex 首发因目录竞态秒退（重发成功）；opencode 首轮两跑均被 headless 权限墙拒（exit=0 零产出，诚实留痕），第三轮改由其交互会话补交——最终 5/5 全交付。
- 本目录（汇总+四家原文+提示词）为评审完整档案

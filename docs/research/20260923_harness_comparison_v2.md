# 各 coding agent 横向比较 v2（15 家全景 + lc 路线图落地复核）

- 日期: 2026-09-23
- 作者: 灵克 (lingclaude)
- 前作: `20260921_coding_agent_expansion.md`（10+5 家机制 + 8 弱点 + P0/P1 路线图）
- 本篇增量: ① 快照源码复核仍在（/tmp/harness_read/ 14 仓）② 09-21→09-23 路线图落地逐项对账（git 实证）③ 弱点清单重排 ④ 新优化方向

## 一、15 家全景与 lc 吸收状态（09-23 快照）

| 家族 | 项目 | 核心机制 | lc 吸收状态 |
|---|---|---|---|
| DSH | deepseek harness | Everything-is-a-Plugin、dispose/reload、自省工具族 | ✅ `lc_plugins_inspect`/`cap_inspect` 插片落地（42858f0） |
| Pi | pi-mono | ~120 行纯函数循环、JSONL 树会话 | ⚠️ 树会话思想进 rollout.py；循环纯化仅钩子化（de1532e 第0步），25 倍纯度差仍在 |
| Orca | stablyai | worktree 隔离 + SQLite 事实源 | ✅ worktree 扇出 P1-6 接线（42858f0）；SQLite 事实源未采纳（lc 用 JSONL） |
| Penguin | PenguinHarness | GOAL.yaml 两值协议、错误分层 | 部分：熔断/重试语义散合于 loop_detector+hooks，无独立两值协议 |
| OMH | oh-my-hermes | 证据边界、repair card、混合路由 | 部分：runtime_observation 进 spec_decision 证据协议（9b5ed34 线）；repair card 未独立落地 |
| codex-host | 投影层 | 保真投影、CLI shim | 未采纳（lc 无宿主投影需求） |
| agent-harness | 配置单源化 | .harness/ 单源 + 漂移 CI | 未采纳（lc 插片体系已覆盖多面配置） |
| hermes-webui | 零构建 WebUI | 进程内直读、离线 cron | 未采纳 |
| hermes-studio | BFF 控制面 | credential_pool LRU 轮转 | ✅ P1-7 接线（factory.py:62,101，env 门禁**默认关**） |
| omarchy | 桌面环境 | agent 懒加载存根 | 未采纳 |
| cc | claude-code | 缓存边界行、三层权限、mods 中间件 | ✅ `__DYNAMIC_BOUNDARY__`（spb.py:45,144）+ CI 断言（test_prefix_cache_boundary.py） |
| codex | openai | rollout JSONL、审批矩阵、大结果瘦身 | ✅✅✅ rollout.py（forked_from_id+revert 原件不删）/ approval_matrix.py（2 维正交+granular auto-reject+批准资产化）/ context_pruning.py 两级瘦身（c3102e1） |
| opencode | sst | Effect 核、--print headless、/share | ✅ headless（app.py:219,885-886）；四件套蓝图另有 delta 文档（0922） |
| crush | charmbracelet | LSP 直查、/model 切换保历史 | ✅ LSP 四件套在位（lsp_provider/session/registry/tools）；⚠️ 切 provider 保历史仅 /model 名字刷新修复（repl.py:835），语义保留未验证 |
| atomcode | Rust | cache_epoch 单调 stub、纯函数循环、turn_start 快照 | ✅ turn_start 快照（b99921c 借鉴落地）；⚠️ cache_epoch 式"每轮至多尾部破缓存一次"未做 |

## 二、8 条弱点重排（09-21 → 09-23）

| # | 弱点(09-21) | 现状(09-23) | 证据 |
|---|---|---|---|
| 2 | 会话不可变+fork 缺失 | **✅ 已消** | `core/rollout.py:8-9` fork/revert 写新文件+forked_from_id，原件永不删改 |
| 3 | 审批未矩阵化 | **✅ 已消** | `core/approval_matrix.py` 2 维正交（沙箱×策略）+ granular=auto-reject |
| 4 | 批准后无资产化 | **✅ 已消** | always_allow 写回规则热更——本会话 09-22 已实测受益（mtime 热加载当场咬住） |
| 6 | 无 headless | **✅ 已消** | `cli/app.py:885` `lc run --print/--json` |
| 7 | 大结果未瘦身 | **✅ 已消** | `engine/context_pruning.py`（相关性剪枝>截断兜底，0 token，c3102e1） |
| 8 | LSP 缺位 | **✅ 已消** | `engine/lsp_{provider,session,registry}.py` + `tool_handlers/lsp_tools.py` |
| 5 | provider 切换不保会话 | **🟡 半消** | /model 名字刷新已修；历史语义保留未验证——待实测断言 |
| 1 | 循环重主干 | **🔴 仍在** | loop_body.py 586 行 + 58 wiring 槽位 vs atomcode/Pi ~120 行；第0步只做了钩子化 |

**路线图 P0(3)+P1(5) 全部 8 项落地**（de1532e = P0+第0步；42858f0 = P1 五连）。执行速度：两天内完成原计划"一周+两周"的量。

## 三、09-23 之后的新优化方向（按优先序）

> 原路线图的"补短板"阶段基本结束。新阶段的主题从**横向补差**转为**纵向三深**：

### A. 落地后的"通电"（一周内）
1. **凭据池通电**：P1-7 默认关（env 门禁）→ 配多账号实测 GLM 1310 周期限额规避，收集命中率数据——不通电的基建是负债不是资产。
2. **headless 自举**：用 `lc run --print` 把 lc 自身的守卫套件/回归套件接入 CI 批处理——lc 用 lc，既是 dogfooding 又是对 headless 的最狠实测。
3. **审批矩阵数据观察**：granular auto-reject 与 always_allow 资产化的规则增长曲线入库（多久收敛到"不再弹窗"），这是 codex 精髓的验收指标。

### B. 缓存治理二期（对标 atomcode cache_epoch，两周内）
4. **stub 单调性**：cc 式边界行只保 system prompt 前缀稳定；atomcode 的 cache_epoch 保证 compaction stub 单调、每轮至多尾部破缓存一次。lc 应把 compact stub 也纳入单调契约 + prefix-cache 命中率遥测（与 0922 的 cache pct 修复线闭环），CI 断言从"字节不变"升级为"命中可测"。

### C. 架构主债务（opencode 蓝图前置，一月内）
5. **循环状态外置**：弱点 #1 是唯一本质未动的主干债。opencode 四件套 delta 文档（0922）已给顺序：先飞轮 3 切片（2.5 天）→ StateStore 唯一事实源 → 循环体瘦身。58 个 wiring 槽位迁 Store 后，loop_body 才有从 586 行向纯函数演进的可能。**这一步同时是 fork/回放/影子评估的地基**。
6. **/model 切换保历史实测**：一条断言测试（切 provider 前后 messages 语义等价），把 🟡 关死。

### D. 吸收清单里明确"不采纳"并留档
 Penguin 两值协议、OMH repair card、codex-host 投影、webui/omarchy——要么已被插片体系覆盖，要么与 lc 定位不符。**不采纳也该是显式决策**，避免下轮对比重新起疑。

## 四、一句话结论

09-21 的对比让 lc 拿到了一张 8 项清单；09-23 复核：**6 项已消、P0+P1 八项全落地、两天干完两周的活**。下一个两周的对手不再是十五家 harness 的功能面，而是三件内部事：**给基建通电（凭据池/headless 自举）、给缓存装表（命中率遥测）、给循环卸重（状态外置）**。

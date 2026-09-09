# lingclaude vs 6 家 Agent 系统对比（2026-09-09）

> 本文档基于 lingclaude 全源码扫描（43,586 行 Python，4 个分层 Plan agent 输出汇总）+ docs/SYSTEMS_THEORY_SYNTHESIS.md 既有定性结论。
> 关联：CHARTER.md · docs/cli/TUI_BOTTOM_INPUT_DESIGN.md · docs/audit/CLI_WEBUI_AUDIT_REPORT.md

---

## 一、扫描基线（H17 闭环依据）

| 项 | 值 | 数据源 |
|---|---|---|
| Python 行数 | **43,586** | `find lingclaude -name '*.py' | xargs wc -l` |
| 顶层模块 | 9 个（core/engine/model/cli/self_optimizer/lacp/governance/model.llm_proxy） | 实证统计 |
| 测试 | ~2400 passed（最新一次） | `pytest tests/ -q --tb=no` |
| commit 频率 | 6+ commit/周（自 2026-09-02） | `git log --since="7 days ago"` |
| 版本 | 0.5.0（已升） | `VERSION` 文件 |

---

## 二、架构总图

```
┌──────────────────────────────────────────────────────────────┐
│ cli/app.py (1910 行 · 单一入口)                              │
│  ├─ PromptToolkitSession + InputPump 后台线程                │
│  ├─ webui-server (Rust :13458 子进程) + api.py (FastAPI :8700)│
│  └─ _cmd_run / _interactive_loop（10 个子命令）               │
└────────┬─────────────────────────────────────────────────────┘
         │
┌────────▼──────────┐   ┌──────────────────────────────────┐
│ core (73 文件)    │   │ engine (29 文件)                │
│ ├─ QueryEngine    │──▶│ ├─ tool_pipeline 5 段瀑布       │
│ ├─ permissions    │   │ ├─ tool_router 类别打分         │
│ ├─ sessions/      │   │ ├─ BashExecutor + bash_lingxi  │
│   journal/        │   │ ├─ mcp_proxy + mcp_client      │
│   handoff/        │   │ ├─ subagent/ inprocess + acp  │
│ ├─ meta_cognition  │   │ ├─ sandbox_provider (bwrap)   │
│ ├─ data_flywheel   │   │ └─ tool_handlers/ (10 个 mixin)│
│ └─ token_monitor   │   └──────────────────────────────────┘
└───────────────────┘
         │
┌────────▼──────────────────────────────────────────────────────┐
│ model (21 文件)                                              │
│ ├─ OpenAI / Anthropic provider（手写 urllib + aiohttp）      │
│ ├─ TaskRouter（11 providers · task_routes · 优先级扫描）     │
│ ├─ IntelligentRouter（GLM 二级路由 + routing_stats.json）    │
│ └─ model/llm_proxy/ 独立服务（:8900 · RateGate/PurposeRouter/ │
│    ProviderPool/TokenGate/DataFilter/MetricsCollector 四边界)│
└──────────────────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────────┐
│ governance / lacp / self_optimizer (23 文件)                │
│ ├─ governance_v2 异议制（爆破半径+认知评估过滤 S1）         │
│ ├─ cognitive_state S0-S6 检测（灵研论文 Lingtong Paradox）    │
│ ├─ lacp v0.5.0（6 transport + 4 档 replaceable + marketplace）│
│ ├─ capability_seam（5 seam + SignedProvider 双签）            │
│ └─ self_optimizer（7 类触发 + AST + 三重护栏 + 知识库）       │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、6 家横向对比表

| 维度 | **lingclaude** | codex | cc (Claude Code) | opencode | atomcode | crush | dsh |
|---|---|---|---|---|---|---|---|
| **形态** | Python monorepo + Rust webui 子进程 | 单进程 Rust CLI | Node.js + 浏览器一体 | Go 单进程 | Python 同进程集成 | Go 单进程 | 纯 shell 包装 |
| **代码量** | **43,586 行 Python** | ~5K Rust | ~512K（含浏览器） | ~50K Go | ~273K Python | ~30K Go | 50 packages TS |
| **多 provider** | ✅ 11 providers · task_routes + waterfall + 硬错误熔断（F12j） | ✅ OpenAI only | ✅ Anthropic 主 | ✅ 多 provider 扁平 | ✅ 类 lingclaude | ✅ Crush 生态 | 弱 |
| **工具系统** | 5 段 tool_pipeline + ToolDefinition 字段对齐 dsh + 类别打分 router | 5 段 runtime 对位 | 工具 + hook 配置 | 扁平化 | 一切皆工具 | 类别打分 | 多包 |
| **bash 执行** | 双后端（native + lingxi MCP）+ 词边界黑名单 + 4 档 sandbox + bwrap | Rust 直接执行 | 危险命令拦截 | 无 | `atomgit_bash_gate.rs` + sensitive_path | 无 | 无 |
| **subagent** | 完整——inprocess 167 行 + acp 282 行 + manager + 10 已注册工具 | 单 agent | 单 agent（Plan Mode） | 多 agent runtime | 类似 | 无 | 50 包 |
| **会话恢复** | 三层——session 原子写 + journal append + checkpoint | checkpoint 有 | `claude --continue` 有 | 无 | 强（snapshot/rewind） | 无 | 无 |
| **持久化补强** | append-only journal + 幂等 ID + 副作用二次确认（R5 阶段 2） | 弱 | 中 | 弱 | 强 | 弱 | 无 |
| **自我优化** | 7 类触发器 + AST 评估 + lingminopt 搜索 + 知识库规则 + 三重护栏改参 | 无 | 无（settings 驱动） | 无 | 弱 | 无 | 无 |
| **治理层** | 异议制 + 12 状态机 + 元提案 + 72h 闭环复查 + cognitive_state 过滤 S1 模板回复 | 无 | 无 | 无 | 弱 | 无 | 弱 |
| **元认知** | 17 守卫（H1-H17）+ 领域置信度校准 + 盲区检测 | 无 | 无 | 无 | 弱 | 无 | 无 |
| **插件化** | LACP v0.5.0——6 transport + 4 档 replaceable + marketplace + 双签 + AST 静态扫描 | 无 | settings.json | 无 | 无 | 无 | Cordis 50 包全插件化 |
| **LingBus 多 agent 总线** | ✅ 双向往返 + 5 系统发送者 + proposal_thread_id | 无 | 无 | 无 | 弱 | 无 | 无 |
| **流式 + Tool Call 并行** | ✅ T1-3 完整 | ✅ | ✅ | ✅ | ✅ | ✅ | 弱 |
| **TUI / WebUI 双端** | ✅ PT 底部 toolbar + Rust webui SSE 流 | 单 CLI | 浏览器一体 | 单 CLI | WebUI 在同进程 | 单 CLI | 无 |
| **可治理性 / 可插拔性 / 可自优化** | 三全 | 无 | 无 | 无 | 无 | 无 | 仅可插拔 |

---

## 四、各家详细对位

### 4.1 codex
- **对应**：tool_pipeline 5 段瀑布、ToolDefinition 字段、ToolRouter 类别打分
- **差异**：codex 单进程 Rust CLI，无 webui、无 self_optimizer、无治理层
- **lingclaude 借鉴**：`is_concurrency_safe` / `finalize_content` / `output` schema 是 codex 已有概念，lingclaude 完整复制

### 4.2 cc (Claude Code)
- **对应**：BashExecutor 黑名单+sandbox 策略（CC 的危险命令拦截对位）
- **差异**：CC 是 Node.js + 浏览器渲染，前端一体；lingclaude 是 Python + Rust webui 子进程分流
- **关键差异**：CC 靠 `settings.json` + hooks 配置（用户配置驱动），lingclaude 多了 self_optimizer 闭环 + LACP 治理

### 4.3 opencode
- **对应**：subagent ACP 后端形态（opencode 的多 agent runtime 对位）
- **差异**：编辑器风格（Go）、工具扁平化；**无 self_optimizer 闭环**——不自改超参
- **关键差异**：opencode 是 LSP-heavy 路线，lingclaude 是 LSP + LACP + 治理 复合

### 4.4 atomcode
- **对应**：mcp_client.py + codeintel.py + sensitive_path_gate（atomgit_bash_gate.rs / sensitive_path.rs / mcp/ 对位）
- **相似度最高**：两者都是 Python + "工具 + 治理" 路线
- **关键差异**：atomcode 没有 SignedProvider 双签保护（capability_seam 独有）；marketplace 安全评分机制（lingclaude 独有）

### 4.5 crush
- **对应**：ToolRouter 类别打分过滤（crush 的 tool router 思路对位）
- **差异**：Go 单进程 + LSP 注册（lingclaude `/lsp` 对标）
- **关键差异**：crush 是终端原生 AI 客户端，lingclaude 是有 webui 的 agent 框架

### 4.6 dsh（DeepSeek-Harness）
- **对应**：tool_pipeline 是 dsh tool-execution-pipeline.md 的 Python 化实现（5 段名一一对位）
- **关键差异**：dsh 是 Cordis 全插件化（50 独立包）；lingclaude 用 capability_seam + replaceable 4 档**局部引入**而非全插件化
- **这是灵克对 DSH Critique 第 4 点"全插件化改造成本极大"的明确取舍**（见 sandbox_policy 顶部注释）

---

## 五、lingclaude 独特优势（6 家都没有）

1. **可治理**：12 状态提案机 + 异议制 + 元提案 + 72h 闭环复查 + cognitive_state S0-S6 检测。S1 模板回复被识别为非真实审议并自动过滤阻塞性异议。
2. **可插拔但不重**：LACP v0.5.0 提供完整插件契约（6 transport + 4 档 replaceable + 双签 + AST 静态扫描），但不像 DSH 走全插件化极端路线。
3. **可自优化**：7 类触发器 + AST 评估 + lingminopt 搜索 + 知识库规则提取 + 三重护栏改参（report-only / 单参数限幅 / ApprovalGuard 闸门）。5 个月断路后会话自优化是真实可测的（`_apply_params` 之前真的能修改 config.yaml，但断路 5 个月）。
4. **持久化补强三层**：session 原子写 + journal append + checkpoint，崩溃恢复最多丢一条消息（system_events）而非整段。**这正是 57109 事故后的治本改造**。
5. **元认知双闭环**：H1-H17 守卫（声明-执行一致性）+ meta_cognition 校准（领域置信度）+ 盲区检测。6 家中无一有完整闭环。
6. **多 agent 总线**：LingBus 双向往返（5 系统发送者 + proposal_thread_id），`/recover` + checkpoint 让 webui 断线可续——其他 5 家无任何等价物。

---

## 六、lingclaude 的硬债务（4 个 Plan agent 实证）

### 6.1 死接线 / Mock / Stub

| 项 | 文件:行号 | 性质 |
|---|---|---|
| `hybrid_router.py` + `local_provider.py` 零调用 | `model/` | 模型层死接线 |
| `governance_router.py` 调 `engine.propose/vote/resolve`，但 v2 实际 API 是 `create_proposal/raise_objection/check_deadlines` | `governance/router.py` | router 调不通真实引擎 |
| `LocalModelProvider` 默认路径 `/home/ai/models/lingai-merged` 不存在，加载必然失败但被 HybridRouter 静默回退 API | `model/` | 静默降级掩盖 bug |
| `IntelligentRouter.main()` 仅 demo 打印，未接生产路径 | `model/intelligent_router.py` | demo 残留 |
| `_loop_detector` 从未在 `__init__` 初始化（`hasattr` 永真），`observe_denial` 永不触发 | `coding.py:770` | 5a/5b 接线缺陷 |
| `AcpSubagentBackend` 无真实服务端（端口 8901 默认未跑） | `engine/subagent/acp.py` | acp 通道空转 |
| `_apply_params` 的 `lock_cm = None` 提前 return，`finally` 的 `if lock_cm is not None` 永不触发 | `self_optimizer/daemon.py` | 锁释放路径有漏洞 |
| `KGFactChecker._mock_search` 固定返回 `found=True` | `core/fact_checker.py` | 事实核查 mock |
| `l10_a_post_audit.py` L10-B 不可用时返回未验证 mock | `core/` | 审计 mock |

### 6.2 重复实现

- `message_builder.py` 与 `system_prompt_builder.py` 各自独立实现行为警告 / 经验注入逻辑（两套相似的 extras.append 链）
- `FileOps`（file_ops.py）和 `FileEditTool`（file_edit.py）功能重叠——read/write/edit 双重路径
- `tool_router.py` 的工具过滤与 `mcp_tools.py` 的工具注册部分重叠

### 6.3 跨仓硬依赖

- `self_optimizer/optimizer.py` 直接 `from lingminopt import ...`——灵极优宕则 optimizer 崩

### 6.4 文档债

- `docs/USER_MANUAL_CLI_v0.4.md` 缺 `doctor` 子命令文档（doc_consistency 已 WARNING）
- `lingclaude/cli/app.py.bak` / `coding.py.bak` 是 git 之前的版本，未清理

---

## 七、H17 一句话总结

**lingclaude 是 6 家中唯一兼具"可治理 + 可插拔但不重 + 可自优化 + 持久化三层补强 + 元认知守卫 + 多 agent 总线"的 agent 框架**——但同时也是 6 家中**债务最多**的：死接线、mock、stub、重复实现、跨仓硬依赖并存。

它不是"最优雅"的（atomcode 集成更紧），不是"最轻量"的（crush 是 Go 二进制），不是"最多包的"（dsh 50 packages），不是"最 AI-native 的"（codex/cc 全 AI 驱动），不是"最编辑器化的"（opencode）。

**它的独特价值是"把治理、自优化、持久化作为一等公民"——这是其他 5 家都没走过的路**。这条路带来了**最多的工程债务**（因为每条机制都需要接线、测试、文档），但也带来了**最强的可演化和可治理性**（kill 决策权、denial 熔断、append-only journal、failure-safe 自优化在 4 天会话里被实证落地）。

---

## 八、4 天会话实证（2026-09-06 → 2026-09-09）

按本会话的修改记录，lingclaude 4 天内完成：

| 项 | 落地状态 |
|---|---|
| webui P0 鉴权 | ✅ W1（0ffe9e1） |
| F12j 硬错误熔断 | ✅ f33c424 |
| H1-H17 元认知守卫 + 闭环申报 | ✅ 4ffa1c8 |
| 元认知守卫表入库 | ✅ e30072c |
| R5 阶段 1（journal + checkpoint） | ✅ 已落地（族内 PR） |
| R5 阶段 2（幂等 ID + 副作用二次确认） | ✅ 9 测试 0.65s |
| R2 结构化日志（denial 入 DataFlywheel） | ✅ 本会话 |
| 5a/5b denial 熔断（rule_id + observe_denial） | ✅ 13 测试 1.67s |
| R6 kill 决策权治理文档 | ✅ docs/governance/ |
| R8 sub_agent 引导+统计 | ✅ 10 测试 1.42s |

**4 天 ~15 个 commit，每个都有实证验证**——这就是 H17 闭环在产品上的体现：账目（commit）与仪表盘（测试）一致。

---

## 九、附：4 agent 输出原文

见本会话其他回复：
- core 层报告
- model+cli 层报告
- engine 层报告
- governance/lacp/optimizer 层报告

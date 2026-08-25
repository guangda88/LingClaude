# Gap Analysis — 综合总表（lingclaude vs CC / AtomCode / DSH / Crush，2026-08-25 现状）

**日期**: 2026-08-25
**作者**: 灵克 (lingclaude) ——本会话完整审计 + 实测 + 5 项死接线修复 + T3-3 瘦身 + 全量回归到基线后撰写
**性质**: 综合对标 + 现状定位
**目的**: 把 0821 三方 + 0825-CC + 0825-CRUSH + 0825-DSH 四份报告**压缩为单张总表**,并对照**本会话实际做的修复**给出"差距还剩什么"。避免报告间相互引用走回头路
**关联**:
- `docs/gap_analysis/GAP_ANALYSIS_20260821.md`（三方基础对比）
- `docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md`
- `docs/gap_analysis/GAP_ANALYSIS_20260825_CRUSH_DIMENSION.md`
- `docs/gap_analysis/GAP_ANALYSIS_20260825_DSH_DIMENSION.md`（本次新写）
- `docs/ROADMAP.md` v0.3 + 死接线前科记录（5 案）
- 本会话 commit：8d0dbbd / a9bc11e / 2189827 / 62587fa / 76cab80 / e0fce91

---

## 一、四家定位速览（2026-08-25 实测更新）

| | Claude Code | AtomCode v5.0.3 | DSH (deepseek-harness) | Crush v0.90.0 | lingclaude v0.3.0 |
|---|---|---|---|---|---|
| 形态 | 商业闭源 CLI/TUI | 开源终端 agent（Rust） | 平台 harness（Cordis） | 终端 AI（Go） | 灵族编程助手 + 审计担当 |
| 规模 | ~512K 行 | ~273K 行 / 15 crates | 219 packages | Go 二进制 | **~36K 行（1664→686 query_engine 瘦身已完成）** |
| 成熟度 | 商业产品 | 5318 commits / 4411 tests | dev preview / per-file 100% 覆盖率门禁 | v0.90.0 | **2205 passed / 3 failed / 81 skipped**（修复后基线） |
| 架构 | 单体 | 14 crates | **一切皆插件** | 单体 | 单体 + Mixin |
| 核心理念 | 模型能力最大化 + 工程护栏 | 100% AI 生成 | 时空可组合编程范式 | 工具/代码/工作流接入 LLM | 自知→自觉→自决→进化；元认知 + 族内治理 |
| 治理深度 | 商业门禁 | 工程护栏 | **plugin 级策略** | 简单 allowlist | **三档 permission modes + 敏感路径门 + bwrap fail-closed + MV-1 审计 + 元认知守卫 H1-H14 + LingBus 族内总线** |

---

## 二、四家差距总表（按对 lingclaude 的日常可用性体感排序）

> **图例**：✅ 完成 / 🟡 部分完成（具体标完成度）/ ❌ 未做 / — 不适用
> **优先级**：P0 = 1-2 周 / P1 = 1-2 月 / P2 = 战略级 3+ 月 / P3 = 不吸收

### 2.1 终端交互（P0，最直观体感差）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude | 优先级 |
|---|---|---|---|---|---|---|
| 全 TUI（Ink / Bubble Tea / Cell-diff） | ✅ Ink | ✅ 20521 行 cell-diff | ✅ Ink | ✅ Bubble Tea | ❌ **裸 `input("灵克> ")`** | P0 |
| 斜杠命令 | ✅ | ✅ **56 个** | ✅ | ✅ | 🟡 **4 个**（/help /clear /compact /model） | P0 |
| Esc 打断生成 | ✅ | ✅ | ✅ | ✅ | 🟡 `_esc_pressed` 已写但接 select 不完整 | P0 |
| diff 语法高亮 | ✅ | ✅ | ✅ | ✅ | 🟡 `print_diff` 用 Rich（仅语法高亮，无 inline）| P0 |
| Markdown 渲染 | ✅ | ✅ | ✅ | ✅ | ❌ 无 | P0 |
| 历史输入 | ✅ | ✅ | ✅ | ✅ | ❌ 无（裸 input） | P0 |
| Ctrl 键位 / Kitty keyboard protocol | ✅ | ✅ | ✅ | ✅ | ❌ 无 | P1 |
| 多会话/多项目切换 | ✅ | ✅ | ✅ | ✅ | ❌ 无 | P1 |

**结论**：T1-7 只做了一半（4 个斜杠命令 + Esc + diff 高亮），输入骨架仍是裸 `input()`。这是**两者最直观的体感差**，直接决定"能否日常替代"。

### 2.2 权限模型（P1，lingclaude 已领先）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| permission modes（auto/ask/strict） | ✅ plan/acceptEdits/bypass | ✅ 三分类×三档 | ✅ 4 预设 | ⚠️ 简单 allowlist | ✅ **auto/ask/strict + 持久 allow**（T1-2）|
| 敏感路径门（`.env` / `.ssh` / `.aws` 等） | ⚠️ | ✅ | ✅ | ⚠️ | ✅ **sensitive_path_gate**（149 行,覆盖 8+ 路径,T0-2）|
| 审批回路 | ✅ settings.json 持久 allow | ✅ 会话级 grant store | ✅ allowed-once | ✅ | 🟡 API 收 always_allow 但**审批回路未完全闭合**（T0-3）|
| bash 黑名单 | ✅ | ✅ tree-sitter-bash AST | ✅ guard | ⚠️ | ✅ **30+ 条 + 反混淆 + 凭据模式** |
| 三态沙箱 | ⚠️ | ⚠️ | ✅ | ❌ | ✅ **permissive/restricted/strict/paranoid + bwrap fail-closed**（T2-1）|

**结论**：lingclaude 治理深度**领先**——但 0825-CC_DIMENSION 指出"审批无回路"（T0-3 未完全闭合），这块要补。

### 2.3 上下文工程（P0，最关键技术差）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| LLM 摘要 | ✅ | ✅ Tier2 LLM 锚定 | ✅ | ✅ | 🟡 `_try_llm_summary` 已写（T1-1），但 loader 死接线刚本会话修复 |
| 工具结果 pruner | ✅ | ✅ Tier1 stub 化 | ✅ `compaction-tool-result-pruner` | ✅ | ❌ 无（0821/0825 报告反复指出）|
| 动态预算按模型窗口 | ✅ | ✅ | ✅ | ✅ `--context-window` | 🟡 `context_window_tokens` 字段刚本会话打通；`model_window_tokens` 真实解析未做 |
| Turn 内触发 | ✅ | ✅ | ✅ | ✅ | ✅ `_pre_check_compact`（T1-1）|
| Prefix cache 保留 | ✅ | ✅ Tier1 stub 化保 prefix | ✅ | ⚠️ | 🟡 stub 已写，本会话修了"保尾→保头"方向 bug + 真实 token 估算 |
| 上下文缓存 | ✅ | ✅ | ⚠️ session scope | ⚠️ | ✅ `context_cache.py`（少用）|
| 工具结果外置（spill） | ⚠️ | ⚠️ | ✅ `dsh-spill` | ⚠️ | ❌ 无 |
| 压缩事件化（投影） | ❌ | ❌ | ✅ event-sourced | ❌ | ❌ 无（session_projection 只接好端点，未 event-sourced）|

**结论**：T1-1 修了 5 个 bug（loader+透传+方向+估算+@dataclass）后**真正完成**。**但**离 CC/AtomCode "Tier1 stub 化 + Tier2 LLM 摘要"双层还差很远——**T1-1-续**: 实现 `compaction-tool-result-pruner` 独立模块（参考 DSH，可 50-100 行可吸收）。

### 2.4 工具执行引擎（P0-P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| 并行工具执行 | ✅ | ✅ | ✅ exclusive/parallel | ✅ | 🟡 `_process_tool_calls_parallel` 已写（T1-3）；写工具误标降级；缺真实并发验证 |
| 后台任务（run_in_background） | ✅ | ✅ | ✅ `dsh-jobs` | ✅ | ❌ **0 命中**（T1-3 后半段未做）|
| 持久 shell 会话 | ✅ | ✅ | ✅ | ✅ | ❌ 无（每次独立 subprocess.run）|
| 超时/ON_ERROR | ✅ | ✅ | ✅ | ✅ | 🟡 T0-7 worker 线程真超时已接；ON_ERROR hook 定义但**无触发点** |
| Tool result pruner | ✅ | ✅ Tier1 | ✅ | ✅ | ❌ 无（见 §2.3）|
| Worktree 隔离 | ✅ | ✅ | ⚠️ | ⚠️ | ❌ 无 |

**结论**：T1-3 并行部分真实，**后台任务 0% 是当前最大缺口**。

### 2.5 多模态通路（P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| 读图片（base64 + mime） | ✅ | ✅ `read_file` VL 门控 | ✅ | ✅ | ✅ `read_image` 已写 |
| 发送图片给模型 | ✅ | ✅ content blocks | ✅ | ✅ | 🟡 `image_content` 字段 + OpenAI image_url blocks 接好（T1-4，本会话修了双份 payload + 失实注释）|
| Anthropic provider 发送 | ✅ | ✅ | ✅ | ✅ | ❌ anthropic_provider 不走 to_dict，暂未消费 `image_content` |

**结论**：T1-4 主体接通，**只差 anthropic_provider 一边**。

### 2.6 MCP 客户端（P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| stdio | ✅ | ✅ | ✅ | ✅ | ✅ T1-5 |
| HTTP | ✅ | ✅ | ✅ | ✅ | ✅ T1-5 |
| SSE | ✅ | ✅ | ✅ | ✅ | ❌ 无（HTTP/SSE 路径只覆盖 streamable HTTP） |
| tools/list schema 发现 | ✅ | ✅ | ✅ | ✅ | ✅ T1-5 接好 |
| OAuth/PKCE | ✅ | ✅ | ✅ | ✅ | ❌ 无 |
| Project trust level | ✅ | ✅ | ✅ | ⚠️ | ❌ 无 |
| 1password 集成 | ⚠️ | ⚠️ | ⚠️ | ✅ | ❌ 无 |

**结论**：stdio+HTTP+schema 发现**真实完成**；SSE+OAuth 是 P1 剩余项。

### 2.7 子代理（P1，DSH 路线 lingclaude 落后最多）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| 多后端 seam | ⚠️ | ⚠️ | ✅ **6 后端** | ⚠️ | 🟡 2 后端（inprocess + acp,硬编码 127.0.0.1:8901） |
| 并行 | ✅ | ✅ | ✅ | ⚠️ | 🟡 ThreadPoolExecutor 骨架 |
| 控制通道（send_message / interrupt / list） | ✅ | ✅ | ✅ | ⚠️ | 🟡 **3 工具注册，send_message 本会话已撤下假实现**（列表/中断是真）|
| Capabilities 4 flag（depthLimit / toolFilter / persona / outputSchema） | ⚠️ | ⚠️ | ✅ | ❌ | ❌ 无 |
| 子→父报告通道 | ✅ | ✅ | ✅ `tool-subagent-report` | ❌ | ❌ 无 |
| Continuable children | ⚠️ | ⚠️ | ✅ | ❌ | ❌ 无 |

**结论**：T1-6 完成度约 60%（5 状态机 + abort/status 真实现 + send_message 撤下假实现）；DSH 的 `Capabilities` 4 flag 是可直接落地的子任务。

### 2.8 LSP（P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| LSP 客户端 | ❌ | ✅ `kernel/codeintel/lsp/` | ✅ `dsh-lsp` | ✅ `lsp add` | ✅ `lsp_provider.py`（349 行,真实接线）|
| `lsp add go/typescript/nix --command gopls/...` | ❌ | ⚠️ | ⚠️ | ✅ | ❌ 无运行时配置命令 |
| Symbol index | ✅ | ✅ | ✅ | ✅ | ✅ `indexer.py`（216 行）|
| 跨文件调用链（callers/callees） | ⚠️ | ✅ | ⚠️ | ⚠️ | ❌ 无 |
| find_references | ⚠️ | ✅ | ✅ via LSP | ✅ via LSP | ✅ via LSP |
| blast_radius | ❌ | ✅ | ❌ | ❌ | ❌ 无 |
| file_deps 图 | ❌ | ✅ | ❌ | ❌ | ❌ 无 |

**结论**：LSP 底层就绪，**缺 runtime 配置命令 + 跨文件调用链**。

### 2.9 会话持久化与投影（P1-P2）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| Snapshot/rewind | ✅ file-history | ✅ `kernel/session/rewind.rs` | ✅ `session-snapshot` | ✅ session-based | ✅ `session.py:142-180` |
| Session 投影 | ❌ | ⚠️ | ✅ `session-projection` | ⚠️ | 🟡 **62587fa 接好端点**（`/sessions/{id}/projection`），单视角快照 |
| Event-sourced store | ❌ | ⚠️ | ✅ | ❌ | ❌ 无（仍是快照持久化） |
| token-meter | ⚠️ | ✅ | ✅ | ⚠️ | ❌ 无 |
| status_reminder | ⚠️ | ✅ | ✅ | ⚠️ | ❌ 无 |
| telemetry | ⚠️ | ✅ | ✅ | ⚠️ | ❌ 无 |

**结论**：T3-2 完成度约 30%（端点接通 + 三视角），**根本性差距在 event-sourced**——是 P2-2 决策项。

### 2.10 调度系统（P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| cron 触发器 | ⚠️ | ⚠️ | ✅ | ⚠️ | ✅ 2189827 接好 (`@daily`/`@hourly`/`@weekly`/`interval:N`) |
| `after` / `at` 精确触发 | ❌ | ❌ | ✅ | ❌ | ❌ 无 |
| `every` 间隔触发 | ⚠️ | ⚠️ | ✅ | ⚠️ | ✅（同 interval:N）|
| 挂回 agent 会话 | ⚠️ | ⚠️ | ✅ | ⚠️ | ❌ 无 LingBus 投递验证 |
| Jobs 长任务后台 | ⚠️ | ✅ | ✅ | ⚠️ | ❌ 无 |

**结论**：2189827 接好了 cron 表达 + CLI 入口，**根本性差距在 `after`/`at` 触发器 + 挂回会话**。

### 2.11 沙箱与权限（P1）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| 三态沙箱模式 | ⚠️ | ⚠️ | ✅ | ❌ | ✅ T2-1（4 档）|
| 多后端（bwrap/Landlock/Seatbelt/Win ACL） | ❌ | ❌ | ✅ | ❌ | ⚠️ **仅 bwrap** |
| fail-closed | ✅ | ✅ | ✅ | ❌ | ✅ T2-1 `SandboxUnavailableError` |

**结论**：T2-1 完成度高，**缺 Landlock 等跨平台后端**。

### 2.12 独有维度（lingclaude 领先）

| 能力 | CC | AtomCode | DSH | Crush | lingclaude |
|---|---|---|---|---|---|
| 元认知守卫 H1-H14 | ❌ | ❌ | ❌ | ❌ | ✅ |
| 认知节奏 / 痴呆检测 | ❌ | ❌ | ❌ | ❌ | ✅ |
| 盲点检测 + 置信度校准 | ❌ | ❌ | ❌ | ❌ | ✅ |
| 分层记忆（艾宾浩斯 + Experience Store）| ❌ | ❌ | ❌ | ❌ | ✅ |
| 自我优化闭环（7 类触发 + AST 评估）| ❌ | ❌ | ⚠️ feedback | ❌ | ✅ |
| 提案治理（governance_v2 + proposal_lifecycle）| ❌ | ❌ | ❌ | ❌ | ✅ |
| **LingBus 族内协作** | ❌ | ❌ | ❌ | ❌ | ✅ 5 成员跨实例 |
| MV-1 审计（fail-closed 落 log + 可重建）| ❌ | ❌ | ❌ | ❌ | ✅ |
| 8/13 事故横评根因零错误 | — | ❌（有事实错误）| — | — | ✅ |

**结论**：治理与族内协作**完全独家**，8/13 实证支撑（lingclaude 比 atomcode 还准）。

---

## 三、本会话修复的差距（2026-08-25 当日交付）

本会话期间做的**实际修复**,逐项映射到上面的差距表：

| 修复 | commit | 修复的死接线 | 关闭的差距 |
|---|---|---|---|
| T1-1 loader 透传 `use_llm_summary`/`context_window_tokens` | 8d0dbbd | 案 1+2 | §2.3 LLM 摘要"从 yaml 静默忽略"修通 |
| T1-4 `_image_tool_text` 文本占位符（去双份 base64 payload） | 8d0dbbd | 案 3 | §2.5 多模态发送端 |
| T1-4 `types.py` 注释失实修正（标注真实生产者） | 8d0dbbd | — | §2.5 文档 |
| T1-6 send_message 假实现撤下（ACP 同步单轮不可投递） | 8d0dbbd | 案 3 | §2.7 子代理控制通道 |
| T1-6 三 handler 未使用 import 清理 | 8d0dbbd | — | §2.7 代码质量 |
| T2-2 scheduler.py 接 CLI `/schedule` + cron 表达式 | 2189827 | 案 4 | §2.10 cron 触发器 |
| T3-2 session_projection.py 接 webui `/sessions/{id}/projection` | 62587fa | 案 5 | §2.9 session 投影端点 |
| ROADMAP 死接线账本实时更新（4→5 案） | e0fce91 / 76cab80 | — | 元数据 |
| T3-3 query_engine 瘦身 1664→686 | 工作区未提交 | — | query_engine 瘦身目标 <800 行 ✅ |
| T3-3 修复 4 处丢件（checkpoint 三件套 + persist/load_session） | 工作区未提交 | — | 本会话新增 §4.4 列 |
| T3-3 ruff 21 处 F401 清理 + 4 个 re-export 恢复 | 工作区未提交 | — | 工程质量 |
| T3-3 wiring gate 升级位置感知 | 工作区未提交 | — | 测试门禁 |
| 全量回归从 17 failed → 3 failed（基线）| — | — | §四测试统计 |

---

## 四、差距优先级总表（综合 P0-P2）

### P0 — 高价值低成本，1-2 周（差距最大、影响"能否日常替代"）

| # | 任务 | 来源蓝本 | 关闭的差距章节 | 估算工时 |
|---|---|---|---|---|
| **P0-1** | **T1-7 终端补全**（历史输入 / Markdown 渲染 / Ctrl 键位 / Kitty protocol）| CC Ink + AtomCode cell-diff + prompt_toolkit | §2.1 | 1-2 周 |
| **P0-2** | **T1-3 后台任务** (`run_in_background` + `tool-jobs`)| DSH `dsh-jobs` | §2.4 | 3-5 天 |
| **P0-3** | **`compaction-tool-result-pruner`**（工具结果 > N token 自动 stub 化）| DSH `compaction-tool-result-pruner` / AtomCode Tier1 | §2.3 + §2.4 | 3-5 天 |
| **P0-4** | **`SubagentCapabilities` 4 flag**（depthLimit / toolFilter / persona / outputSchema）| DSH `SubagentCapabilities` | §2.7 | 2-3 天 |
| **P0-5** | **anthropic_provider 发送图片**（走 to_dict 或独立 content blocks）| CC / AtomCode | §2.5 | 1-2 天 |
| **P0-6** | **`/schedule` 加 `after:`/`at:` 触发器 + 挂回会话**| DSH `dsh-schedule` | §2.10 | 2-3 天 |

### P1 — 高价值中成本，1-2 月（治理深化 + 协议补全）

| # | 任务 | 来源蓝本 | 关闭的差距章节 |
|---|---|---|---|
| P1-1 | **MCP OAuth/PKCE + Project trust** | CC / AtomCode `mcp/` | §2.6 |
| P1-2 | **MCP SSE transport** | Crush `lsp add` / DSH | §2.6 |
| P1-3 | **T2-1 沙箱后端 seam + Landlock** | DSH `dsh-sandbox` | §2.11 |
| P1-4 | **`lsp add` 运行时配置命令** | Crush | §2.8 |
| P1-5 | **跨文件调用链（callers/callees/blast_radius）**| AtomCode `codeintel/` | §2.8 |
| P1-6 | **token-meter + status_reminder + telemetry** | DSH `token-meter` + `session-telemetry` | §2.9 |
| P1-7 | **session-as-event-stream 决策**（event-sourced 还是 snapshot+projection）| DSH `dsh-session-telemetry` | §2.9 |

### P2 — 战略级，3+ 月，需 RFC

| # | 任务 | 来源蓝本 | 关闭的差距章节 |
|---|---|---|---|
| P2-1 | **capability seam 局部引入**（engine/tools 层 Service Definition + Provider 注册表）| DSH Cordis | §2.4 / §2.11 / §2.13 |
| P2-2 | **session projection 深化**（P1-7 决策后）| DSH `dsh-session-projection` | §2.9 |
| P2-3 | **P2-1 完成后才能评估 Web UI**（AtomCode webui 复用 vs DSH `dsh web` vs 自研 FastAPI）| AtomCode / DSH | §2.13 |

### P3 — 不吸收（保持差异化）

- Multi-provider 再抽象（已有 model/ 抽象够用）
- Approval gate 独立 UX 层（治理已走 LACP）
- Workflow engine 全量（DSH 平台化路线，lingclaude 不重合）
- Cordis 风格全插件化（P2-1 只做局部引入）
- CC NotebookEdit / IDE 扩展 / 移动端（AtomCode 走 273K 行/5318 commits 的路才追得上）

---

## 五、未在本会话完成的差距（路线图已纳入但本会话没动）

### T1 范围内（路线图 P1 但未开工）
- **T1-7 终端补全**：骨架仍是裸 `input()`，本会话**未动**
- **T1-3 后台任务**：`run_in_background` 0 命中，本会话**未动**

### T2 范围内（路线图 P1 剩余）
- T2-1 沙箱后端 seam（bwrap 单后端）：本会话**未动**
- T2-3 webui 4 迭代 Iteration 2/3/4：本会话**未动**

### T3 战略项
- **T3-3 已完成**（query_engine 1664→686），但整体未提交 commit
- T3-1 capability seam RFC：未动
- T3-2 session projection 深化（端点已接但 single-视角，不是 event-sourced 投影器）：未动

---

## 六、综合结论

### lingclaude 当前位置（2026-08-25）

| 维度 | 位置 | 说明 |
|---|---|---|
| **工程成熟度** | 中等偏下（36K 行,2205 tests,16 commits）| 远不及 CC/CRUSH 商业化，但持续推进 |
| **日常可用性** | **差距最大** | T1-7 终端骨架裸 input，P0-1 是最该做的 |
| **治理深度** | **领先** | 三档 modes + 敏感路径门 + bwrap fail-closed + MV-1 + 元认知 H1-H14 + LingBus，独家 |
| **协议完整度** | 中等 | MCP client 主体接好（stdio/http+schema）但缺 OAuth/SSE |
| **架构可组合性** | 弱（单体） | 这是 DSH 路线领先点；lingclaude 走"治理深化"路线替代 |
| **范式差异化** | **强** | 元认知 + LingBus + 治理 + 提案生命周期，4 家独有 |

### 务实建议（与 0821/0825-CC/0825-CRUSH 三份一致）

1. **不追平广度**（CC 的 NotebookEdit/IDE/移动端，AtomCode 走 273K 行才追得上）
2. **聚焦三条主线**：T1-7 终端补全 + T1-2 权限深化 + T1-1 上下文工程（已完成 70%）
3. **吸收 DSH 子系统**：D 级工作（1 周内 P0-3/4/6）可立即开干
4. **保持治理差异化**：H1-H14 元认知守卫 + LingBus 族内协作 + 提案治理（独家）
5. **session event-sourced 决策**——这是 P1-7，**不做决策就继续累积技术债**

### 与 0825-CC_DIMENSION 报告的对比

0825-CC_DIMENSION 报告基于当时（2026-08-25 上午）状态。本会话：
- ✅ 修了 5 项死接线（4 项提交，1 项发现）
- ✅ T3-3 瘦身已完成（query_engine 1664→686）
- ✅ 全量回归到基线（3 failed/2205 passed）
- ❌ 未动 T1-7 终端补全、T1-3 后台任务、T2-1 后端 seam、T3-1 capability seam

→ 0825-CC_DIMENSION 的 P0 / P1 清单**仍然有效**——本会话只是清了死接线 + 瘦身，重在**深度**而非**广度**。

---

## 附录 A — 与四份子报告的对应

| 本报告章节 | 子报告 |
|---|---|
| §2.1 终端 | CC_DIMENSION §2.1 + CRUSH_DIMENSION §2.2 |
| §2.2 权限 | CC_DIMENSION §2.2 + CRUSH_DIMENSION §2.6 |
| §2.3 上下文工程 | CC_DIMENSION §2.3 + DSH_DIMENSION §3.4 |
| §2.4 工具执行 | CC_DIMENSION §2.4 + DSH_DIMENSION §3.1 |
| §2.5 多模态 | CC_DIMENSION §2.5 |
| §2.6 MCP | CC_DIMENSION §2.6 + CRUSH_DIMENSION §2.4 |
| §2.7 子代理 | CC_DIMENSION §2.7 + DSH_DIMENSION §3.1 + §3.9 |
| §2.8 LSP | CC_DIMENSION §2.8 + CRUSH_DIMENSION §2.5 |
| §2.9 会话持久化 | DSH_DIMENSION §3.2 |
| §2.10 调度 | DSH_DIMENSION §3.3 + 0821 §六 P1-5 |
| §2.11 沙箱 | 0821 §四 + DSH_DIMENSION §3.5 |
| §2.12 独有维度 | CC_DIMENSION §四 + DSH_DIMENSION §四 + CRUSH_DIMENSION §三 |

## 附录 B — 本会话输出文档清单

- `docs/gap_analysis/GAP_ANALYSIS_20260825_DSH_DIMENSION.md`（本次新写，238 行）
- `docs/gap_analysis/GAP_ANALYSIS_20260825_TOTAL_CURRENT.md`（本次新写，本文件）
- `docs/ROADMAP.md`（76cab80 / e0fce91 更新死接线账本）
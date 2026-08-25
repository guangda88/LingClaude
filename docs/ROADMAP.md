# 灵克 (lingclaude) 后续路线图

> 优先级依据：gap_analysis/GAP_ANALYSIS_20260821.md（灵克，2026-08-21）
> 原稿基于 DSH packages 能力地图，对比后发现 P0 优先级错位；本版已按 gap_analysis 修正
> 优先级：P0 高价值低成本（1-2 周）/ P1 高价值中成本（1-2 月）/ P2 战略级（3+ 月）/ P3 不吸收（保持差异化）

---

## 背景：P0 优先级修正说明

**原版 ROADMAP 的 P0 错位**：原 P0 列出 fs 策略隔离 / llm Provider 抽象 / session 持久化——这些是中高成本项。
**gap_analysis 校准后真正的 P0**（高价值低成本，1-2 周内可落地）：
- Todo list tool（两家都有，灵克缺）
- Tool result pruning（DSH compaction-tool-result-pruner，防止大输出爆上下文）
- request_user_input tool（AtomCode 有，灵克缺）
- Session snapshot/rewind（AtomCode 有，灵克缺）

---

## P0 — 高价值低成本（建议 1-2 周内吸收）

### P0-1：Todo list tool

**现状**：22 个 ToolDefinition 中无 todo 工具
**目标**：新增 `engine/todo.py` + ToolDefinition 注册，支持任务创建/查询/完成/列表
**来源**：AtomCode + DSH 均有一级 todo tool，灵克缺
**落地行数**：50-100 行
**影响文件**：`engine/todo.py`（新建）+ `engine/coding.py`（注册 tool）
**依赖**：无

### P0-2：Tool result pruning（在 tool_pipeline.py 内）

**现状**：大输出（如 large file grep / long ls）全量进入上下文，无阈值裁剪
**目标**：在 `tool_pipeline.py` 的 post-execute 段加 output 大小阈值裁剪——超过 N tokens 的结果写 spill 文件，只返回 locator
**来源**：DSH `compaction-tool-result-pruner`
**落地行数**：约 30-50 行
**影响文件**：`engine/tool_pipeline.py`
**依赖**：无
**注意**：短期可先做 token 计数截断，不必引入 spill 存储； spill storage 是 P1 项

### P0-3：request_user_input tool

**现状**：有 `question` 方法但未注册为 tool，交互模式用户输入走 STDIN
**目标**：包装 `question` 为 ToolDefinition，schema 简单（header/question/mode/options），支持 single/multiple/text 三种模式
**来源**：AtomCode `request_user_input`，DSH `user-questions`
**落地行数**：约 80 行
**影响文件**：`engine/coding.py`（注册）+ `tools/`（新建 tool 文件）
**依赖**：无

### P0-4：Session snapshot / rewind ✅ 已落地

**现状**：`core/session.py` SessionManager 已实现 `snapshot()` / `rewind()` + `SNAPSHOT_PREFIX`
**落地说明**（2026-08-21 审计修复）：
- `snapshot()` 经 `_session_path` 写入 `save_dir/snapshot_<sid>_<ts>.json`
- `rewind(sid, snap_path)` 从快照 JSON 恢复 Session
- `list_sessions()` 跳过 `snapshot_*` 文件，避免把快照当普通会话
- `daemon.py` 启动时从 `save_dir` glob 最近快照恢复，每 5 轮优化自动 snapshot（`_snap_interval`）
- 快照与普通会话同目录（`.lingclaude/sessions/`），`snapshot_` 前缀区分
**来源**：AtomCode `kernel/session/snapshot.rs`
**影响文件**：`core/session.py` + `self_optimizer/daemon.py`

### P0-5：LSP 集成 RFC（先写设计稿）

**现状**：无 LSP 客户端，代码导航靠 grep
**目标**：起草 `lingclaude/engine/lsp.py` RFC（不走实现），定义：
- 走 stdio JSON-RPC，先支持 rust-analyzer / pylsp
- Service Definition：goToDefinition / findReferences / hover / goToImplementation
- Provider 注册机制（lsp-stdio）
**来源**：AtomCode `kernel/codeintel/lsp/` + DSH `dsh-lsp` + `tool-lsp`
**落地**：RFC 文档，不含实现
**影响文件**：`docs/lacp/LSP_DESIGN_RFC.md`（新建）
**依赖**：无
**注意**：这是 gap_analysis "立即可执行 3 个动作"第 3 项；LSP 完整实现是 P1 项

---

## P1 — 高价值中成本（1-2 月）

### P1-1：LSP 集成实现

**现状**：仅有 RFC 草稿
**目标**：实现 `engine/lsp.py`（LSP client）+ `lsp-stdio` provider + `tool-lsp` tool
**意义**：编程体验关键——模型可直接跳转到函数定义，不再靠 grep 猜
**影响包**：`engine/lsp.py`（新建）+ `model/lsp_provider.py`（新建）
**依赖**：P0-5（LSP RFC）

### P1-2：Subagent 多后端

**现状**：`engine/sub_agent.py` 是单一实现，无 Provider 接口
**目标**：抽象出 SubagentProvider 接口，至少支持：
- in-process（当前实现）
- ACP（Agent Client Protocol，对接 DSH acp/）
**来源**：DSH `subagent` 通用 seam（6 后端）
**落地**：重构 `engine/sub_agent.py` + 新建 `engine/subagent/providers/`
**影响文件**：`engine/sub_agent.py`（重构）
**依赖**：P0-3 无依赖；可与 P1-1 并行

### P1-3：Spill storage（大输出外置）

**现状**：P0-2 tool pruning 截断输出但未持久化
**目标**：大输出写本地文件 + 返回 locator；与 `context_cache.py` 集成做 LRU 淘汰
**来源**：DSH `spill` + `spill-local`
**落地行数**：约 150 行
**影响文件**：`engine/spill.py`（新建）+ `core/context_cache.py`（集成）
**依赖**：P0-2（tool pruning 做阈值判断后，spill 承接超阈输出）

### P1-4：Sandbox policy

**现状**：`permissions.py` 有 deny_tools/prefixes 静态列表，无动态沙箱
**目标**：
- 短期：bash 执行加 `bwrap`（bubblewrap）包装，实现 read-only / workspace-write 两种模式
- 长期：借鉴 DSH sandbox 三态（read-only / workspace-write / danger-full-access）
**来源**：DSH `sandbox` package
**落地**：重构 `engine/bash.py` + `engine/sandbox.py`（新建）
**影响文件**：`engine/bash.py`（重构）+ `engine/sandbox.py`（新建）
**依赖**：无（独立于其他 P1 项）

### P1-5：Schedule / Jobs（后台定时任务）

**现状**：daemon 是自优化专用，无通用 schedule；`core/task_scheduler.py`（281 行）机制类写好但零接线（无 cron 语义、无 LingBus 唤醒、全仓唯一消费方是自己的测试文件）
**目标**：
- `schedule`：基于 cron 表达式注册定时任务，到期发 LingBus 消息唤醒
- `jobs`：长任务后台执行，支持 status / cancel / result 查询
**来源**：DSH `schedule` + `jobs` packages
**落地**：新建 `core/scheduler.py` + `core/jobs.py`
**影响文件**：`core/scheduler.py`（新建）+ `core/jobs.py`（新建）
**依赖**：无（基于现有 LingBus 消息机制，实现成本低）
**当前状态**：🔶 **机制就绪、未接线**（2026-08-25 CC 审查降级）

### P1-6：Code intelligence 图谱（基于现有 indexer.py 扩展）

**现状**：`indexer.py` 仅有静态 AST 列表，无跨文件调用链
**目标**：
- 构建 call graph（函数→调用函数）
- 实现 `trace_callers` / `trace_callees`（对标 AtomCode）
- 实现 `find_references`（对标 AtomCode）
- 实现 `blast_radius`（对标 AtomCode）
**来源**：AtomCode `codeintel/` 模块
**落地**：扩展 `engine/indexer.py` + 新建 `engine/codeintel.py`
**影响文件**：`engine/indexer.py`（扩展）+ `engine/codeintel.py`（新建）
**依赖**：无（可与 P1-1 LSP 并行，二者共同构建 code intel 能力）

---

## P2 — 战略级（3+ 月，需 RFC）

### P2-1：插件架构（Cordis 风格 capability seam）

**现状**：monorepo 结构，所有代码平铺在 `lingclaude/core/` 和 `lingclaude/engine/`
**目标**：在 engine/tools 层引入 capability seam——每个能力（fs/shell/llm/subagent 等）为独立 Service Definition + Provider 注册表，插件可替换实现
**来源**：DSH Cordis 插件架构（50 packages）
**注意**：gap_analysis 标注"改造大"，需先 RFC 评估；lingclaude 当前 monorepo 已成型，不可强行拆包
**影响范围**：整体架构，需周密设计

### P2-2：Session projection（会话投影）

**现状**：session 以事件流存储，无投影机制
**目标**：对标 DSH `session-projection`——将 session 事件流投影为不同视角（token 用量 / 工具调用频率 / 轮次统计）
**前置条件**：需先决定 session store 是否 event-sourced
**影响文件**：`core/session_projection.py`（新建）
**依赖**：需先完成 session 事件化改造（P0-4 snapshot/rewind 是前置）

### P2-3：Web UI（可选，非核心）

**现状**：纯 CLI，无 Web UI
**gap_analysis 标注**：灵族走 LingBus，Web UI 不是核心价值，**低优先**
**建议**：如要实现，走 DSH `dsh web` 模式（端口 3080），而非重造轮

**参考实现（2026-08-21 调研 AtomCode webui）**：AtomCode 提供完整可复用架构，三选一：

| 方案 | 说明 | 成本 |
|------|------|------|
| A. AtomCode webui 整体复用 | Preact + vite，`rust-embed` 把 `dist/` 打进二进制；`/?token=` 一次性 token → HttpOnly Cookie（端口作用域 cookie 名 `atomcode_webui_<port>` 防多实例互踩）；SSE `/chat` 流式 + `/live` 实时同步 | 中 |
| B. DSH `dsh web` | 直接伺服 `apps/web/dist/`，零自建前端 | 低 |
| C. lingclaude 已有 FastAPI `api.py` | 已有 `/ask`/`/exec`/`/status` 端点 + X-API-Key 认证 + CORS 白名单，只缺前端 | 低 |

**关键要点**：AtomCode 的 SSE 事件模型（`tool_start`/`tool_output`/`tool_result`/`permission_request`/`artifact_start` 等）与 lingclaude 的 `tool_pipeline` 高度同构，未来若做可对齐事件 schema。锁冲突观察（wrapper 报 "Another instance holds lock for 灵克"，PID 352255-265）提示：Web UI 需复用 daemon 锁机制，避免与 CLI 双实例互斥。

---

## P3 — 不吸收（保持差异化）

以下 gap_analysis 明确标注为"不吸收"，保持 lingclaude 现状：

| 项 | 原因 |
|----|------|
| **Multi-provider LLM 进一步抽象** | lingclaude 已有 `model/` 多 provider + task_router + intelligent_router，gap_analysis 认定"够用"，再抽象是过度设计 |
| **Approval gate（AtomCode 风格）** | AtomCode 偏 UX 层，lingclaude 治理已走 governance_v2，不重复 |
| **Workflow engine（DSH 风格）** | DSH 定位通用 harness，灵克走 LACP 治理，不重合 |
| **Cordis 风格插件架构（全量）** | 全面插件化改造成本极大，P2-1 已标注"需 RFC"，先做 capability seam 局部引入 |

---

## 实现顺序建议（基于 P0-P1-P2 依赖关系）

```
P0-4 (snapshot/rewind) ──┐
P0-1 (todo tool)  ───────┤
P0-2 (tool pruning) ─────┤
P0-3 (request_user_input) ┤
P0-5 (LSP RFC) ──────────┴──→ P1-1 (LSP 实现)
                                    │
P1-4 (sandbox) ←───────────────────┤
P1-2 (subagent 多后端) ←────────────┤
P1-6 (code intel) ←────────────────┤
                                    │
P0-2 ───────────────────────────────┴──→ P1-3 (spill storage)
P0-4 ───────────────────────────────────→ P2-2 (session projection)
P1-1 + P1-6 ─────────────────────────────→ P2-1 (capability seam RFC)
```

**核心原则**：
- P0-1/2/3/4/5 五个 P0 可完全并行（无相互依赖）
- P1-1 LSP 实现依赖 P0-5 RFC 先完成
- P1-3 spill 依赖 P0-2 tool pruning 的阈值判断
- P2-2 session projection 依赖 P0-4 snapshot/rewind 的事件化改造
- P2-1 capability seam RFC 可在 P1-1/6 完成后启动

---

## T1/T2 完成状态（2026-08-25 CC 审查校准）

### T1 引擎质量（7 项）

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| T1-1 | 上下文工程升级 | ✅ 完成 | 前提是 CC 的 loader 修复（config.py + query_engine.py 透传） |
| T1-2 | 权限模型 | ✅ 完成 | 3 档 modes + 持久 allow + webui 端点 |
| T1-3 | 并行工具执行 + 后台任务 | 🔶 部分完成 | 并行 ✅（ThreadPoolExecutor + write_lock + 冲突检测）；**后台任务 ❌ 0%**（run_in_background 全仓 0 命中） |
| T1-4 | 多模态通路 | ✅ 完成 | image_content 预留字段 + _extract_image_content + 单份 payload |
| T1-5 | 标准 MCP client | ✅ 完成 | stdio/HTTP + tools/list 发现 + LACP manifest transport |
| T1-6 | 子代理多后端 | ✅ 完成 | 双后端 + parallel + abort/status 控制通道（send_message 假实现已撤下） |
| T1-7 | 终端交互升级 | 🔶 半成品 | 斜杠命令/Esc/diff 高亮 ✅；**输入骨架仍是裸 input()**（无历史/Ctrl 键位/渲染） |

### T2 ROADMAP P1 剩余项（3 项）

| # | 任务 | 状态 | 备注 |
|---|------|------|------|
| T2-1 | Sandbox 三态模式 + fail-closed | ✅ 完成 | 4 档 + SandboxUnavailableError fail-closed + bash.py:227 完整接线 |
| T2-2 | Schedule / Jobs | 🔶 **机制就绪、未接线** | `core/task_scheduler.py`（281 行）写好但零接线（无 cron 语义、无 LingBus 唤醒、唯一消费方是自己的测试文件）——第 4 次死接线前科 |
| T2-3 | webui 4 迭代计划 | 🔶 部分完成 | Iteration 1 完成 80%；`/sessions/:id/stop` 已实现；协议 v0.1 已确认 |

### 死接线前科记录（4 次）

1. **use_llm_summary**（T1-1）— config.py 有字段但 loader 不读（CC 已修）
2. **context_window_tokens**（T1-1）— EngineConfig 有字段但 QueryEngineConfig 无（CC 已修）
3. **T1-6 控制工具**（send_message/abort/status）— 注册处假实现（CC 已撤下）
4. **T2-2 Schedule/Jobs** — task_scheduler.py 机制类写好但零接线（待接线或降级）

**教训**：机制类写好 ≠ 功能完成。有单测的孤立模块 = 零接线 = 不可达。T3 启动前必须清账。

---

## 路线图版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-08-20 | 初稿，基于 DSH packages/ 能力地图 |
| v0.2 | 2026-08-21 | 按 gap_analysis/GAP_ANALYSIS_20260821.md 修正：P0 重新定义（todo/pruning/input/snapshot/LSP RFC），llm Provider 抽象降为 P3 不吸收，补充 spill/schedule/codeintel 等遗漏项 |
| v0.3 | 2026-08-21 | P0-4 snapshot/rewind 落地（核心 audit 修复），daemon 快照恢复目录/glob 三处不一致修复 |

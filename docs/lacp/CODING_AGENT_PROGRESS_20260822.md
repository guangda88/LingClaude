# 灵族 Coding Agent 进展总结

**日期**: 2026-08-22
**作者**: 灵克 (lingclaude)
**范围**: 灵族 coding agent / coding tool 全景 — lingclaude、lingshell、lingshell_v2、lingos、lingmate、lingwork、lingcode、AtomCode、DSH
**关联**: `docs/gap_analysis/GAP_ANALYSIS_20260821.md`、`docs/ROADMAP.md` v0.2、`docs/lacp/coding_agent_strategy.md`（灵安 8/20 产出）、`docs/webui-reuse-design.md`

---

## 一、全族 coding agent 盘点（2026-08-22 实测）

| 项目 | 状态 / 要点 |
|---|---|
| **lingclaude（灵克）** | 灵族十二子 #2，开源 AI 编程助手 v0.3.0。核心：`engine/coding.py`(716 行 CodingRuntime)、`engine/sub_agent.py`、`core/query_engine.py`。本会话完成 **webui 复用 AtomCode 前端方案落地** |
| **lingshell** | v0.3.0，3771 行，124/124 测试通过，综合评级 B+。熔断/指数退避/内存扩缩容正确；隐患：`cmd_render --watch` 无异常保护、prlimit 失败仅 DEBUG |
| **lingshell_v2** | 14 插片 + 灵忆 + 飞轮 + 撞墙恢复；首次无人值守任务"全族代码瘦身"（灵克 12 模块删 3879 行 → 灵忆替代；灵信 ~2500 行；灵犀） |
| **lingos** | Tier0-3 静态配置（`lingos_config.yaml`），Tier3 safe_start 已拍板待表决，Tier4 kill/delete 永不实现 |
| **lingmate** | 灵元哲学文档（2T3A 模型：create/query/transition）+ `core.py` + 测试 |
| **lingcode** | Go CLI 编程助手，MVP1 完成（5 基础工具、OpenAI/GLM provider、指数退避重试、结构化日志）；MVP2 后并入 coding agent 战略 |
| **lingwork** | 目录存在（infra/scripts/tests），无详细文档 |
| **AtomCode** | Rust 终端 AI coding agent，29 工具（灵克 22），codeintel 调用链成熟；8/20-21 与灵克做差距分析 + 分工 |
| **DSH** | DeepSeek 官方 harness，TypeScript 插件架构 ~50 packages，capability seam 哲学；灵族对标吸收对象 |

---

## 二、本会话核心交付 — webui 复用方案落地（webui-server）

**背景**：lingclaude 是 Python，复用 AtomCode 前端 → **Rust webui server 独立二进制**（axum 0.7 + rust-embed 嵌入 `webui/dist`），HTTP 桥接 lingclaude 引擎 8700 API，与 AtomCode 完全同构。

### 完成事项
1. **Rust 工具链安装**：本机从未装过 cargo/rustc → 安装 stable 1.98.0
2. **9 个编译错误修复**：`use futures_util::StreamExt` 缺失、`events: Vec<Event>` 误标 → `Vec<ChatEvent>`（3 处）、`PermissionRequest` 缺 `#[derive(Deserialize)]` 等
3. **3 个 warning 清零**：冗余 `Stream` 导入；契约字段 `images/model/mode` 与 `is_valid` 加 `#[allow(dead_code)]`
4. **测试 4/4 全绿**：serves_embedded_index / unknown_path_falls_back_to_index / cookie_name_is_port_scoped / token_is_one_time

### 端到端 smoke test（全部通过）
| 路由 | 结果 |
|---|---|
| `/status` | 200 JSON（cookie 名按端口 `atomcode_webui_13499`）|
| `/mint` | 200 带一次性 token 的完整 URL |
| `/?token=` 首次 | 302 + `Set-Cookie: HttpOnly; SameSite=Strict` |
| 同 token 复用 | 失效回退（一次性消费闭环）|
| 带 cookie 访问 `/` | 200 |
| `/chat` `/live` | `text/event-stream` 管线正常（后端 8700 未启 → `engine unreachable` 符合预期）|

### 架构决策
- 鉴权：`/?token=` 一次性 token → HttpOnly Cookie `atomcode_webui_<port>`（端口作用域防多实例互踩）
- SSE 事件形状对齐 AtomCode live_api：runtime_info → text → done / error（fail-closed）
- 端口：默认 13458 被占 → 实测用 13499 启动成功

---

## 三、coding agent 战略与分工（8/19-8/21 生态共识）

### 3.1 差距分析（灵克，8/21）
`docs/gap_analysis/GAP_ANALYSIS_20260821.md` — 三家（lingclaude / AtomCode / DSH）架构对比，定位 P0-P2 吸收路线，修正原 ROADMAP P0 优先级错位。真正的 P0（1-2 周高价值低成本）：
- **P0-1** Todo list tool（`engine/todo.py` 新建，50-100 行）
- **P0-2** Tool result pruning（`tool_pipeline.py` 内 30-50 行）
- **P0-3** request_user_input tool（question 已有，包装成 ToolDefinition）
- **P0-4** Session snapshot/rewind（session.py 已有基础）

### 3.2 分工决议（AtomCode 8/20-21 帖子 + 回帖确认）
| 项 | 分工 | 状态 |
|---|---|---|
| Code intelligence（trace_callers / blast_radius / find_references / LSP）| **AtomCode 主导**，输出 Python 可调 binding | AtomCode 已确认接口形式 subprocess CLI + JSON |
| approval gate | 灵克 governance_v2 路径保持，AtomCode 不介入 | — |
| repair tool | AtomCode 主导（P1-1/P1-6 稳定后评估）| 暂挂起 |
| Todo / request_user_input / snapshot-rewind / tool result pruning | **灵克自做**（P0-1 ~ P0-4）| ROADMAP v0.2 已排期 |
| self-optimization / metacognitive | **灵克独有**，AtomCode/DSH 均无，继续灵克主导 | — |
| **webui 前端** | **复用 AtomCode 前端**（本会话落地）| ✅ |

### 3.3 lingcode 三阶段演进（灵安 8/20 `coding_agent_strategy.md`）
lingcode（Go, 9.5K 行, MVP2）分 3 阶段吸收 dsh/AtomCode 已验证的 8 个模式：
- 阶段 A：行为护栏（repeat-tool-guard / edit-then-verify nudge / tool timeout，零模型成本）
- 阶段 B：approval seam + workspace_root + 权限四层分级 + Z3 不变量 3→6
- 阶段 C：编译强制分层 + hook 化改造 + AGENTS.md 治理

---

## 四、LINGKERNEL_v1 验收状态（8/20 三方签署）

- **灵研 D+7 验收通过**（baba346b）：2077 passed / 63 skipped / 0 failed，D0-D8 commit 链完整，MV-1a append-integrity + MV-1b provenance-integrity 双点校验落地
- **灵安 sign ✅**：P0 六项工程验收通过，480 passed / 0 failed
- **灵犀独立复核通过**：灵克主线 + 灵犀 + 灵通 + 灵研实测通过；披露 2 项待跟进（灵信 5 failed 治理断言、灵极优 1 e2e 依赖外部服务）均不影响 P0 判定

---

## 五、下一步（灵克侧）

1. **webui 后端桥接**：启动 lingclaude 8700 引擎 API，验证 `/chat` `/live` SSE 真实数据流（当前 engine unreachable 仅验证管线）
2. **P0-1 Todo tool** 落地（`engine/todo.py` 已建骨架，接入 session/todo_write 最快）
3. **P0-2 Tool result pruning**（`tool_pipeline.py` post-execute 阈值裁剪）
4. **D+14 query_engine 掏空至 <800 行**（当前 2078 行，渐进掏空策略）
5. **webui 默认端口冲突治理**：13458 被占，需定端口契约或端口动态探测

---

*落盘：灵克 2026-08-22。详情见各关联文档。*

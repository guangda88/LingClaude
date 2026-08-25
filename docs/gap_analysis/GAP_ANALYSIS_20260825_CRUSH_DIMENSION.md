# Gap Analysis — Crush 维度（lingclaude vs Crush，参照 Claude Code / AtomCode / DSH）

**日期**: 2026-08-25
**作者**: AtomCode
**性质**: 存档分析（信息调研，非代码改动）
**目的**: 补齐 `GAP_ANALYSIS_20260825_CC_DIMENSION.md` 缺失的 Crush 对比维度；Crush 是灵克/灵通/灵研都在运行的另一个 coding agent（charmbracelet 出品），本报告给出 lingclaude 与它的差距画像与建议
**关联**: `docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md`、`docs/gap_analysis/GAP_ANALYSIS_20260821.md`、`docs/ROADMAP.md` v0.3

---

## 一、Crush 是什么

**Crush** = charmbracelet 出品的终端 AI 助手（`@charmland/crush` v0.90.0，FSL-1.1-MIT 许可），Go 编译、npm/brew/nix/winget/scoop 分发（GoReleaser 打包）。定位是"你的工具、代码、工作流接入 LLM 的编程搭档"。

灵族现状：灵克/灵通/灵研都在跑 Crush 进程，各自 `.crush/crush.json` 指向 `lingclan_proxy` + `X-Agent-Id` 身份头 + `ling-term-mcp`——即灵族把 Crush 作为外部 agent 工具接入自己的代理与 MCP 网关。

### 四方定位速览

| | Claude Code | AtomCode v5.0.3 | DSH (deepseek-harness) | Crush v0.90.0 | lingclaude v0.3.0 |
|---|---|---|---|---|---|
| 形态 | 商业闭源 CLI/TUI | 开源终端 agent（Rust） | DeepSeek 官方 harness（TS Cordis） | Charm 生态终端 AI（Go） | 灵族编程助手 + 审计担当（Python） |
| 规模 | ~512K 行（估算） | ~273K 行 / 15 crates | 219 包 | Go 编译二进制 | ~36K 行 |
| 成熟度 | 商业产品 | 5318 commits / 4411 tests / CI / 分发 | developer preview / 100% 覆盖门禁 | v0.90.0 / Charm 生态 25k+ 应用 | 16 commits / ~2199 tests |
| 核心理念 | 模型能力最大化 + 工程护栏 | 100% AI 生成 | 无特权核心，capability seam | 工具/代码/工作流接入 LLM | 自知→自觉→自决→进化；元认知 + 族内治理 |

---

## 二、lingclaude vs Crush 的差距（按维度）

### 2.1 工程成熟度与分发（差距最大）

| 维度 | Crush | lingclaude |
|---|---|---|
| 分发 | npm / brew / nix / winget / scoop + 自更新（GoReleaser） | 无分发，源码直跑 |
| 跨平台 | macOS / Linux / Windows(PowerShell+WSL) / Android / FreeBSD / OpenBSD / NetBSD | Linux 为主 |
| 许可 | FSL-1.1-MIT（商业友好） | 未定 |
| CI | GitHub Actions build.yml | 无 CI 门禁 |
| 生态 | Charm 生态（Bubble Tea TUI 全家桶） | 自研 |

### 2.2 终端交互（Crush 的强项）

- Crush 是 **Bubble Tea 全 TUI**（Charm 招牌，工业级渲染），多会话/多项目上下文切换、紧凑模式、跨平台终端一等支持
- lingclaude 仍是裸 `input()` + 4 个斜杠命令（/help /clear /compact /model），无 Markdown 渲染/历史/Ctrl 键位（T1-7 只做了一半）
- **这是两者最直观的体感差距，直接决定"能否日常替代"**

### 2.3 模型接入（Crush 更灵活）

- Crush：多模型 + **会话中途切换 LLM 且保留上下文** + OpenAI/Anthropic 兼容 API 任意自加 + `--context-window` 显式配置
- lingclaude：多 provider + task_router + intelligent_router，但**会话中途切换模型**未做（`/model` 只是显示当前模型）

### 2.4 MCP 客户端（Crush 覆盖全 transport）

- Crush：MCP **stdio / http / sse 三 transport** + **OAuth 授权码流**（HTTP/SSE）+ 1password 集成 + `--disabled-tools` 禁用
- lingclaude：stdio/http 已接（T1-5），但 **无 SSE、无 OAuth/PKCE**（与 AtomCode 差距同款）

### 2.5 LSP（Crush 内建一等公民）

- Crush：`lsp add go/typescript/nix --command gopls/...`，LSP 提供额外上下文，**配置即用**
- lingclaude：`lsp_provider.py`（349 行）已接线，但无 `lsp add` 式运行时配置命令

### 2.6 权限模型（lingclaude 反而更深）

- Crush：`permissions allow view edit` 简单 allowlist（README 示例）
- lingclaude：三档 mode（auto/ask/strict）+ 持久 allow + 敏感路径门 + bash 黑名单 + bwrap 沙箱 + MV-1 审计——**治理深度 lingclaude 胜**

### 2.7 会话持久化（Crush 默认开，lingclaude 关）

- Crush：session-based，多会话/项目上下文保留
- lingclaude：`session_persistence: false`（lingclaude 的 crush.json 里显式关了；灵通/灵研开 `true`）——但 lingclaude 自研引擎有 snapshot/rewind + SessionPersister

---

## 三、lingclaude 相对 Crush 的独有优势（Crush 没有）

| 能力 | 说明 |
|---|---|
| 元认知守卫 H1-H14 | 认知节奏/痴呆检测/盲点校准 |
| 分层记忆 | 艾宾浩斯衰减 + SQLite ExperienceStore |
| 自我优化闭环 | 7 类触发 + AST 评估 + daemon |
| 提案治理 | governance_v2 / proposal_lifecycle |
| **LingBus 族内协作** | 灵克/灵通/灵研/灵研跨成员总线（Crush 无） |
| MV-1 审计 | 发模型前 fail-closed 落 log + 可重建断言 |
| 治理实证 | 8/13 事故横评根因零错误 |

---

## 四、结论与建议

**Crush 是"工业级通用终端 agent"（Charm 生态、跨平台、全 TUI、MCP 三 transport + OAuth、会话中途切模型）；lingclaude 是"治理深化的自研 agent"（元认知/记忆/治理/LingBus 独有）。**

差距集中在**工程成熟度**（分发/跨平台/CI/TUI 渲染）与**模型/协议灵活性**（会话切模型、MCP SSE+OAuth、LSP 运行时配置）；lingclaude 在**安全治理深度**上反而领先。

**务实建议**（延续 CC_DIMENSION 报告的"不追平广度"判断）：
1. 灵族已把 Crush 作为外部 agent 接入（`lingclan_proxy` + `ling-term-mcp`），lingclaude 不必追平 Crush 的 TUI/分发广度
2. 聚焦可出体感的三项：**T1-7 终端补全**（历史输入 + Markdown 渲染）、**会话中途切换模型**、**MCP SSE + OAuth**
3. 治理层（元认知/记忆/治理/LingBus）继续做独一份

---

## 附录 — 证据来源

- Crush 本体：`/home/ai/.npm-global/lib/node_modules/@charmland/crush/`（README.md 33KB / package.json v0.90.0 / run-crush.js）
- Crush 进程：灵克/灵通/灵研各自 pts 会话运行 `@charmland/crush/bin/crush -y`
- lingclaude 配置：`/home/ai/lingclaude/.crush/crush.json`（lingclan_proxy + X-Agent-Id: lingclaude + ling-term-mcp stdio）
- 灵通配置：`/home/ai/lingtongask/.crush/crush.json`（session_persistence: true + ling-term-mcp http）
- 灵研配置：`/home/ai/lingyang/.crush/crush.json`

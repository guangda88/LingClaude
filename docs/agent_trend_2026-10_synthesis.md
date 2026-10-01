# Agent 进化趋势综合报告（2026-10-01）
> 综合来源：本地代码级侦察（learn-claude-code / claude-code-port / Kode-Agent）+ 前轮外部 Agent 联审（cc/codex/opencode/crush）+ Claude Code SDK 文档

---

## 一、趋势分级（代码级实证，非新闻稿）

### T0｜治理的工程化（P0，四家共识，最高权重）

**核心断言**：授权判定从 prompt 约定下沉为网关层策略对象。

**代码实证**：

| 项目 | 证据 |
|---|---|
| **Claude Code 权限系统** | `claude-code-port/src/permissions.py`：`ToolPermissionContext` 纯数据结构，`deny_names`/`deny_prefixes` 双字段 frozenset 锁死，无模型参与 |
| **Claude Code Hook 退出码** | `learn-claude-code/agents/s08_hook_system.py`：Exit code contract `0=continue/1=block/2=inject`，确定性语义 |
| **Claude Code 四档** | SDK 文档：`auto`/`pre_approve`/`ask`/`block` 四档策略对象，接 PreToolUse hook |
| **Kode-Agent TOOL_CATEGORIES** | 硬编码五类（read/edit/execution/web/other），工具白名单化 |
| **learn-claude-code s07** | 完整 permission system 教学，`.claude/trust_level` 配置文件 |

**lc 当前状态**：本轮 P0 已落地四档策略引擎（`tool_auth_policy.yaml`），但权限语义是 deny-by-name（黑名单），应升级为 `ToolPermissionContext` 式的**显式白名单 + deny_prefixes 双字段**，与 claude-code-port 对齐。

### T1｜Agent 声明化（markdown 即 agent，T1 权重）

**代码实证**：

```yaml
# learn-claude-code/agents/s04_subagent.py 实测格式
---
name: code-reviewer
description: Use when code needs quality review...
model: sonnet        # 指定档位
color: blue          # UI 配色
tools: [Read,Grep,Glob,Bash]   # 显式白名单
disallowedTools: [Write,Edit]  # 显式黑名单
skills: [code-review, security]  # 关联 skill
hooks: [pre-commit]            # 关联 hook
---
[markdown 指令体，含触发条件、职责边界、工作流模板、输出格式]
```

**关键字段**（代码级，非文档猜测）：

| 字段 | 说明 | lc 对应 |
|---|---|---|
| `name` | agent 唯一标识 | ❌ 无 |
| `model` | 指定推理档位（sonnet/opus/haiku） | ⚠️ 有 model_policy，但无 per-agent 指定 |
| `color` | UI 配色元数据 | ❌ 无 |
| `tools` | 显式白名单 | ⚠️ 有 registry，但无 per-agent 过滤 |
| `disallowedTools` | 显式黑名单 | ❌ 无 |
| `skills` | 关联 skill 列表 | ❌ 无 |
| `hooks` | 关联 hook 列表 | ⚠️ 有 pre_tool_use，但无 per-agent hook 绑定 |
| `auto` (frontmatter) | 自动触发条件 | ❌ 无 |

### T2｜Hook 生命周期注入体系（T2）

**完整事件清单**（代码级，learn-claude-code s08 + Claude Code SDK 文档交叉验证）：

| 事件 | 触发时机 | 超时 | lc 当前 |
|---|---|---|---|
| `SessionStart` | 会话建立时 | 1.5s | ⚠️ `session_start` 事件已有，无超时 |
| `PreToolUse` | 工具调用前 | 10min（SDK）/ 30s（教学版） | ✅ `pre_tool_use` 已实现 |
| `PostToolUse` | 工具调用后 | 10min | ❌ 无 |
| `ToolResult` | 工具结果返回后 | — | ❌ 无 |
| `PreToolUseAsync` | 异步工具调用前 | — | ❌ 无 |
| `ApprovalRequired` | 需要用户确认时 | — | ❌ 无 |
| `Message` | 消息发送/接收时 | — | ❌ 无 |
| `SessionEnd` | 会话结束时 | 1.5s | ❌ 无 |
| `HttpHook` | HTTP 请求层 | — | ❌ 无 |
| `PromptHook` | Prompt 注入前 | — | ❌ 无 |
| `McpToolHook` | MCP 工具级 | — | ❌ 无 |

**信任锚**：`.claude/.claude_trusted` 文件存在才运行 hook（learn-claude-code s08:52）。lc 当前无此机制——任何项目都触发 hook，有安全风险。

### T3｜Agent Teams / Teammate 持久化（T3）

**代码实证**（learn-claude-code s15）：

```
.team/config.json                   .team/inbox/
+----------------------------+      +------------------+
| {"team_name": "default",   |      | alice.jsonl      |
|  "members": [              |      | bob.jsonl        |
|    {"name":"alice",        |      | lead.jsonl       |
|     "role":"coder",        |      +------------------+
|     "status":"idle"}       |
|  ]}
+----------------------------+

spawn_teammate("alice") → Thread: alice
  while True:
    msgs = read_inbox("alice")
    drain inbox
    agent_loop(msgs)
    status → idle
```

**与 subagent 的本质区别**：
- Subagent（s04）：一次性 → 执行 → 返回 summary → 销毁
- Teammate（s15）：持久 → idle → 工作 → idle → ... → shutdown

**lc 当前状态**：无 teammate 概念。lc 的 MCP server（lingxi）可以做这件事——teammate inbox = MCP 消息队列，这是自然的架构延伸。

### T4｜Skill 系统（T4）

**SKILL.md 标准格式**（Kode-Agent + learn-claude-code 双重验证）：

```yaml
---
name: agent-builder
description: |
  Design and build AI agents for any domain. Use when users:
  (1) ask to "create an agent", "build an assistant"...
  (2) want to understand agent architecture...
  (3) need help with capabilities, subagents, planning...
  (4) ask about Claude Code, Cursor, or similar agent internals
  Keywords: agent, assistant, autonomous, workflow...
auto: true  # 可选：自动触发
---
```

**Kode-Agent skill 集成**（`src/commands/agents/tooling.ts`）：
- `TOOL_CATEGORIES` 五类：read/edit/execution/web/other
- `getAvailableTools()` 动态合并 core tools + MCP tools
- `add-skill` CLI 命令

**lc 当前状态**：`~/.lingclaude/skills/` 目录已建，SKILL.md 格式未标准化，skill 加载机制未实现。

### T5｜ACP 协议（Agent Communication Protocol，T5）

**Kode-Agent ACP 实现**（`src/acp/protocol.ts`）：

```typescript
interface AcpMessage {
  id: string
  type: 'request' | 'response' | 'notification'
  method?: string
  params?: Record<string, unknown>
  result?: unknown
  error?: { code: number; message: string }
  protocolVersion: number  // 版本协商
}
```

**关键设计**：版本协商（`protocolVersion`）+ stdio transport（`type?: 'stdio'`）+ JSON-RPC 基础。lc 的 MCP server（lingxi）已在走 MCP 协议，ACP 是另一种多 agent 通信方案。

---

## 二、lc 进化路线图（代码级可落地）

### P0｜已完成（已落库）

| 任务 | 状态 | 提交 |
|---|---|---|
| 四档策略引擎 | ✅ | `29f8132` |
| credential-leak 正则守卫 | ✅ | `e242d02` |

### P1｜凭证句柄化 proxy3

**目标**：模型永不见明文 API key，所有凭证走 vault proxy。

**代码路径**：
- `proxy3` 已有路径 → 补 `vault/` 目录（凭证存储）
- `bash_network.py` 中所有 `os.environ.get("OPENAI_API_KEY")` → `vault.get("openai_api_key")`
- `model_call.py` 中的 provider 凭证初始化 → vault lookup

**前置条件**：vault 存储方案（SQLite 加密 或直接用 keyring）

**工作量**：半天

### P2｜Agent 声明化支持（AGENTS.md + agents/*.md）

**目标**：`.lingclaude/agents/` 目录，markdown 即 agent 定义。

**实现**：

```python
# lingclaude/core/agent_registry.py（新增）
AGENT_DIR = Path("~/.lingclaude/agents").expanduser()

def load_agent_definitions() -> dict[str, AgentDef]:
    """扫描 agents/ 目录，解析 frontmatter"""
    ...

@dataclass
class AgentDef:
    name: str
    description: str
    model: str | None        # sonnet/opus/haiku
    color: str | None        # UI 配色
    tools: list[str]         # 白名单
    disallowedTools: list[str]  # 黑名单
    skills: list[str]        # 关联 skill
    hooks: list[str]         # 关联 hook
    auto: bool = False       # 自动触发
    instruction: str         # markdown body
```

**复用资产**：
- `sgr_styles.yaml` 的 YAML frontmatter 解析已有（`pyyaml`）
- `slash_plugin_loader.py` 的动态加载机制可复用（扫描目录 + 首次 import）
- `/agent` 斜杠命令（参考 `/policy` 框架）

**工作量**：半天

### P3｜Hook 生命周期扩展

**目标**：从当前 `pre_tool_use` 扩展到 10+ 事件。

**优先级排序**（基于 Claude Code SDK 文档 + 实际价值）：

| 事件 | 价值 | 工作量 |
|---|---|---|
| `PostToolUse` | 高：结果拦截/改写 | 0.5h |
| `ToolResult` | 高：结果后处理 | 0.5h |
| `SessionEnd` | 中：会话清理/台账 | 0.5h |
| `ApprovalRequired` | 高：交互确认点 | 1h |
| `TrustMarker` | 高：安全隔离 | 1h |
| `HttpHook` | 中：代理层拦截 | 2h |
| `PromptHook` | 中：prompt 注入点 | 1h |
| `McpToolHook` | 中：MCP 工具级 | 1h |

**架构**：事件总线（参考 `agent_trend_2026-09_external_review.md` 中的 `EventBus` 模式）

**工作量**：2-3 天（完整实现所有事件）

### P4｜Skill 系统标准化

**目标**：SKILL.md 标准格式 + skill 加载器 + `/skill` 命令。

**实现**（参考 Kode-Agent + learn-claude-code）：

```python
# lingclaude/core/skill_loader.py（新增）
SKILL_DIR = Path("~/.lingclaude/skills").expanduser()

@dataclass
class SkillDef:
    name: str
    description: str
    keywords: list[str]
    auto: bool = False
    instruction: str  # markdown body
    tools: list[str] | None = None

def load_skills() -> dict[str, SkillDef]: ...
def match_skills(query: str) -> list[SkillDef]: ...  # 语义匹配
```

**工作流**：
1. 用户输入 → 触发 skill matching（关键词 + 语义）
2. matched skill → 注入 system prompt（SKILL.md body）
3. 可选：skill 指定 `tools` 白名单覆盖全局

**工作量**：1 天

### P5｜Agent Teams / 常驻 Teammate

**目标**：基于 MCP 协议的常驻 agent inbox 系统。

**架构**（参考 learn-claude-code s15）：

```
.teammates/config.json
.teammates/inbox/{name}.jsonl  ← append-only 消息队列

MCP Server (lingxi)
  ↕ 消息路由
Teammate agents
  - 独立 agent loop
  - 状态机：idle ↔ working
  - 广播支持
```

**与当前 MCP 的关系**：
- 当前：lingxi 作为 MCP server 供 lc 调用工具
- 扩展：lingxi 增加 teammate inbox 路由，lc 可以向 teammate 发消息

**工作量**：3-5 天（涉及 MCP 协议扩展）

### P6｜Tool Search（语义工具检索）

**目标**：工具数量超阈值时，从枚举切换到语义检索。

**代码路径**：
- `provider_registry` 已有 provider 路由 → 扩展为 tool-level registry
- `provider_registry.py` 加 `search_tools(query: str)` 方法
- embedding 模型可选（轻量方案：关键词匹配 + TF-IDF）

**触发条件**：`len(available_tools) > 50` 时启用

**工作量**：1 天

---

## 三、关键设计决策（代码级论证）

### 决策 1：Hook 信任锚

**问题**：当前 lc 的 hook 在所有项目都触发，安全风险。

**方案**：`.lingclaude/.trusted_projects` 白名单文件（类比 `.claude/.claude_trusted`）

**代码位置**：在 `tool_auth_hook.py` 的 `check_tool_call()` 入口判断

### 决策 2：Agent 定义存储位置

**选项 A**：`~/.lingclaude/agents/*.md`（用户级）
**选项 B**：项目级 `.lingclaude/agents/*.md`
**选项 C**：两者兼有，项目级优先

**推荐 C**——与 Claude Code 一致（用户级全局定义 + 项目级覆盖）

### 决策 3：Skill 与 Agent 的关系

**当前认知**：skill 是 agent 的 building block（一个 agent 可以关联多个 skill）

**推荐关系**：
```
Agent = instruction + model + tools + skills + hooks
Skill = description + keywords + instruction + (optional tools)
```

### 决策 4：Teammate 通信协议

**选项 A**：扩展现有 MCP 协议（lingxi 已支持）
**选项 B**：引入 Kode-Agent ACP（JSON-RPC + 版本协商）
**选项 C**：文件-based JSONL inbox（learn-claude-code s15 方案）

**推荐 A**——MCP 已有 transport + JSON-RPC 基础设施，扩展成本最低

---

## 四、已知台账债（与进化路线重叠部分）

| 债单 ID | 内容 | 与进化路线关系 |
|---|---|---|
| `arch-guard-gate-commit-latency` | pre-commit 门延迟 | P3（Hook 扩展）可顺带解决 |
| `seamtype-specialcase-plugin-loader` | plugin_loader 特例化 | P2（Agent 声明化）可顺带解决 |
| `dual-session-staging-race` | 双会话竞态 | P5（Teammate inbox）可顺带解决 |
| `cli-selfopt-core-private-penetration` | cli 穿透 core 私有面 | P3 需注意边界 |

---

## 五、总结：lc 进化坐标系

```
当前 lc                         目标 lc
─────────────────────────────────────────────────
脚本式 hook (gate_scripts/)  →  策略对象 + 事件总线
黑名单权限                  →  白名单 + deny_prefixes 双字段
硬编码 agent               →  AGENTS.md 声明式定义
无 skill 系统              →  SKILL.md 标准 + 语义匹配
pre_tool_use 单事件        →  10+ 事件 Hook 体系
一次性 subagent            →  常驻 Teammate inbox
凭证明文传递               →  vault proxy3
无信任锚                   →  .trusted_projects 白名单
```

**优先级**：P0（已完成）→ P1（凭证句柄化，0.5天）→ P2（Agent 声明化，0.5天）→ P3（Hook 扩展，2-3天）→ P4（Skill 系统，1天）→ P5（Teammate，3-5天）→ P6（Tool Search，1天）

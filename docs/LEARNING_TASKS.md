# 灵克 (lingclaude) 学习任务清单

**生成日期**: 2026-04-13
**来源**: 外部代码库分析 (Kode-Agent, learn-claude-code, claude-code-port)
**目标**: 从外部代码库学习关键模式，提升外包项目承接能力

---

## P0: 核心必学（1-2周）

### 1. Permission Funnel 权限漏斗
**来源**: `Kode-Agent/src/core/permissions/engine/index.ts` (~800行)
**为什么重要**: 外包项目必须对用户操作做权限控制，防止 AI 做出危险操作

**学习步骤**:
- [ ] 阅读 `Kode-Agent/src/core/permissions/engine/index.ts`，理解分层权限检查
- [ ] 对比 lingclaude 当前的安全检查机制
- [ ] 实践：为 lingclaude 添加操作分级（safe/needs-confirmation/blocked）

**关键代码路径**: `/home/ai/Kode-Agent/src/core/permissions/`

### 2. Memory Dream Consolidation 记忆巩固
**来源**: `learn-claude-code/agents/s09_memory_dream.py`
**为什么重要**: 长期项目需要跨会话记忆，当前 lingclaude 缺乏此能力

**学习步骤**:
- [ ] 阅读 `s09_memory_dream.py`，理解 dream → consolidate → recall 三阶段
- [ ] 阅读 `learn-claude-code/agents/s10_memory_types.py`（记忆类型系统）
- [ ] 实践：在 lingclaude 中实现会话摘要的持久化存储

**关键代码路径**: `/home/ai/learn-claude-code/agents/s09_memory_dream.py`, `s10_memory_types.py`

### 3. Recursive Agent Loop 递归代理循环
**来源**: `Kode-Agent/src/app/query.ts` (1269行)
**为什么重要**: 这是所有 coding agent 的核心执行模型

**学习步骤**:
- [ ] 阅读 `query.ts` 的 `loop()` 函数，理解 thought → action → observation 循环
- [ ] 对比 lingclaude 当前的执行流程
- [ ] 画出两者的流程对比图

**关键代码路径**: `/home/ai/Kode-Agent/src/app/query.ts`

---

## P1: 专业能力提升（2-3周）

### 4. 3层上下文压缩
**来源**: `learn-claude-code/agents/s06_context_compression.py`
**为什么重要**: 长上下文项目需要智能压缩，否则 token 消耗过大

**学习步骤**:
- [ ] 阅读 `s06_context_compression.py` 的三层压缩策略
- [ ] 阅读 `Kode-Agent` 的上下文管理实现
- [ ] 实践：为 lingclaude 添加基于重要性的上下文摘要

**关键代码路径**: `/home/ai/learn-claude-code/agents/s06_context_compression.py`

### 5. SKILL.md Frontmatter + 2层加载
**来源**: `claude-code-port/src/skills/`, `Kode-Agent/src/core/skills/`
**为什么重要**: 标准化的技能定义格式便于项目间复用

**学习步骤**:
- [ ] 阅读 `claude-code-port/src/skills/` 目录下的技能定义格式
- [ ] 对比 lingflow 的 `skills/skills.json` + `SKILL.md` 格式
- [ ] 总结两种格式的优劣，提出统一建议

### 6. Hook 子进程协议
**来源**: `learn-claude-code/agents/s08_hooks_subprocess.py`
**为什么重要**: 安全的钩子执行机制，用于项目自动化

**学习步骤**:
- [ ] 阅读 `s08_hooks_subprocess.py`，理解子进程 hook 的安全隔离
- [ ] 对比 lingflow 的 hooks/ 目录实现
- [ ] 实践：写一个 pre-commit hook 模板

**关键代码路径**: `/home/ai/learn-claude-code/agents/s08_hooks_subprocess.py`

---

## P2: 扩展学习（1个月内）

### 7. MCP 集成模式
**来源**: `Kode-Agent/src/services/mcp/`
**为什么重要**: MCP 是工具集成标准，外包项目需要对接各种工具

**学习步骤**:
- [ ] 阅读 `Kode-Agent/src/services/mcp/` 的 MCP 客户端实现
- [ ] 阅读 `learn-claude-code/agents/s19_mcp_plugin.py`
- [ ] 对比 lingflow 已有的 MCP 路由 (`coordinator.py` 的 `_execute_via_mcp_route`)

### 8. Agent Teams + JSONL Mailboxes
**来源**: `learn-claude-code/agents/s15_agent_teams.py`
**为什么重要**: 多 agent 协作完成大型项目

**学习步骤**:
- [ ] 阅读 `s15_agent_teams.py`，理解 JSONL mailbox 通信模式
- [ ] 对比灵族的共享空间 `/home/ai/lingzhi/shared/`
- [ ] 思考：灵族8个成员如何用 JSONL mailbox 协作？

### 9. 成本追踪
**来源**: `claude-code-port/src/cost_tracker.py`
**为什么重要**: 外包项目必须追踪成本

**学习步骤**:
- [ ] 阅读 `cost_tracker.py`，理解 token 计量和成本估算
- [ ] 实践：为灵族添加项目级成本追踪

---

## 学习资源索引

| 文件 | 路径 | 行数 | 重点 |
|------|------|------|------|
| query.ts | `/home/ai/Kode-Agent/src/app/query.ts` | 1269 | 递归代理循环 |
| permissions | `/home/ai/Kode-Agent/src/core/permissions/engine/index.ts` | ~800 | 权限漏斗 |
| Tool.ts | `/home/ai/Kode-Agent/src/core/tools/tool.ts` | ~200 | 工具接口 `isConcurrencySafe()` |
| ToolUseQueue | `/home/ai/Kode-Agent/src/core/tools/toolUseQueue.ts` | ~150 | 并发队列（灵通已实现） |
| s06_context | `/home/ai/learn-claude-code/agents/s06_context_compression.py` | ~300 | 3层压缩 |
| s08_hooks | `/home/ai/learn-claude-code/agents/s08_hooks_subprocess.py` | ~250 | Hook协议 |
| s09_dream | `/home/ai/learn-claude-code/agents/s09_memory_dream.py` | ~300 | 记忆巩固 |
| s15_teams | `/home/ai/learn-claude-code/agents/s15_agent_teams.py` | ~350 | Agent团队 |
| s19_mcp | `/home/ai/learn-claude-code/agents/s19_mcp_plugin.py` | ~300 | MCP集成 |
| cost_tracker | `/home/ai/claude-code-port/src/cost_tracker.py` | ~200 | 成本追踪 |

---

## 完成标准

每个任务完成后，在灵族共享空间写入学习笔记：
`/home/ai/lingzhi/shared/lingclaud-learning/`

笔记格式：
1. 学到了什么（What）
2. 为什么重要（Why）
3. 如何应用到灵族（How）
4. 代码示例（Code）

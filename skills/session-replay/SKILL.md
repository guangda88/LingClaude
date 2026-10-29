---
name: session-replay
description: lc 会话回放器——把 journal/rollout/决策流三源聚合为 agentplay/v1 兼容时间线 + 本地 HTML 视图（零外发零依赖）。用于复盘、失败路径分析、决策点审计。
---

# Session Replay（lc 最小回放器）

借鉴 AgentPlay 的**脱敏 schema**（不借其运行时——lc 会话布局与 Claude Code/Codex 不同）。peer-borrow 实证：2026-10-29 五方评审第 1 批④。

## 用法

```bash
# 导出会话时间线（JSON）+ 本地 HTML 视图
python3 scripts/session_map.py --session <session_id>

# 脱敏模式：不含 tool_result 预览文本（外发/分享前必用）
python3 scripts/session_map.py --session <id> --redact

# 仅导出 HTML
python3 scripts/session_map.py --session <id> --html out/replay.html
```

## 数据源（三源聚合）

| 源 | 提供 | 键 |
|---|---|---|
| `data/journals/<session>.jsonl` | tool_call/tool_result 事件（`tool_call_id` 配对 call↔result）| session_id = 文件名 |
| `~/.lingclaude/rollouts/*.jsonl` | token 曲线（turn_end 累计）| **陷阱：文件名是 thread_id，session_id 在首行 session_meta** |
| `data/arch_ledger/decisions_YYYYMM.jsonl` | 决策点（domain/confidence/kind）| session_id 字段 |

## 契约（单测钉住）

- **幂等**：journal 行数水位，无新增行时短路不重写（不是截断式假幂等）
- **确定性**：同输入两次导出逐字节一致
- **脱敏**：`--redact` 时输出零 prompt/result 原文（schema 要求）
- **决策↔工具反查**：decisions 的 `tool_call_id` 可 join journal 事件

## 验收惯例（⑪d）

新增 CLI 工具应**成对交付** skill 文档（可发现性），本 skill 即范例。反向：skill 描述的能力必须有真实 CLI/模块支撑，禁止只写文档不落码。

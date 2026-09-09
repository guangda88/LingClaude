# 灵克自优化计划：长任务轮次与 Token 效率

> 2026-09-09 立项。起因：审计长任务反复撞「最大工具调用轮次」墙，被迫多轮接力，浪费大量 token。

## 一、问题诊断

| 问题 | 根因 | 状态 |
|---|---|---|
| 会话级次数墙 | `verification.max_tool_calls_per_session`（原 200） | ✅ 已设 0（不限） |
| 运行时轮次墙 | `engine.max_turns`（原 128）+ `query_engine` 默认 8 + `model_call` 兜底 40 + `l5_conversation_loop` 默认 max_rounds=4 | ✅ 已调 500；⚠ 兜底链仍多层，见 P2 |
| token 浪费大头 | 非调用次数，而是：大输出直塞上下文、重复读取、无压缩 | ⬜ 未做，见 P1 |

## 二、路线图

### P1 省 token 三板斧（收益最大）
1. **工具输出截断**：工具执行层统一 `max_output_chars`（建议 20000），读取限行、diff 只给 stat、grep 限条数。预期省 50%+。
2. **结果缓存**：LRU，key=文件路径+mtime / 命令哈希，重复调用不重复执行。
3. **上下文压缩**：预算 70% 时把已完成工具输出替换为摘要，只留结论。

### P2 墙的兜底链收敛
- `query_engine.max_turns` 默认 8、`l5_conversation_loop.max_rounds` 默认 4：确认是否被 config 覆盖，未覆盖路径统一走 `engine.max_turns`。
- 以 `max_context_tokens`（如 200000）作为唯一硬保险，取代多层轮次魔法数。

### P3 按 token 记账（远期）
- `check_rate_limit()` 从按次数改为按 token 软预算：超阈值仅提醒+自动压缩，不硬拦。

## 三、执行纪律（已验证的教训）
1. 改配置必须端到端验证运行时真值（曾误以为 `agent.max_turns`，实为 `engine.max_turns`，正则误加重复段）。
2. 长任务 = 后台化 + 分批接力，前台只做轻量调度，独立调用并行发。
3. 备份文件（*.bak*）不得提交进仓库。

# 自优化计划：拆「最大工具调用轮次墙」+ Token 节省

日期：2026-05-25 ｜ 状态：进行中

## 一、根因链（已查明）

| 层 | 位置 | 现状 | 结论 |
|---|---|---|---|
| 轮次墙（真正撞的） | `core/config.py:26` `EngineConfig.max_turns` 默认 **8**；`model_call.py` `_resolve_max_tool_rounds()` 读 `agent.max_turns` | config.yaml `engine.max_turns: 16`（已调过一次） | **16 不够长任务** → 本轮调至 **48** |
| 会话次数墙 | `verification.max_tool_calls_per_session` | 已是 `0`（不限） | 无需改 |
| 会话次数墙（引擎侧） | `core/tool_executor.py:69` 读 `config.max_tool_calls_per_session` | 默认 0=不限 | 无需改 |
| 输出截断 | `tool_executor.py._prune_output()` head+tail | 已存在 | 无需重复实现 |
| 上下文压缩 | `tool_executor.py._compact_if_needed()` 按轮数/预算 | 已存在 | 无需重复实现 |
| 结果缓存 | `ContextCache.read_file` cache_hit | 已存在 | 无需重复实现 |

## 二、已执行

- [x] config.yaml: `engine.max_turns: 16 → 48`（备份：config.yaml.bak_turns）

## 三、待办（按性价比排序）

1. **截断参数可配置化**：`_prune_output()` 阈值硬编码，提为 `engine.max_output_chars`（收益最大）
2. **grep/glob 输出限条数**：默认仅返回前 N 条匹配，提示模型加过滤条件
3. **工具结果 LRU 缓存扩展**：bash 命令（只读类）结果按 命令+目录 mtime 缓存
4. **按 token 记账替代按次数**：`check_rate_limit()` 增加软阈值提醒（只提示不硬拦）
5. **BUG：todo 工具线程不安全**：`SQLite objects created in a thread...` 报错，todo MCP 需要每线程独立连接或加锁
6. **撞墙降级提示优化**：`[达到最大工具调用轮次]` 出现时，应在消息中附带「已完成进度摘要」，方便下一轮续跑而非重做

## 四、防撞墙执行纪律（经验）

- 长任务前先 `max_turns` 检查；>30 轮的任务拆成多次会话，靠计划文件交接
- 每轮工具调用「能并行则并行」：无依赖的调用放同一轮
- 先跑 `pytest -x -q` 快速失败定位，再全量

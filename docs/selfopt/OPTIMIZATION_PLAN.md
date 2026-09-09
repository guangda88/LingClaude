# 灵克自优化计划：突破工具调用轮次墙（2026-09-09+）

## 问题
`max_tool_calls_per_session` 轮次墙反复中断长任务（审计+全量测试+修复），浪费大量 token 重试。

## 对策（按优先级）
1. **高并行、少轮次**：无依赖的读取/搜索合并为单轮多 tool call；一轮顶多轮。
2. **长任务后台化**：全量测试用 run_in_background，轮询 job_status 不占执行轮次。
3. **整文件写入**：规避片段级编辑触发语法验证关卡误判+重试。
4. **复用已有审计**：先读 docs/audit/ 增量审计，不重复全量扫描。
5. **配置层放宽**：config.yaml `max_tool_calls_per_session: 0`（0=不限）+
   `agent.max_turns: 128`。保持循环检测/连续失败熔断，用安全门替代低轮次墙。
6. **崩溃恢复入口**：`/recover` 接通 `resume_interrupted()`，中断工具轮可显式续跑。
7. **运行观测**：每轮/恢复尝试追加 `.lingclaude/long_task_metrics.jsonl`，
   记录工具数、错误数、journal/checkpoint 尺寸与 outcome。
8. **受管监督**：`deploy/systemd/user/lingclaude-daemon-watch.service`
   使用 30s RestartSec，防裸后台进程被误杀或重启风暴。

## 目标
- 长任务（>50 次工具调用）不被轮次墙中断
- 单轮平均工具调用数 ≥2（并行度指标）

# PanesRenderer 设计参考 — lingcode 多 agent 面板（供 lingclaude WebUI P2-3 / L3 TUI）

**日期**: 2026-08-26
**作者**: AtomCode
**性质**: 设计参考（仅吸收思想，不移植代码）
**来源**: lingcode `pkg/display/`（Go，panes.go 351 行 + timeline.go 114 行 + display.go 152 行 + types.go 16 行，含测试共 1208 行）
**用途**: lingclaude WebUI（P2-3 三选一）与 L3 全 TUI 的多 agent 面板设计参考
**关联**: `docs/lacp/CLI_INTERACTION_RFC.md`（L3 定义）、`docs/lacp/FULL_TUI_NECESSITY_20260826.md`（L3 论证）

---

## 一、设计思想提炼（7 条核心）

### 1. 渲染与执行解耦（事件通道）

**来源**：`PanesRenderer.Events() chan<- PaneOrchestratorEvent`（缓冲 `numPanes*64`）+ `renderLoop()` 独立 goroutine 消费。

**思想**：agent 执行侧**只发事件**（不碰渲染），渲染侧**只消费事件**（不碰执行）。双方通过有界通道解耦，天然支持并发多 agent。

**映射到 lingclaude**：WebUI 的 SSE 事件流（`tool_start`/`tool_output`/`tool_result`）正是同一模式——lingclaude 的 `stream_submit` 事件已具备，前端面板消费即可。

### 2. 事件驱动 + 定时刷新的双轨渲染

**来源**：`renderLoop()` 的 `select { case evt := <-p.events: ...; case <-ticker.C: p.frame++; p.render() }`（120ms ticker）。

**思想**：事件到达**即时渲染**（响应快），无事件时**定时刷新**（spinner 动画/时钟走动，体感"活着"）。两轨合并到同一渲染循环，无竞态。

**映射到 lingclaude**：L3 TUI / WebUI 的面板刷新用"事件即时 + 定时兜底"双轨，避免纯事件驱动的静止感。

### 3. 环形缓冲防无限增长

**来源**：`StreamingText` 超 500 字符截尾（`pane.StreamingText[len-500:]`）；`Steps` 超 `paneH-4` 步丢旧（`pane.Steps[len-maxSteps:]`）。

**思想**：流式文本与步骤列表**有界**——长会话不撑爆面板/内存，且始终展示**最新**内容。

**映射到 lingclaude**：与 lingclaude `tool_pipeline._prune_output`（8KB 阈值 spill）同思路——面板层也应有界。

### 4. 状态机驱动面板状态

**来源**：`started→running / completed / error`（`renderLoop` 的 switch）；`PaneState.Status` 字段。

**思想**：面板状态是**显式状态机**（idle/running/completed/error），事件只做状态迁移，渲染读状态而非读事件历史。

**映射到 lingclaude**：WebUI 面板/工具卡片用显式状态机，而非"追加日志后推断状态"——与 lingclaude `JobStatus`（pending/running/completed/failed/cancelled）同构。

### 5. agent 身份视觉化（颜色 + 图标）

**来源**：`agentColors`（lingclaude⚡青/lingflow🌊品红/lingresearch🔬黄/lingzhi📚绿/lingxi🔗蓝）+ `IconForAgent`/`ColorForAgent` 回退白/●。

**思想**：每个 agent 固定**颜色 + 图标**，多 agent 同屏可瞬间区分身份，视觉记忆稳定。

**映射到 lingclaude**：灵族成员（灵克/灵通/灵研/灵创/灵犀）各配固定色 + 图标，WebUI/TUI 面板沿用。

### 6. 时间线 + 结果卡片的复盘视图

**来源**：`RenderTimeline`（按耗时降序 + `TimelineBar` █░ 比例条 + ✓/✗ + 耗时 + 百分比 + 总计行）+ `RenderResultCard`（agent 头 + 响应限 30 行 + 耗时）+ `RenderDashboard`（Header + Timeline + 逐 agent 卡片）。

**思想**：执行完成后提供**复盘视图**——谁慢谁快一目了然（时间线），每个 agent 结果独立成卡（结果卡片）。

**映射到 lingclaude**：WebUI 的会话复盘页 / L3 TUI 的结束视图可复用此布局；lingclaude `QualityReport`/`SessionSummary` 数据可直接喂给时间线。

### 7. 通用渲染原语（ANSI 辅助）

**来源**：`display.go`——颜色常量、`Spinner`（10 帧 ⠋⠙⠹...）、`ProgressBar`/`TimelineBar`（█░）、`FormatDuration`（μs/ms/s 自适应）、`Header`/`AgentHeader`。

**思想**：渲染原语（spinner/进度条/时长格式化/标题）独立成工具层，面板/时间线/卡片复用，不重复实现。

**映射到 lingclaude**：lingclaude `display.py` 已有 `print_markdown`/`ToolCallPanel`/`StatusBar`——补充 spinner/进度条/时长格式化即可覆盖。

---

## 二、映射到 lingclaude 的落地建议

### WebUI（P2-3）优先吸收

| 设计思想 | WebUI 落地 |
|---|---|
| 事件通道解耦 | 复用 `stream_submit` SSE 事件（tool_start/tool_output/tool_result）→ 前端面板 |
| 双轨渲染 | 事件即时 + 定时心跳（前端 setInterval 兜底） |
| 状态机 | 面板状态 = JobStatus 同构（pending/running/completed/failed/cancelled） |
| agent 视觉化 | 灵族成员固定色 + 图标 |
| 时间线/卡片 | 会话复盘页：时间线 + 结果卡片（喂 QualityReport/SessionSummary） |

### L3 TUI（按"不追平广度"后置）

| 设计思想 | L3 TUI 落地（Textual） |
|---|---|
| 渲染与执行解耦 | Textual 的 Reactive + 事件回调 |
| 环形缓冲 | Textual 的 `max_lines` / 滚动区 |
| 状态机 | Textual 的 Reactive 属性 |
| 通用原语 | Textual 自带 ProgressBar/Spinner |

---

## 三、明确边界

- **仅吸收思想，不移植代码**：lingcode 是 Go（ANSI 直接渲染），lingclaude 是 Python（Rich/Textual 或 Web 前端）——**代码不可移植**，只借鉴架构模式
- **不引入 lingcode 依赖**：不把 lingcode 作为 lingclaude 的依赖
- **优先级**：WebUI P2-3 优先吸收（与灵族 LingBus 路线一致）；L3 TUI 按 FULL_TUI_NECESSITY 论证保持后置

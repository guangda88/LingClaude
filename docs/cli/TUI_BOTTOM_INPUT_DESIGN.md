# CLI TUI 改造方案 — 底部固定输入框 + 实时状态栏（未实施 2026-09-06）

> 用户需求：lingclaude CLI 下方加固定输入框和状态栏，可随时输入问题和指令，
> 流式生成中先挂起入队，进程结束后执行；状态栏显示模型/工作目录/上下文占比/轮次/任务。
> 状态：**P1 降级形态已落地**（组件 status.py/input_queue.py/interface.py +
> app.py 接线，2026-09-08），与设计的差距见文末「实际落地 § P1 降级」小节。
> P2 全屏 TUI 未实施，下文 §一~§九 为原始设计存档。

---

## 一、目标与边界

把交互从"输入和生成串行阻塞"改成"主线程 TUI + 后台线程流式"的单向事件架构：
- **始终有固定底部输入框**（prompt_toolkit TextArea）
- **状态栏持续显示** 6 项关键状态（见 §三）
- **流式中键入**：普通文本进 FIFO 队列，不影响当前生成
- **当前生成结束**：自动取下一项
- **退出方式保留**：`/quit`、`/exit`、Ctrl+D、空输入 ESC 不退出（仅取消当前）
- **plain/Fallback 路径不变**：`LINGCLAUDE_CLI_MODE=plain`、非 TTY、WebUI 不动

---

## 二、Layout（prompt_toolkit Application + HSplit）

```
[ 输出窗口（可滚动，wrap_lines=True） ]
[ ────── 分隔线 ────── ]
[ StatusBar（1 行） ]
[ 输入框（TextArea，多行可扩展） ]
```

依赖：prompt_toolkit 当前在 `lingclaude/cli/interface.py` 是**可选依赖**——必须
保留回退（CI/headless/webUI 不受影响）。

---

## 三、状态栏字段（6 项）

| 字段 | 数据源（优先级回退） | 显示规则 |
|---|---|---|
| 模型名 | `engine._provider._config.model` → `_model_config.model` → `config.model.model` → `unknown` | 全名（如 `meta/llama-3.3-70b-instruct`） |
| 工作目录 | `Path.cwd()` | 优先显示 `~/project` 缩略；过长截断 |
| 上下文占比 | `_estimate_message_tokens(engine._messages) / 上限` | 显示 `~34%`（估算标识）；上限缺失显示 `?` |
| 对话轮次 | `engine.get_stats()["turns"]` | 整数 |
| 当前任务 | 内部状态机 `idle/streaming/tool_call/queued(N)/guard_pending` | 文字描述 |
| Provider 健康 | 复用 `_provider_status(engine)` | 已连接 / 缺 key 等 |

---

## 四、核心调度规则（最关键）

- **同时间只有 1 个 worker**——engine 不是为并发设计的，硬约束
- **入队**：`_handle_slash_command` 先拦截；普通文本进 `queue.Queue[str]`
- **worker 生命周期**：单次 `engine.stream_call_model(prompt)` 完整迭代
- **完成回调**：exactly-once 投递 `_on_stream_finished(prompt, result)`，主线程统一执行收尾 + 弹队头启下一项
- **ESC**：仅设当前请求的 cancel event，**不删队列**；用 `request_id` 防止迟到事件污染下一项

---

## 五、状态机

```
idle → streaming ↔ tool_call → 完成
  ↓         ↓
queued(N) ← 入队时有当前任务
  ↓
退出时 queued → 丢弃（不写历史）
```

---

## 六、关键风险与对策

| 风险 | 对策 |
|---|---|
| **Rich Live × prompt_toolkit 终端控制权冲突** | TUI 模式**完全弃用 Rich Live**，仅 plain 路径保留 |
| **`_esc_listen_loop` 用 `tty.setraw` 破坏键位** | TUI 禁用，改用 KeyBinding 绑 `Keys.Esc` |
| **engine 非线程安全** | 强约束单一 worker；`_finalize_turn` 仅主线程 |
| **API/版本差异** | `call_from_executor`、控件构造封装成 5-6 个适配函数 |
| **输出区无限增长** | 行数/字符上限，保留最近 N 行 |

---

## 七、实施顺序（7 步）

1. 冻结现有行为 + 定义 `has_full_tui_support()` 探测
2. 抽取 helper：`TUIStatusSnapshot` + `StatusBarWidget` + 输出缓冲适配器 + 模型名/context helper
3. 单请求后台 worker：迭代 `stream_call_model`，每个事件通过 UI 投递
4. FIFO 队列 + 状态机：`_on_stream_finished` 原子地完成当前 + 取下一条
5. 接入 Application/Layout：TextArea + FormattedTextControl 输出 + StatusBar Window + HSplit
6. 迁移斜杠命令 + 退出收尾：命令处理器注入 `emit` 回调
7. 回归验证：10-15 条端到端 + 1 个 fake engine

---

## 八、工作量估

| 项 | 行数 | 风险 |
|---|---|---|
| 主代码 | ~600 行（display.py 200 + interface.py 200 + app.py 200） | Rich × prompt_toolkit 嵌套 |
| 测试 | ~300 行（fake engine + 队列/状态机/命令/退出覆盖） | 终端环境依赖 |
| 调试缓冲 | 2-3 轮 | 嵌套 race |

**总计：1 PR ≈ 1000+ 行，4-6 轮会话**。

---

## 九、入口环境开关

```bash
LINGCLAUDE_TUI=0  # 强制 plain（CI/headless）
LINGCLAUDE_TUI=1  # TTY 下强制尝试 full TUI
LINGCLAUDE_CLI_MODE=plain  # 原有 fallback
```

---

## 十、相关文件

- `lingclaude/cli/app.py`（1394 行）——`_interactive_loop` 在 465 行
- `lingclaude/cli/interface.py`（162 行）——`PromptToolkitSession` 当前是薄封装
- `lingclaude/cli/display.py`（352 行）——已有 `StatusBar` 但只是单次打印
- `lingclaude/core/query_engine.py`——`get_stats()` 在 line 411
- `tests/test_cli_interaction.py`——已有 20+ 测试可作为回归基线

---

## 十一、未决的设计权衡

1. **斜杠命令并发**：active worker 时 `/model` `/clear` 等进入"命令队列"还是拒绝？建议第一版**拒绝**，等下一项完成再处理
2. **daemon cycle 时机**：放在 Application 退出后执行；状态栏显示 `daemon_cycle` 状态
3. **输出区行数上限**：建议 1000 行 + 滚动截断
4. **fake engine 设计**：可控事件序列（delta / tool / done / error / 延迟）模拟真实流式

---

## 十二、实际落地 § P1 降级（H17/R3 同步，2026-09-08）

> 本节由落地侧补写，消除「设计承诺 vs 实际形态」的 H17 完整性破坏。
> 事实来源：`lingclaude/cli/{status,input_queue,interface,app}.py` + `git diff`。

### 12.1 已落地（P1 降级形态）

| 设计项 | 实际形态 | 位置 |
|---|---|---|
| 状态栏 6 字段（§三） | `StatusModel` + `toolbar_fragments`：模型[PINNED]/cwd缩略/上下文占比3色/轮次/任务/挂起×N | `status.py` |
| 状态栏挂载 | PT `bottom_toolbar` 回调，**仅 prompt() 渲染期可见**；Fallback/json/jsonl 自动 no-op | `interface.py:install_bottom_toolbar` |
| 轻量常驻提示 | prompt 行本身带 `[模型|ctx|任务|队列]`，PT/Fallback 都可见；输出区仍保持线性 | `app.py:_status_prompt` |
| 生成中键入 FIFO（§四） | `InputQueue` + `InputPump` 后台线程；EOF 哨兵 `\x00EOF` 信号化传递 | `input_queue.py` |
| 单 worker 硬约束（§四） | 主循环唯一消费者；生成结束后消费挂起队列（斜杠命令即时执行，首个文本作下一轮输入） | `app.py:_interactive_loop` |
| stdin 读者唯一化 | pump **会话级**独占 `session.prompt()`（修复 2026-09-08 "Application is already running" 并发事故）；pump 死亡自动降级阻塞直读 | `app.py:_next_input` |
| 退出语义（§一） | `/quit` `/exit` Ctrl+D 均走 drain() 丢弃清单（半成品不落盘）；空输入不退出 | `app.py:_drain_pending_notice` |
| 环境开关（§九） | `LINGCLAUDE_TUI=0` 强制 Fallback；`=1` 覆盖 `CLI_MODE=plain`；未设置走原语义 | `interface.py:create_session` |
| 逐轮落盘 | 生成结束即 `persist_session()`（非退出时一次性写，R5） | `app.py:967` |

### 12.2 与设计的偏差（有意决策，非遗漏）

| 设计承诺 | 实际形态 | 偏差原因 |
|---|---|---|
| §二 HSplit 全屏布局 + 可滚动输出窗口 | 线性 stdout + 底部单行 toolbar | 全屏 TUI 与 Rich 流式渲染冲突面大，降级为 P1（文档头旧版却写"P2 解决常驻可见性"——即本次 R3 要修正的债） |
| §四 ESC 仅取消当前生成 | **Esc 让位给输入框编辑**；中断语义由 Ctrl+C 承担（pump 模式下） | pump 独占 stdin 后 Esc 键位归 prompt_toolkit 编辑层 |
| §五 状态机 5 态 | 简化为 `空闲 / 生成中 / 工具:<name>` + `挂起×N` 计数 | toolbar 单行宽度约束；guard_pending 态未单独建模 |

### 12.3 验证基线

- `tests/test_cli_input_queue.py` + `tests/test_cli_status.py`：21 passed
- 端到端：TTY 下状态栏随提示符渲染；生成中键入 → 轮结束自动排队执行；退出打印丢弃清单
- 2026-09-09 追加：动态提示头 + `/recover` + `--recover` + JSONL 长任务指标；
  `tests/test_long_task_ops.py` 等精准回归 90 passed

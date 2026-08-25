# RFC — lingclaude CLI 交互形态补全（L1 + L2）

**日期**: 2026-08-25（v1: AtomCode 初稿 / v1.1: 灵克增补 §3.3 I/O 抽象层 + §五测试范式修正 + §八决策点）
**作者**: AtomCode（v1） / 灵克 session 32（v1.1 增补）
**性质**: RFC 设计稿（不包含实现，实现后续排期）
**目的**: 补齐 lingclaude 与 Claude Code / AtomCode / Crush 的最大差距——交互形态（裸 `input()` vs 全 TUI）。本 RFC 覆盖 L1（prompt_toolkit 基础交互）+ L2（Rich Live 流式渲染），L3（Textual 全屏）后置。
**关联**: `docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md`（§2.1 交互形态差距）、`docs/gap_analysis/GAP_ANALYSIS_20260825_CRUSH_DIMENSION.md`、`docs/gap_analysis/GAP_ANALYSIS_20260825_TOTAL_CURRENT.md`（§四 P0-1）、`docs/ROADMAP.md`（T1-7）、本会话产出

---

## 一、背景与现状

### 1.1 问题

lingclaude 交互模式 = 裸 `input("灵克> ")`（`cli/app.py:206`）+ `sys.stdout.write` 逐字符流式。对标产品全是全 TUI：

| | 交互形态 |
|---|---|
| Claude Code | Ink 全 TUI（React 组件） |
| AtomCode | 自研 20521 行 Ink 式 cell-diff 渲染器（56 斜杠命令 + 16 modal + Kitty keyboard protocol） |
| Crush | Bubble Tea TUI（Go） |
| **lingclaude** | **裸 input() + 4 斜杠命令** |

这直接决定"能否日常替代"：内容再强，交互撑不住长会话。

### 1.2 已有资产（不浪费）

- `cli/app.py:137` `_esc_pressed()` — POSIX select + raw tty 的 Esc 打断生成 ✅
- `cli/app.py:212` `_handle_slash_command()` — /help /clear /compact /model 4 个命令 ✅
- `cli/display.py` — Rich Panel/Table/`print_diff`（`display.py:209`）✅
- 工具调用/结果流式事件已存在（`stream_submit` 的 message_delta / tool_call_start / tool_call_end）

### 1.3 缺失清单

| 能力 | 现状 |
|---|---|
| 历史输入（上下键） | ❌ |
| Ctrl 键位（C/D/W/L） | ❌ |
| Tab 补全 / 多行编辑 | ❌ |
| Markdown 渲染 | ❌ |
| 流式渲染（工具面板） | ❌（只逐字符 print） |
| diff 高亮 | 部分（print_diff 有，未接入主循环） |
| 状态栏（模型/token/模式） | ❌ |
| 斜杠命令扩充 | 仅 4 个 |

---

## 二、目标与范围

### 2.1 本 RFC 覆盖（L1 + L2）

- **L1**（1-2 天）：prompt_toolkit 替换 `input()` — 历史/Ctrl 键位/Tab 补全/多行编辑
- **L2**（1-2 周）：Rich Live 流式渲染 — Markdown 渲染/diff 高亮接入/工具调用面板/状态栏/斜杠命令扩充

### 2.2 明确不做（L3 后置）

- Textual 全屏多面板 TUI
- Kitty keyboard protocol / bracketed paste
- cell-diff 渲染器（AtomCode 20521 行级别的自研渲染）

### 2.3 依赖决策（已确认接受）

新增正式依赖：
- `prompt_toolkit` — 输入层（历史/键位/补全）
- `rich` — 渲染层（display.py 已在可选 import，转正式声明）

---

## 三、设计

### 3.1 L1：prompt_toolkit 输入层

**位置**：`cli/app.py` 的 `_read_input()`（现 204-210 行）替换为 prompt_toolkit PromptSession。

**能力**：
- 上下键历史（`PromptSession` 默认）
- Ctrl-C 取消当前输入 / Ctrl-D 退出 / Ctrl-W 删词
- Tab 补全（斜杠命令补全：`/help /clear /compact /model /bg /undo /quit`）
- 多行编辑（Alt-Enter 换行）

**关键点**：
- `_esc_pressed()` 的 raw tty 逻辑与 prompt_toolkit 输入共存——Esc 打断生成仍走原 select 逻辑，输入走 PromptSession，两者不冲突（Esc 在输入态由 prompt_toolkit 处理，在生成态由 select 处理）
- 历史持久化到 `~/.lingclaude/history`（可选，L2 一起）

### 3.2 L2：Rich Live 流式渲染

**位置**：`cli/display.py` 扩展 + `cli/app.py` 的流式消费段（`stream_submit` 事件循环）。

**新增渲染组件**（`display.py`）：

| 组件 | 说明 |
|---|---|
| `print_markdown(text)` | rich.markdown 渲染模型输出 |
| `print_diff_live(diff_text)` | rich.syntax 高亮 diff（接入主循环，替代裸 print） |
| `ToolCallPanel` | Rich Live 面板：工具调用/结果流式展示（tool_call_start/end 事件） |
| `StatusBar` | 底部状态栏：模型 / token 用量 / 当前模式（plan/ask/auto/strict） |

**事件映射**（`stream_submit` → 渲染）：

| stream_submit 事件 | 渲染 |
|---|---|
| `message_delta` | 流式文本（Rich Live 刷新） |
| `tool_call_start` | ToolCallPanel 显示工具名 + 参数 |
| `tool_call_end` | ToolCallPanel 显示结果预览（成功/错误着色） |
| `status` | StatusBar 提示 |
| `hard_interrupt` / `error` | 红色告警 |

**斜杠命令扩充**（`_handle_slash_command`）：
- `/bg` — 后台任务入口（T1-3 run_in_background 落地后接线）
- `/undo` — 回退上一轮（依赖 checkpoint）
- `/quit` — 退出
- `/model` 增强 — 显示当前模型 + token 用量

**状态栏数据源**：`engine.usage`（token 用量）+ `engine.config.mode`（权限模式）+ `engine.config.model`（模型）。

### 3.3 I/O 抽象层（灵克增补 v1.1）

**目的**：避免 CLI 行为变更破坏 WebUI / IDE / 非 TTY 调用；让单测可注入假 I/O。

**新增 `cli/interface.py`**：
```python
from typing import Protocol, Callable, Any

class PromptSessionInterface(Protocol):
    """CLI 输入抽象 — 解耦 _interactive_loop 与具体输入库。"""

    def prompt(self, message: str = "") -> str:
        """阻塞读一行；EOF/中断抛 EOFError / KeyboardInterrupt。"""
        ...

    def push_to_history(self, text: str) -> None:
        """把当前输入写入历史（prompt_toolkit 自动做；Fallback 也调）。"""
        ...

    def stream_print(self, renderable: Any) -> None:
        """生成中流式输出（prompt_toolkit 用 patch_stdout；Fallback 用 sys.stdout.write）。"""
        ...

    def interrupt_event(self) -> Any:
        """返回 threading.Event，set 后取消当前生成（Esc / Ctrl+C 触发）。"""
        ...

class PromptToolkitSession:
    """L1 实现 — 包 prompt_toolkit.PromptSession + rich.live.Live。"""

class FallbackSession:
    """兜底实现 — 原裸 input() + sys.stdout.write + threading.Event。
    WebUI/IDE/CI 强制走这个；CLI TTY 默认走 PromptToolkitSession。"""
```

**入口选择逻辑**（在 `_interactive_loop` 顶部）：
```python
if os.environ.get("LINGCLAUDE_CLI_MODE") == "plain" or not sys.stdin.isatty():
    session = FallbackSession(history_file=...)
else:
    session = PromptToolkitSession(history_file=...)
```

**关键点**：
- **CLI 行为变更对 WebUI/IDE 零影响**——`api.py` 不动，WebUI 走 FastAPI/原生 HTML，与 CLI 完全解耦
- **CI 走 FallbackSession**——避免 prompt_toolkit 在非 TTY 下失败
- **单测可注入假实现**——`FakePromptSession(prompt=lambda _: "...", stream_print=lambda x: None, interrupt_event=lambda: threading.Event())`
- **`_esc_pressed()` 改造**：原 select+tty raw 逻辑由 `PromptToolkitSession.interrupt_event()` 接管；FallbackSession 仍保留 raw tty 兜底

---

## 四、文件改动清单

| 文件 | 改动 |
|---|---|
| `cli/interface.py`（新建） | **§3.3 I/O 抽象层**：`PromptSessionInterface` Protocol + `PromptToolkitSession` + `FallbackSession` 两个实现；`LINGCLAUDE_CLI_MODE=plain` 环境变量兜底 |
| `cli/app.py` | `_read_input()` → 通过 `interface.py` 拿；流式消费段接 Rich Live；斜杠命令扩充；`_esc_pressed` 改由接口的 `interrupt_event()` 触发 |
| `cli/display.py` | 新增 print_markdown / print_diff_live / ToolCallPanel / StatusBar |
| `pyproject.toml` / `requirements*.txt` | 新增 `prompt_toolkit` + `rich` 正式依赖 |
| `tests/test_cli_interaction.py`（新建） | **§五 测试范式**：用 in-memory history + fake stream 真功能测试，替换 `inspect.getsource` 假测试 |

---

## 五、验证

### 5.1 真功能测试（替换现有假测试）

**问题（灵克 session 32 实测）**：`tests/test_t1_wiring.py` 中 T1-7 的 5 个测试用 `inspect.getsource(_interactive_loop)` 验证源码字符串匹配——只证明"代码字面上有 `/help`"，不证明"`/help` 真能跑出 '[斜杠命令]' 文案"。**假测试**。

**修复**：新建 `tests/test_cli_interaction.py`，用 I/O 抽象层做真功能测试：
```python
def test_help_outputs_command_list():
    """注入 FakePromptSession，第一次 prompt 返回 '/help'，验证 stdout。"""
    fake = FakePromptSession(prompt_responses=["/help", "exit"])
    captured = []
    with patch("sys.stdout.write", side_effect=caught.append):
        run_loop(engine=fake_engine, session=fake)
    assert any("斜杠命令" in line for line in captured)

def test_history_persists_across_sessions():
    """PromptToolkitSession(in_memory_history=[...]) 喂 '/help' 后,历史应增加该条。"""
    sess = PromptToolkitSession(history=InMemoryHistory())
    sess.prompt = MagicMock(return_value="/help")
    sess.prompt("/help 测试")
    assert "/help 测试" in sess.history

def test_interrupt_cancels_stream():
    """生成中 set interrupt_event,验证 stream_submit 早退。"""
    fake = FakePromptSession()
    fake.interrupt_event.set()
    with pytest.raises(StreamCancelled):
        list(fake.stream_print_stream(["a", "b", "c"]))
```
**验证项**：
- L1：`PromptSessionInterface` 两个实现都过单测 + 交互冒烟（历史/键位/Tab 补全）
- L2：流式输出 Markdown 渲染 + 工具面板 + 状态栏
- 单测：`tests/test_cli_interaction.py`（替换 `test_t1_wiring.py::TestT1-7` 5 个假测试）
- 不破坏：`_esc_pressed()` 打断、现有 4 斜杠命令、`print_diff` 回归

### 5.2 回归基线

- 全量基线 2205 passed / 3 failed（已知）/ 81 skipped
- L1 落地后应**保持基线或更高**（替换 5 个假测试 → 真测试通过,test 数 +N）
- L2 落地后建议新增 8-12 个真功能测试

---

## 六、风险与对策

| 风险 | 对策 |
|---|---|
| prompt_toolkit 与 raw tty 冲突 | Esc 打断走 select（生成态），输入走 PromptSession（输入态），分离生命周期 |
| rich 转正式依赖体积 | rich 已是 display.py 可选依赖，转正式无新增代码量 |
| 流式渲染性能（大输出） | Rich Live 节流刷新（>200ms 间隔），大输出截断预览 |
| 与 WebUI/API 无关 | 纯 CLI 层改动，不影响 api.py / webui |

---

## 七、结论

L1 + L2 用现成库（prompt_toolkit + rich）补齐交互形态核心差距，1-2 周落地，不追平 L3 全 TUI 广度（延续 CC_DIMENSION 报告"不追平广度"原则）。实现后续排期，本 RFC 先行定稿。

---

## 八、决策点（需灵克 + AtomCode 拍板，v1.1 增补）

| # | 决策点 | 建议 | 影响 |
|---|---|---|---|
| **D1** | **接受 prompt_toolkit 新依赖**（150KB，成熟 8 年库） | 接受 | 锁定 L1 路线；如否决退回 L1 自研（成本翻倍） |
| **D2** | **rich 转正式依赖**（display.py 已可选 import，转正式零代码量） | 接受 | 锁定 L2 路线 |
| **D3** | **一并补 stream_submit 的 Esc 打断**（patch_stdout + threading.Event） | 一并补 | 真正的"生成中可打断"是 stream 体验核心；不补则 L2 仍是半截 |
| **D4** | **`LINGCLAUDE_CLI_MODE=plain` 环境变量兜底** | 必加 | WebUI / IDE / CI 不受 CLI 改动影响；无此兜底，CI 可能挂 |
| **D5** | **真功能测试替换假测试**（in-memory history + fake stream） | 一并做 | 不换则 L1/L2 落地的回归保护仍为 0；建议作为合并前置 |
| **D6** | **历史持久化路径**：`~/.lingclaude/history` 单文件 | 接受 | 默认文件，路径可配置；与其他工具惯例一致 |
| **D7** | **多轮会话状态栏数据**：从 `engine.usage / config.mode / config.model` 取 | 接受 | 现状已具备；无需新增数据源 |

---

## 九、执行计划（6 步落地，v1.1 增补）

| 步骤 | 工时 | 内容 | 验收 |
|---|---|---|---|
| **1. I/O 抽象层** | 1 天 | 建 `cli/interface.py`：`PromptSessionInterface` Protocol + `PromptToolkitSession` + `FallbackSession` 两个实现 | 单测两实现都过 |
| **2. 替换 _read_input** | 2 天 | `_interactive_loop._read_input()` 走接口；history 持久化 `~/.lingclaude/history`；环境变量兜底选择实现 | 上下箭头恢复历史；**真功能测试**(非源码字符串匹配) |
| **3. Ctrl 键位 + Tab 补全** | 1 天 | `Ctrl+C` 软中断 + `Ctrl+L` 清屏 + `Ctrl+R` 历史搜索 + `Ctrl+D` 退出 + `Tab` 补全 7 斜杠命令 | 单测每个键位 + 真机手测 |
| **4. 流式 spinner + Esc 真正打断** | 2 天 | `stream_submit` 期间 `rich.live.Live` 转圈 + 流式 delta 字符级追加 + `threading.Event` 让 Esc 真生效（patch_stdout） | 长 prompt 测试 Esc 提前退出 |
| **5. Markdown + 工具面板 + 状态栏** | 1 天 | `rich.markdown.Markdown` 渲染模型输出（含代码块 Syntax）；`ToolCallPanel` 流式工具调用/结果；`StatusBar` 底部固定 | 含代码块/列表/链接时正确渲染 |
| **6. 真测试 + 文档** | 1 天 | 替换 5 个 `inspect.getsource` 假测试为 in-memory 真测试；`docs/cli/TERMINAL_UX.md` 用户文档（键位表/斜杠命令表）| T1 套件 100% 真测试 |

**总工时**：~7-8 天（与原 L1+L2 估算 1-2 周一致）

**排期建议**：与 T3-1 capability seam RFC 并行；不与 T3-3 瘦身重叠（本会话已提交 T3-3 修复，等待审计落 commit）。

---

## 十、变更记录

- **v1 (2026-08-25 上午)**：AtomCode 初稿。覆盖 L1 + L2 设计、文件改动、风险。
- **v1.1 (2026-08-25 下午)**：灵克 session 32 增补。
  - 新增 §3.3 I/O 抽象层（避免 WebUI/IDE 回归）
  - §五 验证改为"真功能测试"，揭示原 5 个假测试问题
  - 新增 §八 7 个决策点
  - 新增 §九 6 步执行计划
  - 顶部版本/作者/关联文档同步更新

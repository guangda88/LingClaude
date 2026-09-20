# lc TUI 优化方案（最终版，2026-09-20）

> **实施状态（2026-09-20）: P0-1~P0-4 / P1-1~P1-3 / P2-1~P2-3 共 9 项全部落码，
> 回归测试见 tests/test_full_tui.py（TestTuiOptimizationP0 类）。实施中的两处
> 源码级修正（对照原方案）：① P1-1「修饰方向键映射」删除——PT 3.0.53 已内建
> `\x1b[1;5A→ControlUp` 等（ansi_escape_sequences.py:213/242），重复映射有害无益；
> ② P1-2 防御表实测收窄：DECRQM 应答是**终结字节 $**（Dollar-y, \x1b[?2026;1$y），
> 而非方案误写的 `\x1b[?...$y` 正则尾缀误读（$ 非终结字节范围）；DECSCUSR 应答
> \x1b[2 q 形态已收编。另补 SS3（\x1bOA DECCKM 方向键）三字节吞，跨片前缀表
> 加 \x1bO——均为验收场景实测暴露的方案盲点。
> 调研来源：claude(CC) / crush / atomcode 三家 CLI 并行调研 + 本仓库源码逐条查证。
> codex / opencode 因上游配额限流（2026-09-22 18:03:43 重置）缺席本轮，待配额恢复可补跑 `scripts/tui_research_prompt.md`。
> 可信度分级：crush 与 atomcode 真实读取了仓库代码（引用 file:line 抽查全部属实）；CC 给出外部工程视角但 3 处关键断言被查证证伪（见附录 A），已剔除。

## 一、7 问题根因 × 现状 × 方案总表

| # | 症状 | 根因（源码证据） | 现状 | 方案 | 批次 |
|---|------|----------------|------|------|------|
| 1 | 生成期输出冲乱输入 | P1 非全屏形态：repl_io.py:184 直接 `sys.stdout.write`，与 PT 渲染的输入行互踩 tty；P2 全屏已由 _StdoutProxy 架构性免疫（full_tui.py:98-152） | P2 ✅ / P1 ❌ | P1 流式输出改走 `patch_stdout()` | P0-1 |
| 2 | 粘贴前后多出标记 | interface.py:411-421 用 startswith 判定 paste 标记，分片边界（`\x1b[200~` 被内核 read 切开）漏剥 | 部分 | 跨分片状态机（尾部保留 6 字节前缀拼接）+ 提交前 sanitize 兜底 | P0-2 |
| 3 | 多行被截断/分段提交 | 单行模式把粘贴内 `\n` 当 Enter | P2 ✅（multiline+paste 事件）/ P1 ✅（_rl_in_paste 状态机） | 与 P0-2 一并加固分片边界 | P0-2 |
| 4 | 生成结束输入被吞 | input_queue.py:119-163 stop() 三步法（抢救半行→app.exit→join）+ prompt_collect（:185-199）已修主链 | 大体 ✅ | 生成结束路径 finally 必 notify_all（消费端唤醒审计） | P0-3 |
| 5 | 方向键变字面 ^[[A/[27u | kitty keyboard protocol / 焦点(1004)/鼠标(1000/1003/1006) 被前序崩溃 TUI 残留；PT 3.0.53 对表外序列 flush 逐字符兜底（vt100_parser.py:151-171） | 大体 ✅ | 启动复位已上线（lineedit.py:52-90）；补 close() 复位（防自杀残留）+ CSI u 家族语义映射 | P0-4 / P1-1 |
| 6 | 光标移动出现位置码噪声 | 同 5：未映射 CSI（CPR 响应/DECRQM/焦点）兜底成正文 | 部分 | PT 内建 CPR/鼠标识别（vt100_parser.py:19-33）已覆盖大头；扩充 Keys.Ignore 防御表 | P1-2 |
| 7 | PageUp/滚轮看历史；退出后无入口 | PT 原生滚轮只发给焦点控件（BufferControl mouse_handler），输出窗 focusable=False 永不聚焦；全屏 alternate screen 屏蔽终端原生 scrollback | P2 ✅（_OutputScrollControl 拦截） | 维持现实现；补 /history 命令 + 输出窗行上限扩容 | P2 |

## 二、架构结论（三家一致 + 自家事故史验证）

**维持架构 A（prompt_toolkit 单读者全接管）为唯一主线。**

- 架构 B（自管 termios + 自写解析器）：只作为非 TTY/降级路径最小化存在（现状已如此：lineedit.py + interface.py _nonblocking_readline）。
- 架构 C（PTY + 异步读 + 状态机）：仅吸收其思想——「未知序列显式处置」进入 A 的防御表（P1-1/P1-2）。
- 三铁律（crush 总结，与 2026-09-08/09/12/15/18 历次事故完全吻合）：
  1. stdin 读者恒为 1；
  2. termios 恢复只用 TCSADRAIN（repl.py:149 已固化），永不 TCSAFLUSH；
  3. 启动主动复位终端增强模式，不假设前一个程序是良民。

## 三、落地清单

### P0-1 P1 形态流式输出 patch_stdout 化（问题 1 根治）
- 现状：interface.py:113 docstring 声称「prompt_toolkit 用 patch_stdout」，实际全仓零调用（grep 实证），流式走 repl_io.py:184 裸写。
- 改法：P1（逐次 prompt + InputPump）形态下，流式输出段包 `prompt_toolkit.patch_stdout.patch_stdout()` context；它内置「擦当前行→写输出→重绘提示符」算法。无 PT 时维持现裸写（FallbackSession 本就独占输出）。
- 验收：P1 形态下生成期输入行内容在输出滚动后仍完整、位置正确。

### P0-2 粘贴标记跨分片状态机 + 提交前兜底（问题 2/3）
- 状态机改造（interface.py _nonblocking_readline 的 drain 段与 Esc 分支）：
  - 任何位置匹配开/闭标记并翻转 `_rl_in_paste`，废除 startswith 前缀判定；
  - 分片尾部若以 `\x1b[200` 等**不完整标记前缀**结尾，保留 ≤6 字节与下一分片拼接后再判定；
  - paste 段内 `\n` 保持正文语义（已有），跨调用状态已持久（interface.py:327）。
- 兜底（低成本必做）：prompt() 返回前 `text.replace("\x1b[200~","").replace("\x1b[201~","")`，上游漏网也到不了 LLM。
- 验收：mock 分片边界把 `\x1b[200~ab` / `cd\x1b[201~` 切成两片读入，提交文本 == `abcd`。

### P0-3 生成结束唤醒审计（问题 4 残余）
- `_run_stream_turn` 正常/异常退出路径 finally 中确认消费端被唤醒（FullTui prompt 的 `_submit_cond.notify_all` / InputQueue 语义）；
- 状态栏「挂起×N」已有（pending_submissions），补充区分「未收到」与「未消费」的提示语。

### P0-4 终端复位双保险（问题 5 收尾）
- 启动复位已有（repl.py:944）；
- **补 close()/_restore_tty 路径再发一次 reset_terminal_key_modes()**：防止自家进程异常退出后把污染留给下一个进程（对称卫生：清别人残留，也别留自己的）；
- future-proof 注释：PT 3.0.53 无 kitty 协议支持（全库 grep 实证），任何人不得按外部建议「启用 kitty protocol」——启用而无解析器会让方向键 100% 变字面。

### P1-1 CSI u 家族语义映射（问题 5/6 收敛核心）
- 新增静态映射（经 interface._patch_pt_modifier_enter 同一机制）：
  - `\x1b[27u` → Enter（kitty 残留模式下 Enter 仍能提交）；
  - `\x1b[27;5u` / `\x1b[27;2u` → (Escape, ControlM)（修饰 Enter 换行）；
  - `\x1b[1;5A/B/C/D` 等修饰方向键 → 对应方向键。
- **禁止把 CSI u 家族映射为 Keys.Ignore**：`\x1b[27u` 是残留模式下的 Enter，Ignore 会让用户回车失灵——这是「方向键变字面 + 回车无响应」并发症状的解释链。
- 其余不可识别 CSI u 变体（字母键编码等）→ Keys.Ignore 兜底。

### P1-2 Keys.Ignore 防御表扩充（问题 6）
- 新增：DECRQM 响应 `\x1b[?...$y`、`\x1b[...$y`；DECSCUSR `\x1b[... q`。
- CPR 响应（`\x1b[r;cR`）与 SGR 鼠标（`\x1b[<...M/m`）PT 内建识别（vt100_parser.py:19-33 实证），无需重复。
- 开发期手段：包一层 Vt100Input 打印未匹配序列，逐个收编（atomcode 建议）。

### P1-3 _StdoutProxy 转义清洗（窗口层防御）
- 现状：full_tui.py:111-126 逐字符累积，终端残留的转义序列会字面进输出窗。
- 改法：写入侧对 `\x1b` 启动的序列做与 P1 输入路径同款的「读到终结字节整体吞」清洗（CSI 终结字节 ∈ @-~）。

### P2-1 /history 命令（问题 7 退出后入口）
- 复用 core/session.py:59 SessionManager（list_sessions :209 已存在）+ .lingclaude/sessions/ 快照；
- `/history [N]`：列出最近 N 条会话；`/history show <id>` 用 `${PAGER:-less}` 打开对话记录（cc 建议，实现成本极低）。

### P2-2 输出窗行上限扩容
- MAX_OUTPUT_LINES 800 → 5000（约 0.5MB 内存，长会话生成内容不再被静默裁剪）。

### P2-3 全屏健康自检横幅
- start() 后 1s 检查 `self._running and self._app.is_running`，失败时 stderr 留痕并显式提示「已降级为简易输入模式」（消除用户不知情降级）。

## 四、验收测试
- 每项 P0 配套回归测试进 tests/test_full_tui.py（分片 mock：`b"\x1b[200~ab"` + `b"cd\x1b[201~"` 两片；`b"\x1b[200"` + `b"~ab"` 跨分片标记）。
- 手工验收矩阵：kitty / tmux / gnome-terminal / VS Code terminal 四类终端过一遍 5/6 症状。

## 附录 A：外部建议查证记录（防幻觉归档）

| 断言 | 来源 | 查证结论 |
|------|------|---------|
| PT ≥3.0.43 有 `enable_kitty_protocol=True` | CC | **证伪**：本机 3.0.53 全库无 "kitty" 字样。启用建议剔除，反成教训（P0-4 注释） |
| 发 CSI ? u 查询终端能力 | CC | PT 3.0.53 输入/输出层均无该查询实现，剔除 |
| `app.pasted_text` 可判断粘贴块 | CC | **证伪**：PT 全库无此属性 |
| `input.flush_keys()` 清积压键 | CC | 属实（input/base.py:47），留作 P0-3 参考 |
| BufferControl.mouse_handler 焦点限制导致输出窗滚轮失效 | crush/atom | 属实（full_tui.py:72-95 的子类化正是解法，已在库中） |
| PT 内建 CPR/鼠标序列识别 | crush（间接） | 属实（vt100_parser.py:19-33），CC「需自写过滤」不必要 |
| PT 默认 paste 处理把 `\r\n→\n` 整块插入不触发 accept | 实测 | 属实（key_binding/bindings/basic.py:239-249），atom 的「accept 被 Enter 短路」疑虑排除 |
| TCSADRAIN 而非 TCSAFLUSH | crush/atom | 属实且已固化（repl.py:149） |

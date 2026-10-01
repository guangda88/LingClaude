"""REPL I/O 层（P4.1 从 cli/app.py 拆出）— 流式事件渲染 + Esc 打断监听。

拆分说明见 repl.py 模块 docstring；本文件函数体自 app.py 原样迁移。
"""

import json
import os
import select
import shutil
import sys
import termios
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lingclaude.cli.interface import PromptSessionInterface

from lingclaude.core import policy_loader

# P0-行缓冲:跨事件聚合流式 delta,残行待下次事件或 flush 收尾
_stream_line_buf: list[str] = []
# B(2026-09-26): 无换行流防御（atomcode retained.rs 1MiB cap 借鉴，行缓冲
# 收紧到 64KiB）。病态流（minified JSON/base64 无换行 delta）会让残行
# 缓冲无限增长；达上限强制断行输出（内容不丢，只是提前落一行）。
_STREAM_LINE_BUF_MAX = 65536
_stream_lines_emitted = 0  # 本轮已输出行数(done 时用于 ANSI 擦除重渲染)

# 2026-09-29 Markdown 表格对齐：模型按"字符数"手感补白，终端按显示列
# (全角=2/半角=1) 排版 → 含 CJK 的 `|` 表竖线逐行错位。修复：连续 `|`
# 行块暂存，块结束（下一非 `|` 行 / flush 点）后按显示列取每列最大宽、
# 统一补半角空格再输出。仅影响 plain 模式正文文本流；代价是表格块整体
# 延迟到块结束才显示（通常几行，无感）。
_table_buf: list[str] = []

# 2026-09-29 灵元 R2 路线1：表格渲染规则外置 policies/table_render.yaml（热更 data，
# 不改 module）。与 interface.py SGR 白名单同一先例：策略缺失/损坏回退内置默认。
_FALLBACK_TABLE_POLICY: dict[str, Any] = {
    "enabled": True,
    "pad_char": " ",
    "min_col_width": 0,
    "row_prefix": "|",
    "row_suffix": "|",
    "max_block_rows": 200,
}


def _table_policy() -> dict[str, Any]:
    """读取表格渲染策略（mtime watch 热更）；失败/缺项回退内置默认。"""
    try:
        from lingclaude.core.policy_loader import get as _policy_get
        data = _policy_get("table_render")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    merged = dict(_FALLBACK_TABLE_POLICY)
    for k in merged:
        v = data.get(k)
        if v is not None:
            merged[k] = v
    # 类型防护：坏 yaml 不得污染渲染路径
    if not isinstance(merged["pad_char"], str) or len(merged["pad_char"]) != 1:
        merged["pad_char"] = " "
    if not isinstance(merged["max_block_rows"], int) or merged["max_block_rows"] < 2:
        merged["max_block_rows"] = 200
    if not isinstance(merged["min_col_width"], int) or merged["min_col_width"] < 0:
        merged["min_col_width"] = 0
    merged["enabled"] = bool(merged["enabled"])
    return merged


def _disp_w(text: str) -> int:
    """显示列宽（全角 CJK=2，半角=1；与 interface._disp_width 同口径）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in text)


def _is_table_row(line: str, policy: dict[str, Any] | None = None) -> bool:
    """判定表格行：按策略前后缀起止（分隔行 |---|---| 也是表格行）。"""
    p = policy or _table_policy()
    s = line.strip()
    return len(s) > 1 and s.startswith(p["row_prefix"]) and s.endswith(p["row_suffix"])


def _table_grid(rows: list[str]) -> list[list[str]]:
    """`|`行 → cells（去首尾空部件）。_pad_table_block 与 _defuse_wide_tables 共用。"""
    grid: list[list[str]] = []
    for line in rows:
        s = line.strip()
        grid.append([c.strip() for c in s[1:-1].split("|")])
    return grid


def _is_sep_cell(c: str) -> bool:
    """markdown 表分隔单元格：---、:---:、---: 等。"""
    return bool(c) and set(c) <= set("-: ")


def _render_max_width() -> int:
    """窗内可用渲染宽度（2026-10-01 宽表错乱修复）。

    输出窗右侧有滚动条(1列)+余量(1列)，富文本渲染宽度若取全终端宽，
    宽表格行必超出窗实宽 → prompt_toolkit 二次软折 → 边框全错位。
    统一 -2；非 TTY 回落 78。rich 渲染与流式 plain 表格共用此值。
    """
    import shutil
    import sys

    try:
        cols = shutil.get_terminal_size().columns or 80
    except Exception:  # noqa: BLE001
        cols = 80
    return max(60, cols - 2) if sys.stdout.isatty() else 78


def _stack_table_block(grid: list[list[str]]) -> list[str]:
    """宽表堆叠降级：每行一条记录，其余列变「列名: 值」缩进列表。

    宽表（列宽总和超窗）无论 rich 画框还是 plain 补白，单行都超窗宽
    → 窗内二次软折 → 边框/竖线全错位（2026-10-01 用户实报）。堆叠行
    无横向对齐结构，软折只影响文本自身，永不错乱；内容零丢失。
    """
    headers = grid[0] if grid else []
    out: list[str] = []
    for r in grid[1:]:
        cells = [r[i] if i < len(r) else "" for i in range(len(headers))]
        # 分隔行（| --- |---| 形态）是结构噪音，堆叠格式里跳过
        if cells and all(c == "" or _is_sep_cell(c) for c in cells):
            continue
        head = cells[0] if cells else ""
        out.append(f"- {head}" if head else "-")
        for name, val in zip(headers[1:], cells[1:]):
            if val:
                out.append(f"  - {name}: {val}" if name else f"  - {val}")
    return out


def _table_grid_width(grid: list[list[str]]) -> int:
    """按 _pad_table_block 同口径估算补白后总宽（含分隔符开销）。"""
    ncols = max((len(r) for r in grid), default=0)
    widths = [0] * ncols
    for r in grid:
        for i in range(min(len(r), ncols)):
            widths[i] = max(widths[i], _disp_w(r[i]))
    return sum(widths) + 3 * ncols + 2 if ncols else 0


def _defuse_one_table(buf: list[str]) -> list[str]:
    """单表判定：宽表(≥2列且总宽超限)转堆叠，窄表原样。"""
    grid = _table_grid(buf)
    ncols = max((len(r) for r in grid), default=0)
    if ncols >= 2 and _table_grid_width(grid) > _render_max_width():
        return _stack_table_block(grid)
    return buf


def _defuse_wide_tables(text: str) -> str:
    """rich 渲染前宽表转堆叠（2026-10-01 宽表错乱修复）。

    rich Table 列有最小宽，CJK 长句无空格导致列宽压不下去：实测 5 列
    宽表在 width=118 的 console 下输出 max=289 显示宽（超 144%），进窗
    二次软折 → 边框全错位。唯一可靠治法是不让宽表走表格布局：切列实测
    总宽超限（与 _pad_table_block 同口径同限宽）→ markdown 源改写为堆
    叠列表。窄表原样保留（边框视觉价值高）。
    """
    lines = text.split("\n")
    out: list[str] = []
    buf: list[str] = []
    for ln in lines:
        if _is_table_row(ln):
            buf.append(ln)
            continue
        if buf:
            out.extend(_defuse_one_table(buf))
            buf = []
        out.append(ln)
    if buf:
        out.extend(_defuse_one_table(buf))
    return "\n".join(out)


def _pad_table_block(rows: list[str], policy: dict[str, Any]) -> list[str]:
    """表格块按显示列重排补白，返回成品行。

    2026-09-30 从 _flush_table_buf 抽出：落窗（流式路径）与指纹
    （_expected_window_lines）共用同一实现——两套各写一遍必然漂移，
    漂移即指纹恒失配、原位上色静默回退（当日已复现的事故）。
    2026-10-01 宽表错乱修复：列宽总和超 _render_max_width() 的表
    （≥2列）改堆叠降级（_stack_table_block），落窗与指纹同走此分支
    保持同构。单列表无对齐意义，不参与。
    """
    pad_char = policy["pad_char"]
    # 切列：strip 后去首尾部件，按内部分隔切（与 _defuse_wide_tables 共用）
    grid = _table_grid(rows)
    ncols = max(len(r) for r in grid)
    if ncols >= 2 and _table_grid_width(grid) > _render_max_width():
        return _stack_table_block(grid)
    widths = [policy["min_col_width"]] * ncols
    for r in grid:
        for i in range(min(len(r), ncols)):
            widths[i] = max(widths[i], _disp_w(r[i]))
    out_lines = []
    for r in grid:
        out = []
        for i in range(ncols):
            cell = r[i] if i < len(r) else ""
            pad = widths[i] - _disp_w(cell)
            out.append(cell + pad_char * max(pad, 0))
        out_lines.append("| " + " | ".join(out) + " |")
    return out_lines


def _flush_table_buf() -> None:
    """按显示列重排暂存的表格块并逐行输出（列宽取全列最大，规则来自策略）。"""
    global _table_buf
    if not _table_buf:
        return
    p = _table_policy()
    rows = _table_buf
    _table_buf = []
    for ln in _pad_table_block(rows, p):
        _stream_write(ln + "\n")
        globals()["_stream_lines_emitted"] = globals()["_stream_lines_emitted"] + 1
        _ledger_append("body", ln)


# _StdoutProxy 超长半行强制断行阈值（full_tui._frag > 4096 即 flush，
# 故首个窗行恰 4097 字符）；指纹侧必须复刻同一断行粒度。
_PROXY_FRAG_FLUSH_AT = 4097




def _expected_window_lines(content: str) -> list[str]:
    """done.content → 窗内期望素字行序列（原位替换指纹专用）。

    与流式落窗管线逐字符同构（2026-09-30 表格重排失配修复）：
      1. _strip_ansi_text —— _stream_write 出口无条件清洗
      2. 表格行块按同一策略重排补白（_pad_table_block 与落窗共用）
      3. \r 丢半行、超长行 4097 字符断行 —— _StdoutProxy 写入语义
      4. content 尾换行：content.split("\n") 末尾的 "" 是流关闭信号，
         streaming 路径不把它变成窗内空白行。旧代码 pop() 删了 lines
         末尾但没删 out 末尾的 ""（由 _flush_buf 产生），导致 fingerprint
         比窗口多一行，表格轮 100% 回退。
    未来任何改变流式落窗形态的规则，必须同步改本函数（锚定注释）。
    """
    from lingclaude.cli.interface import _strip_ansi_text

    p = _table_policy()
    lines = content.split("\n")
    out: list[str] = []
    buf: list[str] = []

    def _flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        rows, buf = buf, []
        for ln in _pad_table_block(rows, p):
            # 换行统一由 content.split("\n") 提供（缝隙里的 \n）。表格行 flush
            # 不在这里补 \n——_flush_table_buf 写 ln+"\n"，那是 streaming 真实出口。
            # 2026-09-30 空行保真：max(len,1) 保证空行至少产出一个分块。
            n = max(len(ln), 1)
            out.extend(
                ln[i : i + _PROXY_FRAG_FLUSH_AT] for i in range(0, n, _PROXY_FRAG_FLUSH_AT)
            )

    for raw in lines:
        line = _strip_ansi_text(raw)
        if p["enabled"] and _is_table_row(line, p):
            buf.append(line)
            if len(buf) >= p["max_block_rows"]:
                _flush_buf()
            continue
        _flush_buf()
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        if line:
            out.append(line)
        else:
            out.append("")  # 空行
    _flush_buf()
    # 2026-09-30 content 尾换行修复：content.split("\n") 末尾的 ""
    # 对应 content 末尾的 "\n"（流关闭信号），streaming 路径把它变成
    # out.append("")，但真实窗口不含这个空行。剥掉末尾空元素使
    # fingerprint 与真实窗口行数对齐。（content 中间真正的空行
    # 不会变成末尾空元素，因为后面还有非空行顶着。）
    if content.endswith("\n") and out and out[-1] == "":
        out.pop()
    return out

def render_markdown_lines(text: str) -> tuple[list[str], list[list[tuple[int, int, str]]]]:
    """markdown → (净化行, 每行 SGR spans)。原位上色共用渲染入口。

    白名单 Theme 给标题明亮前景（SGR 90-97，2026-10-01 标题醒目化）；
    force_terminal=True 产出 ANSI 再经 _extract_sgr_styles 解析为零 ESC
    净化文本。此版 rich 标题样式键无 .style 后缀（rich/default_styles.py:155）。
    """
    from io import StringIO

    from rich.console import Console
    from rich.markdown import Markdown
    from rich.theme import Theme

    _heading_theme = Theme(
        {
            "markdown.h1": "bold bright_cyan",
            "markdown.h2": "bold bright_green",
            "markdown.h3": "bold bright_magenta",
            "markdown.h4": "bold bright_blue",
            "markdown.h5": "bold bright_yellow",
            "markdown.h6": "bold bright_white",
        }
    )
    buf = StringIO()
    cols = _render_max_width()
    ansi_console = Console(file=buf, force_terminal=True, width=cols, theme=_heading_theme)
    ansi_console.print(Markdown(_defuse_wide_tables(text)))
    styled_lines: list[str] = []
    spans_list: list[list[tuple[int, int, str]]] = []
    from lingclaude.cli.interface import _extract_sgr_styles

    for ln in buf.getvalue().split("\n"):
        t, sp = _extract_sgr_styles(ln)
        styled_lines.append(t)
        spans_list.append(sp)
    return styled_lines, spans_list


_OUTPUT_FORMAT = "plain"  # P0-2: plain | json | jsonl
_json_event_buffer: list[dict[str, Any]] = []  # json 模式事件缓冲

# 分段原位上色（2026-10-01）：带工具调用轮次的窗内区段 = 正文行与工具轨迹行
# （空行 + "  [bash] cmd=… ... " + "✅ preview"）交错，与 done.content 推导的
# 纯正文指纹整段比对必失配 → 回退素字（真实轮几乎全中，用户长期只见素字）。
# 现按实写顺序记录本轮工具轨迹行，done 时窗内区段按轨迹逐行归类：轨迹行原样
# 保留，正文行段与 rich 渲染段指纹比对后上色。env 关闭可退回整段旧行为。
_turn_tool_trace: list[str] = []
_turn_tool_trace_on = os.environ.get("LINGCLAUDE_SEG_STYLE", "1") != "0"


def _trace_on() -> bool:
    return _turn_tool_trace_on


# 原位上色台账（2026-10-01 第三代定位）：流式期实录每一行实际写入——
# (kind, line)，kind ∈ {"body","tool"}（空行按写入者归类）。done 时与
# 输出窗**尾部**做后缀对齐：对上才替换正文段，对不上静默不动窗。
# 取代「绝对行号 mark + done.content 猜测式指纹」——生产日志实证
# （tui_style_debug.log 12:03 FP_MISMATCH）：粘贴回执 / resync 重绘 /
# 标记误消费会让 mark 指向陈旧区段，从错误起点整段比对必然失配，
# 用户长期只见素字。台账尾部天然对应窗尾，无起点漂移问题。
_turn_ledger: list[tuple[str, str]] = []
# 同轮重复 done 守卫：第二次起的同 content done 不再补空行/重复替换
_last_done_content: str = ""
# tool_call_start 暂存前缀（tool_call_end 合并成窗内单行后记台账）
_turn_pending_tool: str = ""


def _turn_trace_reset() -> None:
    """轮次起点清台（set_streaming False→True 与 repl 流循环双保险调用）。

    _last_done_content 一并清：重复 done 守卫的生命周期=单轮流式期，
    跨轮相同内容（重复提问）是合法新轮，不得误杀。
    """
    global _last_done_content, _turn_pending_tool
    _turn_tool_trace.clear()
    _turn_ledger.clear()
    _last_done_content = ""
    _turn_pending_tool = ""


def _ledger_append(kind: str, line: str) -> None:
    """台账追加（rstrip 归一，与窗内比对口径一致）；异常静默——不反噬输出链。"""
    try:
        _turn_ledger.append((kind, line.rstrip()))
    except Exception:  # noqa: BLE001 — 诊断增强路径绝不反噬
        pass

# H19: bracketed paste 包裹标记（\x1b[200~ 开 / \x1b[201~ 闭）
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"

# 2026-09-18 吞字修复:_esc_pressed 生成期探测时读到非 Esc 字节（用户在
# 打字）——旧实现静默吞掉，正文缺字。现在塞进 replay 缓冲，由
# FallbackSession._nonblocking_readline 的 drain 循环优先取回拼进输入行。
_REPLAY_LOCK = threading.Lock()
_REPLAY: list[bytes] = []


def replay_stdin_bytes() -> bytes:
    """取回 Esc 探测线程替读的字节（原子弹出全部）。"""
    with _REPLAY_LOCK:
        if not _REPLAY:
            return b""
        data = b"".join(_REPLAY)
        _REPLAY.clear()
        return data


# P0-1（2026-09-20，TUI 优化方案）: P1 形态流式输出桥。
# 生成期流式输出此前直接 sys.stdout.write（:184 等），与 prompt_toolkit
# 渲染的输入行/底部状态栏互踩 tty（输出冲乱输入的根因）。
# 桥接层：P1 形态生成期由 repl.py 用 patch_stdout() 进入 PT 托管窗口，
# 此时 sys.stdout 是 PT StdoutProxy —— 本层只写 sys.stdout（代理内部
# run_in_terminal：隐藏提示符→输出→重绘提示符）。代理缺席时退回裸写。
# 注意 StdoutProxy 自带 200ms 合并刷新，_stream_write 不再额外缓冲；
# 非 PT 环境（Fallback 独占输出/CI 管道）短路直写，行为不变。
_stream_bridged = False  # repl.py 生成期进入 patch_stdout 时置 True


def set_stream_bridged(bridged: bool) -> None:
    """P1 生成期进入/退出 patch_stdout 托管窗口时由 repl.py 调用。"""
    global _stream_bridged
    _stream_bridged = bool(bridged)


# 乱码第四路径修复（2026-09-20）：全屏 TUI 托管旗标。
# 症状：每条回复生成完毕时 repl_io.py done 分支调 print_markdown(content)
# 用 rich 重渲染「正式版」，而 display.py:49 的 Console(stderr=True) 把带
# SGR 的渲染结果写进 stderr 直达真实终端——完全绕开 stdout 侧全部清洗
# （proxy/append_output/回放/stream_write 四处），全屏 TextArea 里 0x1b
# 渲染成 '?' 再漏 '[48;5;235m' 明文（rich Markdown 代码块底色指纹）。
# 处置：全屏 TUI 下输出窗已有流式全文，done 重渲染冗余且必乱 → 跳过；
# plain 单行模式不走此旗标，保留 rich 彩色渲染（真实终端上是设计意图）。
_full_tui_managed = False


def set_full_tui_managed(managed: bool) -> None:
    """全屏 TUI 会话启动/收尾时由 repl.py 调用（:1028 安装输出源处）。"""
    global _full_tui_managed
    _full_tui_managed = bool(managed)


def is_full_tui_managed() -> bool:
    """P2 单一输出 owner（2026-09-20）：display._get_console 据此选路。

    True = 全屏 TUI 托管期 → rich Console 应写 sys.stdout（已被
    _StdoutProxy 接管，片段进输出窗统一清洗），而非 stderr 直通终端。
    """
    return _full_tui_managed


def _stream_write(s: str) -> None:
    """流式输出统一出口：PT 托管期写代理，其余清洗后裸写并 flush。

    2026-09-20 乱码修复：非托管期裸写分支此前不清洗，模型回复内嵌
    SGR（\\x1b[1;4m…）直落终端/下游。模型文本中的转义序列是「数据」
    而非渲染指令，统一剥除（与 full_tui._write_via_buffer 同一剥离器，
    幂等无害）。已知边界：SGR 序列恰被 chunk 边界切开时会漏出末段残片
    （如 "m"），概率低且噪声量级远小于完整序列，先修大头留观察。
    """
    from lingclaude.cli.interface import _strip_ansi_text  # 函数内延迟 import 防环

    s = _strip_ansi_text(s)
    if _stream_bridged and sys.stdout is not None:
        try:
            sys.stdout.write(s)
            sys.stdout.flush()
            return
        except Exception:  # noqa: BLE001 — 代理异常退回裸写，输出不丢
            pass
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except (OSError, ValueError, AttributeError):
        pass  # 退出期管道断裂：静默（原实现同样会炸，这里收口）


def set_output_format(fmt: str) -> None:
    """P0-2: 设置输出格式（plain | json | jsonl）。原 app.py 模块级 global 赋值。"""
    global _OUTPUT_FORMAT
    _OUTPUT_FORMAT = fmt


def get_output_format() -> str:
    return _OUTPUT_FORMAT


def _esc_pressed() -> bool:
    """T1-7: 非阻塞检测 Esc(0x1b) 按键。POSIX select + tty 半原始模式，超时 0.05s。

    2026-09-16 修复「[201~ 残留」: 此前读到 \\x1b 单字节即返回 True，
    转义序列（方向键 \\x1b[A、粘贴包裹 \\x1b[200~..\\x1b[201~）的剩余字节
    留在内核缓冲区，稍后被 prompt_toolkit/裸读回显成 "[201~" 等残骸。
    现改为: \\x1b 后 20ms 静默（孤立 Esc）才判打断；多字节序列整体排空
    消费，返回 False 不误触、不留残字节；非 ESC 杂散字节同样静默吞掉。
    """
    try:
        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            # 修复:此前 tty.setraw 会清除 OPOST(输出后处理),而本线程生成期间
            # 每 50ms 循环进出 raw 模式 → 流式输出大多落在 OPOST 关闭窗口,
            # 终端收到裸 LF 不回车 → "空格逐行累加"阶梯缩进。
            # 只关 ICANON/ECHO(非阻塞读所需),保留 OPOST/ISIG:
            # \n 仍被内核转 \r\n,Ctrl+C 中断语义不变。
            new = [list(x) if isinstance(x, list) else x for x in old]
            new[3] &= ~(termios.ICANON | termios.ECHO)  # lflag
            new[6][termios.VMIN] = 0
            new[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new)
            readable, _, _ = select.select([fd], [], [], 0.05)
            if not readable:
                return False
            first = os.read(fd, 1)
            if not first:
                return False
            if first != b"\x1b":
                # 2026-09-18 吞字修复:非 Esc 字节 = 用户生成期真实键入，
                # 替读后不能凭空吞掉 —— 塞回 replay 缓冲，输入行读端取回。
                with _REPLAY_LOCK:
                    _REPLAY.append(first)
                return False
            # \x1b 后 20ms 无跟随字节 → 孤立 Esc → 打断
            r2, _, _ = select.select([fd], [], [], 0.02)
            if not r2:
                return True
            # 转义序列（方向键/粘贴包裹等）:整体排空,不留残字节,不算打断。
            # H19 粘贴段识别:分片到达时以 \x1b[200~ 为界——paste 段内只认
            # 结束标记,静默超时不再提前放弃(否则粘贴首个分片慢于 20ms
            # 到达时,\x1b[200~ 前缀会被误判为孤立 Esc,正文全部丢失);
            # paste 段外维持旧语义(20ms 静默即孤立序列,整体消费丢弃)。
            in_paste = False
            for _ in range(256):  # 上限防异常输入流死循环
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                if in_paste:
                    # 粘贴正文不做打断信号(正确语义),整段消费到闭合标记为止
                    if bytes(chunk).endswith(_PASTE_END):
                        in_paste = False
                        break
                elif bytes(chunk).endswith(_PASTE_END):
                    break
                elif bytes(chunk).endswith(_PASTE_START):
                    in_paste = True
                r2, _, _ = select.select([fd], [], [], 0.05)
                if not r2 and not in_paste:
                    break
            return False
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 — 非 tty/无 termios 时静默禁用 Esc 打断
        return False


def _esc_listen_loop(session: "PromptSessionInterface", stop: threading.Event) -> None:
    """Step 4: 后台线程监听 Esc → set interrupt_event（生成态 stdin 空闲）。

    审计#6 修复:此前退出条件只有 interrupt_event — 流结束后上层立刻 clear()
    把它"复活"，没按过 Esc 的线程永不退出 → 线程逐轮堆积且持续抢 stdin
    （setraw/read 吞掉 prompt_toolkit 正在读的键入字符）。增加 per-turn
    stop 事件，回合结束必退。
    2026-09-18 吞字修复:退出前替读到的用户键入不再凭空丢弃 —— replay
    缓冲由 FallbackSession._nonblocking_readline 优先取回；若本轮从未进入
    非阻塞读（流正常结束），残留字节直接排队给下一次 prompt 的读端
    （此处无法安全写回 fd —— OPOST/回显语义不确定），只做日志留痕。
    """
    while not stop.is_set() and not session.interrupt_event().is_set():
        if _esc_pressed():
            session.interrupt_event().set()
            break



def _flush_stream_line() -> None:
    """P0-行缓冲:输出聚合中的不完整行(补换行),清空缓冲。"""
    if _stream_line_buf:
        text = "".join(_stream_line_buf)
        _stream_line_buf.clear()
        _stream_write(text + "\n")
        globals()["_stream_lines_emitted"] = globals()["_stream_lines_emitted"] + 1
        _ledger_append("body", text)
    # 表格块随 flush 点强制输出（tool_call/done/error 打断表块时兜底）
    if _table_buf:
        _flush_table_buf()


def _handle_stream_event(event: dict[str, Any]) -> None:
    global _turn_pending_tool, _last_done_content
    # P0-行缓冲:流式 delta 按行聚合,只在行边界输出 — 根治半截表格行与
    # 多写入者交错导致的"空格逐行累加"错位(对齐 atomcode UiLine 语义行)。
    etype = event.get("type")
    if _OUTPUT_FORMAT in ("json", "jsonl"):
        # P0-2: 机器可读输出。jsonl = 每事件一行；json = 缓冲,done 时汇总单对象。
        payload = {k: v for k, v in event.items() if k != "content"} if _OUTPUT_FORMAT == "jsonl" else None
        if _OUTPUT_FORMAT == "jsonl":
            _stream_write(json.dumps({"type": etype, **({"text": event["text"]} if "text" in event else {}), **(payload or {})},
                             ensure_ascii=False) + "\n")
            return
        _json_event_buffer.append(event)
        if etype == "done":
            _stream_write(json.dumps({
                "type": "done",
                "content": event.get("content", ""),
                "events": [{"type": e.get("type"), **({"text": e["text"]} if "text" in e else {})}
                           for e in _json_event_buffer],
            }, ensure_ascii=False) + "\n")
            _json_event_buffer.clear()
        elif etype == "error":
            _json_event_buffer.clear()
        return
    if etype == "text_delta":
        _stream_line_buf.append(event["text"])
        # 聚合后一次性切行:完整行立即输出,残行留缓冲
        pending = "".join(_stream_line_buf)
        _stream_line_buf.clear()
        while "\n" in pending:
            line, _, pending = pending.partition("\n")
            # 2026-09-29 表格对齐:连续 | 行暂存为块,块结束后按显示列重排。
            # 规则走 policies/table_render.yaml（热更）：enabled=false 时直出不重排。
            _tp = _table_policy()
            if _tp["enabled"] and _is_table_row(line, _tp):
                _table_buf.append(line)
                # 病态流防御：超上限按当前块 flush 开新块（不丢行）
                if len(_table_buf) >= _tp["max_block_rows"]:
                    _flush_table_buf()
                continue
            if _table_buf:
                _flush_table_buf()
            _stream_write(line + "\n")
            globals()["_stream_lines_emitted"] += 1
            _ledger_append("body", line)
        if pending:
            # E13 外置（2026-09-30）：上限走 tuning.stream_line_buf_max（钳位
            # [1KiB, 1MiB]），代码常量只兜底
            if len(pending) >= policy_loader._tuned(
                "stream_line_buf_max", _STREAM_LINE_BUF_MAX, lo=1024, hi=1_048_576
            ):
                # B(2026-09-26): 无换行流防御——达上限强制断行（不丢内容）
                _stream_write(pending + "\n")
                globals()["_stream_lines_emitted"] += 1
            else:
                _stream_line_buf.append(pending)
    elif etype == "tool_call_start":
        _flush_stream_line()
        name = event.get("name", "?")
        args = event.get("arguments", "")
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                args_preview = " ".join(f"{k}={v}" for k, v in list(parsed.items())[:3])
            else:
                args_preview = str(parsed)[:60]
        except (json.JSONDecodeError, TypeError, AttributeError):
            args_preview = args[:60] if isinstance(args, str) else str(args)[:60]
        # 分段上色轨迹（2026-10-01）：前导 \n 在窗内产生一个空行，随后是
        # 未闭合的 "  [name] args ... "（tool_call_end 才补 ✅/❌ 收尾）。
        # 按实写顺序记入轨迹，done 分段原位上色据此逐行归类。轨迹行一律
        # rstrip 存储：窗内比对走 rstrip 归一，且 10:02 生产日志实证前缀行
        # 与结果行在 drain 竞态下各落一行（不合并），轨迹照实分行记。
        _turn_tool_trace.append("")
        _turn_tool_trace.append(f"  [{name}] {args_preview} ...")
        # 台账（2026-10-01 第三代定位）：照实分行——窗内（_StdoutProxy drain
        # 语义，10:02 事故实证）前缀行与结果行各落一行不合并，台账分行记
        _ledger_append("tool", "")
        _ledger_append("tool", f"  [{name}] {args_preview} ...")
        # M-B 修复（2026-10-01 22:37 LEDGER_MISMATCH 实证）：前缀行直写
        # 不带 \n 时是否与结果行合一行取决于 StdoutProxy 200ms 批量 drain
        # 的时序（竞态）——测试同步驱动恒分行（全绿假象），生产 22:22 赶上
        # 分行 ok=True、22:37 赶上合并必败。前缀行自带换行闭行后窗内
        # **确定性两行**（前缀行/结果行），台账照记两行，逐行校验不再
        # 依赖时序。视觉上「[bash] …」与 ✅ 各占一行（此前合并时同行的
        # 紧凑观感放弃，确定性优先）。
        _stream_write(f"\n  [{name}] {args_preview} ...\n")
    elif etype == "tool_call_end":
        is_error = event.get("is_error", False)
        preview = event.get("output_preview", "")
        mark = "❌" if is_error else "✅"
        if preview:
            # 直接外显工具输出内容（此前只显示 "(N chars)" 计数，用户看不到内容）；
            # 终端宽度动态截断——窄终端不低于 60，宽终端用 columns-8 留余白；
            # 非 TTY 回落 80
            try:
                cols = max(60, (shutil.get_terminal_size().columns or 80) - 8) if sys.stdout.isatty() else 80
            except Exception:  # noqa: BLE001
                cols = 80
            preview = preview[:cols].replace("\n", " ")
            _stream_write(f"{mark} {preview}\n")
            if _trace_on():
                _turn_tool_trace.append(f"{mark} {preview}")
            _ledger_append("tool", f"{mark} {preview}")
            _turn_pending_tool = ""
        else:
            _stream_write(f"{mark}\n")
            if _trace_on():
                _turn_tool_trace.append(mark)
            _ledger_append("tool", mark)
            _turn_pending_tool = ""
    elif etype == "status":
        _flush_stream_line()
        # M-B 同源修复：status 前缀行同样闭行（合并竞态与 tool_call_start 同理）
        _stream_write(f"\n  [{event.get('message', '')}] \n")
        _ledger_append("tool", "")
        _ledger_append("tool", f"  [{event.get('message', '')}] ")
    elif etype == "done":
        _flush_stream_line()
        # P0-完成渲染:TTY 下擦除裸文本行,用 rich Markdown 重渲染正式版
        # 2026-09-06 修复:之前用 `\x1b[{n}A` 上移 N 行 + 清屏重渲染,但 prompt_toolkit
        # 同步维护自己的 bottom_toolbar(N 不含 toolbar 行)→ cursor 操作覆盖了 toolbar
        # 文字("灵克[..]ude │ 上下文 0%"被截成 ude)、产生位移。
        # 新策略:TTY 下不再用 ANSI cursor 操作(保留 raw stream 输出),改用 Rich 的
        # `erase + replace` 在底部追加正式版,而非覆盖——避免与 PT 的 toolbar 控制权冲突。
        content = event.get("content", "")
        # 同轮重复 done 守卫（2026-10-01）：同 content 的 done 第二次到达
        # 直接素字收尾——生产日志实证重复 done 会二次消费标记/重复补空行
        if content and content == _last_done_content:
            _stream_write("\n\n")
            globals()["_stream_lines_emitted"] = 0
            return
        _last_done_content = content
        # 乱码第四路径守卫：全屏 TUI 下输出窗已有流式全文，rich 重渲染经
        # stderr 直达终端（绕开 stdout 全部清洗）→ 跳过，只补空行收尾。
        # 2026-09-22 双输出修复：纯文本模式（默认）下流式 delta 已把全文
        # 写屏，「正式版」与裸文本内容完全相同（无色无结构差），重渲染
        # 零增益、内容输出两遍 → 一并跳过，只补空行收尾；彩色 opt-in
        # （LINGCLAUDE_COLOR=1）时保留「底部追加正式版」路径。
        if content and sys.stdout.isatty() and not _full_tui_managed:
            from lingclaude.cli.display import _plain_no_color

            if _plain_no_color():
                _stream_write("\n\n")
                globals()["_stream_lines_emitted"] = 0  # 复位：本轮已收尾
            else:
                # ANSI 光标下移一行(到达 stream 输出末尾之下),再向上滚回渲染
                # ——比上移 N 行覆盖安全(N 不必精确)
                _stream_write("\x1b[1B\n")
                try:
                    # TUI 插片优先（render_facade 内部: provider→cli.display 回退）
                    from lingclaude.cli.render_facade import print_markdown

                    print_markdown(content)
                except Exception:  # noqa: BLE001 — 渲染失败时保底输出纯文本
                    _stream_write("\n" + content + "\n\n")
                globals()["_stream_lines_emitted"] = 0  # 复位：P0 完成行已就位
        elif content and _full_tui_managed:
            # 2026-09-30 TUI 原位上色：托管期不再素字收尾。流式素字全文已在
            # 输出窗（流式出口 _stream_write 必剥 ANSI，样式过不来），此处用
            # rich(ANSI) 渲染同一 content，经 _extract_sgr_styles 白名单解析、
            # replace_turn_styled 原位替换本轮流式段——零 stderr 直写（第四
            # 路径守卫不破），内容一遍不重复。失败/标记失效回退素字收尾。
            try:
                from io import StringIO

                from rich.console import Console

                from lingclaude.cli.interface import _extract_sgr_styles

                buf = StringIO()
                cols = _render_max_width()
                # 2026-10-01 标题醒目化：rich Markdown 默认标题只加粗无前景色，
                # 用户的深色终端上 bold 渲染不亮时标题与正文糊成一片。挂自定义
                # Theme 给标题明亮的白名单内前景色（SGR 90-97），对比度远强于
                # bold-only。渲染仍走 force_terminal=True，零 stderr 直写不变。
                # 注：此版 rich(rich/default_styles.py:155) 标题样式键无 .style
                # 后缀，为 markdown.h1 / markdown.h2 …；实测 .style 后缀键不生效。
                from rich.theme import Theme

                _heading_theme = Theme(
                    {
                        "markdown.h1": "bold bright_cyan",
                        "markdown.h2": "bold bright_green",
                        "markdown.h3": "bold bright_magenta",
                        "markdown.h4": "bold bright_blue",
                        "markdown.h5": "bold bright_yellow",
                        "markdown.h6": "bold bright_white",
                    }
                )
                ansi_console = Console(
                    file=buf, force_terminal=True, width=cols, theme=_heading_theme
                )
                from rich.markdown import Markdown

                ansi_console.print(Markdown(_defuse_wide_tables(content)))
                ansi_text = buf.getvalue()
                styled_lines: list[str] = []
                spans_list: list[list[tuple[int, int, str]]] = []
                for ln in ansi_text.split("\n"):
                    t, sp = _extract_sgr_styles(ln)
                    styled_lines.append(t)
                    spans_list.append(sp)
                proxy = getattr(sys, "stdout", None)
                owner = getattr(proxy, "_owner", None)
                _exp = _expected_window_lines(content)
                # 台账窗尾对齐（2026-10-01 第三代定位）：取代绝对行号 mark +
                # done.content 整段指纹——生产日志实证 mark 会被粘贴回执/
                # resync 重绘/重复 done 漂移到陈旧区段，整段比对必失配。
                # 台账按流式实写行打点，从窗尾锚定，天然免疫起点漂移。
                # 台账空（无写入或全被裁）→ 整段旧语义。
                # 快照后立即清（2026-10-01）：台账生命周期=单轮流式期，
                # done 消费即终结——不清则残留行污染下一轮快照（跨轮
                # 累积 → 台账比窗长 → LEDGER_TOO_LONG 恒拒，测试批次实证）。
                _ledger = list(_turn_ledger)
                _turn_ledger.clear()
                _turn_pending_tool = ""
                if owner is not None and _ledger:
                    _ok = owner.replace_turn_styled_from_ledger(_ledger, content)
                else:
                    _ok = owner is not None and owner.replace_turn_styled(styled_lines, spans_list, _exp)
                if _ok:
                    pass  # 原位替换成功：窗内素字段已变彩色版，无需追加
                else:
                    _stream_write("\n\n")  # 回退：无标记/区间失效，旧行为收尾
                    globals()["_stream_lines_emitted"] = 0
            except Exception as _styled_err:  # noqa: BLE001 — 渲染/替换失败回退素字收尾
                _stream_write("\n\n")
                globals()["_stream_lines_emitted"] = 0
        else:
            _stream_write("\n\n")
    elif etype == "error":
        _flush_stream_line()
        # UI 对齐修复:前后各留空行,与 rich stderr 日志/下一提示符隔离。
        _stream_write(f"\n\n[错误] {event.get('error', '')}\n")
        _stream_write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n\n")

"""

PromptSessionInterface Protocol + 两个实现：
- PromptToolkitSession: L1 实现，包 prompt_toolkit.PromptSession + rich.live.Live
- FallbackSession: 兜底实现，原裸 input() + sys.stdout.write + threading.Event

入口选择：LINGCLAUDE_CLI_MODE=plain 或非 TTY → FallbackSession（WebUI/IDE/CI 零影响）；
CLI TTY 默认 → PromptToolkitSession。
"""
from __future__ import annotations

import os
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lingclaude.cli.repl_io import replay_stdin_bytes
from lingclaude.core.policy_loader import get as _policy_get
from lingclaude.engine.lineedit import add_history_line, ensure_readline, load_history_file

# prompt_toolkit 为可选依赖 — 未安装时 PromptToolkitSession 不可用，FallbackSession 兜底
try:
    from prompt_toolkit import PromptSession as _PTSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.shortcuts import prompt as _pt_prompt
    from prompt_toolkit.styles import Style as _PTStyle

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False


# ---------------------------------------------------------------------------
# 状态栏样式表（2026-09-22）：toolbar_fragments / full_tui 全部用
# class:X fragment 选择器着色（状态球 绿/黄/红、待办 ⚙、模型名 accent、
# 上下文分色），但 PT 从未注册过对应样式规则 —— fragment 的 class:
# 前缀是「选择器语法」，规则字典的 key 必须是裸类名（PT3 的
# CLASS_NAMES_RE ^[a-z0-9.\s_-]*$ 不允许冒号，'class:X' 当 key 会
# AssertionError）。此表用裸类名 key 定义颜色，由两代 TUI 构造点统一
# 传入 style=；未 import PT 时为 None（调用方需判空回退）。
# ---------------------------------------------------------------------------
PT_TUI_STYLE = (
    _PTStyle.from_dict({
        "green": "ansigreen",
        "yellow": "ansiyellow",
        "red": "ansired bold",
        # 2026-09-26: toolbar 去高亮（用户要求正常显示）——accent 原为
        # "ansicyan bold"（模型名/⏵⏵/⚙/🔄/挂起 全部加粗亮青），现置空为
        # 普通前景。片段结构不变，仅样式规则清空；full_tui 输入区共用
        # 此表但 input class 本就为空，无回归面。
        "accent": "",
        # 2026-09-26: toolbar 背景去高亮 —— PT 默认样式表内置
        # ("bottom-toolbar", "reverse")（prompt_toolkit/styles/defaults.py:129），
        # 用户样式表在 merge_styles 中排其后可覆盖；置 noreverse 后工具栏
        # 不再整行反白（两代 TUI：PromptSession.bottom_toolbar 与全屏
        # FullTuiSession 状态窗共用此表，一处修复两路生效）。
        "bottom-toolbar": "noreverse",
        "status": "noinherit",
        "sep": "ansibrightblack",
        "output": "",
        "input": "",
    })
    if _HAS_PROMPT_TOOLKIT
    else None
)


# 2026-09-15（会话问题重构 P1-2）: 命令历史从全局移到项目内 —— 此前
# "~/.lingclaude/history" 被所有项目进程共享，在 ~/lingflow 输入的命令
# 在 ~/lingclaude 按上键也会还原出来（跨项目泄露）。改为 ".lingclaude/history"
# （相对当前工作目录）：每个项目独立历史，不跨项目污染。
DEFAULT_HISTORY_FILE = ".lingclaude/history"


# ---------------------------------------------------------------------------
# 2026-09-18 多行输入增强：Ctrl+Enter / Shift+Enter 换行（单源，全局幂等）
#
# 事实链（全部实测于 prompt_toolkit 3.0.53 源码）：
# 1. PT 内建表把 "\x1b[27;5;13~"（Ctrl+Enter）和 "\x1b[27;2;13~"（Shift+Enter）
#    都映射成 Keys.ControlM（= 回车 = 提交）→ 修饰键被吃掉，按了等于 Enter。
# 2. PT 的 Keys 枚举没有名为 "Enter" 的成员；键位注册时 add("enter") 经
#    KEY_ALIASES 解析为 Keys.ControlM（c-m）。因此 chord (Escape, ControlM)
#    与上层 _build_key_bindings 的 add("escape", "enter") 精确同键。
# 3. vt100 解析器查表用的是 ansi_escape_sequences.ANSI_SEQUENCES 模块级 dict
#    （非 import-time 快照），运行前改写即时生效。
#
# 因此最优修法不是新增键位绑定，而是把这两个序列改映射为
# (Keys.Escape, Keys.ControlM) —— 精确复用三处 UI（P1 interface / P2
# full_tui / repl 内联）已有的 Esc+Enter 换行 chord，一处数据改动全形态生效。
# 改表失败（PT 缺失/版本变化）静默跳过 —— 增强绝不反噬输入路径。
# ---------------------------------------------------------------------------
_CTRL_ENTER_SEQ = "\x1b[27;5;13~"  # CSI 27;5;13~ = xterm 修饰回车：Ctrl
_SHIFT_ENTER_SEQ = "\x1b[27;2;13~"  # CSI 27;2;13~ = xterm 修饰回车：Shift
# 2026-09-19 上键历史修复：终端焦点上报（DECSET 1004）的失焦/聚焦序列。
# 来源实证（2026-09-20 修正）：PT 3.0.53 的 enable_mouse_support 只发
# 1000/1003/1015/1006，并不含 1004 —— 这些序列来自终端里其他开启过焦点
# 上报的程序（vim/tmux/kitty 系 TUI 等）残留的模式。它们常独立成 read 块
# 到达，不映射就会被 vt100 解析器拆成字面 '[' 'O' / '[' 'I' 插进输入框。
_FOCUS_OUT_SEQ = "\x1b[O"  # 失焦
_FOCUS_IN_SEQ = "\x1b[I"   # 聚焦

# H19: bracketed paste 包裹标记（与 repl_io.py 对齐；P0-2 清洗状态机用）
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"

_MAPPED_SEQUENCES = False

# P0-2（2026-09-20）: 降级读路径的转义清洗状态机（TUI 优化方案 P0-2/P1-2）。
# 内核 read 可把 \x1b[200~ 等序列切在任意字节边界（\x1b[200~ab / cd\x1b[201~
# 两片、甚至 \x1b[200 单独成片），startswith/单次 replace 均会漏剥。
def _fallback_strip_ansi(
    data: bytes, in_paste: bool, keep_sgr: bool = False
) -> tuple[bytes, bytes, bool]:
    """清洗一个 read 分片：返回 (正文, 待拼接的不完整序列尾部, 新 in_paste)。

    keep_sgr=False（默认）：所有 CSI 全吞（历史行为，零回归）。
    keep_sgr=True（2026-09-27 受限富文本）：SGR（ESC[...m，final='m'）
    保留进正文——由 _extract_sgr_styles 在写窗层二次解析成列区间样式表；
    非 SGR CSI 仍吞。仅 _StdoutProxy 降级路径使用 True（原始 stdout 的
    探测器/进度条需要 SGR 到达解析器），主路径保持 False。
    """
    # ⚠ \x1bO（SS3 前缀，DECCKM 应用光标模式方向键）必须在此列——
    # 否则 ESC+O 拆片时 'O' 被误吞、final 字节漏成正文（实测场景 8）。
    # 2026-09-27 keep_sgr：补 \x1b[3 / \x1b[30 前缀——SGR（如 \x1b[31m）
    # 跨片（b"\x1b[3"+b"1mXY"）时旧表无此前缀，后半截 "1m" 漏成正文（实测）。
    for p in (
        b"\x1b[200", b"\x1b[201", b"\x1b[20", b"\x1b[2", b"\x1b[",
        b"\x1b[3", b"\x1b[30", b"\x1bO", b"\x1b",
    ):
        if data.endswith(p):
            hold = p
            data = data[:-len(p)]
            break
    else:
        hold = b""
    if (data.count(_PASTE_START) + data.count(_PASTE_END)) % 2:
        in_paste = not in_paste
    data = data.replace(_PASTE_START, b"").replace(_PASTE_END, b"")
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == 0x1B:
            # ESC+[...final(0x40-0x7E)：CSI 整体吞（keep_sgr 时 SGR 例外，
            # 序列原样保留）；ESC+O：SS3 三字节吞（DECCKM 应用光标模式方向
            # 键 \x1bOA/B/C/D，只吞两字节会漏尾字节）；ESC+其他：吞两字节。
            # 到片尾仍无终结字节：扣下待拼接（正常不会发生——hold 已扣尾，
            # 此分支兜底数据中间态）。
            j = i + 1
            if j < n and data[j] == 0x5B:  # '['
                k = j + 1
                while k < n and not (0x40 <= data[k] <= 0x7E):
                    k += 1
                if k < n:
                    if keep_sgr and data[k] == 0x6D:  # 'm' = SGR final
                        out.extend(data[i : k + 1])
                    i = k + 1
                    continue
                break  # 不完整 CSI（不应发生，防御）
            if j < n and data[j] == 0x4F and j + 1 < n:  # 'O' → SS3
                i = j + 2
                continue
            i = min(j + 1, n)
            continue
        out.append(data[i])
        i += 1
    return bytes(out), hold, in_paste


_SGR_BASE = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

# 2026-09-29 灵元 R2：SGR 白名单策略外置 —— policies/sgr_styles.yaml，
# 改收录范围只改 YAML 不动代码（PolicyLoader mtime watch 热更，不重启进程）。
# _FALLBACK 与 YAML 保持同步，作为读失败兜底（graceful degrade）。
_FALLBACK_SGR_POLICY: dict[str, Any] = {
    "modifiers": {
        "bold": {"enable": [1, 2], "disable": 22},
        "italic": {"enable": [3], "disable": 23},
        "underline": {"enable": [4], "disable": 24},
        "strike": {"enable": [9], "disable": 29},
        "reverse": {"enable": [7], "disable": 27},
    },
    "colors": {
        "fg_normal_lo": 30, "fg_normal_hi": 37,
        "fg_bright_lo": 90, "fg_bright_hi": 97,
        "bg_normal_lo": 40, "bg_normal_hi": 47,
        "bg_bright_lo": 100, "bg_bright_hi": 107,
        "default_fg": 39, "default_bg": 49,
    },
    "extended": "reject",
    "max_params": 32,
    "reset_code": 0,
}


def _sgr_policy() -> dict[str, Any]:
    """读 policies/sgr_styles.yaml；读失败/字段缺失回退 _FALLBACK（graceful degrade）。"""
    try:
        data = _policy_get("sgr_styles")
        if isinstance(data, dict) and data.get("modifiers") and data.get("colors"):
            return data
    except Exception:  # noqa: BLE001 — 策略加载失败绝不影响主流程
        pass
    return _FALLBACK_SGR_POLICY


def _sgr_params_to_style(params: str) -> str | None:
    """SGR 参数串 → PT 样式字符串（白名单）；空/全 reset 返回 None。

    受限富文本（2026-09-27 性价比两步之一）：输出窗不渲染裸 SGR（会被
    PT 画成 '?[1;4m' 明文），而是写入时把 SGR 转成 PT fragment 样式。
    白名单范围由 policies/sgr_styles.yaml 驱动（2026-09-29 外置，
    mtime watch 热更）：默认收粗体/斜体/下划线/删除线/反转 + 基础 8 色
    前景/背景 + 默认色 + 0 清除。不支持 256/24bit 色（extended: reject
    时跳过参数，不产生样式）；未知参数忽略（对齐终端宽容语义）。
    """
    if not params:
        return None
    pol = _sgr_policy()
    modifiers: dict[str, Any] = pol.get("modifiers") or {}
    colors: dict[str, Any] = pol.get("colors") or {}
    max_params = int(pol.get("max_params") or 32)
    reset_code = int(pol.get("reset_code") or 0)

    try:
        parts = [int(p) if p else 0 for p in params.split(";")]
    except ValueError:  # 非数字参数串（异常构造）：整体放弃
        return None
    if len(parts) > max_params:  # 防御：超长参数串拒绝
        return None

    # 预建查表：SGR 码 → ("add", mod名) / ("del", mod名)
    enable_map: dict[int, str] = {}
    disable_map: dict[int, str] = {}
    for mod_name, spec in modifiers.items():
        if not isinstance(spec, dict):
            continue
        for code in spec.get("enable") or []:
            enable_map[int(code)] = mod_name
        dis = spec.get("disable")
        if dis is not None:
            disable_map[int(dis)] = mod_name

    fg_nl = int(colors.get("fg_normal_lo", 30)); fg_nh = int(colors.get("fg_normal_hi", 37))
    fg_bl = int(colors.get("fg_bright_lo", 90)); fg_bh = int(colors.get("fg_bright_hi", 97))
    bg_nl = int(colors.get("bg_normal_lo", 40)); bg_nh = int(colors.get("bg_normal_hi", 47))
    bg_bl = int(colors.get("bg_bright_lo", 100)); bg_bh = int(colors.get("bg_bright_hi", 107))
    default_fg = int(colors.get("default_fg", 39)); default_bg = int(colors.get("default_bg", 49))

    fg: list[str] = []
    bg: list[str] = []
    mods: list[str] = []
    i = 0
    n = len(parts)
    while i < n:
        p = parts[i]
        if p == reset_code:
            fg.clear(); bg.clear(); mods.clear()  # noqa: E701 — SGR 0 全清
        elif p in enable_map:
            m = enable_map[p]
            if m not in mods:
                mods.append(m)
        elif p in disable_map:
            m = disable_map[p]
            mods[:] = [x for x in mods if x != m]
        elif fg_nl <= p <= fg_nh or fg_bl <= p <= fg_bh:
            _fi = p - fg_nl if p < fg_bl else p - fg_bl
            fg = [("ansibright" if p >= fg_bl else "ansi") + _SGR_BASE[_fi]]
        elif p == default_fg:
            fg.clear()
        elif bg_nl <= p <= bg_nh or bg_bl <= p <= bg_bh:
            _bi = p - bg_nl if p < bg_bl else p - bg_bl
            bg = [("bg:ansibright" if p >= bg_bl else "bg:ansi") + _SGR_BASE[_bi]]
        elif p == default_bg:
            bg.clear()
        elif p in (38, 48):
            # 扩展色：按参数长度跳过（38;5;n / 38;2;r;g;b），不产生样式
            if i + 1 < n and parts[i + 1] == 5:
                i += 2
            elif i + 1 < n and parts[i + 1] == 2:
                i += 4
            # 形态非法（38;7 之类）：不跳，交给循环忽略
        # 其余（掩码/字体选择等）忽略
        i += 1
    out = []
    if fg:
        out.extend(fg)
    if bg:
        out.extend(bg)
    out.extend(mods)
    return " ".join(out) if out else None


def _strip_ansi_text(s: str) -> str:
    """字符串级 ANSI/控制序列剥离（写窗汇聚点统一清洗用）。

    - CSI（ESC[...final）/ SS3（ESCO..）：整体吞；
    - 孤立 ESC 或尾部截断序列：连同 ESC 一并吞（文本渲染语义下残骸比
      半截参数更糟——TextArea 会把 0x1b 渲染成 '?'，再漏出 '[1;4m' 明文）；
    - 其他 C0 控制字符（除换行/回车/制表）替换为空格，防 '?' 渲染噪声。

    2026-09-20 修复：原快速路径仅查 ESC，含裸 C0（NUL/BEL 等）但无
    ESC 的字符串绕过清洗直落 TextArea，仍渲染 '?' 噪声——快速路径必须
    同时确认无 C0 噪声才可直通。
    """
    if not s:
        return s
    ESC = chr(27)
    C0_KEEP = "\n\r\t"
    if ESC not in s and not any(ord(ch) < 32 and ch not in C0_KEEP for ch in s):
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == ESC:
            j = i + 1
            if j < n and s[j] == "[":
                k = j + 1
                while k < n and not (0x40 <= ord(s[k]) <= 0x7E):
                    k += 1
                i = k + 1 if k < n else n  # 终结或截断：整体吞
                continue
            if j < n and s[j] == "O":
                i = min(j + 2, n)
                continue
            i = j  # 孤立 ESC：吞
            continue
        if ord(ch) < 32 and ch not in C0_KEEP:
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ── 2026-09-27 受限富文本：SGR→列区间样式表 ─────────────────────────────
# 设计（PT3 BufferControl 实码验证）：UIContent 行数固定取 document.line_count
# （controls.py create_content），processor 插 \n 必然布局错位 → 行内样式
# 不能用字符 marker + \n 方案。改用「清洗后零字符污染 + 列区间样式表 +
# processor 按列上色」：SGR 与文本在写入层分离存储，渲染层合成。


def _disp_width(ch: str) -> int:
    """单字符显示列宽：全角（East Asian F/W）=2，其余=1。

    坐标系统一（2026-09-29 CJK 对齐）：_extract_sgr_styles 的列区间与
    full_tui 的 fragment 宽度计算（_style_range/_reverse_range）必须同用
    本函数。否则中文全角行按 codepoint 计列，样式段与 PT 排版（wcwidth
    感知）整体左移，带 SGR 的表格/文本列错位。
    """
    # 零宽字符（组合符 Zs/ Mn 类、BOM/零宽空格等）PT 排版按 0 列，
    # 但本坐标系为防 span 倒挂（end<start 会触发 a>=b 误吞）统一记 1 列。
    # 有界近似：CJK/全角场景（本修复目标）wcwidth==east_asian_width 判 2，
    # ASCII/数字/box 绘图等场景==1，行为不变。
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1


def _line_disp_width(text: str) -> int:
    """整行显示列宽 = Σ _disp_width(ch)（fragment 宽度语义）。"""
    return sum(_disp_width(c) for c in text)


def _col_to_char_index(text: str, col: int) -> int:
    """显示列 → 字符下标映射（全角行切 fragment 用）。

    返回最小字符下标 i，使 text[:i] 的显示列宽 >= col；col 落在某全角
    字符内部时返回该字符下标（切点左移到字符边界，PT 排版层再对齐）。
    col<=0→0；col>=行宽→len(text)。
    """
    if col <= 0:
        return 0
    pos = 0
    for i, c in enumerate(text):
        pos += _disp_width(c)
        if pos >= col:
            return i + 1
    return len(text)


def _extract_sgr_styles(line: str) -> tuple[str, list[tuple[int, int, str]]]:
    """单遍扫描：提取 SGR 白名单样式 → (净化文本, [(start,end,style)])。

    - SGR（ESC[...m）：白名单内 → 开新样式段；SGR 0/空 → 复位结算；
      非白名单（256/24bit 色等）→ 忽略不切段；
    - 其他 CSI / SS3 / 孤立 ESC / 截断序列：整体吞（对齐 _strip_ansi_text
      的「残骸比半截参数更糟」语义）；
    - 返回文本保证零 ESC；列区间 = 净化文本**显示列**索引（全角=2 列，
      与 PT 排版 wcwidth 感知一致；纯 ASCII 行 显示列==codepoint，零回归）。
    """
    if "\x1b" not in line and not any(
        ord(ch) < 32 and ch != "\t" for ch in line
    ):
        return line, []  # 快速路径：无 ESC 且无 C0 噪声（对齐 _strip_ansi_text）
    clean: list[str] = []
    spans: list[tuple[int, int, str]] = []
    cur_style: str | None = None
    span_start = 0
    clean_len = 0
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch != "\x1b":
            # C0 控制字符（除 \t）替换为空格——对齐 _strip_ansi_text 防 '?'
            # 渲染噪声语义（\n/\r 不会到这：调用方按行拆分/清frag）。
            # 显示列坐标系（2026-09-29 CJK 对齐）：全角 +2，半角 +1。
            if ord(ch) < 32 and ch != "\t":
                clean.append(" ")
                clean_len += 1  # 替换产物是空格（半角），恒 +1
            else:
                clean.append(ch)
                clean_len += _disp_width(ch)
            i += 1
            continue
        j = i + 1
        if j < n and line[j] == "[":
            k = j + 1
            while k < n and not (0x40 <= ord(line[k]) <= 0x7E):
                k += 1
            if k >= n:  # 截断 CSI：吞到行尾
                i = n
                continue
            if line[k] == "m":
                params = line[j + 1 : k]
                st = _sgr_params_to_style(params)
                if st or params in ("", "0"):
                    # 段边界：白名单开新样式 / SGR 0 全复位——结算上一段
                    if cur_style and clean_len > span_start:
                        spans.append((span_start, clean_len, cur_style))
                    cur_style = st
                    span_start = clean_len
                # 非白名单 SGR：忽略不切段
                i = k + 1
                continue
            i = k + 1  # 非 SGR CSI：整体吞
            continue
        if j < n and line[j] == "O":
            i = min(j + 2, n)  # SS3：吞两字节
            continue
        i = j  # 孤立 ESC：吞
    text = "".join(clean)
    # 2026-09-29 CJK 对齐：行末段收尾判断按显示列（clean_len 已是 wcwidth
    # 累加）；旧版 len(text)>span_start 是 codepoint 比较，全角行使
    # 「空段」误判翻转。
    if cur_style and clean_len > span_start:
        spans.append((span_start, clean_len, cur_style))
    return text, spans



def _sanitize_submitted(text: str) -> str:
    """P0-2 兜底：提交前剥离任何残留的 bracketed paste 包裹标记。

    上游（状态机/PT 解析器/全屏代理）漏网时，标记也到不了 LLM。
    PT 路径解析器已剥标记（vt100_parser feed 专用通道），本函数为纯保险。
    """
    if "\x1b[200~" in text or "\x1b[201~" in text:
        return text.replace("\x1b[200~", "").replace("\x1b[201~", "")
    return text



# ---------------------------------------------------------------------------
# P0-5（2026-09-20）: 粘贴爆发重组器 —— 窗口期残留多行合并为单条提交。
#
# 根因：bracketed paste 标记只在 PT prompt 运行期间由终端开启；生成结束
# （InputPump 停转/终端模式复位）到下轮 prompt 启动之间的「窗口期」里，
# 粘贴的多行文本以裸 \n 进内核行缓冲，无 \x1b[200~/201~ 包裹。下轮读入时
# 每个 \n 都被键位表解释为提交 → 多行文本拆成多轮单行命令（用户报告：
# 「输入遇到换行符即传到 LLM」）。P0-2 状态机只处理带标记的粘贴段，
# 对窗口期残留无能为力，故在提交入口补这道合并防线。
#
# 安全论证：仅在 canonical 模式下探测——canonical 下 select 可读 ⟹ 内核
# 已按行缓冲交付完整行，不存在偷半行。raw 模式直接放行（PT 事件循环自理）。
_BURST_GAP_MS = float(os.environ.get("LINGCLAUDE_BURST_GAP_MS", "30"))
_BURST_MAX_LINES = 200
_BURST_MAX_BYTES = 65536


def _reconcile_burst_lines(first_line: str, _select: Any = None,
                           _readline: Any = None,
                           _tcgetattr: Any = None) -> str:
    """首行读出后探测 tty 残留爆发段：行间隔 <LINGCLAUDE_BURST_GAP_MS
    （默认 30ms，env 置 0 一键关闭）视为同一粘贴动作，合并为单条提交。

    人手逐行敲（间隔 >100ms）不受影响；斜杠命令语义不变（合并段仍走
    既有消费判定）。注入参数仅供测试替身使用。
    """
    if not first_line or _BURST_GAP_MS <= 0 or not sys.stdin.isatty():
        return first_line
    try:
        import select as _sel
        import termios as _tio

        fd = sys.stdin.fileno()
        attrs = (_tcgetattr or _tio.tcgetattr)(fd)
        if not (attrs[3] & _tio.ICANON):
            return first_line  # raw 模式：禁碰（PT 事件循环自理）
        sel = _select or _sel.select
        readline = _readline or sys.stdin.readline
    except Exception:  # noqa: BLE001 — 非 tty/异常环境：不重组，原样返回
        return first_line

    gap = _BURST_GAP_MS / 1000.0
    lines = [first_line]
    total = len(first_line.encode("utf-8", "replace"))
    try:
        while len(lines) < _BURST_MAX_LINES and total < _BURST_MAX_BYTES:
            r, _, _ = sel([fd], [], [], gap)
            if not r:
                break  # 静默：爆发段结束（或本就是人手输入）
            line = readline()
            if not line:
                break  # EOF
            line = line.rstrip("\n")
            lines.append(line)
            total += len(line.encode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — 已收部分原样合并返回
        pass
    if len(lines) == 1:
        return first_line
    return "\n".join(lines)


def _patch_pt_modifier_enter() -> None:
    """把 Ctrl+Enter / Shift+Enter 序列改映射为 (Escape, ControlM)。

    幂等；依赖 PT 时所有 import 均放函数内（PT 是可选依赖）。
    """
    global _MAPPED_SEQUENCES
    if _MAPPED_SEQUENCES or not _HAS_PROMPT_TOOLKIT:
        return
    try:
        from prompt_toolkit.input import ansi_escape_sequences as _aes
        from prompt_toolkit.keys import Keys

        _aes.ANSI_SEQUENCES[_CTRL_ENTER_SEQ] = (Keys.Escape, Keys.ControlM)
        _aes.ANSI_SEQUENCES[_SHIFT_ENTER_SEQ] = (Keys.Escape, Keys.ControlM)
        # 2026-09-19 上键历史修复：焦点上报事件映射为 Keys.Ignore。
        # 终端处于 focus reporting 模式（\x1b[?1004h，来源为其他程序残留，
        # 见 :58 注释 2026-09-20 实证修正）后，窗口失焦/聚焦会发
        # \x1b[O / \x1b[I。PT 内建序列表没有这两条 ——
        # 事件与其它按键同批到达时解析正常，但**独立 read 块到达时**（真实
        # 终端 alt-tab 切换即如此）vt100 解析器 flush 后按「无匹配」逐字符
        # 兜底，'[' 'O' / '[' 'I' 以字面量插进输入框：既污染当前输入，又
        # 让后续 Up 翻历史被前缀过滤（_history_matches startswith）卡死。
        # Keys.Ignore 是 PT 自家无害落地先例（ansi_escape_sequences.py:166
        # \x1b[E 同款）；KeyProcessor 无绑定直接丢弃，不进任何 buffer。
        _aes.ANSI_SEQUENCES[_FOCUS_OUT_SEQ] = Keys.Ignore
        _aes.ANSI_SEQUENCES[_FOCUS_IN_SEQ] = Keys.Ignore

        # P1-1（2026-09-20，TUI 优化方案）: kitty 键盘协议残留 CSI u 家族。
        # 前序崩溃 TUI（含自家异常路径）留下增强键模式时，方向键/Enter 发成
        # CSI u 序列；PT 3.0.53 内建表无这些条目 → flush 逐字符兜底字面入框。
        # ⚠ 禁止把 CSI u 家族整体 Keys.Ignore：\x1b[27u 是残留模式下的 Enter，
        # Ignore = 回车失灵（「方向键变字面 + 回车无响应」并发症状的根源）。
        # 语义依据 kitty keyboard-protocol 规范（2026-09-20 实抓）：
        #   \x1b[27u=Enter；27;5u=Ctrl+Enter；27;2u=Shift+Enter（换行 chord 同款）；
        #   1;5A/B/C/D=Ctrl+方向（PT 已有 ControlUp/Down/Right/Left 键位）。
        # PT 3.0.53 已内建 \x1b[1;5A→ControlUp 等 CSI 修饰方向键
        # （ansi_escape_sequences.py:213/242），此处只补 CSI u 缺口。
        _aes.ANSI_SEQUENCES["\x1b[27u"] = Keys.ControlM          # 残留模式 Enter=提交
        _aes.ANSI_SEQUENCES["\x1b[27;5u"] = (Keys.Escape, Keys.ControlM)  # Ctrl+Enter=换行
        _aes.ANSI_SEQUENCES["\x1b[27;2u"] = (Keys.Escape, Keys.ControlM)  # Shift+Enter=换行
        # P1-2: 表外查询/上报类序列防御（独立 read 块到达时字面入框的来源）。
        # CPR 响应(\x1b[r;cR)/SGR 鼠标(\x1b[<..M/m) PT 内建正则已识别
        # （vt100_parser.py:19-33），不重复；此处收编 DECRQM/DECSCUSR。
        _aes.ANSI_SEQUENCES["\x1b[?2026;2$y"] = Keys.Ignore      # DECRQM styled underline 应答
        _aes.ANSI_SEQUENCES["\x1b[?2026;1$y"] = Keys.Ignore      # DECRQM styled underline 应答
        _aes.ANSI_SEQUENCES["\x1b[2 q"] = Keys.Ignore            # DECSCUSR 稳定块形光标
        _aes.ANSI_SEQUENCES["\x1b[4 q"] = Keys.Ignore            # DECSCUSR 稳定下划线光标
        _MAPPED_SEQUENCES = True
    except Exception:  # noqa: BLE001 — 版本差异/结构性变化时静默放弃
        _MAPPED_SEQUENCES = True  # 不反复重试注定失败的补丁


@runtime_checkable
class PromptSessionInterface(Protocol):
    """CLI 输入/输出/打断的统一接口。"""

    def prompt(self, message: str = "") -> str:
        """读取一行输入（含历史/键位）。"""
        ...

    def push_to_history(self, text: str) -> None:
        """把输入写入历史（prompt_toolkit 自动做；Fallback 也调）。"""
        ...

    def stream_print(self, renderable: Any) -> None:
        """生成中流式输出（prompt_toolkit 用 patch_stdout；Fallback 用 sys.stdout.write）。"""
        ...

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """P1: 挂载状态栏回调（仅 PT 实现有效；Fallback 为 no-op）。"""
        ...

    def interrupt_event(self) -> threading.Event:
        """返回 threading.Event，set 后取消当前生成（Esc / Ctrl+C 触发）。"""
        ...

    def set_streaming(self, streaming: bool) -> None:
        """标记当前是否在流式输出期间（影响 prompt() 行为）。"""
        ...

    def prompt_collect(self, message: str = "") -> str:
        """输入泵专用收集读：无视 streaming 短路，真读一行输入。

        背景（2026-09-18 重复输入事故）：H17 会话级泵架构下，生成期唯一
        调 prompt() 的是 pump 线程，而 PT 包装层 streaming 短路让它空转
        完全不读 stdin —— 用户生成期打的命令滞留终端缓冲，流结束才被
        一次性处理（失活重建 TCSAFLUSH/序列混入时首条真丢）→ 体感
        「无响应需重输」。pump 应优先用本方法；未实现时调用方降级
        prompt()（兼容第三方/fake session）。
        """
        ...


class PromptToolkitSession:
    """L1 实现 — 包 prompt_toolkit.PromptSession + FileHistory。"""

    def __init__(
        self,
        history_file: str = DEFAULT_HISTORY_FILE,
        completer: Any | None = None,
    ) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError("prompt_toolkit 未安装，请使用 FallbackSession")
        history_path = Path(history_file).expanduser()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history = FileHistory(str(history_path))
        # 2026-09-22: Shift+Tab 模式环回调（repl 装配时经 install_mode_toggler
        # 注入；键位按下时才读取，构造后再注入同样生效）
        self._mode_toggler = None
        # 2026-09-16（长文截断修复）:multiline=True — 单行模式粘贴长文/多行文本
        # 时 prompt_toolkit 只保留第一行、其余行被当作 Enter 提交丢弃（「长文字
        # 被截断吞没」）。多行模式下 Enter 重绑为提交、Shift+Enter 换行（见下方
        # _build_key_bindings），保持 CLI「敲 Enter 提交」习惯不变。
        # 2026-09-18 多行输入增强:构造前先改写 PT 的输入序列表，让
        # Ctrl+Enter / Shift+Enter 复用下方 Esc+Enter 换行 chord（单源见上）。
        _patch_pt_modifier_enter()
        self._session = _PTSession(
            history=self._history,
            completer=completer,
            multiline=True,
            key_bindings=self._build_key_bindings(),
            # 2026-09-22: 注册状态栏样式表 —— toolbar_fragments 的
            # class:green/yellow/red/accent fragment 此前无规则可匹配，
            # 状态球/上下文分色/Todo 标记全部渲染为默认色（不可见）。
            style=PT_TUI_STYLE,
        )
        self._interrupt = threading.Event()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 标志。streaming 期间
        # prompt() 不阻塞（流式输出占用主线程，输入由 pump 线程异步收集），
        # 防止 session.prompt() 和 pump 线程双阻塞导致假死。
        self._streaming = False

    def _build_key_bindings(self) -> Any:
        """多行模式键位：Enter（无修饰）提交、Esc+Enter 换行。

        2026-09-16（长文截断修复）:multiline=True 后 prompt_toolkit 默认
        Enter 是换行、Meta+Enter 才提交 —— 不符合 CLI 习惯。按官方配方重绑：
        - Enter       → 提交（validate_and_handle）
        - Esc + Enter → 插入换行（粘贴长文/显式多行时用）
        单缓冲 PromptSession 无需 HasFocus 过滤；自动补全未展开时 Enter 仍
        先收下补全选择。
        """
        try:
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.keys import Keys
        except Exception:  # noqa: BLE001 — PT 版本差异时静默回退默认键位
            return None

        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event: Any) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state is not None:
                # 自动补全下拉未关闭：Enter 先确认补全候选，不提交整行
                event.app.current_buffer.complete_state = None
                return
            buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.app.current_buffer.insert_text("\n")

        # 2026-09-22: Shift+Tab 循环切换工作模式（auto→ask→strict→plan，同全屏
        # TUI；回调按下时才读取，未注入时静默空转）。键名用 Keys.BackTab 枚举
        # （PT 别名表无 "backtab" 字符串；\x1b[Z → Keys.BackTab，实测确认）。
        @kb.add(Keys.BackTab)
        def _cycle_work_mode(event: Any) -> None:
            toggler = getattr(self, "_mode_toggler", None)
            if callable(toggler):
                toggler()

        return kb

    def install_mode_toggler(self, toggler: Any) -> None:
        """2026-09-22: 注入 Shift+Tab 模式环回调（提示行直接打 stdout）。"""
        self._mode_toggler = toggler

    def set_streaming(self, streaming: bool) -> None:
        """标记当前是否在流式输出期间。

        streaming=True 时 prompt() 返回 ""（不阻塞），让主线程继续流式输出。
        pump 线程异步收集输入，用户随时可打字；流结束后调用 prompt() 正常读取。
        """
        self._streaming = streaming

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 期间不阻塞。
        # pump 线程在读 stdin，主线程阻塞 prompt() 会形成双阻塞：
        # 主线程等 prompt() 返回 ← 用户按 Enter ← pump 线程读完 ← 流结束
        # → pump 线程永远等用户按 Enter（因为 prompt() 在等）→ 假死。
        # 返回 "" 让上层立即处理队列已有输入（pump 已收集），不等待。
        if self._streaming:
            return ""
        try:
            # P0-5（2026-09-20）: 窗口期粘贴爆发重组 —— 多行残留合并为单条提交
            return _sanitize_submitted(_reconcile_burst_lines(self._session.prompt(message)))
        except KeyboardInterrupt:
            # Ctrl+C 软中断：清空当前输入，返回空串让上层继续。
            # 2026-09-15（会话问题重构 P0-2）：pump 模式下生成期 Ctrl+C 此前
            # 在此被吞成 ""（清行），InputPump 的 except KeyboardInterrupt 分支
            # 永不触发（异常已被本层捕获）→ 生成期中止无响应。修复：清行的
            # 同时 set interrupt_event —— 主线程流循环（repl.py 检查
            # interrupt_event）立即打断生成；空闲期 set 的 interrupt 由下一次
            # prompt() 开头的 clear() 清除，无副作用。
            self._interrupt.set()
            return ""
        # 审计#1/#7 修复:EOF（Ctrl+D / stdin 耗尽）直接传播让上层退出。
        # 此前默认吞成 ""，`lingclaude run -i < /dev/null` 会「空输入→continue→
        # 再读→再 EOF」死循环 100% CPU；exit 途径只剩输入 exit/quit。
        # （旧 LINGCLAUDE_RAISE_EOF=1 测试逃生门已无必要 — 默认即重抛，env 失效。）
        # 注意：不要在此捕获 EOFError 返回 ""。

    def push_to_history(self, text: str) -> None:
        # 审计#11 修复:PromptSession(prompt_toolkit) 在 prompt() 返回时已自动
        # 写入 FileHistory — 这里再 append_string 会让每条输入在历史文件里
        # 出现两遍。保留接口（Protocol 一致性），实现为 no-op。
        _ = text

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：无视 streaming 短路，真读 PT session。

        streaming 短路（prompt 返回 ""）是给主线程的防双阻塞设计；
        pump 线程是生成期唯一 stdin 读者，短路会让它空转不读 stdin
        （2026-09-18 重复输入事故根因）。本方法供 pump 使用，永远真读。

        修复（2026-09-18 五症同源）：必须先临时关闭 _streaming 标志，
        否则 PT 内部 PromptSession.prompt() 在 streaming 期间会直接返回 ""
        （根本不碰 stdin）。关闭后再读，读完恢复原值——PT PromptSession
        本身不支持"永远不短路"，只能靠外层包装绕行。

        二次修复（2026-09-18 打字被吞）：PT prompt() 默认 in_thread=False，
        在 pump 线程里尝试嵌套运行 Application，与主线程已有的 Application
        实例冲突（两个 Application 同时读同一 stdin fd，用户字节被其中一个
        吞掉）。必须传 in_thread=True，让 PT 在独立线程里运行 Application，
        独立管理 raw mode stdin，与主线程完全隔离。
        """
        try:
            # 2026-09-18 五症同源修复: 临时撤销 streaming 标志 → PT 真读
            # 2026-09-18 二次修复: 加 in_thread=True 避免与主线程 Application 冲突
            _was_streaming = self._streaming
            self._streaming = False
            try:
                # P0-5（2026-09-20）: 泵收集路径同样合并窗口期粘贴爆发
                return _sanitize_submitted(_reconcile_burst_lines(self._session.prompt(message, in_thread=True)))
            finally:
                self._streaming = _was_streaming
        except KeyboardInterrupt:
            # 生成期用户 Ctrl+C：set interrupt 让流循环打断（与 prompt()
            # 的软中断语义一致），本层不吞异常（pump 线程有自己的
            # KeyboardInterrupt 分支负责清行续转）。
            self._interrupt.set()
            raise

    def stream_print(self, renderable: Any) -> None:
        # 流式输出：直接写 stdout（Rich Live 在调用方管理刷新）
        print(renderable, end="", flush=True)

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """P1: 挂载状态栏回调。prompt() 渲染时自动调用 get_fragments() 取片段。

        prompt_toolkit 原生管理该行（不碰光标位置，规避审计#6 的 termios 病灶）。
        已知边界：toolbar 仅在 prompt() 渲染期间可见 — 生成期屏幕上没有提示符，
        常驻可见性由 P2 全屏 TUI 解决；生成期反馈由挂起行回显提供。
        """
        try:
            self._session.bottom_toolbar = get_fragments
        except Exception:  # noqa: BLE001 — PT 版本差异时静默降级为无状态栏
            pass

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


class FallbackSession:
    """兜底实现 — 原裸 input() + sys.stdout.write + threading.Event。

    WebUI/IDE/CI 强制走这个；非 TTY 下 prompt_toolkit 不可用时的安全回退。
    streaming 期间用 termios 非阻塞 select 读单行（不卡死流式输出）。
    支持方向键上/下翻历史。
    """

    def __init__(self, history_file: str = DEFAULT_HISTORY_FILE) -> None:
        self._history: list[str] = []
        self._history_file = Path(history_file).expanduser()
        self._interrupt = threading.Event()
        self._load_history()
        # 2026-09-18 方向键/历史修复：已落盘历史喂进 readline 内存历史 ——
        # 裸 input() 路径（含 streaming/泵收集）上键翻历史跨进程延续。
        # prompt() 的非流式分支同样经 ensure_readline 挂钩（见下）。
        load_history_file(str(self._history_file))
        # 2026-09-16（TUI 输入泵问题修复）: streaming 标志
        self._streaming = False
        # readline 历史翻页位置（-1 = 最末，即新输入位置）
        self._rl_pos = -1
        # H19:streaming 非阻塞读超时时遗留的半行（下轮拼接续传，不再凭空丢失）
        self._rl_pending = b""
        # 2026-09-18 多行截断修复:bracketed paste 状态跨调用/跨超时持久 ——
        # 粘贴段内的 \n 是正文换行（不提交），只有裸 Enter（paste 段外的
        # \n/\r）才提交整行。旧实现读到一个 \n 就 break，多行粘贴只剩首行。
        self._rl_in_paste = False
        # P0-2（2026-09-20）:跨分片清洗状态机的持久缓冲 —— 不完整转义序列
        # 尾部（如 \x1b[200 拆在两片）扣下与下一分片拼接后再判定。
        self._rl_hold = b""

    def set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 期间非阻塞读。
        if self._streaming:
            # P0-5（2026-09-20）: 流式期间的窗口期残留同样合并（空串早退，契约不变）
            return _sanitize_submitted(_reconcile_burst_lines(self._nonblocking_readline(message)))
        # 2026-09-18 方向键/历史修复：裸 input() 挂 readline —— 方向键/退格
        # 由 GNU readline 解释（不再 ^[[A 字面回显），行写入内存历史供上键翻。
        ensure_readline()
        try:
            _line = input(message)
        except EOFError:
            # 审计#1 修复:EOF 传播（同 PromptToolkitSession）— 默认吞掉会造成
            # 非 TTY 场景「空输入→continue」死循环挂死。
            raise
        except KeyboardInterrupt:
            return ""
        # P0-5（2026-09-20）: 窗口期粘贴爆发重组 —— 多行残留合并为单条提交
        _merged = _reconcile_burst_lines(_line)
        add_history_line(_merged.split("\n", 1)[0])
        return _sanitize_submitted(_merged)

    def _nonblocking_readline(self, message: str = "") -> str:
        """streaming 期间非阻塞读一行。

        用 termios + select 非阻塞读取，键盘缓冲区有完整行时立即返回，
        无数据时返回空串（不卡死流式输出主线程）。
        支持方向键上/下翻历史（readline 序列：\\x1b[A 上 / \\x1b[B 下）。
        """
        import select
        import termios

        if not sys.stdin.isatty():
            return ""

        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001
            return ""

        try:
            # 原始模式：读单字节，不回显
            new = [list(x) if isinstance(x, list) else x for x in old]
            new[3] &= ~(termios.ICANON | termios.ECHO)
            new[6][termios.VMIN] = 0
            new[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new)

            buf = bytearray()
            if message:
                os.write(sys.stdout.fileno(), message.encode())

            while True:
                # 2026-09-18 吞字修复:Esc 探测线程替读的用户键入优先取回
                # （必须最前，顺序在新字节之前 —— 探测先于本轮读发生）
                _rp = replay_stdin_bytes()
                if _rp:
                    buf.extend(_rp)
                    os.write(sys.stdout.fileno(), _rp)

                # H19:上轮超时遗留的半行先续传（必须在 drain 之前拼接，否则
                # 新到字节先进 buf、pending 尾随 → 顺序颠倒 "defabc"）
                if self._rl_pending:
                    buf.extend(self._rl_pending)
                    os.write(sys.stdout.fileno(), self._rl_pending)
                    self._rl_pending = b""

                # drain 残留字节（Ctrl+C / 方向键序列首字节触发 UnicodeDecodeError）
                # H19:此前读后即弃 — 粘贴大文本分片在 select 空窗期落入时
                # 被整片丢弃，是"长文本分段丢失"的第一来源。现把文本字节
                # 追加进 buf（顺带修退格回显错位）。
                while True:
                    r, _, _ = select.select([fd], [], [], 0.0)
                    if not r:
                        break
                    try:
                        leftover = os.read(fd, 4096)
                        if not leftover:
                            raise EOFError
                    except OSError:  # noqa: BLE001
                        break
                    # P0-2（2026-09-20）:清洗状态机 —— 粘贴标记奇偶计数
                    # 翻转 _rl_in_paste（任何位置/跨片），其他 CSI 整体吞，
                    # 尾部不完整序列扣 _rl_hold 与下一分片拼接再判定。
                    leftover = self._rl_hold + leftover
                    leftover, _hold, self._rl_in_paste = _fallback_strip_ansi(
                        leftover, self._rl_in_paste
                    )
                    self._rl_hold = _hold
                    if leftover:
                        buf.extend(leftover)
                        os.write(sys.stdout.fileno(), leftover)

                # 等键盘（0.05s 超时，避免卡住流式输出）
                r, _, _ = select.select([fd], [], [], 0.05)
                if not r:
                    # 超时：无完整行 — 半行留到下轮拼接，不再凭空丢失
                    if buf:
                        self._rl_pending = bytes(buf)
                        buf = bytearray()
                    os.write(sys.stdout.fileno(), b"\r\x1b[K")
                    return ""

                ch = os.read(fd, 1)
                if not ch:
                    raise EOFError
                buf.extend(ch)
                os.write(sys.stdout.fileno(), ch)

                # 2026-09-18 转义序列统一处理（合并原 H18 分支）:
                # - \x1b[200~/\x1b[201~ 粘贴标记 → 翻转跨调用状态机（段内
                #   换行是正文不提交 = 多行粘贴不再截断）
                # - \x1b[A/\x1b[B → 历史翻页（此前 CSI 整体被字面回显 = 方向键 bug）
                # - 其他 CSI → 读到终结字节整体消费，不留残字节
                if ch == b"\x1b":
                    buf[-1:] = b""  # 摘掉先入 buf 的 \x1b（任何分支都不算正文）
                    r5, _, _ = select.select([fd], [], [], 0.05)
                    if not r5:
                        if self._rl_hold:
                            # P0-2: hold 扣着不完整前缀（如 \x1b[200），本 ESC 是接续
                            _joined, _hold, self._rl_in_paste = _fallback_strip_ansi(
                                self._rl_hold + b"\x1b", self._rl_in_paste
                            )
                            self._rl_hold = _hold
                            if _joined:
                                buf.extend(_joined)
                                os.write(sys.stdout.fileno(), _joined)
                        continue  # 孤立 Esc：消费掉
                    _n = os.read(fd, 4096)
                    if _n[:4] in (b"[200", b"[201") and len(_n) < 5:
                        # P0-2: 标记被拆片引导（\x1b+"[200"）—— 60ms×3 内补齐判定
                        _tries = 3
                        while len(_n) < 5 and _tries > 0:
                            _r6, _, _ = select.select([fd], [], [], 0.05)
                            if not _r6:
                                _tries -= 1
                                continue
                            _n += os.read(fd, 1)
                    if _n.startswith(b"[200~"):
                        self._rl_in_paste = True
                        _n = _n[5:]
                    elif _n.startswith(b"[201~"):
                        self._rl_in_paste = False
                        _n = _n[5:]
                    elif _n == b"[A" or _n == b"[B":  # 上/下:历史翻页
                        up = _n == b"[A"
                        if up:
                            if self._history and self._rl_pos < len(self._history) - 1:
                                self._rl_pos += 1
                            line = (
                                self._history[-(self._rl_pos + 1)]
                                if self._rl_pos >= 0
                                else ""
                            )
                        else:
                            if self._rl_pos > 0:
                                self._rl_pos -= 1
                                line = self._history[-(self._rl_pos + 1)]
                            elif self._rl_pos == 0:
                                self._rl_pos = -1
                                line = ""
                            else:
                                line = ""
                        self._erase_and_show(fd, buf, line)
                        buf = bytearray(line.encode())
                        continue
                    elif _n[:1] == b"[":
                        # 其他 CSI：读到终结字节（0x40-0x7E）为止，整体丢弃
                        while not (_n and 0x40 <= _n[-1] <= 0x7E):
                            r6, _, _ = select.select([fd], [], [], 0.05)
                            if not r6:
                                break
                            _n += os.read(fd, 4096)
                        continue
                    # 剩余正文（含标记剥离，防跨分片残留）
                    _n = _n.replace(_PASTE_START, b"").replace(_PASTE_END, b"")
                    if _n:
                        buf.extend(_n)
                        os.write(sys.stdout.fileno(), _n)
                    continue

                # Enter 提交 —— 仅 paste 段外；段内换行保留为正文
                # （多行粘贴不再只剩首行；段内换行回显已随上方 os.write(ch) 完成）
                if ch == b"\n" and not self._rl_in_paste:
                    break

                # 退格:先摘掉退格字节本身，再删它前面的字符 —— 旧实现只删
                # 退格字节，前字符留在 buf（视觉删了、提交时又出现）。
                if ch in (b"\x7f", b"\x08"):
                    buf = buf[:-1]
                    if buf:
                        buf = buf[:-1]
                        os.write(sys.stdout.fileno(), b"\x08 \x08")
                    continue

                # Ctrl+C
                if ch == b"\x03":
                    os.write(sys.stdout.fileno(), b"^C\n")
                    self._interrupt.set()
                    return ""

                # Ctrl+D
                if ch == b"\x04":
                    os.write(sys.stdout.fileno(), b"^D\n")
                    raise EOFError

                # CR → LF
                if ch == b"\r":
                    os.write(sys.stdout.fileno(), b"\n")
                    buf = buf[:-1] + b"\n"
                    break

                # 其他控制字符忽略
                if ch[0] < 32:
                    continue

            result = bytes(buf).decode("utf-8", errors="replace").rstrip("\n")
            if result:
                self._history.append(result)
                self._save_history()
            self._rl_pos = -1
            return result

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    @staticmethod
    def _erase_and_show(fd: int, old_buf: bytearray, new_line: str) -> None:
        """擦掉旧行内容，显示新内容。"""
        spaces = " " * max(len(old_buf), 1)
        new_bytes = new_line.encode()
        os.write(fd, (f"\r\x1b[K{spaces}\r{new_bytes}").encode())

    def push_to_history(self, text: str) -> None:
        if text.strip():
            self._history.append(text)
            self._save_history()

    def stream_print(self, renderable: Any) -> None:
        sys.stdout.write(str(renderable))
        sys.stdout.flush()

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        # 兜底实现无状态栏能力 — no-op 保持接口一致
        _ = get_fragments

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：无视 streaming 短路，真读一行。

        修复（2026-09-18 五症同源）：prompt() 在 streaming 期间走
        _nonblocking_readline（非阻塞，无输入时立即返回空串），
        pump 线程空转 → 用户输入完全无响应。必须临时关闭 _streaming
        走阻塞 input()，等用户输完一行再恢复。
        """
        _was_streaming = self._streaming
        self._streaming = False
        try:
            # 2026-09-18 方向键/历史修复：挂 readline + 写内存历史（泵收集行
            # 同样上键可翻；落盘仍由 repl 主循环 push_to_history 负责）。
            ensure_readline()
            _line = input(message)
            # P0-5（2026-09-20）: 泵收集路径同样合并窗口期粘贴爆发（历史记首行）
            _merged = _reconcile_burst_lines(_line)
            add_history_line(_merged.split("\n", 1)[0])
            return _sanitize_submitted(_merged)
        finally:
            self._streaming = _was_streaming

    def interrupt_event(self) -> threading.Event:
        return self._interrupt

    def _load_history(self) -> None:
        try:
            if self._history_file.exists():
                self._history = [
                    line for line in self._history_file.read_text(encoding="utf-8").splitlines() if line.strip()
                ]
        except OSError:
            self._history = []

    def _save_history(self) -> None:
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text("\n".join(self._history[-200:]), encoding="utf-8")
        except OSError:
            pass


def create_session(completer: Any | None = None) -> PromptSessionInterface:
    """入口选择（优先级从高到低，设计文档 docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §九）：

    1. LINGCLAUDE_TUI=0   → 强制 Fallback（CI/headless 关 TUI）
    2. LINGCLAUDE_TUI=2   → 强制 P2 全屏 TUI（TTY+PT 可用时；Q4 落地 2026-09-15）
    3. LINGCLAUDE_TUI=1   → TTY+PT 可用时强制启用（覆盖 CLI_MODE=plain；P1 形态）
    4. LINGCLAUDE_CLI_MODE=plain → Fallback（原有开关）
    5. 非 TTY / PT 未安装 / PT 构造失败 → Fallback
    """
    tui_env = os.environ.get("LINGCLAUDE_TUI")
    if tui_env == "0":
        return FallbackSession()
    if tui_env not in ("1", "2") and os.environ.get("LINGCLAUDE_CLI_MODE") == "plain":
        # 未设置 TUI 开关时维持原有 plain 语义；=1/=2 时 plain 被覆盖
        return FallbackSession()
    if not sys.stdin.isatty():
        return FallbackSession()
    if not _HAS_PROMPT_TOOLKIT:
        return FallbackSession()
    if tui_env == "2":
        # P2 全屏 TUI（Q4 落地）：构造失败回退 P1 形态，再失败回退 Fallback。
        # 全屏模式由 LINGCLAUDE_TUI=2 显式开启 —— 默认路径不受影响（P1 形态）。
        try:
            from lingclaude.cli.full_tui import FullTuiSession

            return FullTuiSession(completer=completer)
        except Exception:  # noqa: BLE001 — 全屏构造失败降级 P1，不崩
            pass
    try:
        return PromptToolkitSession(completer=completer)
    except RuntimeError:
        return FallbackSession()

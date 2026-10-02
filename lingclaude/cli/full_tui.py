"""P2 全屏 TUI 会话 — 常驻形态（2026-09-16，用户需求：输入行常驻屏幕底部）。

设计文档: docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §二 HSplit 全屏布局。

形态（常驻 Application，v2 重写）:
- 后台线程运行全屏 Application（HSplit：可滚动输出窗 + 分隔线 + 状态栏 + 底部输入框），
  从 start() 起持续驻留，直到 close()。
- 输入框 Enter 提交 → 内部提交队列（deque+Condition）→ prompt() 阻塞取用。
  生成期提交同样入队（不退出全屏），轮结束后被主循环消费 —— 「随时可输入」。
- 输出历史滚动（2026-09-19）：滚轮 / PageUp/PageDown / Shift+↑↓ / Ctrl+Home
  / Ctrl+End 移动输出 buffer 光标行实现回看；光标回文末自动恢复跟随模式
  （分隔线提示「回看输出历史」）。输出窗=Window+BufferControl 子类手工组合
  （TextArea 不支持传 key_bindings，且其控件对无焦点滚轮直接 NotImplemented）；
  滚轮事件在控件层拦截 —— 无需焦点、不抢输入框焦点。
- stdout 代理：Application 运行期间接管 sys.stdout，所有输出（含生成期
  sys.stdout.write 流式）按行追加进输出窗 —— 输出与输入框互不践踏。
- Ctrl+C：有文字清行；空缓冲 set interrupt_event 打断当前生成（与
  PromptToolkitSession 的 Ctrl+C 软中断语义对齐）。
- Ctrl+D（空输入）→ EOF 哨兵入队 → prompt() 抛 EOFError → 主循环正常退出。
- Esc 让位输入框编辑；Esc+Enter 换行（multiline 提交习惯不变）。

安全约束（P0/P1 事故防复发）:
- 不启用 InputPump / _esc_listen_loop 双读者（调用方 repl.py 对 FullTui
  不启 pump —— isinstance 判定天然跳过；stdin 唯一读者是 PT 事件循环）。
- prompt() 不直接操作终端；终端控制权全程归 PT 事件循环（后台线程）。
- 主循环退出路径 close() → app.exit() → PT 恢复终端状态，再打退出统计。

降级:
- start() 失败（极端终端）→ _running=False → prompt() 回退裸 input()。
- prompt_toolkit 未安装 → 构造抛 RuntimeError（create_session 捕获回退 P1）。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from lingclaude.core import policy_loader

logger = logging.getLogger(__name__)

from lingclaude.cli.input_queue import EOF_SENTINEL
from lingclaude.cli.interface import (
    PT_TUI_STYLE,
    _col_to_char_index,
    _disp_width,
    _extract_sgr_styles,
    _fallback_strip_ansi,
    _line_disp_width,
    _patch_pt_modifier_enter,
    _strip_ansi_text,
)
from lingclaude.engine.lineedit import add_history_line, ensure_readline

# prompt_toolkit 为可选依赖 — 未安装时构造抛 RuntimeError（create_session 捕获回退）
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        CompletionsMenu,
        FloatContainer,
        HSplit,
        Layout,
        Window,
    )
    from prompt_toolkit.layout.containers import Float
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.processors import (
        Processor,
        Transformation,
        TransformationInput,
    )
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
    from prompt_toolkit.output import create_output
    from prompt_toolkit.widgets import TextArea

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

if _HAS_PROMPT_TOOLKIT:
    from prompt_toolkit.keys import Keys  # noqa: E402 — 可选依赖条件导入

# 与 interface.py 对齐的历史文件（项目内隔离，2026-09-15 P1-2）
DEFAULT_HISTORY_FILE = ".lingclaude/history"

# 输出窗行数上限（超出丢最旧行）。
# P2-2（2026-09-20）: 800→5000 —— 长会话生成内容此前被静默裁剪，回看不完整；
# 5000 行约 0.5MB 内存，代价可忽略。
MAX_OUTPUT_LINES = 5000

# B(2026-09-26): 输入框**显示**行数硬上限（atomcode retained.rs 借鉴）。
# 场景：无占位符的长粘贴（< _PASTE_FOLD_MIN_LINES 的多次短粘贴累积、
# 或占位符被还原态编辑）把输入框撑成几十行，输出窗被挤到只剩一两行。
# 显示封顶 6 行：超出部分 prompt_toolkit 输入窗内部滚动；buffer 文本
# 完整保留，提交原文不截断（与 atomcode「display capped, content intact」
# 语义一致）。
INPUT_DISPLAY_MAX_LINES = 6

# ---------------------------------------------------------------------------
# 长文本粘贴折叠（2026-09-21）：
# P2 全屏输入框粘贴多行长文本时逐行平铺——占满视口、淹没正在编辑的短行，
# 提交后输出窗回显也会被同一段文本二次刷屏。改为 Claude Code 式占位块：
# 粘贴 ≥ _PASTE_FOLD_MIN_LINES 行时，输入框内折叠为单行占位符
# 「[文本块 #N · M行 · C字符]」，提交时还原全文进提交队列（prompt() 的
# 消费方拿到的与未折叠行为无差异；输出窗回显保留占位符形态防刷屏）。
# 阈值常数便于小测试 monkeypatch 调低。
_PASTE_FOLD_MIN_LINES = 6

# 占位符模板与还原正则。还原按「编号在 _paste_registry 中存在」判定——
# 用户手打的同形字面串若编号从未登记过，不会被误还原。
_PLACEHOLDER_FMT = "[文本块 #{n} · {lines}行 · {chars}字符]"
_PLACEHOLDER_RE = re.compile(r"\[文本块 #(\d+) · \d+行 · \d+字符\]")

# TUI 图片直接粘贴（2026-10-01）：图片附件占位符（仅展示；图片本体走
# _pending_images 侧信道，repl.py 提交时 drain 附到消息，占位符文本本身
# 随消息发出，对模型起到「此处有图」的说明作用）。
_IMAGE_PLACEHOLDER_FMT = "[图片 #{n} · {fmt} · {kb}KB]"
# 已知图片格式 magic 头（PIL 缺席时的兜底验真）。与 commands.py /image 的
# PIL verify 双轨——粘贴高频路径保持零重依赖；BM（2字节）太弱不收，
# 防普通文本粘贴误判。
_IMAGE_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_FILE_URI_RE = re.compile(r"^file://", re.IGNORECASE)

# C(2026-09-26): 粘贴注册表持久化条目上限（超出丢最旧——防注册表
# 无限增长把 pastes.json 撑爆；单条 1MiB 上限见 _load_paste_store）。
_PASTE_STORE_MAX = 200


_OSC52_DEBUG = os.environ.get("LING_OSC52_DEBUG", "") not in ("", "0", "false")

# ── 信号级终端复位（2026-09-27，仿 atomcode signal_restore.rs）──────────
# 被 SIGTERM/SIGHUP 直接杀进程时，Python 层 finally/atexit 全部不执行，
# PT 已进的备用屏/鼠标上报/隐藏光标全部残留（鼠标残码事故的最后一类
# 未覆盖路径）。handler 里只做 async-signal-safe 近似操作：os.write 一段
# 静态字节串（无分配、无锁、无任意 Python 调用），然后恢复默认 disposition
# 并 re-raise 保持退出码语义。SIGINT 不碰——那是 Ctrl+C 打断机制的。
_SIGNAL_RESTORE_SEQ = (
    b"\x1b[<u"            # kitty 键盘协议 pop
    b"\x1b[=0;1u"         # kitty flags 清零
    b"\x1b[?1004l"        # 焦点上报 off
    b"\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l"  # 鼠标上报全关
    b"\x1b[?2004l"        # bracketed paste off
    b"\x1b[?1049l"        # 退出备用屏（自动恢复主屏内容）
    b"\x1b[?25h"          # 显示光标
    b"\x1b[0m"            # SGR 全复位
)


def _signal_restore_handler(signum: int, frame: Any) -> None:
    """SIGTERM/SIGHUP handler：仅 signal-safe 写复位序列，再恢复默认终止。"""
    try:
        os.write(1, _SIGNAL_RESTORE_SEQ)
    except Exception:  # noqa: BLE001 — 终端已关（SIGHUP 常态）：放弃即走
        pass
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:  # noqa: BLE001 — re-raise 失败则直接退出
        os._exit(128 + signum)


def _install_signal_restore() -> None:
    """装 SIGTERM/SIGHUP 复位 handler（幂等；非主线程静默放弃）。"""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _signal_restore_handler)
        except (ValueError, OSError):  # 非主线程 / 不支持的信号
            pass


if _HAS_PROMPT_TOOLKIT:

    def _style_range(
        fragments: list[tuple], lo: int, hi: int, add: str
    ) -> list[tuple]:
        """对 fragments 的显示列区间 [lo, hi) 追加样式串 add（SGR 白名单）。

        与 _reverse_range 同构（按绝对显示列切 fragment），区别是不限定
        reverse 而是合并任意样式串；事件样式（*rest）原样透传。
        显示列坐标：全角字符占 2 列（_disp_width，2026-09-29 CJK 对齐），
        与 _extract_sgr_styles 产出的 span 列号同坐标系。
        """
        out: list[tuple] = []
        pos = 0
        for style, txt, *rest in fragments:
            w = _line_disp_width(txt)  # 全角=2/半角=1；旧 len(txt) 全角行错位
            seg_lo, seg_hi = pos, pos + w
            pos = seg_hi
            a = max(seg_lo, lo)
            b = min(seg_hi, hi)
            if a >= b:  # 无交集
                out.append((style, txt, *rest))
                continue
            # 显示列→字符下标（全角切点落字符边界，2026-09-29 CJK 对齐）
            ci_a = _col_to_char_index(txt, a - seg_lo)
            ci_b = _col_to_char_index(txt, b - seg_lo)
            pre = txt[:ci_a]
            mid = txt[ci_a:ci_b]
            post = txt[ci_b:]
            if pre:
                out.append((style, pre, *rest))
            if mid:
                merged = f"{style} {add}" if style else add
                out.append((merged, mid, *rest))
            if post:
                out.append((style, post, *rest))
        return out

    def _reverse_range(
        fragments: list[tuple], lo: int, hi: int | None
    ) -> list[tuple]:
        """对 fragments 的**字符下标**区间 [lo, hi)（hi=None 到行尾）追加 reverse。

        坐标系 = PT 鼠标坐标语义：containers.py:2076-2078 的映射循环里
        `col += 1`（每字符进 1，全角字也只进 1）而 `x += char_width`
        （屏幕列全角进 2）——即 position.x 是字符下标，不是显示列。
        与 _extract_selected_text 的 Python 切片（lines[r][sc:ec+1]）同系。

        勿"统一"成显示列（_line_disp_width/_col_to_char_index）：那会让
        高亮相对鼠标指针向左漂 N 格（N=点击点前全角字数），2026-09-29
        拖选高亮偏移根因即此。_style_range 的 SGR spans 是显示列系，
        两者输入来源不同、坐标系本就不同，勿互相看齐。
        跨界 fragment 按字符切三段，仅交集段带反色；事件样式（*rest，
        如鼠标处理器）原样透传。
        """
        out: list[tuple] = []
        pos = 0
        for style, txt, *rest in fragments:
            n = len(txt)  # 字符数 = PT col 坐标系；刻意不用 _line_disp_width
            seg_lo, seg_hi = pos, pos + n
            pos = seg_hi
            a = max(seg_lo, lo)
            b = seg_hi if hi is None else min(seg_hi, hi)
            if a >= b:  # 无交集
                out.append((style, txt, *rest))
                continue
            pre = txt[: a - seg_lo]
            mid = txt[a - seg_lo : b - seg_lo]
            post = txt[b - seg_lo :]
            if pre:
                out.append((style, pre, *rest))
            if mid:
                out.append((style + " reverse", mid, *rest))
            if post:
                out.append((style, post, *rest))
        return out

    class _SelectionHighlightProcessor(Processor):
        """拖选反色高亮（2026-09-27 OSC52 方案的视觉反馈层）。

        坐标系与取词一致：鼠标 DOWN/MOVE/UP 已被 Window 换算为 buffer
        行/列，选区存于 FullTuiSession._sel_start/_sel_end。BufferControl
        每行渲染都会跑 input_processors，在 fragment 层插 style 即可实现
        反色，不动 Window/布局。原地点击（起终点同格）不高亮。
        """

        def __init__(self, session: "FullTuiSession") -> None:
            self._session = session

        def apply_transformation(self, ti: TransformationInput) -> Transformation:
            sess = self._session
            row = ti.lineno
            # 受限富文本（2026-09-27）：先按样式表上 SGR 色，再做拖选反色。
            spans = sess._style_map.get(row)
            if spans:
                frags = ti.fragments
                for lo, hi, st in spans:
                    frags = _style_range(frags, lo, hi, st)
                ti = TransformationInput(
                    ti.buffer_control,
                    ti.document,
                    lineno=row,
                    source_to_display=ti.source_to_display,
                    fragments=frags,
                    width=ti.width,
                    height=ti.height,
                )
            start = sess._sel_start
            end = sess._sel_end
            if start is None or end is None or start == end:
                return Transformation(ti.fragments)
            if start > end:
                start, end = end, start
            sr, sc = start
            er, ec = end
            if not (sr <= ti.lineno <= er):
                return Transformation(ti.fragments)
            if sr == er:  # 单行：[sc, ec]
                frags = _reverse_range(ti.fragments, sc, ec + 1)
            elif ti.lineno == sr:  # 首行：起始列 → 行尾
                frags = _reverse_range(ti.fragments, sc, None)
            elif ti.lineno == er:  # 末行：行首 → 终点列（含）
                frags = _reverse_range(ti.fragments, 0, ec + 1)
            else:  # 中间整行
                frags = _reverse_range(ti.fragments, 0, None)
            return Transformation(frags)

    class _OutputScrollControl(BufferControl):
        """输出窗控件 — 在 BufferControl 基础上拦截滚轮事件。

        为什么子类化：原生 BufferControl.mouse_handler 只在**当前聚焦控件**
        是自己时才处理滚轮，否则直接 NotImplemented（controls.py:829 分支）。
        输出窗 focusable=False 永不聚焦 → 原生滚轮永远失效。这里把
        SCROLL_UP/SCROLL_DOWN 转成 on_wheel(+1/-1) 回调（无需焦点、不抢
        输入框焦点），其余事件交还父类。
        """

        def __init__(self, *args: Any, on_wheel: Callable[[int], None] | None = None, on_select: Callable[[str, Any], None] | None = None, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._on_wheel = on_wheel
            self._on_select = on_select

        def mouse_handler(self, mouse_event: MouseEvent) -> Any:
            et = mouse_event.event_type
            if self._on_wheel is not None:
                if et == MouseEventType.SCROLL_UP:
                    self._on_wheel(-1)
                    return None
                if et == MouseEventType.SCROLL_DOWN:
                    self._on_wheel(1)
                    return None
            # 拖选复制（2026-09-27 OSC52）：DOWN 记起点 / LEFT+MOVE 更新终点 /
            # UP 落锤取词 → OSC52 写终端剪贴板（穿透 SSH 直达本地）。
            # 细节委托 _on_select 回调；None 一律交回父类。
            if self._on_select is not None:
                if et == MouseEventType.MOUSE_DOWN:
                    self._on_select("down", mouse_event.position)
                    return None
                if et == MouseEventType.MOUSE_MOVE and mouse_event.button is MouseButton.LEFT:
                    self._on_select("move", mouse_event.position)
                    return None
                if et == MouseEventType.MOUSE_UP:
                    self._on_select("up", mouse_event.position)
                    return None
            return super().mouse_handler(mouse_event)


def _warn_raw(msg: str) -> None:
    """P0 普查（2026-09-25）：模块级原始 fd 预警，60s 节流。

    2026-09-27 通道修正：全屏 TUI 驻留期 stderr 也是被 PT 管理的同一
    tty——raw 模式下裸写 os.write(2) 会顶乱 alternate screen 光标，
    物理屏幕结构错位（鼠标偏移/toolbar 消失的帮凶之一，见
    hard_resync docstring 证据链）。改走 logger.warning：
    host_root.log 文件兜底（app.py:_install_root_file_guard）落盘，
    渲染层零字节直写终端。故障可见性不变（文件里有），屏幕不再被撕。
    60s 节流保留。
    """
    now = time.monotonic()
    if now - getattr(_warn_raw, "_last", 0.0) < 60.0:
        return
    _warn_raw._last = now  # type: ignore[attr-defined]
    try:
        logger.warning("full_tui raw warn: %s", msg)
    except Exception:  # noqa: BLE001 — 预警失败即放弃
        pass


_warn_raw._last = 0.0  # type: ignore[attr-defined]


class _StdoutProxy:
    """stdout 代理 — 按行累积写入全屏输出窗（线程安全）。

    Application 驻留期间替换 sys.stdout；任何线程的 print / sys.stdout.write
    都被路由进输出窗 TextArea。isatty=False → rich Console 自动走 plain 渲染。
    \\r（进度式覆写）简单丢弃当前半行，不做原地重绘（输出窗是纯文本追加模型）。
    """

    def __init__(self, owner: "FullTuiSession", original: Any) -> None:
        self._owner = owner
        self._original = original
        self._frag: list[str] = []
        # P1-3（2026-09-20）: 跨 write 调用的不完整转义序列尾部（拆片拼接）
        self._esc_hold = b""
        # 2026-09-25 P0 普查（toolbar 事故教训推广）：输出静默丢失预警节流游标。
        # write 炸 = 输出无声消失（最危险的静默 except），改为 os.write(2)
        # 原始 fd 提示（绕过可能已损坏的流对象），60s 节流防刷屏。
        self._last_write_err = 0.0

    def write(self, s: str) -> int:
        if not s:
            return 0
        try:
            # P1-3（2026-09-20，TUI 优化方案）: 转义清洗 —— 终端残留/上游
            # 混入的 CSI 序列（CPR 应答、DECSCUSR、DECRQM 等）不清洗会字面
            # 进输出窗成噪声。复用 P0-2 状态机（CSI 纯 ASCII，UTF-8 编码后
            # 处理安全：多字节字符的首/续字节均不落 0x40-0x7E）。
            data = self._esc_hold + s.encode("utf-8", errors="replace")
            self._esc_hold = b""
            # keep_sgr=True（2026-09-27 受限富文本）：SGR 保留，供写窗层
            # _extract_sgr_styles 解析成列区间样式表；非 SGR CSI 仍吞。
            data, hold, _ = _fallback_strip_ansi(data, False, keep_sgr=True)
            self._esc_hold = hold
            s2 = data.decode("utf-8", errors="replace")
            for ch in s2:
                if ch == "\n":
                    self._flush_line(force=True)  # 行边界确凿：空行也落窗
                elif ch == "\r":
                    self._frag.clear()
                else:
                    self._frag.append(ch)
                    if len(self._frag) > 4096:  # 超长无换行防御
                        self._flush_line()
        except Exception as _proxy_err:  # noqa: BLE001 — 输出代理异常不反噬调用方
            self._warn_output_loss(_proxy_err)  # 2026-09-25 P0: 可见降级
        return len(s)

    def _warn_output_loss(self, err: Exception) -> None:
        """输出丢失预警：原始 fd 直写 stderr，60s 节流。

        P0 普查（2026-09-25）：write/flush 炸曾静默 pass——流式输出无声消失
        且零证据。预警本身再炸（stderr 坏）则彻底放弃，绝不反噬输出调用方。
        """
        now = time.monotonic()
        if now - self._last_write_err < 60.0:
            return
        self._last_write_err = now
        try:
            os.write(2, f"\n⚠ 输出窗写入异常[{type(err).__name__}]：近 60s 输出可能丢失\n".encode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — 预警失败即放弃
            pass

    def _flush_line(self, force: bool = False) -> None:
        line = "".join(self._frag)
        self._frag.clear()
        # force=True（2026-09-30 空行丢失修复）：write() 遇 \n 即行边界确凿
        # 发生，frag 为空也必须落一行（空行）。此前 `if line:` 把 "\n\n" 的
        # 空行整行吞掉 → 窗内段落间距塌缩 + 与流式素字指纹（含空行）恒失
        # 配 → done 原位上色永远回退。flush()（外部周期调用）保持原语义：
        # frag 空时不造假行。
        if line or force:
            self._owner._write_via_buffer(line + "\n")

    def flush(self) -> None:
        # 半行也落窗（流式中途停滞时内容可见）
        try:
            self._flush_line()
        except Exception as _flush_err:  # noqa: BLE001
            self._warn_output_loss(_flush_err)  # 2026-09-25 P0: 可见降级
        if self._esc_hold:
            # P1-3: flush 时仍扣着的不完整序列 = 无终结字节的残骸，丢弃
            self._esc_hold = b""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("stdout proxy has no fd")

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class FullTuiSession:
    """P2 常驻全屏 TUI 会话（PromptSessionInterface 协议实现）。

    架构：start() 后台线程跑常驻 Application；输入框 accept → 提交队列 →
    prompt() 返回；生成期 stdout 被代理进输出窗，输入框全程可编辑可提交。
    """

    def __init__(
        self,
        history_file: str = DEFAULT_HISTORY_FILE,
        completer: Any | None = None,
        output_source: Callable[[], list[str]] | None = None,
    ) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError("prompt_toolkit 未安装，请使用 FallbackSession")
        history_path = Path(history_file).expanduser()
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history = FileHistory(str(history_path))
        except OSError:
            self._history = InMemoryHistory()
        self._completer = completer
        self._output_source = output_source or (lambda: [])
        self._status_cb: Callable[[], list[tuple[str, str]]] | None = None
        # 2026-09-22: Shift+Tab 模式环回调（repl 装配时经 install_mode_toggler
        # 注入；键位按下时才读取，注入时序无关）
        self._mode_toggler: Callable[[], None] | None = None
        # 2026-09-27: Ctrl+T 任务面板显隐回调（repl 装配时经
        # install_todo_panel_toggler 注入；键位按下时才读取，注入时序无关）
        self._todo_panel_toggler: Callable[[], None] | None = None
        self._interrupt = threading.Event()
        # 2026-09-18 多行输入增强:构造 UI 前改写 PT 输入序列表，让
        # Ctrl+Enter / Shift+Enter 复用下方 Esc+Enter 换行 chord
        # （单源补丁在 interface.py，P1/P2 共用）。
        _patch_pt_modifier_enter()
        # 流式生成期标志（set_streaming 置位；prompt 据此决定是否消费 interrupt）
        self._streaming = False

        # 长文本粘贴折叠状态（_register_paste 写 / _expand_placeholders 读）：
        # _paste_seq 占位符编号单调递增；_paste_registry 编号 → (全文, 行数)。
        # 生命周期：全会话累计、不随提交清空——已消费占位符的残留条目无害
        # （还原只发生在提交瞬间，按当前 buffer 文本里出现的编号命中）。
        # C(2026-09-26) 富历史：注册表落盘 .lingclaude/pastes.json，会话
        # 启动时回灌——修复「上键召回含占位符的旧条目，提交后占位符字面
        # 发给模型」缺陷（_paste_registry 纯内存，重启即空；而 FileHistory
        # 里的历史行原样含占位符）。
        self._paste_seq = 0
        self._paste_registry: dict[int, tuple[str, int]] = {}
        self._paste_store_path = history_path.parent / "pastes.json"
        self._load_paste_store()

        # C(2026-09-26): _paste_seq 续接已持久化最大编号（回灌的条目占据
        # 编号段，新粘贴不得与之冲突）
        if self._paste_registry:
            self._paste_seq = max(self._paste_registry)

        # 常驻运行状态
        self._submit_q: deque[str] = deque()
        self._submit_cond = threading.Condition()
        self._app: Any = None
        self._app_thread: threading.Thread | None = None
        self._running = False
        self._ever_started = False  # prompt() 降级判定：未启动过→input()；已启动→EOF
        self._app_error: str = ""  # 后台线程异常留痕（2026-09-09 静默死亡教训）
        self._stdout_proxy: _StdoutProxy | None = None
        self._stdout_original: Any = None
        # 输出窗行缓冲（跨线程安全追加）：任意线程 write → pending_lines，
        # drain 进 TextArea。_area_lock 保护 TextArea 的读改写竞态
        # （多线程同时 join 搬运会互相覆盖丢行）。
        self._out_lock = threading.Lock()
        self._pending_lines: list[str] = []
        self._area_lock = threading.Lock()
        # 受限富文本（2026-09-27）：输出窗行号→列区间样式表。行文本入
        # Buffer（无 ESC），SGR 样式单独存这里，渲染时 _SgrStyleProcessor
        # 按 (行,列起,列止) 上色——零字符污染，不碰 PT 布局（processor 插
        # \n 会被 document.line_count 错位，实码验证见 interface.py 注释）。
        # 写侧持 _out_lock 序列化；读侧（渲染线程）持 _style_lock，双锁
        # 分离避免渲染与行搬运互等。
        self._style_map: dict[int, list[tuple[int, int, str]]] = {}
        self._style_lock = threading.Lock()
        # 与 _pending_lines 平行的样式暂存队列（_out_lock 保护，元素与
        # pending 行一一对应）；drain 时按起始行号并入 _style_map。
        self._style_spans_pending: list[list[tuple[int, int, str]]] = []
        # TUI 原位上色轮次标记（2026-09-30）：-1=无活动标记；set_streaming(True)
        # 记下流式素字段起点，done 原位替换时消费。替换失败/轮次收尾均复位，
        # 防止跨轮误替换旧行。
        self._stream_start_mark = -1
        # Raw 看门狗节流游标（2026-09-26 假死根治）：见 _check_tty_raw_drift。
        # app 活着但终端被外部踩回 canonical 时，PT 逐键读者收不到任何事件，
        # 表现为整屏假死（19:20-19:45 事故）——等待循环每轮顺带检测自愈。
        self._raw_guard_last = 0.0

        # TUI 图片粘贴（2026-10-01）：待附图片队列，每条 (raw_bytes, mime_type)。
        # /image 命令追加（读剪贴板得到 hex → decode）；repl.py 主循环提交
        # 时 drain 给 engine.submit，_build_messages 转为 base64 填入 ModelMessage。
        self._pending_images: list[tuple[bytes, str]] = []
        # TUI 图片直接粘贴（2026-10-01）：图片粘贴事件计数（诊断+测试断言用）
        self._image_paste_count: int = 0

        # 拖选复制状态机（2026-09-27 OSC52）：DOWN 记起点，LEFT+MOVE 更新
        # 终点，UP 落锤取词。_sel_active 防丢 UP 后 MOVE 续画；_sel_dragged
        # 区分「原地点击」与「真拖动」（点击不清不复制，不干扰终端原生行为）。
        self._sel_start: tuple[int, int] | None = None
        self._sel_end: tuple[int, int] | None = None
        self._sel_active = False
        self._sel_dragged = False

        # OSC52 写入通道（2026-09-27）：默认 /dev/tty（SSH 场景 sys.stdout
        # 可能是管道）；App 驻留期改用 app.output 的 raw fd（PT 正在管终端）。
        self._osc52_fd: int | None = None

        # 控件（常驻复用）。输出窗 = Buffer + BufferControl子类 + Window 手工
        # 组合（TextArea 不支持传 key_bindings/自定义控件；见模块 docstring）。
        # 滚动模型：输出 buffer 光标 = 视口锚点（PT 渲染层 keep-cursor-visible
        # 按 cursor 行钳制 vertical_scroll）。跟随模式 = 光标钉在文末；滚轮/
        # 翻页把光标移进历史 → 回看模式；光标回文末自动恢复跟随。
        self._follow_output = True
        self._out_buffer = Buffer(
            document=Document("", 0),
            read_only=True,
            multiline=True,
            name="output-window",
        )
        self._out_control = _OutputScrollControl(
            buffer=self._out_buffer,
            focusable=False,
            focus_on_click=False,
            include_default_input_processors=False,
            input_processors=[_SelectionHighlightProcessor(self)],
            on_wheel=self._on_out_wheel,
            on_select=self._on_select_event,
        )
        self._output_area = Window(
            content=self._out_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:output",
            always_hide_cursor=True,
        )
        self._input_area = TextArea(
            text="",
            multiline=True,
            wrap_lines=True,
            # B(2026-09-26): 输入框显示行数封顶（防长粘贴撑爆布局），
            # 见 INPUT_DISPLAY_MAX_LINES 注释。内部滚动，内容不截断。
            height=Dimension(min=1, max=INPUT_DISPLAY_MAX_LINES),
            completer=self._completer,
            history=self._history,
            accept_handler=self._on_accept,
            style="class:input",
        )
        self._status_win = Window(
            # 2026-09-22: todo panel 常驻状态栏上方——toolbar_fragments 现返回
            # 多行片段（清单行 + 状态行），高度必须跟随内容行数自适应，
            # 写死 1 会把清单截进状态行造成错位。
            height=Dimension(min=1, max=8),
            content=FormattedTextControl(self._status_fragments),
            style="class:status",
        )
        self._sep_win = Window(
            height=1,
            content=FormattedTextControl(self._sep_fragments),
            style="class:sep",
        )

        # 键绑定：Enter 提交 / Esc+Enter 换行 / Ctrl+C 清行或打断 / Ctrl+D 空退出。
        # 注（2026-09-27 实测）：换行的通用键是 Esc+Enter——它只由普通按键序列
        # 组成，不依赖任何终端增强协议。Ctrl+Enter / Shift+Enter 换行依赖终端发
        # xterm modifyOtherKeys / kitty 增强键盘序列（interface._patch_pt_modifier_enter
        # 已备映射），但多数终端（含 Windows Terminal，IME 探针实证）对 Enter
        # 组合键统一发裸 \r，这些序列永远不会到达——需要组合键换行时，在客户端
        # 用 sendInput 把它绑成 ESC+CR。
        self._kb = KeyBindings()

        # 输出历史滚动键（app 级：优先级高于 emacs 默认绑定，application.py
        # _create_key_bindings 反转列表后当前控件链 > app > 默认）。全部为
        # 「移动输出 buffer 光标行」语义；光标回文末自动恢复跟随模式。
        @self._kb.add("pageup")
        def _out_page_up(event: Any) -> None:
            self._scroll_out_pages(-1)

        @self._kb.add("pagedown")
        def _out_page_down(event: Any) -> None:
            self._scroll_out_pages(1)

        @self._kb.add("s-up")
        def _out_line_up(event: Any) -> None:
            self._scroll_out_lines(-1)

        @self._kb.add("s-down")
        def _out_line_down(event: Any) -> None:
            self._scroll_out_lines(1)

        @self._kb.add("c-home")
        def _out_home(event: Any) -> None:
            self._out_buffer.cursor_position = 0
            self._invalidate()

        @self._kb.add("c-end")
        def _out_end(event: Any) -> None:
            self._out_buffer.cursor_position = len(self._out_buffer.text)
            self._invalidate()

        # 2026-09-22: Shift+Tab 循环切换工作模式（auto → ask → strict → plan，
        # 单键管所有模式，对标 cc）。回调由 repl 注入（install_mode_toggler），
        # 未注入时静默空转——TUI 层不 import engine/permissions，保持解耦。
        # 键名用 Keys.BackTab 枚举（实测 PT 别名表无 "backtab" 字符串，会
        # ValueError 炸构造；物理 Shift+Tab 的 \x1b[Z 经 ANSI_SEQUENCES
        # 解析正是 Keys.BackTab）。
        @self._kb.add(Keys.BackTab)
        def _cycle_work_mode(event: Any) -> None:
            toggler = self._mode_toggler
            if toggler is not None:
                toggler()

        # 长文本粘贴折叠：接管 BracketedPaste（app 级绑定优先于 PT 默认的
        # 「直接整段插入」绑定——application.py:_create_key_bindings 反转
        # 绑定列表后 key_processor._process 只调 matches[-1]，唯一赢家，
        # 默认 handler 不会重复执行）。eager=True：粘贴语义独立成键，不等
        # 更长序列匹配，行为确定。
        @self._kb.add(Keys.BracketedPaste, eager=True)
        def _on_bracketed_paste(event: Any) -> None:
            self._handle_paste(event)

        @self._kb.add("enter")
        def _on_enter(event: Any) -> None:
            # 2026-09-19 输入历史修复：走 PT 标准 accept 流程
            # （validate_and_handle → accept_handler → append_to_history →
            # reset），保证提交的输入写进 FileHistory（Up 可翻）。
            # 旧实现直调 _on_accept 且内部清空文本，历史写入永远拿到空串。
            buf = event.app.layout.current_buffer
            if buf is not None:
                buf.validate_and_handle()

        @self._kb.add("escape", "enter")
        def _on_newline(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is not None:
                buf.insert_text("\n")

        # Ctrl+L 硬重绘（2026-09-27 错位根治）：必须覆盖 PT 默认
        # clear-screen（basic.py:155 → renderer.clear() →
        # erase(leave_alternate_screen=True)）——那个默认实现会退出
        # \x1b[?1049h 备用屏，全屏 TUI 之后画在主缓冲区上，物理屏幕
        # 结构永久错位（鼠标点击偏上 N 行 + toolbar 顶出视口的同源病根，
        # 实码证据见 hard_resync docstring）。app 级绑定优先级高于默认，
        # 反转列表后唯一赢家，默认 handler 不再执行。eager=True 语义独立。
        @self._kb.add("c-l", eager=True)
        def _on_hard_resync(event: Any) -> None:
            self.hard_resync()

        # Ctrl+T 任务面板显隐切换（2026-09-27）：翻转 status.show_todo_panel，
        # 渲染层 toolbar_fragments 据此决定 ⚙{ip}·{pd} 角标输出。回调由 repl
        # 注入（install_todo_panel_toggler），未注入时静默空转——TUI 层不
        # import status/repl，保持解耦（与 Shift+Tab 模式环同款注入模式）。
        @self._kb.add("c-t")
        def _on_toggle_todo_panel(event: Any) -> None:
            toggler = self._todo_panel_toggler
            if toggler is not None:
                toggler()

        @self._kb.add("c-c")
        def _on_ctrl_c(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is not None and buf.text:
                buf.text = ""
            else:
                # 空缓冲 Ctrl+C = 打断当前生成（interrupt_event 语义与 PT 会话对齐）
                self._interrupt.set()

        @self._kb.add("c-d")
        def _on_ctrl_d(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is None or not buf.text:
                self._submit(EOF_SENTINEL)

    # ── 生命周期 ──

    def _build_application(self, output: Any) -> Any:
        """P2-13（Pi chord 双代热更）：新一代全屏 Application 的构造工厂。

        把 start() 里原本硬编码的 Application 构造抽成工厂——双代 cutover
        对「新一代」与「首代」共用同一构建逻辑。output 由调用方注入
        （首代取真实 stdout；候选代可注 fake 做离线 verify，不抢终端控制权）。
        返回未 run 的 Application 实例（生命周期归 cutover/start 管）。
        """
        return Application(
            # A(2026-10-02) 斜杠补全菜单：PT 补全下拉必须挂 FloatContainer
            # float（裸 HSplit 无浮层容器，completer 算出候选也无处渲染）。
            # xcursor/ycursor 跟随输入光标，max_height=8 防长清单撑爆布局。
            layout=Layout(FloatContainer(
                content=HSplit([
                    self._output_area,
                    self._sep_win,
                    self._status_win,
                    self._input_area,
                ]),
                floats=[
                    Float(
                        xcursor=True, ycursor=True,
                        content=CompletionsMenu(max_height=8, scroll_offset=1),
                    ),
                ],
            )),
            key_bindings=self._kb,
            # 2026-09-22: 状态栏样式注册 —— class:green/yellow/red/accent
            # fragment（状态球、上下文分色、todo ⚙/·）此前无规则匹配，
            # 全部默认色。规则表见 interface.PT_TUI_STYLE（裸类名 key，
            # PT3 规则字典禁带 class: 前缀）。PT 缺席时 None 走 PT 默认。
            style=PT_TUI_STYLE,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
            # PT3: output/input 只能在构造期注入（run() 不接受 output 参数，
            # 传了直接 TypeError → 全屏线程启动即死）。
            output=output,
        )

    def start(self) -> None:
        """启动常驻全屏 Application（后台线程 + stdout 代理接管）。"""
        if self._running:
            return
        self._refresh_output_area()
        # 先保存真实 stdout（代理替换之前）—— PT 渲染输出必须直连真 stdout，
        # 否则走 sys.stdout 命中 _StdoutProxy → 写进输出窗 → invalidate →
        # 再渲染 → 死循环。
        self._stdout_original = sys.stdout
        out = create_output(stdout=self._stdout_original)
        self._app = self._build_application(out)
        self._stdout_proxy = _StdoutProxy(self, self._stdout_original)
        sys.stdout = self._stdout_proxy
        self._running = True
        self._ever_started = True
        # 信号级终端复位（2026-09-27）：覆盖 SIGTERM/SIGHUP 直接杀进程时
        # finally/atexit 全部不执行的残留场景（ac signal_restore.rs 同型）。
        # 原子操作失败（非主线程等）静默放弃，与兜底链其他层互不依赖。
        _install_signal_restore()
        self._app_thread = threading.Thread(
            target=self._run_app, daemon=True, name="full-tui-app",
        )
        self._app_thread.start()
        # C-fix（2026-09-27 鼠标残码根治）: 进程终态兜底 —— 解释器正常退出
        # （含未捕获异常路径，这类路径不经过 _hard_exit_after_close）时，
        # atexit 回调先于 daemon 线程冻结执行，此处补发增强模式复位，防
        # PT 全屏线程被硬杀后鼠标上报模式残留（移动鼠标刷序列码的病灶）。
        # 幂等：重复注册/重复执行均无害（复位序列可重入）。
        try:
            import atexit as _atexit
            from lingclaude.engine.lineedit import (
                reset_terminal_key_modes as _reset_modes,
            )

            _atexit.register(_reset_modes)
        except Exception:  # noqa: BLE001 — 增强路径，绝不反噬启动
            pass
        # P2-3（2026-09-20，TUI 优化方案）: 全屏健康自检 —— 1s 后未驻留
        # （启动即死/从未进入运行态）时 stderr 显式留痕，消除「用户不知情
        # 被降级成简易输入模式」的静默失败。
        _probe = threading.Timer(1.0, self._startup_health_probe)
        _probe.daemon = True
        _probe.start()

    def cutover_generation(
        self,
        build_new: "Callable[[], Any] | None" = None,
        verify: "Callable[[Any], None] | None" = None,
    ) -> bool:
        """P2-13（Pi chord 双代热更）：新一代渲染 cutover（蓝绿，零不可用窗口）。

        Pi chord 语义：新一代渲染器以 **candidate** 先构建 + 验证（不动现役
        app），验证成功才 **cutover**（旧代优雅退役 → 新代接管），失败则
        **dispose candidate**、旧代继续服务。旧代全程在线兜底，用户零感知。

        参数：
        - build_new: 新一代 Application 工厂。缺省用 self._build_application
          （注真实 stdout 同源 output，等价当前 start() 的构造逻辑）；
          宿主可注入自定义工厂（换渲染主题/布局/刷新策略等「新一代」形态）。
        - verify: 候选验证钩子（candidate → 断言/异常）。缺省做最小健全性
          检查（非 None + 是 Application + 有 layout）。verify 抛异常即候选
          验证失败 → dispose 回退旧代。

        返回 True=切换成功；False=候选失败已回退（旧代仍服务，零不可用窗口）。

        与 plugin_lifecycle.hot_swap 对齐的蓝绿语义（本方法是其「渲染层」
        对位——插片实例面已在 plugin_lifecycle 落地，此处补齐 TUI 渲染代次
        的双代 cutover，即 §3.2 P2-13 缺口）：
        - candidate 构建在现役 app 之外（不抢终端控制权，output 可离线注入）；
        - 切换原子化：旧代 app.exit() + 线程 join 完成后才起新代线程；
        - 失败不破坏旧代：candidate dispose + 旧代零扰动。

        线程安全：持有 self._app_thread 的 join 语义；与 prompt()/_run_app
        通过 _submit_cond / _running 状态协作（切换瞬间输出窗按「旧线程退出、
        新线程接管」有序接力，不丢行——pending_lines 跨代保留）。
        """
        # 1) 构建候选（不动现役 self._app）
        try:
            if build_new is not None:
                candidate = build_new()
            else:
                # 缺省工厂：与 start() 同源——真实 stdout 的 output
                out = create_output(stdout=self._stdout_original or sys.stdout)
                candidate = self._build_application(out)
        except Exception:  # noqa: BLE001 — 构建失败 = 候选不可用，旧代不动
            logger.warning("cutover_generation: 候选构建失败，保留旧代", exc_info=True)
            return False

        # 2) 验证候选（候选态，未接管）
        try:
            if verify is not None:
                verify(candidate)
            else:
                self._verify_candidate(candidate)
        except Exception:  # noqa: BLE001 — 验证失败 = dispose 候选，旧代不动
            logger.warning("cutover_generation: 候选验证失败，保留旧代", exc_info=True)
            return False

        # 3) 切换（原子接力：旧代退役 → 新代接管）
        old_app = self._app
        old_thread = self._app_thread
        self._app = candidate

        # 3a) 优雅退役旧代（若正在运行）：exit + join（超时兜底不阻塞）
        if old_app is not None:
            try:
                old_app.exit()
            except Exception:  # noqa: BLE001
                pass
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=3.0)

        # 3a') 双代互斥门（2026-09-26 假死根治）：旧代未退不得起新代 ——
        # 两个 PT Application 并存会争抢同一 pts 的 termios/渲染，是 raw
        # 失同步与键盘事件分裂的第二窗口。join 超时 = 旧代卡死，宁可保持
        # 旧代（用户仍有可用界面）也不强行并立；候选弃置（未 run 过，
        # 无终端副作用，交给 GC）。
        if old_thread is not None and old_thread.is_alive():
            self._app = old_app
            logger.error(
                "cutover_generation: 旧代线程 join(3s) 未退出，放弃切换（保留旧代，"
                "禁止双 PT Application 并存）"
            )
            return False

        # 3b) 起新代线程（接管 stdout 代理与事件循环）
        if self._running or (old_thread is not None):
            self._app_thread = threading.Thread(
                target=self._run_app, daemon=True, name="full-tui-app-gen2",
            )
            self._app_thread.start()
        else:
            # 首代尚未 start（冷 cutover）：补全 start() 的初始化路径
            self._stdout_proxy = _StdoutProxy(self, self._stdout_original or sys.stdout)
            sys.stdout = self._stdout_proxy
            self._running = True
            self._ever_started = True
            self._app_thread = threading.Thread(
                target=self._run_app, daemon=True, name="full-tui-app-gen2",
            )
            self._app_thread.start()

        logger.info("cutover_generation: 蓝绿切换完成（旧代退役 → 新代接管）")
        return True

    def _verify_candidate(self, candidate: Any) -> None:
        """P2-13: 候选代最小健全性验证（verify 缺省实现）。

        健全性门（不真 run 抢终端）：非 None + 是 Application 实例 + 有
        layout + 有 key_bindings。宿主可传自定义 verify 覆盖（更强断言）。
        """
        if candidate is None:
            raise ValueError("候选 Application 为 None")
        from prompt_toolkit.application import Application as _PTApp
        if not isinstance(candidate, _PTApp):
            raise TypeError(f"候选不是 prompt_toolkit Application: {type(candidate)}")
        if not getattr(candidate, "layout", None):
            raise ValueError("候选 Application 缺 layout（无法接管渲染）")

    def _startup_health_probe(self) -> None:
        """P2-3: start 后 1s 自检 —— 全屏未驻留时显式告知已降级。"""
        try:
            alive = (
                self._running
                and self._app is not None
                and self._app_thread is not None
                and self._app_thread.is_alive()
            )
            if alive:
                return
            err = self._app_error or "Application 未进入运行态"
            print(
                f"[全屏TUI] 启动自检未通过，已降级为简易输入模式（原因: {err}）",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001 — 自检失败不影响主流程
            pass

    def close(self) -> None:
        """退出全屏并恢复 stdout（主循环退出路径调用；幂等）。"""
        if not self._running:
            return
        self._running = False
        # 唤醒可能阻塞在 prompt() 的等待者
        with self._submit_cond:
            self._submit_cond.notify_all()
        try:
            if self._app is not None:
                self._app.exit()
        except Exception:  # noqa: BLE001 — app 已退出等场景静默
            pass
        if self._app_thread is not None:
            self._app_thread.join(timeout=3.0)
        if self._stdout_proxy is not None:
            sys.stdout = self._stdout_original
            self._stdout_proxy = None
        self._app = None
        # P0-4（2026-09-20，TUI 优化方案）: 退出全屏后再发一次终端增强模式
        # 复位（对称卫生：清别人残留，也别留自己的）。⚠ 顺序必须 app.exit()
        # 并还原 stdout 之后 —— 先复位会被 PT 退场序列/重绘重新进入增强模式，
        # 等于白发。reset_terminal_key_modes 内部自带 isatty 防御。
        try:
            from lingclaude.engine.lineedit import reset_terminal_key_modes

            reset_terminal_key_modes()
        except Exception:  # noqa: BLE001 — 增强路径，绝不反噬退出流程
            pass

    def _check_tty_raw_drift(self) -> None:
        """Raw 失同步检测与自愈（2026-09-26 假死根治，节流 0.5s）。

        病灶：全屏 Application 必须持有 raw 模式才能逐键收输入；外部路径
        （repl 失活门的 _reset_tty_now / 降级直读）用启动时的 canonical
        快照 tcsetattr，把活 app 脚下的终端踩回 cooked 态 → 按键被内核
        行规程按行缓冲，PT 逐键读者永远收不到 → 无法输入/翻页/提交，
        而进程、事件循环、pump 三者全部健康（19:20-19:45 整屏假死）。

        自愈：检测 ICANON 置位即 tty.setraw(TCSADRAIN) 恢复 + resync 重绘。
        只在「已经坏」时才写终端（健康路径零触碰）；与 PT 内部 attrs 的
        竞争窗口仅 tcsetattr 几微秒，且 PT 的 raw_mode 上下文退出时按其
        进场快照还原，本恢复不破坏该语义。本方法在 pump 线程执行（等待
        提交队列期间），resync 跨线程调用本身幂等（见 resync docstring）。
        非 TTY / termios 缺失 / fd 不可用一律静默跳过——看门狗永不反噬输入。
        """
        import time as _time

        now = _time.monotonic()
        if now - self._raw_guard_last < 0.5:
            return
        self._raw_guard_last = now
        try:
            import termios as _termios
        except ImportError:
            return
        try:
            fd = sys.stdin.fileno()
            attrs = _termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001 — 非 TTY/管道/fd 关闭：跳过
            return
        if not (attrs[3] & _termios.ICANON):
            return  # raw 态健康，零触碰
        try:
            import tty as _tty

            _tty.setraw(fd, when=_termios.TCSADRAIN)
        except Exception:  # noqa: BLE001 — 恢复失败下轮重试
            return
        try:
            logger.warning(
                "raw_guard: 检测到终端被踩回 canonical（app 活着但按键失联），"
                "已恢复 raw 模式并 resync —— 2026-09-26 假死根治自愈"
            )
            self.resync()
        except Exception:  # noqa: BLE001 — 重绘失败不影响终端恢复本身
            pass

    # ── 拖选复制（2026-09-27 OSC52）───────────────────────────────────────

    def _extract_selected_text(self) -> str:
        """按当前选择框取输出 buffer 文本（行式，鼠标坐标语义）。

        坐标来自 Window 派发（containers.py:1847-1862）：position 已换算为
        buffer 行/列（视口外/行尾点击已被 clamp）。行式复制是鼠标选择惯例：
        终行含尾列之后的整行内容（与终端拖选行为一致）。
        """
        try:
            with self._area_lock:
                lines = self._out_buffer.text.split("\n")
                start = self._sel_start
                end = self._sel_end
            if start is None or end is None:
                return ""
            if start > end:
                start, end = end, start
            sr, sc = start
            er, ec = end
            if sr == er:
                return lines[sr][sc : ec + 1]
            parts = [lines[sr][sc:]]
            parts.extend(lines[r] for r in range(sr + 1, er))
            parts.append(lines[er][: ec + 1])
            return "\n".join(parts)
        except Exception:  # noqa: BLE001 — 取词失败不影响 UI
            return ""

    def _osc52_copy(self, text: str) -> bool:
        """OSC52 序列写终端剪贴板：ESC]52;c;<base64>ST（ST=ESC\\）。

        truecolor/256 色等颜色能力无关；终端禁用（VTE 默认 allowClipboard
        off 等）时序列被忽略，无副作用。上限 8MiB（xterm 序列缓冲经验值）。
        通道：App 驻留用 app.output raw fd；否则 /dev/tty（SSH 场景
        sys.stdout 可能被代理/管道替换，/dev/tty 才是控制终端本体）。
        """
        try:
            payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
            if len(payload) > 8 * 1024 * 1024:
                return False
            seq = "\x1b]52;c;" + payload + "\x1b\\"
            data = seq.encode("ascii")
            fd = self._osc52_fd
            if fd is not None:
                os.write(fd, data)
            else:
                with open("/dev/tty", "wb") as tty:
                    tty.write(data)
            if _OSC52_DEBUG:
                logger.debug(
                    "osc52_copy: %s chars via fd=%s", len(text), fd if fd is not None else "/dev/tty"
                )
            return True
        except Exception:  # noqa: BLE001 — 复制失败不反噬 UI（静默，debug 日志留痕）
            return False

    def _on_select_event(self, kind: str, position: Any) -> None:
        """输出窗鼠标三态回调（_OutputScrollControl on_select）。

        DOWN 记起点；LEFT+MOVE 更新终点并标记拖动；UP 落锤——真拖动才
        取词复制，原地点击不干扰。全程吞异常 + 最终 _invalidate。
        """
        try:
            if _OSC52_DEBUG:
                logger.debug(
                    "osc52_select: kind=%s pos=(%s, %s)", kind, position.y, position.x
                )
            pos = (position.y, position.x)
            if kind == "down":
                self._sel_start = pos
                self._sel_end = pos
                self._sel_active = True
                self._sel_dragged = False
            elif kind == "move":
                if self._sel_start is None:
                    return
                self._sel_end = pos
                if pos != self._sel_start:
                    self._sel_dragged = True
            elif kind == "up":
                if self._sel_start is not None:
                    # UP 坐标即最终终点（终端拖选惯例）：先落终点再取词
                    self._sel_end = pos
                if self._sel_start is not None and self._sel_dragged:
                    text = self._extract_selected_text()
                    if text:
                        self._osc52_copy(text)
                self._sel_start = None
                self._sel_end = None
                self._sel_active = False
                self._sel_dragged = False
        except Exception:  # noqa: BLE001 — 选择事件异常绝不反噬事件循环
            self._sel_start = None
            self._sel_end = None
            self._sel_active = False
            self._sel_dragged = False
        finally:
            self._invalidate()

    def _run_app(self) -> None:
        tty_fd = None
        try:
            # OSC52 通道（2026-09-27）：App 驻留期专用 /dev/tty raw fd——
            # 拖选复制在 PT 管理终端的窗口内发生，独立 fd 避免与 stdout
            # 代理/PTY 状态纠缠；退出 finally 统一关闭（不吞 PT 终端状态）。
            try:
                tty_fd = os.open("/dev/tty", os.O_WRONLY)
                self._osc52_fd = tty_fd
            except Exception:  # noqa: BLE001 — 无控制终端：/dev/tty 回退路径兜底
                tty_fd = None
                self._osc52_fd = None
            # PT3: output 已在构造期注入（start()），run() 不得再传 —— 会
            # TypeError。事件循环在本后台线程创建运行，终端控制权全程归 PT。
            self._app.run()
        except Exception as e:  # noqa: BLE001 — 全屏线程死亡必须留痕
            self._app_error = f"{type(e).__name__}: {e}"
            self._running = False
        finally:
            # OSC52 fd 收尾
            if tty_fd is not None:
                try:
                    os.close(tty_fd)
                except Exception:  # noqa: BLE001
                    pass
            self._osc52_fd = None
            # 线程死亡/退出时还原 stdout —— 否则后续输出全进不可见的
            # 输出窗缓冲，用户看不到任何内容（静默死亡事故防复发）。
            if self._stdout_proxy is not None:
                try:
                    sys.stdout = self._stdout_original
                except Exception:  # noqa: BLE001
                    pass
                self._stdout_proxy = None
            # C-fix（2026-09-27 鼠标残码根治）: app 线程任何形式的终态
            # （异常死亡/外部 exit）都在线程侧幂等补发终端增强模式复位
            # （kitty 键盘/焦点/鼠标上报）。正常路径 close() 已复位，此处
            # 双保险；isatty 防御与静默失败内置于 reset_terminal_key_modes。
            try:
                from lingclaude.engine.lineedit import reset_terminal_key_modes

                reset_terminal_key_modes()
            except Exception:  # noqa: BLE001 — 增强路径，绝不反噬线程收尾
                pass

    def _load_paste_store(self) -> None:
        """C(2026-09-26): 启动时回灌粘贴注册表（富历史重水化）。

        文件格式：{"max_seq": N, "pastes": {"1": [text, lines], ...}}。
        文件缺失/损坏静默跳过——增强路径，绝不反噬输入。条目上限
        _PASTE_STORE_MAX 条（超出丢最旧），单条全文超 1MiB 丢弃（防病态）。
        """
        try:
            import json as _json

            raw = self._paste_store_path.read_text(encoding="utf-8")
            data = _json.loads(raw)
            pastes = data.get("pastes") or {}
            for k, v in list(pastes.items())[-policy_loader._tuned(
                "paste_store_max", _PASTE_STORE_MAX, lo=20, hi=5000
            ):]:
                n = int(k)
                text, lines = v[0], int(v[1])
                if len(text) > 1_048_576 or lines < 1:
                    continue
                self._paste_registry[n] = (text, lines)
        except (OSError, ValueError, TypeError, IndexError):
            pass  # 缺失/损坏 → 空注册表（占位符不还原，行为同修复前）

    def _save_paste_store(self) -> None:
        """C(2026-09-26): 粘贴注册表落盘（_register_paste 达到折叠阈值时调用）。

        只保留最新 _PASTE_STORE_MAX 条；写失败静默（增强路径不反噬）。
        """
        try:
            import json as _json

            paste_max = policy_loader._tuned(
                "paste_store_max", _PASTE_STORE_MAX, lo=20, hi=5000
            )
            items = sorted(self._paste_registry.items())[-paste_max:]
            data = {
                "max_seq": self._paste_seq,
                "pastes": {str(n): [text, lines] for n, (text, lines) in items},
            }
            self._paste_store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._paste_store_path.with_suffix(".json.tmp")
            tmp.write_text(
                _json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(self._paste_store_path)
        except (OSError, ValueError, TypeError):
            pass  # 写盘失败 → 内存注册表仍有效（本会话内还原不受影响）

    def _register_paste(self, data: str) -> tuple[str, int]:
        """登记一次粘贴：返回（插入物, 行数）。

        换行数 < _PASTE_FOLD_MIN_LINES → 原样返回（短粘贴不打扰编辑）；
        达到阈值 → 折叠为占位符并登记全文（提交时还原）。占位符编号
        全会话单调递增，注册表只增不减（残留条目无害，见 __init__ 注释）。
        """
        # 与 PT 默认绑定对齐的换行归一（iTerm2 粘贴 \r\n，见 basic.py:244）
        data = data.replace("\r\n", "\n").replace("\r", "\n")
        lines = data.count("\n") + 1
        if lines < _PASTE_FOLD_MIN_LINES:
            return data, lines
        self._paste_seq += 1
        n = self._paste_seq
        self._paste_registry[n] = (data, lines)
        # C(2026-09-26): 立即落盘（进程随时可能被杀，不能等会话结束）
        self._save_paste_store()
        placeholder = _PLACEHOLDER_FMT.format(n=n, lines=lines, chars=len(data))
        return placeholder, lines

    def _handle_paste(self, event: Any) -> None:
        """BracketedPaste 处理器：折叠长粘贴 → 插入占位符/短文本。

        仅在聚焦 buffer 成功时插入；任何异常静默降级为默认行为
        （直接插入原文）——粘贴是高频路径，不能因折叠逻辑故障而丢输入。
        """
        data = event.data or ""
        buf = event.app.layout.current_buffer if event.app is not None else None
        if buf is None:
            return
        try:
            # TUI 图片直接粘贴（2026-10-01）：优先识别图片粘贴（终端
            # 8-bit 透传二进制 / 单行图片路径 / file:// URI），命中则
            # 附件走 _pending_images 侧信道、输入框插图片占位符；
            # 未命中原样走长文本折叠，行为零变化。
            insert = self._try_paste_image(data)
            if insert is None:
                insert, _lines = self._register_paste(data)
        except Exception:  # noqa: BLE001 — 折叠失败降级为原样插入
            insert = data.replace("\r\n", "\n").replace("\r", "\n")
        buf.insert_text(insert)

    # ── TUI 图片直接粘贴（2026-10-01） ────────────────────────────────────

    @staticmethod
    def _sniff_image_magic(raw: bytes) -> str | None:
        """magic 头嗅探图片格式。返回 mime；非已知图片返回 None。"""
        for head, mime in _IMAGE_MAGICS:
            if raw.startswith(head):
                return mime
        return None

    def _try_paste_image(self, data: str) -> str | None:
        """识别图片粘贴；命中登记附件并返回占位符，未命中返回 None。

        三形态（按序判定，全失败即回退文本粘贴链）：
        A. 8-bit 透传二进制：终端把剪贴板图片原字节经 stdin 送达
           （kitty/wezterm 开 allowHyperlinks+传递、xterm tier2）——
           magic 嗅探验真，伪装字节误判不可能通过。
        B. 单行图片路径/URL：截图工具「复制文件路径」、文件管理器
           拖拽产生的引用串——本地绝对路径 / file:// URI，存在且
           magic/PIL 验真才收（文本误伤：路径必须真实存在）。
        C. base64 图片串：外部工具直接产出的 data URI——宽松前缀
           匹配，解码后 magic 复验，未过即按文本处理。

        返回 None（非图片，走原链）或占位符字符串（已登记附件）。
        失败附件登记不会发生（先验后登），异常向上抛由
        _handle_paste 统一降级，绝不吞输入。
        """
        # ── 形态 A：二进制透传 ──
        # event.data 已按终端 charset 解码；二进制字节需 round-trip 还原
        # 再嗅探。双通道：utf-8+surrogateescape（PT 解码-errors=surrogateescape
        # 路径，\udcXX 代理原样还原）、latin-1（全字节 1:1 映射路径）。
        # 单用 utf-8 会把 \x89 编成 0xC2 0x89（PNG magic 必失配）。
        if data:
            raw_candidates: list[bytes] = []
            try:
                raw_candidates.append(data.encode("utf-8", errors="surrogateescape"))
            except Exception:  # noqa: BLE001 — 编码异常跳过该通道
                pass
            try:
                raw_candidates.append(data.encode("latin-1"))
            except Exception:  # noqa: BLE001 — 含 >U+00FF 字符则跳过
                pass
            for raw in raw_candidates:
                mime = self._sniff_image_magic(raw)
                if mime is not None:
                    # utf-8+surrogateescape 通道在前（PT 解码主路径，
                    # round-trip 双射=字节精确）；latin-1 兜底（errors=
                    # latin-1/replace 之外的解码路径）。先命中先用。
                    return self._register_image_attachment(raw, mime)

        # ── 形态 B：单行路径 / file:// URI ──
        stripped = data.strip().strip("'\"")
        if stripped and "\n" not in stripped and len(stripped) <= 4096:
            path_candidate = stripped
            if _FILE_URI_RE.match(path_candidate):
                try:
                    from urllib.parse import unquote as _unquote, urlparse as _urlparse
                    path_candidate = _unquote(_urlparse(path_candidate).path)
                except Exception:  # noqa: BLE001 — URI 解析失败按普通文本
                    path_candidate = ""
            p = Path(path_candidate).expanduser()
            try:
                is_file = p.is_file()
            except OSError:
                # 文件名过长（>255）/ 权限拒绝等 —— 按非文件处理，
                # 回退文本链（2026-10-01 测试揪出：长单行文本粘贴
                # 走到这里抛 OSError 36，会炸掉整个折叠链）。
                is_file = False
            if is_file:
                mime = self._sniff_image_file(p)
                if mime is not None:
                    try:
                        return self._register_image_attachment(
                            p.read_bytes(), mime
                        )
                    except OSError:
                        return None  # 读文件失败 → 按普通文本粘贴

        # ── 形态 C：base64 / data URI ──
        if stripped.startswith("data:image/") and ";base64," in stripped:
            b64_part = stripped.split(";base64,", 1)[1].split()[0]
        elif data and "\n" not in data and len(data) > 512 and stripped == data:
            # 无空白长单行——疑似裸 base64；解不开不伤（原链回退）
            b64_part = data
        else:
            b64_part = ""
        if b64_part:
            try:
                raw = base64.b64decode(b64_part, validate=True)
            except Exception:  # noqa: BLE001 — 解码失败按普通文本
                return None
            mime = self._sniff_image_magic(raw)
            if mime is not None:
                return self._register_image_attachment(raw, mime)

        return None

    @staticmethod
    def _sniff_image_file(p: Path) -> str | None:
        """文件验真：PIL verify（强断言）优先，缺席时 magic 头兜底。"""
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(p) as im:  # noqa: SIM115 — 校验用，立即关
                fmt = im.format
                im.verify()
            if fmt:
                return f"image/{fmt.lower()}"
        except ImportError:
            pass  # 无 PIL → magic 兜底
        except Exception:  # noqa: BLE001 — 损坏/伪装文件
            return None
        try:
            with open(p, "rb") as f:
                return FullTuiSession._sniff_image_magic(f.read(16))
        except OSError:
            return None

    def _register_image_attachment(self, raw: bytes, mime: str) -> str:
        """登记图片附件 + 输出窗回执。返回输入框占位符。"""
        self._image_paste_count += 1
        n = self.register_image_attachment(raw, mime)
        fmt = mime.split("/")[-1].upper()
        try:
            self.append_output(
                f"[图片 #{n} 已附着 · {fmt} · {len(raw) // 1024}KB]"
                " 输入文字消息后随消息发送。\n"
            )
        except Exception:  # noqa: BLE001 — 回执失败不伤附件
            pass
        return _IMAGE_PLACEHOLDER_FMT.format(
            n=n, fmt=fmt, kb=max(1, len(raw) // 1024)
        )

    def _expand_placeholders(self, text: str) -> str:
        """提交前还原：buffer 文本中的占位符 → 登记的粘贴全文。

        只还原注册表里存在的编号（用户手打的同形字面串不会误伤）；
        已注册但文本中不存在的占位符忽略（复制粘贴占位符本身的边界）。
        """
        if not self._paste_registry:
            return text

        def _sub(m: Any) -> str:
            entry = self._paste_registry.get(int(m.group(1)))
            return entry[0] if entry is not None else m.group(0)

        return _PLACEHOLDER_RE.sub(_sub, text)

    def _fold_echo(self, full_text: str) -> str:
        """回显折叠：还原后的全文把已登记粘贴重新折回占位符（防刷屏）。

        按粘贴长度降序替换——长粘贴可能是短粘贴的超集（两次粘贴部分
        重叠），先替换短的会把长粘贴内部截断、导致其全文匹配失败串位。
        """
        if not self._paste_registry:
            return full_text
        for n, (data, lines) in sorted(
            self._paste_registry.items(), key=lambda kv: len(kv[1][0]), reverse=True
        ):
            if data in full_text:
                full_text = full_text.replace(
                    data, _PLACEHOLDER_FMT.format(n=n, lines=lines, chars=len(data))
                )
        return full_text

    def _submit(self, text: str) -> None:
        with self._submit_cond:
            self._submit_q.append(text)
            self._submit_cond.notify_all()

    def pending_submissions(self) -> int:
        """未消费的提交数（状态栏「挂起×N」用，EOF 哨兵不计）。"""
        with self._submit_cond:
            return sum(1 for x in self._submit_q if x != EOF_SENTINEL)

    # ── TUI 图片粘贴（2026-10-01） ──────────────────────────────────────────

    def register_image_attachment(self, raw_data: bytes, mime_type: str) -> int:
        """追加待附图片到 pending 队列。返回当前队列长度。"""
        self._pending_images.append((raw_data, mime_type))
        return len(self._pending_images)

    def pending_image_count(self) -> int:
        """当前待附图片数量。"""
        return len(self._pending_images)

    def append_output(self, s: str) -> None:
        """公开追加接口：任意线程输出进窗（stdout 代理与渲染层共用）。"""
        self._write_via_buffer(s)

    def mark_stream_start(self) -> int:
        """记下当前输出窗行数作为「本轮流式段」起点（行裁剪安全：见 replace_range_styled）。

        2026-09-30 TUI 原位上色：流式素字全文已入窗，done 时用带样式版本
        原位替换该区间，消除「素字 + 彩色两遍」。
        """
        with self._area_lock:
            text = self._out_buffer.text
            return (text.count("\n") + 1) if text else 0

    def drop_stream_mark(self) -> None:
        """无条件作废轮次标记（打断/异常收尾用；正常轮由 replace_turn_styled 消费）。"""
        with self._style_lock:
            self._stream_start_mark = -1

    def replace_turn_styled(
        self,
        styled_lines: list[str],
        spans_list: list[list[tuple[int, int, str]]],
        expected_plain: list[str],
    ) -> bool:
        """消费轮次标记，把本轮流式素字段原位替换为带样式行（行数可变）。

        expected_plain = done.content 按行拆分的素字版本。替换前与窗内
        [mark, cur) 区间做 rstrip 归一比对：不一致（工具行交错 / 表格重排 /
        verifier 改写 / 裁剪吃字）→ 返回 False，调用方回退旧行为。误删在
        构造上不可能：区间内容与 content 不完全一致就绝不动窗。

        返回 False = 无标记 / 指纹不符，调用方回退素字收尾，内容不丢。
        """
        with self._style_lock:
            mark = self._stream_start_mark
            self._stream_start_mark = -1
        if mark < 0:
            return False
        with self._area_lock:
            text = self._out_buffer.text
            all_lines = text.split("\n") if text else []
            cur_count = len(all_lines)
            if mark > cur_count:  # 起点行已被滚动裁剪吃掉
                return False
            segment = all_lines[mark:]
            _exp_rstrip = [ln.rstrip() for ln in expected_plain]
            _seg_rstrip = [ln.rstrip() for ln in segment]
            if _seg_rstrip != _exp_rstrip:
                return False  # 指纹不符：工具行交错等，回退
            return self._replace_range_locked(
                mark, cur_count - mark, styled_lines, spans_list
            )

    def _replace_range_locked(
        self,
        start: int,
        old_len: int,
        styled_lines: list[str],
        spans_list: list[list[tuple[int, int, str]]],
    ) -> bool:
        """原位替换 [start, start+old_len) 为 styled_lines（须持 _area_lock）。"""
        buf = self._out_buffer
        old_text = buf.text
        all_lines = old_text.split("\n") if old_text else []
        if start < 0 or start + old_len > len(all_lines):
            return False
        end = start + old_len
        tail = all_lines[end:]
        new_lines = all_lines[:start] + list(styled_lines) + tail
        dropped = 0
        if len(new_lines) > MAX_OUTPUT_LINES:
            # 增行替换超限丢最旧行（与追加同语义）；等行替换不触发
            dropped = len(new_lines) - MAX_OUTPUT_LINES
            new_lines = new_lines[-MAX_OUTPUT_LINES:]
            start = max(0, start - dropped)
            end -= dropped
        new_text = "\n".join(new_lines)
        buf.set_document(Document(new_text, 0), bypass_readonly=True)
        # ── 样式表按新行号重建：新区间 + 区间前原样 + 区间后平移 ──
        with self._style_lock:
            new_map: dict[int, list[tuple[int, int, str]]] = {}
            for i, sp in enumerate(spans_list):
                if sp:
                    new_map[start + i] = list(sp)
            shift = len(styled_lines) - old_len
            for row, sp in self._style_map.items():
                if row < start:
                    new_map[row] = sp
                elif row >= end:
                    shifted = row + shift
                    if shifted >= 0:
                        new_map[shifted] = sp
            self._style_map = new_map
        follow = self._follow_output
        if follow or not new_text:
            buf.cursor_position = len(new_text)
        else:
            row = buf.document.cursor_position_row
            row = max(0, min(row, len(new_lines) - 1))
            buf.cursor_position = buf.document.translate_row_col_to_index(row, 0)
        follow_now = buf.document.is_cursor_at_the_end
        self._follow_output = follow_now
        self._invalidate()
        return True

    def replace_turn_styled_segmented(
        self,
        content: str,
        expected_plain: list[str],
        tool_trace: list[str],
    ) -> bool:
        """分段原位上色（2026-10-01）：工具轨迹行原位保留，正文段替换为样式行。

        2026-10-01 10:02 FP_MISMATCH 事故：带工具调用轮次的窗内区段 =
        正文行与工具轨迹行（空行 + "  [bash] …" + "✅ preview"）交错，
        与 done.content 推导的整段指纹必失配 → 几乎所有真实轮都回退素字。
        本方法按 tool_trace（repl_io 流式出口实写顺序快照）走查对齐：
          - 窗内命中轨迹行 → 归工具段，原样保留（先来先服务，防同名误吞）；
          - 其余窗内行须与期望指纹逐行一致（含空行容差）→ 归正文段。

        按段渲染（2026-10-01 二次设计）：正文段不取「整篇渲染」的行——
        rich 对长段折行使整篇行号与窗内行号错位（串色隐患），改为每个
        正文段单独过 repl_io.render_markdown_lines，整段替换窗内对应行。

        空行容差（两类不对称实证）：
          - 窗内多出的空行：工具行前后导 \\n 的落窗产物，跳过；
          - 期望多出的空行：P1 尾随空行缺陷 + content 尾 \\n 剥除不对称，跳过。
        任一非空行失配 / 走查后存在未归类行 → 返回 False（一行不动），
        调用方回退素字。构造上不可能误删：所有被替换字符都能在窗内原位找到。
        """
        if not tool_trace:
            # 纯文本轮（无工具调用）：由调用方走整段旧语义，不达此处
            return False
        from lingclaude.cli.repl_io import render_markdown_lines

        with self._style_lock:
            mark = self._stream_start_mark
            self._stream_start_mark = -1
        if mark < 0:
            return False
        with self._area_lock:
            all_lines = self._out_buffer.text.split("\n") if self._out_buffer.text else []
            if mark > len(all_lines):
                return False
            seg = all_lines[mark:]
            E = [ln.rstrip() for ln in expected_plain]
            S = [ln.rstrip() for ln in seg]
            T = [ln.rstrip() for ln in tool_trace]
            used: set[int] = set()

            def _find_trace(j: int) -> int | None:
                if j >= len(S):
                    return None
                for k, tl in enumerate(T):
                    if k not in used and S[j] == tl:
                        return k
                return None

            def _next_is_trace(j: int) -> bool:
                return j < len(S) and _find_trace(j) is not None

            plans: list[tuple[int, int, int, int]] = []  # (exp_s, exp_e, win_s, win_e)
            i = j = 0
            while i < len(E) or j < len(S):
                if _next_is_trace(j):
                    ws = j
                    while _next_is_trace(j):
                        used.add(_find_trace(j))  # type: ignore[arg-type]
                        j += 1
                    plans.append((-1, -1, ws, j))
                    continue
                if i >= len(E) or j >= len(S):
                    break
                # 窗内空行且下一窗行是轨迹行（轨迹边界产物）→ 吸收
                if S[j] == "" and _next_is_trace(j + 1):
                    j += 1
                    continue
                if S[j] == E[i]:
                    es, ws = i, j
                    while (
                        i < len(E)
                        and j < len(S)
                        and S[j] == E[i]
                        and not _next_is_trace(j + 1)
                    ):
                        i += 1
                        j += 1
                    if j == ws:
                        # 零推进保护：下一行是轨迹行但当前行也须归入本段，
                        # 否则外层 while 空转死循环（11:14 MemoryError 事故）
                        i += 1
                        j += 1
                    plans.append((es, i, ws, j))
                    continue
                if S[j] == "" and E[i] != "":
                    j += 1  # 窗内多余空行（工具行边界落窗产物）
                    continue
                if E[i] == "" and S[j] != "":
                    i += 1  # 期望多余空行（P1 尾随空行缺陷/尾换行不对称）
                    continue
                return False
            # 走查收尾：剩余期望必须全空行；剩余窗行必须是空行或未消耗轨迹行
            if any(ln != "" for ln in E[i:]):
                return False
            while j < len(S):
                if S[j] == "":
                    j += 1
                    continue
                k = _find_trace(j)
                if k is not None:
                    used.add(k)
                    j += 1
                    continue
                return False

            text_plans = [p for p in plans if p[0] >= 0]
            if not text_plans:
                # 全是工具轨迹（正文为空）：无可替换，视为成功保持原样
                return True
            # 渲染增行可能超窗上限：超限直接放弃（保素字，不裁历史）
            new_total = len(all_lines) + sum(
                (p[1] - p[0]) - (p[3] - p[2]) for p in text_plans
            )
            if new_total > MAX_OUTPUT_LINES:
                return False

            # —— 组装：按正文段单独渲染（rich 折行安全），轨迹行原样保留 ——
            buf = self._out_buffer
            new_seg_lines: list[str] = []
            new_seg_styles: list[list[tuple[int, int, str]] | None] = []
            j = 0
            for es, ee, ws, we in text_plans:
                while j < ws:
                    new_seg_lines.append(seg[j])
                    new_seg_styles.append(None)
                    j += 1
                # 本段源文本从 content 按期望行号切片（E 与 _expected_window_lines
                # 同构，空行边界即源 \n 边界）；单独渲染后整段替换
                try:
                    src_text = "\n".join(
                        expected_plain[es:ee]
                    )
                    seg_lines, seg_spans = render_markdown_lines(src_text)
                    while seg_lines and seg_lines[-1] == "":
                        seg_lines.pop()
                        seg_spans.pop()
                    while seg_lines and seg_lines[0] == "":
                        seg_lines.pop(0)
                        seg_spans.pop(0)
                except Exception as _r_err:  # noqa: BLE001 — 段渲染失败保素字
                    return False
                for ln, sp in zip(seg_lines, seg_spans):
                    new_seg_lines.append(ln)
                    new_seg_styles.append(sp if sp else None)
                j = we
            while j < len(seg):
                new_seg_lines.append(seg[j])
                new_seg_styles.append(None)
                j += 1
            new_lines = all_lines[:mark] + new_seg_lines
            new_text = "\n".join(new_lines)
            buf.set_document(Document(new_text, 0), bypass_readonly=True)
            # 样式表重建：< mark 原样保留；段内按新行号落新样式（轨迹行无样式）
            with self._style_lock:
                new_map: dict[int, list[tuple[int, int, str]]] = {
                    row: sp for row, sp in self._style_map.items() if row < mark
                }
                for idx, sp in enumerate(new_seg_styles):
                    if sp:
                        new_map[mark + idx] = sp
                self._style_map = new_map
            follow = self._follow_output
            if follow or not new_text:
                buf.cursor_position = len(new_text)
            else:
                row = buf.document.cursor_position_row
                row = max(0, min(row, len(new_lines) - 1))
                buf.cursor_position = buf.document.translate_row_col_to_index(row, 0)
            follow_now = buf.document.is_cursor_at_the_end
            self._follow_output = follow_now
            self._invalidate()
            return True

    def replace_turn_styled_from_ledger(
        self,
        ledger: list[tuple[str, str]],
        content: str,
    ) -> bool:
        """台账窗尾对齐原位上色（2026-10-01 第三代定位）。

        台账 = 流式期实写行 (kind,line) 快照（repl_io 侧逐出口打点）。
        对齐方式：**从窗尾向上找台账首行锚点**，向下逐行校验到窗尾，全对
        上才替换——不依赖绝对行号，粘贴回执/resync 重绘/mark 漂移免疫。
        对不上 → False，调用方静默回退（内容已在窗内，不丢）。

        成功时：工具行原样保留，正文段按台账行号从 content 切片单独过
        rich 渲染整段替换（折行互不影响）。
        """
        from lingclaude.cli.repl_io import render_markdown_lines

        with self._style_lock:
            self._stream_start_mark = -1
        with self._area_lock:
            all_lines = (
                self._out_buffer.text.split("\n") if self._out_buffer.text else []
            )
            if not ledger:
                return False
            if len(ledger) > len(all_lines):
                return False
            W = [ln.rstrip() for ln in all_lines]
            L = ledger
            n = len(L)
            anchors = [
                x for x in range(len(W) - n, -1, -1) if W[x] == L[0][1]
            ]
            if not anchors:
                return False
            for start in anchors:
                if not all(W[start + t] == L[t][1] for t in range(1, n)):
                    continue
                # —— 校验通过：台账 → 正文段计划 ——
                # (content_s, content_e含, t_s, t_e含)：t 是台账槽位（工具条目
                # 占槽 → t ≠ content 行号），ci 单独计数正文行。
                plans: list[list[int]] = []
                cur: list[int] | None = None
                ci = 0
                for t, (kind, _ln) in enumerate(L):
                    if kind == "tool":
                        cur = None
                        continue
                    if cur is None:
                        cur = [ci, ci, t, t]
                        plans.append(cur)
                    else:
                        cur[1] = ci
                        cur[3] = t
                    ci += 1
                return self._apply_ledger_plans_locked(
                    start, plans, L, W, all_lines, content, render_markdown_lines
                )
            return False

    def _apply_ledger_plans_locked(
        self,
        start: int,
        plans: list[list[int]],
        L: list[tuple[str, str]],
        W: list[str],
        all_lines: list[str],
        content: str,
        render_markdown_lines: Any,
    ) -> bool:
        """台账计划执行（须持 _area_lock）：按段渲染替换正文段，工具行原样。"""
        new_seg_lines: list[str] = []
        new_seg_styles: list[list[tuple[int, int, str]] | None] = []
        t_cursor = 0
        for cs, ce, ts, te in plans:
            # 段前的工具行（含边界空行）原样拷贝
            while t_cursor < ts:
                new_seg_lines.append(W[start + t_cursor])
                new_seg_styles.append(None)
                t_cursor += 1
            # 正文段：content 行 [cs, ce]（含端点，ci 计数不含工具槽位），
            # 整段单独渲染（rich 折行互不影响）。
            try:
                src_lines = content.split("\n")
                if src_lines and src_lines[-1] == "":
                    src_lines.pop()
                src_text = "\n".join(src_lines[cs : ce + 1])
                seg_lines, seg_spans = render_markdown_lines(src_text)
                while seg_lines and seg_lines[-1] == "":
                    seg_lines.pop()
                    seg_spans.pop()
                while seg_lines and seg_lines[0] == "":
                    seg_lines.pop(0)
                    seg_spans.pop(0)
            except Exception as _r_err:  # noqa: BLE001 — 段渲染失败整轮保素字
                return False
            for ln, sp in zip(seg_lines, seg_spans):
                new_seg_lines.append(ln)
                new_seg_styles.append(sp if sp else None)
            t_cursor = te + 1
        # 段后残留（尾部工具行/空行）原样保留
        while t_cursor < len(L):
            new_seg_lines.append(W[start + t_cursor])
            new_seg_styles.append(None)
            t_cursor += 1
        if not plans and L and L[-1][0] == "tool":
            # 纯工具收尾（无正文段）：补一个空行分隔（对齐素字路径 \n\n 视觉）
            new_seg_lines.append("")
            new_seg_styles.append(None)
        new_lines = all_lines[:start] + new_seg_lines
        new_text = "\n".join(new_lines)
        self._out_buffer.set_document(Document(new_text, 0), bypass_readonly=True)
        with self._style_lock:
            new_map: dict[int, list[tuple[int, int, str]]] = {
                row: sp for row, sp in self._style_map.items() if row < start
            }
            for idx, sp in enumerate(new_seg_styles):
                if sp:
                    new_map[start + idx] = sp
            self._style_map = new_map
        follow = self._follow_output
        if follow or not new_text:
            self._out_buffer.cursor_position = len(new_text)
        else:
            row = self._out_buffer.document.cursor_position_row
            row = max(0, min(row, len(new_lines) - 1))
            self._out_buffer.cursor_position = (
                self._out_buffer.document.translate_row_col_to_index(row, 0)
            )
        follow_now = self._out_buffer.document.is_cursor_at_the_end
        self._follow_output = follow_now
        self._invalidate()
        return True

    def _write_via_buffer(self, s: str) -> None:
        if not s:
            return
        # 2026-09-27 受限富文本重写：SGR 不再盲剥——逐行解析成「净化文本
        # + 列区间样式表」。净化文本走旧 pending 队列入 Buffer（零 ESC，
        # TextArea 渲染 '?[1;4m' 明文的病根消除）；样式随行暂存
        # _style_spans_pending（与 pending 行一一对应），drain 时按起始
        # 行号落 _style_map 供渲染 processor 消费。非 SGR CSI/SS3/孤立
        # ESC 仍整体吞（旧行为不变）；半行滞留 flush() 兜底不变。
        stashed: list[tuple[str, list[tuple[int, int, str]]]] = []
        cur_text: list[str] = []
        with self._out_lock:
            for ch in s:
                if ch == "\n":
                    text, spans = _extract_sgr_styles("".join(cur_text))
                    stashed.append((text, spans))
                    cur_text.clear()
                elif ch == "\r":
                    cur_text.clear()
                else:
                    cur_text.append(ch)
            if cur_text:
                text, spans = _extract_sgr_styles("".join(cur_text))
                stashed.append((text, spans))
            # 与旧实现对齐：空行（ch=='\n' 连续）也占行——stashed 不含
            # 空串行会压缩行数，破坏 _flush_line 的行边界契约。
            self._pending_lines.extend(t for t, _ in stashed)
            self._style_spans_pending.extend(sp for _, sp in stashed)
        self._drain_output_to_area()

    def _drain_output_to_area(self) -> None:
        """把 pending 行搬进 TextArea（跨线程调用安全：两把锁分段）。"""
        with self._out_lock:
            if not self._pending_lines:
                return
            lines = self._pending_lines
            self._pending_lines = []
            # 样式与行同队列出（_write_via_buffer 保证一一对应）
            spans_list = self._style_spans_pending
            self._style_spans_pending = []
        if lines:
            self._append_output_lines(lines, spans_list)

    # ── PromptSessionInterface 协议 ──
    def push_to_history(self, text: str) -> None:
        # FileHistory 在 accept 时自动写入；此接口保留协议一致性
        _ = text

    def stream_print(self, renderable: Any) -> None:
        # stdout 代理驻留期间 print 自动进输出窗；协议一致性实现
        print(renderable, end="", flush=True)

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """保存状态栏片段回调（全屏期间每帧渲染调用）。"""
        self._status_cb = get_fragments

    def install_mode_toggler(self, toggler: Callable[[], None]) -> None:
        """2026-09-22: 注入 Shift+Tab 模式环回调（模式切换提示经 stdout 代理进输出窗）。"""
        self._mode_toggler = toggler

    def install_todo_panel_toggler(self, toggler: Callable[[], None]) -> None:
        """2026-09-27: 注入 Ctrl+T 任务面板显隐回调。"""
        self._todo_panel_toggler = toggler

    def interrupt_event(self) -> threading.Event:
        return self._interrupt

    def set_streaming(self, streaming: bool) -> None:
        """标记流式生成期。

        streaming=True 期间 prompt()（由 InputPump 线程调用）只等提交、
        **不消费 interrupt_event** —— Ctrl+C 打断归流循环检查
        （repl._run_stream_turn 每事件轮询）；否则 pump 线程会在 ≤0.2s 内
        清掉 interrupt，生成永远无法被打断。

        2026-09-30 TUI 原位上色：False→True 跳变时记下输出窗行数作为本轮
        流式素字段起点，done 事件据此原位替换为带样式版本。
        """
        was = self._streaming
        self._streaming = streaming
        if streaming and not was:
            self._stream_start_mark = self.mark_stream_start()
            # 台账同步清台（2026-10-01）：台账生命周期锚定轮次起点，与
            # mark 同源——防上轮无 done 收尾（error/打断路径）时残留行
            # 跨轮累积（批次测试实证：LEDGER_TOO_LONG 恒拒 → 上色回退）。
            # repl 流循环入口另有双保险清台（repl.py 每轮 reset）。
            try:
                from lingclaude.cli.repl_io import _turn_trace_reset

                _turn_trace_reset()
            except Exception:  # noqa: BLE001 — 清台失败不阻断流启动
                pass

    def prompt(self, message: str = "") -> str:
        """阻塞取一条已提交输入；EOF 哨兵抛 EOFError；空闲 Ctrl+C 返回 ""。

        Application 未启动（start 失败/未调用）→ 降级裸 input()（P1 逃生语义）。
        已启动后 close()/后台线程死亡 → EOFError（pump 优雅退出；不得回退
        input() 与 PT 事件循环抢 stdin —— 全屏已还原终端，直读会挂死/错乱）。
        """
        if not self._ever_started:
            # 从未成功启动（start 失败/未调用）→ 降级裸 input()（P1 逃生语义）
            try:
                # 2026-09-18 方向键/历史修复：降级 input() 同样挂 readline +
                # 写内存历史 —— 全屏降级路径与主输入路径行为对齐。
                ensure_readline()
                _line = input(message)
                add_history_line(_line)
                return _line
            except KeyboardInterrupt:
                self._interrupt.set()
                return ""
        while True:
            with self._submit_cond:
                while not self._submit_q:
                    self._submit_cond.wait(timeout=0.2)
                    # Raw 看门狗（2026-09-26 假死根治）：等待循环每 ≤0.2s 醒来
                    # 一次，顺带检测终端 raw 失同步并自愈（内部 0.5s 节流）。
                    # pump 阻塞在此期间覆盖了空闲/生成两态，检测永不反噬
                    # （详见 _check_tty_raw_drift）。
                    try:
                        self._check_tty_raw_drift()
                    except Exception:  # noqa: BLE001 — 看门狗异常绝不阻塞取输入
                        pass
                    # 注意：流式标志必须每轮实时读 —— pump 线程是长驻阻塞的
                    # （生成开始前就进入 prompt 等下一轮输入），快照会永远
                    # 停在进入时刻 → 流式期 Ctrl+C 仍被 pump 消费 → 打断失效。
                    if not self._streaming and self._interrupt.is_set():
                        # 空闲期 Ctrl+C 软中断语义：清事件、返回空串继续
                        self._interrupt.clear()
                        return ""
                    if not self._running:
                        # Application 已退出（close 或后台线程死亡）→ 结束会话。
                        # 死因留痕到 stderr（真实流，stdout 可能已还原）。
                        if self._app_error:
                            print(f"[全屏TUI异常退出] {self._app_error}", file=sys.stderr)
                        raise EOFError
                item = self._submit_q.popleft()
            if item == EOF_SENTINEL:
                raise EOFError
            return item

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：全屏 prompt() 走内部提交队列（不碰 stdin、无
        streaming 短路），直接委托，行为与旧路径一致。"""
        return self.prompt(message)

    # ── 扩展接口 ──

    def install_output_source(self, source: Callable[[], list[str]]) -> None:
        """注入输出窗初始内容来源（会话历史行）。"""
        self._output_source = source

    def resync(self) -> None:
        """P3 全量重绘原语（2026-09-20，atomcode invalidate 借鉴）。

        终端状态可疑（resize/怀疑渲染失步/想强制刷新回放）时，调用方
        无需知道哪条路径漏了——直接从 output_source 重建整个输出窗文档
        并触发重绘。一个原语覆盖所有「窗口内容 vs 期望状态」失步场景，
        替代逐路径打补丁。线程安全性同 _write_via_buffer（主线程调用
        最佳；他线程调用经 _invalidate 请求重绘，文档替换本身幂等）。
        """
        self._refresh_output_area()
        self._invalidate()

    def hard_resync(self) -> None:
        """硬 resync（2026-09-27）：renderer 全量复位 + 结构性重建。

        与软 resync（resync，只换文档+invalidate）的区别：renderer.reset()
        把 PT 渲染器的 diff 基线、alternate-screen 标志、鼠标处理表全部
        清零，下一帧按「全新首帧」整屏重画。

        为什么必须有：软 resync 治不了「物理屏幕结构被破坏」的场景——
        证据链（2026-09-27 用户实机两症状同偏移）：
        1. Ctrl+L 落到 PT 默认 clear-screen（basic.py:155）→
           renderer.clear() → erase(leave_alternate_screen=True 默认值)
           → 退出 \x1b[?1049h 备用屏 → 全屏画在主缓冲区，结构永久错位；
        2. 全屏期任何裸写 stderr/stdout 的字节（_warn_raw、异常打印）
           顶乱 alternate screen 光标，PT diff 认为画面没变 → 不重画。
        两个症状由此同源：物理屏幕被顶高 N 行后，鼠标物理坐标直接当
        布局坐标用（bindings/mouse.py:275 y-=rows_above_layout，全屏恒
        0）→ 点击偏上 N 行；toolbar 被顶出视口 → 「toolbar 不可见」。

        受控复现（repro7：进程内 Application + 同构布局 + SGR 鼠标注入）
        证明布局与坐标映射本身零缺陷——toolbar 精确落 22 行、屏幕行 19
        点击精确映射 buffer 行 18。错位只来自渲染器外部破坏。

        注意：本方法在 alternate screen 内部调用时 reset 会先发 1049l
        退出备用屏，下一次 render 检测 _in_alternate_screen=False 会重新
        1049h 进入——等效「擦掉重来」，正是我们要的语义。
        线程安全：仅请求重绘路径，主线程/他线程均可（同 resync）。
        """
        app = self._app
        if app is not None and self._running:
            try:
                renderer = app.renderer
                if renderer is not None:
                    renderer.reset()
            except Exception:  # noqa: BLE001 — 复位失败退回软 resync 语义
                pass
        self._refresh_output_area()
        self._invalidate()

    # ── 内部 ──

    def _refresh_output_area(self) -> None:
        """启动时重绘输出窗（取历史最近 MAX_OUTPUT_LINES 行）。"""
        try:
            lines = list(self._output_source() or [])
        except Exception:  # noqa: BLE001 — 历史源异常不阻塞输入
            lines = []
        if len(lines) > MAX_OUTPUT_LINES:
            lines = lines[-MAX_OUTPUT_LINES:]
        self._set_output_lines(lines)

    def _set_output_lines(self, lines: list[str]) -> None:
        """整体替换输出窗内容，光标钉回文末（跟随模式）。

        注意：文本末尾**不加**换行 —— 否则光标钉文末时落在幻影空行上，
        cursor_position_row = line_count（比最后一行实际行号大 1），滚动
        计算会整体偏 1。

        2026-09-20 乱码修复（第二路径）：历史回放（_refresh_output_area）
        的行来自会话 transcript，可能含模型回复内嵌的 SGR 序列——此前
        唯一未清洗的 set_document 入口，重启回放后 0x1b 渲染成 '?' 再漏
        明文。与 _write_via_buffer 同用一剥离器（幂等，双洗无害）。
        """
        text = "\n".join(_strip_ansi_text(line) for line in lines)
        # 2026-09-27 受限富文本：整体替换 = 旧样式表全部失效，清空防错位。
        with self._style_lock:
            self._style_map.clear()
        self._out_buffer.set_document(Document(text, 0), bypass_readonly=True)
        self._out_buffer.cursor_position = len(text)
        self._follow_output = True

    def _invalidate(self) -> None:
        """请求重绘（app 未运行时静默；可从任意线程调用）。"""
        app = self._app
        if app is not None and self._running:
            try:
                app.invalidate()
            except Exception:  # noqa: BLE001 — 重绘请求失败不反噬调用方
                pass

    # ── 输出历史滚动（滚轮回调与键绑定共用「移动光标行」语义） ──

    def _on_out_wheel(self, direction: int) -> None:
        """滚轮事件（_OutputScrollControl 回调）：+1 向下 / -1 向上。"""
        self._scroll_out_lines(direction)

    def _scroll_out_lines(self, delta: int) -> None:
        """输出窗光标上/下移 delta 行（负值向历史）；边界钳制。

        滚到最后一行时光标钉到文末 → is_cursor_at_the_end=True → 自动
        恢复跟随模式（后续新输出把视口拽回底部）。
        """
        try:
            buf = self._out_buffer
            doc = buf.document
            line_count = doc.line_count
            if not line_count:
                return
            row = doc.cursor_position_row + delta
            row = max(0, min(row, line_count - 1))
            if row >= line_count - 1:
                buf.cursor_position = len(buf.text)
            else:
                buf.cursor_position = doc.translate_row_col_to_index(row, 0)
            self._follow_output = buf.document.is_cursor_at_the_end
            self._invalidate()
        except Exception:  # noqa: BLE001 — 滚动异常不反噬事件循环
            pass

    def _scroll_out_pages(self, pages: int) -> None:
        """输出窗整页滚动（PageUp/PageDown）：按可视高度移动光标行。"""
        try:
            info = self._output_area.render_info
            height = info.window_height if info is not None else 0
            if height <= 0:
                height = 10  # 尚无渲染信息（未首帧）时的兜底页高
            self._scroll_out_lines(pages * max(1, height - 1))
        except Exception:  # noqa: BLE001 — 滚动异常不反噬事件循环
            pass

    def _append_output_lines(
        self,
        lines: list[str],
        spans_list: list[list[tuple[int, int, str]]] | None = None,
    ) -> None:
        """追加行进输出窗（跨线程安全：_area_lock 串行化读改写）。

        行数超限丢最旧行；Application 未运行时仅更新缓冲（不渲染，无害）。
        跟随模式：光标钉回文末（新输出可见）；回看模式：光标行保持不变
        （set_document 会重置光标，必须显式恢复），仅当旧行被裁掉时按裁剪
        量上移光标修正视口锚点。

        2026-09-27 受限富文本：spans_list 与 lines 一一对应（无则全 None），
        按最终行号写入 _style_map；首部被裁时行号整体平移 dropped。
        """
        try:
            with self._area_lock:
                buf = self._out_buffer
                old_text = buf.text
                old_row = buf.document.cursor_position_row
                all_lines = old_text.split("\n") if old_text else []
                old_count = len(all_lines)
                all_lines.extend(lines)
                dropped = 0
                if len(all_lines) > MAX_OUTPUT_LINES:
                    dropped = len(all_lines) - MAX_OUTPUT_LINES
                    all_lines = all_lines[-MAX_OUTPUT_LINES:]
                new_text = "\n".join(all_lines)
                buf.set_document(Document(new_text, 0), bypass_readonly=True)
                # ── 样式表同步（2026-09-27 受限富文本）──
                if spans_list is None:
                    spans_list = [None] * len(lines)  # type: ignore[list-item]
                if dropped:
                    for _ in range(dropped):
                        if spans_list:
                            spans_list.pop(0)
                    with self._style_lock:
                        if dropped >= old_count:
                            self._style_map.clear()
                        else:
                            self._style_map = {
                                row - dropped: sp
                                for row, sp in self._style_map.items()
                                if row - dropped >= 0
                            }
                base = max(0, old_count - dropped)
                with self._style_lock:
                    for i, sp in enumerate(spans_list):
                        if sp:
                            self._style_map[base + i] = list(sp)
                if self._follow_output or not new_text:
                    buf.cursor_position = len(new_text)
                else:
                    # 回看中：恢复光标行；首部被裁时按裁剪量上移（视口锚点
                    # 随内容平移，视觉位置不变）；滚到最后一行=回到文末
                    row = max(0, old_row - dropped)
                    if row >= len(all_lines) - 1:
                        buf.cursor_position = len(new_text)
                    else:
                        buf.cursor_position = buf.document.translate_row_col_to_index(
                            row, 0
                        )
                follow_now = buf.document.is_cursor_at_the_end
            self._follow_output = follow_now
            self._invalidate()
        except Exception as _buf_err:  # noqa: BLE001 — 输出窗异常不反噬生成主线程
            # 2026-09-25 P0: 可见降级——刷窗失败曾静默 pass（输出窗停更无证据）
            _warn_raw("\n⚠ 输出窗刷新异常：" + repr(_buf_err) + "\n")

    def _sep_fragments(self) -> list[tuple[str, str]]:
        """分隔线片段：回看模式时在行内提示（含恢复跟随的键位）。"""
        if self._follow_output:
            return [("class:sep", "─" * 200)]
        hint = (
            "← 回看输出历史（滚轮/Shift+↑↓/PageUp·Down 浏览，"
            "Ctrl+End 或滚到底恢复跟随） "
        )
        sep = "─" * max(0, 200 - len(hint) - 1)
        return [("class:sep", sep + "┤ "), ("class:sep:reverse", hint), ("class:sep", " ├")]

    def _status_fragments(self) -> list[tuple[str, str]]:
        if self._status_cb is not None:
            try:
                return self._status_cb() or []
            except Exception as exc:  # noqa: BLE001 — 状态栏异常不再静默消失
                # 2026-09-25 修复：原「except: return []」让回调任何一次异常
                # 表现为状态栏整行空白（用户视角 = toolbar 无声消失，零线索）。
                # 改为降级行：红字可见异常摘要，渲染仍不反噬主循环 ——
                # 再炸时用户直接看到炸点，可即时上报修复。
                msg = f"{type(exc).__name__}: {exc}"
                if len(msg) > 80:
                    msg = msg[:79] + "…"
                return [("class:red", f"⚠ 状态栏异常 {msg} ")]
        return []

    def _on_accept(self, buf: Any) -> bool:
        text = buf.text
        if text:
            # 2026-09-21 输入回显：提交的输入即时进输出窗（"> " 前缀，终端惯例）。
            # 此前提交后 buffer 被 PT reset 清空，输出窗无痕——用户输入与
            # 模型回复在历史里混在一起无法区分。回显走 append_output
            # （_area_lock 跨线程安全，未启动时仅更新缓冲无害）。
            # 注意：回显的是输入文本本身，escape 后换行符已被替换。
            # 2026-09-21 粘贴折叠配套：回显前还原占位符 → 用户在输出窗
            # 看到完整提交内容（含粘贴全文）；但回显再次折叠为占位符——
            # 长粘贴回显会刷屏，占位符形态与输入框所见一致。
            full_text = self._expand_placeholders(text)
            echo_text = self._fold_echo(full_text)
            try:
                # 2026-10-02 用户回显高亮：🧑 图标 + 亮品红粗体（SGR 白名单
                # 内，_write_via_buffer → _extract_sgr_styles 解析进
                # _style_map，processor 按列上色，窗内零裸 ESC）。emoji 🧑
                # 宽度实测三方一致（_disp_width=east_asian_width=W=2、
                # wcwidth=2、PT fragment 宽度=2），色段列坐标不错位。
                # 前缀格式对齐历史回放（repl.py:486 「🧑 用户: 」），活线
                # 与重启回放视觉统一，翻历史会话时按图标即扫到每轮入口。
                # 逐行包裹：多行输入（Esc+Enter 换行）的续行同样着色——
                # _write_via_buffer 按行独立解析 SGR，样式不跨行继承。
                echo_lines = echo_text.split("\n") or [""]
                echo_lines[0] = "🧑 用户: " + echo_lines[0]
                # 用户回显：反显+粗体——反显用终端自身前景/背景互换，任何
                # 配色主题下都与背景强对比（2026-10-02 用户反馈：亮品红在
                # 其背景下对比不足）。SGR 7;1 在白名单内，写窗层解析成
                # 'reverse bold' 样式段；回放链（_strip_ansi_text）剥色后
                # 仍留 🧑 前缀可辨识。
                body = "\x1b[0m\n\x1b[7;1m".join(echo_lines)
                self.append_output("\x1b[7;1m" + body + "\x1b[0m\n")
            except Exception:  # noqa: BLE001 — 回显失败不阻断提交
                pass
            self._submit(full_text)
        # 2026-09-19 输入历史修复：**不得在此清空 buffer.text**。
        # PT 标准 accept 流程（buffer.validate_and_handle）是先调 accept_handler
        # 再 append_to_history → reset；旧实现先置 buf.text=""，append_to_history
        # 读到空串直接跳过（buffer.py:1363 if self.text:）→ P2 全屏会话提交的
        # 输入从未进入 FileHistory → Up 无史可翻。
        # 文本清空交给 PT 的 reset()（accept_handler 返回 False 即可）。
        # 历史游标（working_index）保持不动：翻历史后提交，下一帧首帧渲染时
        # load_history_if_not_yet_loaded 会以 FileHistory 最新内容重放装载。
        return False

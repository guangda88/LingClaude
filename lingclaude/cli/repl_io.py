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


def _pad_table_block(rows: list[str], policy: dict[str, Any]) -> list[str]:
    """表格块按显示列重排补白，返回成品行。

    2026-09-30 从 _flush_table_buf 抽出：落窗（流式路径）与指纹
    （_expected_window_lines）共用同一实现——两套各写一遍必然漂移，
    漂移即指纹恒失配、原位上色静默回退（当日已复现的事故）。
    """
    pad_char = policy["pad_char"]
    # 切列：strip 后去首尾部件，按内部分隔切
    grid: list[list[str]] = []
    for line in rows:
        s = line.strip()
        cells = [c.strip() for c in s[1:-1].split("|")]
        grid.append(cells)
    ncols = max(len(r) for r in grid)
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
        globals()["_stream_lines_emitted"] += 1


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

_OUTPUT_FORMAT = "plain"  # P0-2: plain | json | jsonl
_json_event_buffer: list[dict[str, Any]] = []  # json 模式事件缓冲

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
        globals()["_stream_lines_emitted"] += 1
    # 表格块随 flush 点强制输出（tool_call/done/error 打断表块时兜底）
    if _table_buf:
        _flush_table_buf()


def _handle_stream_event(event: dict[str, Any]) -> None:
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
        _stream_write(f"\n  [{name}] {args_preview} ... ")
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
        else:
            _stream_write(f"{mark}\n")
    elif etype == "status":
        _flush_stream_line()
        _stream_write(f"\n  [{event.get('message', '')}] ")
    elif etype == "done":
        _flush_stream_line()
        # P0-完成渲染:TTY 下擦除裸文本行,用 rich Markdown 重渲染正式版
        # 2026-09-06 修复:之前用 `\x1b[{n}A` 上移 N 行 + 清屏重渲染,但 prompt_toolkit
        # 同步维护自己的 bottom_toolbar(N 不含 toolbar 行)→ cursor 操作覆盖了 toolbar
        # 文字("灵克[..]ude │ 上下文 0%"被截成 ude)、产生位移。
        # 新策略:TTY 下不再用 ANSI cursor 操作(保留 raw stream 输出),改用 Rich 的
        # `erase + replace` 在底部追加正式版,而非覆盖——避免与 PT 的 toolbar 控制权冲突。
        content = event.get("content", "")
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
                cols = max(60, (shutil.get_terminal_size().columns or 80))
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

                ansi_console.print(Markdown(content))
                ansi_text = buf.getvalue()
                styled_lines: list[str] = []
                spans_list: list[list[tuple[int, int, str]]] = []
                for ln in ansi_text.split("\n"):
                    t, sp = _extract_sgr_styles(ln)
                    styled_lines.append(t)
                    spans_list.append(sp)
                proxy = getattr(sys, "stdout", None)
                owner = getattr(proxy, "_owner", None)
                if (
                    owner is not None
                    and owner.replace_turn_styled(
                        styled_lines,
                        spans_list,
                        _expected_window_lines(content),  # 指纹：与流式落窗同构变换后比对
                    )
                ):
                    pass  # 原位替换成功：窗内素字段已变彩色版，无需追加
                else:
                    _stream_write("\n\n")  # 回退：无标记/区间失效，旧行为收尾
                    globals()["_stream_lines_emitted"] = 0
            except Exception:  # noqa: BLE001 — 渲染/替换失败回退素字收尾
                _stream_write("\n\n")
                globals()["_stream_lines_emitted"] = 0
        else:
            _stream_write("\n\n")
    elif etype == "error":
        _flush_stream_line()
        # UI 对齐修复:前后各留空行,与 rich stderr 日志/下一提示符隔离。
        _stream_write(f"\n\n[错误] {event.get('error', '')}\n")
        _stream_write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n\n")

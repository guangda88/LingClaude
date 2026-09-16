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

# P0-行缓冲:跨事件聚合流式 delta,残行待下次事件或 flush 收尾
_stream_line_buf: list[str] = []
_stream_lines_emitted = 0  # 本轮已输出行数(done 时用于 ANSI 擦除重渲染)
_OUTPUT_FORMAT = "plain"  # P0-2: plain | json | jsonl
_json_event_buffer: list[dict[str, Any]] = []  # json 模式事件缓冲



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
                return False  # 杂散字节静默吞掉(不回显),避免污染输入行
            # \x1b 后 20ms 无跟随字节 → 孤立 Esc → 打断
            r2, _, _ = select.select([fd], [], [], 0.02)
            if not r2:
                return True
            # 转义序列（方向键/粘贴包裹等）:整体排空,不留残字节,不算打断。
            # 粘贴正文可能分片到达,读到静默或已见结束标记为止。
            for _ in range(256):  # 上限防异常输入流死循环
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                if bytes(chunk).endswith(b"\x1b[201~"):
                    break
                r2, _, _ = select.select([fd], [], [], 0.05)
                if not r2:
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
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        globals()["_stream_lines_emitted"] += 1


def _handle_stream_event(event: dict[str, Any]) -> None:
    # P0-行缓冲:流式 delta 按行聚合,只在行边界输出 — 根治半截表格行与
    # 多写入者交错导致的"空格逐行累加"错位(对齐 atomcode UiLine 语义行)。
    etype = event.get("type")
    if _OUTPUT_FORMAT in ("json", "jsonl"):
        # P0-2: 机器可读输出。jsonl = 每事件一行；json = 缓冲,done 时汇总单对象。
        payload = {k: v for k, v in event.items() if k != "content"} if _OUTPUT_FORMAT == "jsonl" else None
        if _OUTPUT_FORMAT == "jsonl":
            print(json.dumps({"type": etype, **({"text": event["text"]} if "text" in event else {}), **(payload or {})},
                             ensure_ascii=False), flush=True)
            return
        _json_event_buffer.append(event)
        if etype == "done":
            print(json.dumps({
                "type": "done",
                "content": event.get("content", ""),
                "events": [{"type": e.get("type"), **({"text": e["text"]} if "text" in e else {})}
                           for e in _json_event_buffer],
            }, ensure_ascii=False), flush=True)
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
            sys.stdout.write(line + "\n")
            globals()["_stream_lines_emitted"] += 1
        if pending:
            _stream_line_buf.append(pending)
        sys.stdout.flush()
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
        sys.stdout.write(f"\n  [{name}] {args_preview} ... ")
        sys.stdout.flush()
    elif etype == "tool_call_end":
        is_error = event.get("is_error", False)
        preview = event.get("output_preview", "")
        mark = "❌" if is_error else "✅"
        if preview and not is_error:
            # 终端宽度动态截断（替代固定 80 字符）——窄终端不低于 60，
            # 宽终端用 columns-8 留余白；非 TTY 回落 80
            try:
                cols = max(60, (shutil.get_terminal_size().columns or 80) - 8) if sys.stdout.isatty() else 80
            except Exception:  # noqa: BLE001
                cols = 80
            preview = preview[:cols].replace("\n", " ")
            sys.stdout.write(f"{mark} ({len(preview)} chars)\n")
        else:
            sys.stdout.write(f"{mark}\n")
        sys.stdout.flush()
    elif etype == "status":
        _flush_stream_line()
        sys.stdout.write(f"\n  [{event.get('message', '')}] ")
        sys.stdout.flush()
    elif etype == "done":
        _flush_stream_line()
        # P0-完成渲染:TTY 下擦除裸文本行,用 rich Markdown 重渲染正式版
        # 2026-09-06 修复:之前用 `\x1b[{n}A` 上移 N 行 + 清屏重渲染,但 prompt_toolkit
        # 同步维护自己的 bottom_toolbar(N 不含 toolbar 行)→ cursor 操作覆盖了 toolbar
        # 文字("灵克[..]ude │ 上下文 0%"被截成 ude)、产生位移。
        # 新策略:TTY 下不再用 ANSI cursor 操作(保留 raw stream 输出),改用 Rich 的
        # `erase + replace` 在底部追加正式版,而非覆盖——避免与 PT 的 toolbar 控制权冲突。
        content = event.get("content", "")
        if content and sys.stdout.isatty():
            # ANSI 光标下移一行(到达 stream 输出末尾之下),再向上滚回渲染
            # ——比上移 N 行覆盖安全(N 不必精确)
            sys.stdout.write("\x1b[1B\n")
            try:
                # TUI 插片优先（render_facade 内部: provider→cli.display 回退）
                from lingclaude.cli.render_facade import print_markdown

                print_markdown(content)
            except Exception:  # noqa: BLE001 — 渲染失败时保底输出纯文本
                sys.stdout.write("\n" + content + "\n\n")
            globals()["_stream_lines_emitted"] = 0  # 复位：P0 完成行已就位
        else:
            sys.stdout.write("\n\n")
        sys.stdout.flush()
    elif etype == "error":
        _flush_stream_line()
        # UI 对齐修复:前后各留空行,与 rich stderr 日志/下一提示符隔离。
        sys.stdout.write(f"\n\n[错误] {event.get('error', '')}\n")
        sys.stdout.write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n\n")
        sys.stdout.flush()

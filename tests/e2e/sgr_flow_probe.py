"""决定性实验：真实事件流验证拖选接管 binding。

真实 Application + KeyProcessor + PipeInput，喂原始 SGR 序列，走完整分发链。
不 mock 任何 PT 组件——之前两轮「理论上该通」都是因为没走真实事件流。
"""
import asyncio
import re

from prompt_toolkit.application import Application
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import Condition

state = {"active": False, "handled": []}

_kb = KeyBindings()


@_kb.add(Keys.Vt100MouseEvent, filter=Condition(lambda: state["active"]))
def _on_mouse(ev):
    state["handled"].append(ev.data)
    m = re.match(r"^\x1b\[<(?P<b>\d+);(?P<x>\d+);(?P<y>\d+)(?P<suf>[mM])$", ev.data)
    if m is None:
        return
    b = int(m.group("b"))
    suf = m.group("suf")
    if (b, suf) == (0, "M"):  # 左键按下 → 进入拖选
        state["active"] = True
    elif suf == "m":  # 松手
        state["active"] = False


def main() -> None:
    out_win = Window(
        FormattedTextControl(lambda: "\n".join(f"row{i}" for i in range(40))),
        height=8,
    )
    layout = Layout(HSplit([out_win, Window(height=3)]))
    kb = KeyBindings()
    with create_pipe_input() as pipe:
        state["active"] = True  # 预置：模拟内置 handler 已收到 DOWN 置位
        app = Application(
            layout=layout,
            key_bindings=merge_key_bindings([kb, _kb]),
            output=DummyOutput(),
            input=pipe,
            mouse_support=True,
        )
        def feeder() -> None:
            seqs = (
                b"\x1b[<0;10;3M",
                b"\x1b[<32;10;14M",
                b"\x1b[<32;10;15M",
                b"\x1b[<0;10;14m",
            )
            import threading, time as _t
            def _push():
                for s in seqs:
                    _t.sleep(0.15)
                    pipe.send_bytes(s)
                _t.sleep(0.4)
                app.exit()
            threading.Thread(target=_push, daemon=True).start()

        app.run(pre_run=feeder)
    print("handled:", state["handled"])
    print("final active:", state["active"])


if __name__ == "__main__":
    main()

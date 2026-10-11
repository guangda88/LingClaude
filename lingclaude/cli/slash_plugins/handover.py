"""/handover —— 正门交接（2026-10-10 用户设计）。

设计裁决（用户原话）：「不 kill lc 进程，只做交接文档落盘，新起会话，
清空上下文和预算即可。」——与 R9-2 kill-safe beacon（防猝死兜底）分工：
beacon 管意外死亡，本命令管主动换班。旧会话不 reset、不杀，闲置即退。

三步：
  1. 机械采集（零幻觉）：beacon 快照 + todo 持久层 → 骨架交接文档落盘
     docs/handover/（防 /tmp 清理；「下一步计划」区留白由继任会话补全）
  2. tmux 新窗口拉继任会话（run -i 可见形态，新进程=上下文/预算天然全新）
  3. 打印 attach 命令；用户可直接围观/接手继任，旧会话光荣闲置

配方红线（上会话实战教训）：
  - prompt 先落盘再由 tmux 内层 $(cat) 展开——内嵌引号经外层 shell 会二次解析
  - tmux 内层用 bash -lc（login shell 带 ~/.local/bin PATH，编排器 127 教训）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _collect_todo_snapshot(engine: Any) -> list[dict]:
    """未完成 todo 清单（getattr 链防御，与 _commands_tasks.py:73 同范式）。"""
    runtime = getattr(engine, "_runtime", None)
    store = getattr(runtime, "_todo_store", None)
    if store is None:
        return []
    try:
        items = store.list()
    except Exception:  # noqa: BLE001 — todo 读取失败不阻断交接
        return []
    out = []
    for it in items:
        try:
            d = it.to_dict()
        except Exception:  # noqa: BLE001
            continue
        if str(d.get("status", "")) in ("pending", "in_progress"):
            out.append({"id": d.get("id"), "status": d.get("status"),
                        "content": d.get("content", "")[:200]})
    return out


def _collect_beacon_snapshot(engine: Any, project_path: str) -> dict:
    """写一份即时 beacon 并读回（复用 R9-2 采集器，数据口径单源）。"""
    try:
        from lingclaude.engine.kill_beacon import KillBeacon

        b = KillBeacon(engine, project_path=project_path)
        if not b.write_beacon(reason="handover"):
            return {}
        return json.loads(b.path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — beacon 缺席时交接文档降级继续
        return {}


def _write_skeleton(doc_path: Path, beacon: dict, todos: list[dict],
                    project_path: str) -> None:
    sid = str(beacon.get("session_id", "unknown"))
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    turns = beacon.get("turns", "?")
    in_tok = beacon.get("input_tokens", "?")
    recent = beacon.get("last_user_input", "") or "（未采集到）"
    lines = [
        f"# 会话交接（正门 /handover）{now}",
        "",
        f"- 前会话: `{sid}`（{turns} 轮）· 项目: `{project_path}`",
        f"- token: in={in_tok}",
        "",
        "## 最近用户指令（机械采集）",
        "",
        recent,
        "",
        "## 未完成 todo（机械采集，继任接手清单）",
        "",
    ]
    if todos:
        lines += [f"- [{t['status']}] {t['content']}（id={t['id']}）" for t in todos]
    else:
        lines += ["- （无未完成 todo）"]
    lines += [
        "",
        "## 关键上下文文件（由前会话模型人工补写；本区为空则跳过）",
        "",
        "（待补）",
        "",
        "## 下一步计划（由继任会话填写：按上面 todo 给出可执行步骤，",
        "## 每步带实测文件路径锚点，禁止凭记忆转述）",
        "",
        "（继任会话在此补全）",
        "",
    ]
    doc_path.write_text("\n".join(lines), encoding="utf-8")


def _launch_successor(doc_path: Path, project_path: str) -> tuple[bool, str]:
    """tmux 拉可见继任会话；返回 (ok, attach 指引/失败原因)。"""
    sid = _dt.datetime.now().strftime("%H%M%S")
    sess = f"handover_{sid}"
    prompt_path = doc_path.parent / f"{doc_path.stem}_successor_prompt.md"
    prompt_path.write_text(
        f"你是前一灵克会话的继任者。执行交接协议：\n"
        f"1) 通读 {doc_path}\n"
        f"2) 在「下一步计划」区按未完成 todo 补全计划（每步带实测锚点）\n"
        f"3) 打印 HANDOVER-ACK-OK 与三条自验（各带实测证据）\n"
        f"完成后保持运行等待检阅，不要退出。\n",
        encoding="utf-8",
    )
    sock = "/tmp/tmux-lingrelay.sock"
    inner = f'lingclaude run -i "$(cat {shlex.quote(str(prompt_path))})"'
    tmux = shutil.which("tmux")
    if tmux is None:
        return False, f"tmux 未安装。手动开新终端执行: lingclaude run -i \"$(cat {prompt_path})\""
    cmd = [tmux, "-S", sock, "new-session", "-d", "-s", sess,
           "bash", "-lc", inner]
    # 防消失三件套：继任干完活退出时，窗口保留退出画面并打印去向，
    # 避免「attach 时 can't find session」无从取证（20261011 003733 教训）。
    subprocess.run([tmux, "-S", sock, "set-option", "-g",
                    "remain-on-exit", "on"],
                   capture_output=True, timeout=10)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                              cwd=project_path or None)
    except Exception as e:  # noqa: BLE001
        return False, f"tmux 启动失败: {e}"
    if proc.returncode != 0:
        return False, f"tmux rc={proc.returncode}: {proc.stderr.strip()[:200]}"
    return True, (f"TMUX_TMPDIR={os.path.dirname(sock)} "
                  f"tmux -S {sock} attach -t {sess}")


_AUTO_HANDOVER_RATIO = 0.85
_AUTO_STATE = {"done": False}  # 进程级幂等闩：自动换班一次后不再触发


def _auto_handover_if_needed(ctx: Any) -> None:
    """预算自动换班入口（repl.py 每轮持久化后调用）。

    幂等闩语义：自动模式每进程至多一次（防 85% 触发后下一轮 84%→死循环）；
    手动 /handover 不受闩限制。任何异常静默吞（repl 侧同样全兜）。
    """
    if _AUTO_STATE["done"]:
        return
    processor = getattr(ctx, "processor", None)
    if processor is None or not callable(getattr(processor, "submit", None)):
        return  # 无注入通道的形态（如部分测试 ctx）不触发
    _AUTO_STATE["done"] = True
    print("\n[预算自动换班] 用量达 85%，执行 /handover v2 进程内换班…")
    try:
        handover_cmd(processor, "")
    except Exception:  # noqa: BLE001
        print("[预算自动换班] 执行失败（已留痕，不影响当前会话）")


def _inplace_handover(processor: Any, doc_path: Path) -> None:
    """v2 进程内换班（2026-10-11 用户设计）：工位不动、脑子换新。

    1. 交接文档已由调用方落盘
    2. /clear 内核 = engine.reset()（存档→新 session_id→清上下文九项）
       + session_budget_gate.reset()（预算清零，commands.py:138-146 同一出口）
    3. submit 回调注入续作指令（repl.py:1609 /multi 同款 queued_next 通道，
       「主循环既有消费机制，零新竞态」）→ turn 结束后新会话第一件事=读文档继续
    """
    engine = processor.engine
    # 防御：submit 不可用（极端嵌入形态）→ 退化为纯 clear，至少保住换班
    submit = getattr(processor, "submit", None)
    if not callable(submit):
        try:
            engine.reset()
            from lingclaude.core import session_budget_gate as _gate

            _gate.reset()
        except Exception:  # noqa: BLE001 — 换班失败不崩 REPL
            pass
        print("[/handover] submit 通道不可用，已仅执行清空（无续作注入）")
        return
    try:
        clear = getattr(processor, "_cmd_clear", None)
        if callable(clear):
            clear()
        else:
            engine.reset()
            from lingclaude.core import session_budget_gate as _gate

            _gate.reset()
    except Exception as e:  # noqa: BLE001
        print(f"[/handover] 清空失败（未注入续作）: {e}")
        return
    todos = _collect_todo_snapshot(engine)
    todo_hint = (
        f"未完成 todo {len(todos)} 条已列入交接文档，完成后按其推进。"
        if todos else ""
    )
    submit(
        f"【交接续作】读取 {doc_path} 交接文档全文，理解前会话进度，"
        f"按其中「下一步计划」继续执行。{todo_hint}"
        "此消息由 /handover 自动生成。"
    )
    print("[换班完成] 上下文/预算已清零，续作指令已注入（下一轮生效）")


def handover_cmd(processor: Any, arg: str = "") -> None:
    """/handover [tmux] —— v2 默认进程内换班；tmux=跨窗口可见继任（v1 备选）。"""
    mode = arg.strip().lower()
    if mode not in ("", "tmux"):
        print("[/handover] 用法：/handover（进程内换班）| /handover tmux（跨窗口继任）")
        return
    engine = processor.engine
    project_path = os.getcwd()
    docs = Path(project_path) / "docs" / "handover"
    docs.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    doc_path = docs / f"{stamp}_relay_handover.md"

    beacon = _collect_beacon_snapshot(engine, project_path)
    todos = _collect_todo_snapshot(engine)
    _write_skeleton(doc_path, beacon, todos, project_path)
    print(f"[交接文档] {doc_path}")
    if beacon.get("last_user_input"):
        print(f"[最近指令] {str(beacon['last_user_input'])[:80]}")
    print(f"[未完成 todo] {len(todos)} 条已写入交接文档")

    if mode == "tmux":
        ok, msg = _launch_successor(doc_path, project_path)
        if ok:
            print("[继任会话] 已在 tmux 窗口拉起（run -i 可见形态；上下文/预算全新）")
            print(f"[围观/接手] {msg}")
            print("[旧会话] 未杀未清，继续可用；闲置即可退出")
        else:
            print(f"[继任拉起失败] {msg}")
            print("[交接文档已落盘] 可手动开新终端按上面 prompt 恢复")
        return
    _inplace_handover(processor, doc_path)


def register(add) -> None:
    """斜杠插件注册契约（slash_plugin_loader.py:5）。"""
    add("/handover", handover_cmd,
        "正门交接v2：落盘交接文档→/clear(清上下文+预算)→注入续作指令；"
        "/handover tmux=跨窗口继任")

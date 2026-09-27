#!/usr/bin/env python3
"""IME/中文输入两阶段探针 —— 定谳「lc 里打不了中文」的权威手段。

用法：在 SSH 会话里运行本脚本（不要经 lc，直接在裸终端跑）：

    python3 scripts/ime_probe.py

两个阶段，每阶段：先切中文输入法（Shift / Ctrl+Space / Win+Space），
尝试输入 nihao 并上屏；再随便敲几个字母。按 q 退出当前阶段。

判读（看 hex 输出）：
  出现 e4/e5/e6/e7 开头的多字节序列  → 中文字节已到达服务端
      → 问题在 lc 全屏装配层，把输出发回来，查 lc
  只有 6e 69 68 61 6f（"nihao" 裸字母）→ IME 从未生效（切换键无效）
      → 客户端问题：Windows Terminal/ConPTY 与输入法的交互，lc 侧无解
  切输入法时完全无输出              → IME 切换本身被吞，同为客户端问题

两阶段差异：
  阶段1 = 普通 raw 模式（对照）
  阶段2 = raw + 鼠标上报模式（\x1b[?1000h 1003h 1006h，即 lc 全屏的实际模式）
  若阶段1能出中文、阶段2不能 → 鼠标模式触发，回 lc 查
"""
import os
import sys
import termios
import tty

MOUSE_ON = b"\x1b[?1000h\x1b[?1003h\x1b[?1006h"
MOUSE_OFF = b"\x1b[?1000l\x1b[?1003l\x1b[?1006l"


def read_burst(fd: int) -> bytes:
    seq = os.read(fd, 256)
    while True:
        import select
        r, _, _ = select.select([fd], [], [], 0.08)
        if not r:
            break
        seq += os.read(fd, 256)
    return seq


def phase(name: str, fd: int, mouse: bool) -> bool:
    print(f"\n=== 阶段{name}{'（已开鼠标上报模式）' if mouse else '（普通 raw 模式）'} ===")
    print("切中文输入法 → 输 nihao 上屏 → 再敲几个字母 → 按 q 结束本阶段\n")
    if mouse:
        os.write(fd, MOUSE_ON)
    n = 0
    try:
        while n < 8:
            seq = read_burst(fd)
            if not seq:
                continue
            if b"q" == seq or b"q" in seq.split():
                print("(q) 本阶段结束")
                return True
            hexes = " ".join(f"{b:02x}" for b in seq)
            cjk = any(b >= 0xE4 and b <= 0xE9 for b in seq)
            tag = "  ← 中文字节!" if cjk else ""
            print(f"  按键{n + 1}: {hexes}{tag}")
            sys.stdout.flush()
            n += 1
    finally:
        if mouse:
            os.write(fd, MOUSE_OFF)
    print("(本阶段按键次数已达上限)")
    return True


def main() -> None:
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        print("必须在真实终端里运行（当前 stdin 不是 tty）。")
        return
    print("IME 探针：两个阶段各输入一次中文，看哪些阶段字节能到达。")
    try:
        tty.setraw(fd)
        phase("1", fd, mouse=False)
        phase("2", fd, mouse=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n探针结束。把全部输出发回会话即可定谳。")


if __name__ == "__main__":
    main()

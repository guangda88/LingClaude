#!/usr/bin/env python3
"""按键字节探针 —— 定谳「换行键」问题的唯一权威手段。

在终端里运行本脚本，依次按：
  1. Esc 然后 Enter（分开按，1 秒内）
  2. Shift+Enter
  3. Ctrl+Enter
  4. 裸 Enter
每按一次打印实际到达应用的字节序列（hex）。连按 10 次或 Ctrl+C 退出。

判读：
  Esc+Enter 期望 1b 0d；Shift+Enter 若只打印 0d → 终端默认不区分
  （需 modifyOtherKeys/kitty 协议，或改用 Esc+Enter / /multi）。
"""
import sys, os, tty, termios, select

NAMES = {0x0D: "CR(Enter)", 0x0A: "LF(Ctrl+J)", 0x1B: "ESC", 0x03: "Ctrl+C"}


def read_burst(fd: int) -> bytes:
    """读一个按键 burst：首字节阻塞，后续 60ms 窗口内非阻塞收割。"""
    seq = os.read(fd, 256)
    while True:
        r, _, _ = select.select([fd], [], [], 0.06)
        if not r:
            break
        more = os.read(fd, 256)
        if not more:
            break
        seq += more
    return seq


def main() -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print("按键探针：依次按 Esc+Enter / Shift+Enter / Ctrl+Enter / 裸 Enter")
    print("（每按一次打印实际字节，10 次后自动退出）\n")
    n = 0
    try:
        tty.setraw(fd)
        while n < 10:
            seq = read_burst(fd)
            if not seq:
                continue
            if seq in (b"\x03",):
                print("^C 退出")
                break
            pretty = " ".join(
                f"{b:02x}({NAMES.get(b, chr(b) if 32 <= b < 127 else '?')})"
                for b in seq
            )
            print(f"按键 {n + 1}: {pretty}")
            sys.stdout.flush()
            n += 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n退出。")


if __name__ == "__main__":
    main()

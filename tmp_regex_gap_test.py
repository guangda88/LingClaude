"""审计用：验证 bash.py 词边界黑名单尾部 \\b 缺口（临时，验证后删除）"""
import re

SL = chr(47)  # slash
cases = [
    ("rm -rf " + SL, "rm -rf " + SL + " --no-preserve-root"),
    ("dd " + "if=", "dd " + "if=/dev/zero of=/dev/sda"),
    ("mk" + "fs", "mk" + "fs.ext4 /dev/sda"),
    ("su" + "do", "su" + "do reboot"),
    ("apt", "pytest --ca" + "pture=none -q"),
]
for needle, text in cases:
    old = bool(re.search(r"(?<![\w-])" + re.escape(needle.rstrip()) + r"\b", text))
    new = bool(re.search(r"(?<![\w-])" + re.escape(needle.rstrip()) + r"(?!\w)", text))
    flag = "  <<< GAP (old漏检, new命中)" if new and not old else ""
    print(f"rule={needle!r:14} old\\b={old!s:5} new(?!\\w)={new!s:5}{flag}")

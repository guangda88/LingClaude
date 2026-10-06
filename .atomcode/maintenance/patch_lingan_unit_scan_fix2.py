#!/usr/bin/env python3
"""灵安 secret_scan round-2 补丁: Environment= 行的熵值兜底让位 systemd_env 专规

背景: round-1 把 .service 纳入扫描后, 预先存在的 inline 熵值兜底会把
`Environment=LC_ALL=C.UTF-8` 这类良性赋值整行当高熵 token 误报 (entropy 类)。
专规 (systemd_env) 对该行形态有精确语义, 熵值兜底应整体让位:
- vendor 前缀/JWT 规则在熵值兜底之前执行, 不受影响 (sk-/ghp_/eyJ... 仍命中)
- 残余风险 (已接受并记录): Environment= 的变量名不含 KEYWORD 且无 vendor 形态的
  秘密不再被熵值兜底捕获 — 专规关键字表 9 词已覆盖常见命名
幂等: 锚点缺失或已打过则中止不动文件。
"""
import sys
from pathlib import Path

SS = Path("/home/ai/lingan/secret_scan.py")

OLD = (
    '    if not any(f[0] in ("field", "weak_field") for f in findings):\n'
    '        for tok in re.findall(r"[A-Za-z0-9_\\-.+/=]{16,}", line):\n'
)
NEW = (
    '    if not any(f[0] in ("field", "weak_field") for f in findings) and not (\n'
    '        SYSTEMD_ENV_LINE_RE.match(line)\n'
    '    ):\n'
    '        # Environment= 行由 systemd_env 专规负责, 熵值兜底让位\n'
    '        # (避免 LC_ALL=C.UTF-8 等良性赋值误报; vendor/JWT 规则不受影响)\n'
    '        for tok in re.findall(r"[A-Za-z0-9_\\-.+/=]{16,}", line):\n'
)

OLD_DOC = "     值为路径/$引用/URL/占位符时豁免; 多 kv 同行时掩码覆盖全部值防交叉泄漏\n"
NEW_DOC = (
    "     值为路径/$引用/URL/占位符时豁免; 多 kv 同行时掩码覆盖全部值防交叉泄漏\n"
    "   - 熵值兜底对 Environment= 行整体让位 (vendor/JWT 规则不受影响);\n"
    "     已接受残余风险: 变量名不含关键字且无 vendor 形态的秘密不再被熵值兜底捕获\n"
)


def sub_once(src: str, old: str, new: str, tag: str) -> str:
    n = src.count(old)
    assert n == 1, f"[{tag}] 锚点异常 count={n}"
    return src.replace(old, new, 1)


src = SS.read_text()
if "熵值兜底让位" in src:
    print("SKIP: round-2 已应用, 幂等跳过")
    sys.exit(0)
src = sub_once(src, OLD, NEW, "entropy-gate")
src = sub_once(src, OLD_DOC, NEW_DOC, "doc-entropy")
SS.write_text(src)
print(f"OK: {SS} round-2 已应用 ({len(src)} bytes)")

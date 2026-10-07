#!/usr/bin/env python3
"""safe_patch.py —— 补丁防呆三样（2026-10-07 固化）

来源：同日 3 次 heredoc 引号损坏 + 1 次锚点失配事故。
本会话防呆设计两次实战拦截：
  1. 锚点 0 命中拒绝写盘（^SESSION_FILE 未开 MULTILINE，保护性中止，文件分毫未动）
  2. 'headless': True 字典形与假设的关键字形失配被断言拦下

三样：
  ① 锚点唯一性断言——每个 old 必须恰好命中 1 次，0 或多次均拒绝写盘
  ② 原子替换——先写 .patched，成功后才覆盖原文件（替换前已有时间戳备份）
  ③ 写盘后 py_compile——失败自动回滚备份

用法：
    python3 safe_patch.py <target.py> <patches.json>

patches.json 格式：
    [{"old": "精确原文（含缩进）", "new": "替换文本", "why": "一句话说明"}]
"""
import json
import py_compile
import shutil
import sys
import tempfile
import time
from pathlib import Path


def safe_patch(target: str, patches: list[dict], verify: str | None = None) -> int:
    """对 target 应用 patches；verify 为可选的验证命令（默认 py_compile）。

    返回 0 成功；任何一步失败退出码非 0 且文件保持原状（或已回滚）。
    """
    src = Path(target)
    if not src.is_file():
        print(f"[safe_patch] FATAL: 目标不存在: {src}")
        return 2

    text = src.read_text(encoding="utf-8")

    # ① 锚点唯一性断言：先全量验证，任一失配即中止（写盘前）
    for i, p in enumerate(patches):
        old = p["old"]
        n = text.count(old)
        if n != 1:
            print(
                f"[safe_patch] ABORT: patch#{i} 锚点命中 {n} 次（要求恰好1次）"
                f" why={p.get('why', '?')!r} —— 文件未动"
            )
            return 3

    # 时间戳备份
    backup = src.with_suffix(src.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(src, backup)
    print(f"[safe_patch] 备份: {backup}")

    # ② 原子替换：临时文件 + os.replace
    patched = text
    for i, p in enumerate(patches):
        patched = patched.replace(p["old"], p["new"], 1)
        print(f"[safe_patch] patch#{i} 应用: {p.get('why', '')}")

    tmp = Path(tempfile.mktemp(prefix=src.name + ".patched", dir=str(src.parent)))
    tmp.write_text(patched, encoding="utf-8")

    # ③ 写盘后验证（默认 py_compile .py 文件），失败自动回滚
    is_py = src.suffix == ".py"
    if verify is None and is_py:
        verify = f"{sys.executable} -m py_compile {tmp}"

    if verify:
        import subprocess

        rc = subprocess.run(verify, shell=True, capture_output=True, text=True)
        if rc.returncode != 0:
            tmp.unlink(missing_ok=True)
            print(f"[safe_patch] VERIFY FAILED → 已回滚到 {backup}\n{rc.stderr[-800:]}")
            return 4
        print(f"[safe_patch] 验证通过: {verify}")

    tmp.replace(src)  # 原子落盘
    print(f"[safe_patch] OK: {len(patches)} 处补丁已落盘 {src}")
    return 0


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    patches = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    return safe_patch(sys.argv[1], patches)


if __name__ == "__main__":
    sys.exit(main())

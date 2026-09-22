#!/usr/bin/env python3
"""pytest 参数路径守卫——把「凭记忆猜测试文件名」挡在 pytest 启动之前。

背景（2026-09-23 会话复盘）：连续两次凭记忆拼测试文件名
（test_unification_checkpoint_rollout.py / test_unification.py，均不存在），
pytest 以 "no tests ran"（exit 5）静默收场，制造「已验证」假象。
本守卫校验 pytest 文件类参数真实存在，不存在即 fail-fast（exit 2），
并给出仓库内就近真名建议。

设计边界：
- 只校验「测试文件样式」的参数 token（test_*.py / *_test.py / 路径含 /tests/），
  其余 token（-k 表达式、输出路径、目录、选项值）一律放行——误伤率趋近零。
- 守卫自身崩溃时 fail-open（stderr 告警后放行）：这是防呆设施而非安全边界，
  宁可漏报，不可误伤正常测试流。
- 「显式判定文件不存在」必须 fail-closed——那正是本守卫存在的意义。
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

# 独立形态且「下一个 token 是它的值」的选项：其值不作为文件候选校验，
# 防止 -k "test_foo and not bar" 这类表达式、--junitxml 输出路径被误伤。
_VALUE_OPTS = {
    "-k", "--keyword", "-m", "--markexpr",
    "-o", "--override-ini", "-c", "--confcutdir", "--rootdir",
    "--junitxml", "--result-log", "--basetemp", "--basetemp-dir",
    "--json-report-file", "-p", "--maxfail", "-x", "--exitfirst",
    "-D", "--dest",
}

_PATH_TOKEN = re.compile(r"^[A-Za-z0-9_./\-]+$")


def _is_test_file_token(token: str) -> bool:
    """token 是否长得像一个测试文件路径参数。"""
    if not token.endswith(".py"):
        return False
    if not _PATH_TOKEN.match(token):
        return False
    base = token.rsplit("/", 1)[-1]
    return (
        base.startswith("test_")
        or base.endswith("_test.py")
        or "/tests/" in f"/{token}"
        or token.startswith("tests/")
    )


def _strip_value_tokens(args: list[str]) -> list[str]:
    """摘除值选项的值 token，避免表达式被当路径。"""
    kept: list[str] = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in _VALUE_OPTS:
            skip_next = True
            continue
        if "=" in a:  # option=value 形态整体放行
            continue
        kept.append(a)
    return kept


def _suggestions(repo_root: Path, missing: str) -> list[str]:
    """在 repo_root 下扫描 test_*.py，按核心词给就近真名建议（最多 5 条）。"""
    base = Path(missing).name
    stem = base[:-3] if base.endswith(".py") else base
    words = [w for w in stem.split("_") if len(w) >= 4 and w != "test"]
    if not words:
        words = [stem]
    hits: list[str] = []
    try:
        for p in repo_root.rglob("test_*.py"):
            rel = str(p.relative_to(repo_root))
            name = p.name
            if any(fnmatch.fnmatch(name, f"*{w}*") for w in words):
                hits.append(rel)
            if len(hits) >= 5:
                break
    except OSError:
        pass
    return hits


def main(argv: list[str]) -> int:
    # 逃生门：显式 GUARD_PYTEST_ARGS=0 跳过（守卫自测/特殊场景用）
    if os.environ.get("GUARD_PYTEST_ARGS", "1") == "0":
        return 0
    candidates = [a for a in _strip_value_tokens(argv) if _is_test_file_token(a)]
    missing = [a for a in candidates if not os.path.exists(a)]
    if not missing:
        return 0
    repo_root = Path(__file__).resolve().parent.parent
    for m in missing:
        print(f"[pytest-guard] 参数指向不存在的测试文件: {m}", file=sys.stderr)
        sug = _suggestions(repo_root, m)
        if sug:
            print("[pytest-guard] 仓内就近真名（按核心词匹配）:", file=sys.stderr)
            for s in sug:
                print(f"    {s}", file=sys.stderr)
        else:
            print("[pytest-guard] 仓内未找到核心词相近的测试文件", file=sys.stderr)
    print(
        "[pytest-guard] 拒绝启动 pytest（防 no tests ran 假绿）。\n"
        "  改用真实文件名，或目录参数（tests/），或 GUARD_PYTEST_ARGS=0 临时跳过。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # fail-open：守卫自身崩溃不拦正常流
        print(f"[pytest-guard] 守卫异常放行: {exc!r}", file=sys.stderr)
        sys.exit(0)

#!/usr/bin/env python3
"""CI 覆盖率门槛（SDT-lc-004 升格 2026-10-10）。

对三个核心模块的测试集强制覆盖率 ≥70%：
  lingclaude/model/task_router.py    基线 75%  tests/test_task_router.py
  lingclaude/core/context_window.py  基线 86%  tests/test_context_window.py
  lingclaude/core/data_flywheel.py   基线 79%  tests/test_data_flywheel.py

实现注记: coverage run --source 需接模块/包名，.py 文件路径会报
module-not-imported 且采数为 0（2026-10-10 两次踩坑实录）。故测量整包
lingclaude、报告用 --include 过滤三文件，fail-under 作用于过滤后合计。
pytest 退出码非零先行失败。运行后清理 .coverage。

阈值/模块清单变动须三处同步：本脚本、AGENTS.md SDT-lc-004 行、
data/coverage/baseline_20261010.md。

本地等价执行: python3 scripts/ci_coverage_gate.py
CI 挂载点: .github/workflows/ci.yml coverage job（必过，非观察期）。
"""
import subprocess
import sys

THRESHOLD = 70

MODULES = [
    ("*/model/task_router.py", "tests/test_task_router.py"),
    ("*/core/context_window.py", "tests/test_context_window.py"),
    ("*/core/data_flywheel.py", "tests/test_data_flywheel.py"),
]


def main() -> int:
    run_cmd = [
        sys.executable, "-m", "coverage", "run",
        "--source=lingclaude",
        "-m", "pytest",
        *[t for _, t in MODULES],
        "-q", "-p", "no:cacheprovider",
    ]
    print("+", " ".join(run_cmd), flush=True)
    rc = subprocess.call(run_cmd)
    if rc != 0:
        print(f"ERROR: pytest 退出码 {rc}（测试先于覆盖率失败）", file=sys.stderr)
        return rc
    rep = subprocess.call([
        sys.executable, "-m", "coverage", "report",
        "--include=" + ",".join(m for m, _ in MODULES),
        f"--fail-under={THRESHOLD}",
    ])
    subprocess.call([sys.executable, "-m", "coverage", "erase"])
    if rep != 0:
        print(f"ERROR: 覆盖率低于 {THRESHOLD}% 门槛", file=sys.stderr)
    return rep


if __name__ == "__main__":
    sys.exit(main())

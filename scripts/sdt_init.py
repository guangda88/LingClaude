#!/usr/bin/env python3
"""SDT 三工件脚手架 — Spec Kit 「specify/plan/tasks」思想的 lc 落地（2026-10-07 进化方案）。

用法:
    python scripts/sdt_init.py "任务名"            # 生成 docs/sdt/<date>-<slug>/ 三工件
    python scripts/sdt_init.py --list              # 列出现有任务包及收敛状态
    python scripts/sdt_init.py --status <dir>      # 查看指定任务包收敛状态

来源借鉴: github/spec-kit（MIT）流程思想, 不引入其 CLI（薄主干纪律）。
配套守卫: lingclaude/gov/guard/spec_converge_gate.py（completion 申报前反查本工具产出的工件）。

铁律校验: core/ 零 diff; 纯新增 scripts/ + docs/sdt/ 模板, 零第三方依赖, 删文件即回滚。
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

LC_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = LC_ROOT / "docs" / "sdt" / "templates"


def _slugify(name: str) -> str:
    """任务名 → 目录 slug: 保留中英文数字, 其余转 -。"""
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name).strip("-").lower()
    return slug[:40] or "task"


def create_package(task_name: str) -> Path:
    """生成一个 SDT 任务包（三工件从模板实例化）。"""
    if not TEMPLATE_DIR.exists():
        sys.exit(f"[sdt_init] 模板目录缺失: {TEMPLATE_DIR} — 先确认 docs/sdt/templates/ 已入库")

    date = datetime.date.today().isoformat()
    pkg = LC_ROOT / "docs" / "sdt" / f"{date}-{_slugify(task_name)}"
    if pkg.exists():
        sys.exit(f"[sdt_init] 任务包已存在: {pkg}")

    pkg.mkdir(parents=True)
    for tpl_name in ("spec.md", "plan.md", "tasks.md"):
        text = (TEMPLATE_DIR / tpl_name).read_text(encoding="utf-8")
        text = text.replace("{TASK_NAME}", task_name).replace("{DATE}", date)
        (pkg / tpl_name).write_text(text, encoding="utf-8")

    print(f"[sdt_init] 任务包已生成: {pkg}")
    print("  下一步: 填 spec.md 验收标准（可验证判据）→ plan.md 影响面 → tasks.md 执行清单")
    print("  完成申报前: python -m lingclaude.gov.guard.spec_converge_gate " + str(pkg))
    return pkg


def _checklist_stats(tasks_md: Path) -> tuple[int, int]:
    """返回 (未勾选数, 总数)。"""
    text = tasks_md.read_text(encoding="utf-8")
    total = len(re.findall(r"^\s*-\s\[[ xX]\]", text, re.M))
    done = len(re.findall(r"^\s*-\s\[[xX]\]", text, re.M))
    return total - done, total


def list_packages() -> None:
    root = LC_ROOT / "docs" / "sdt"
    if not root.exists():
        print("[sdt_init] 尚无任务包")
        return
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "templates"):
        tasks_md = pkg / "tasks.md"
        if tasks_md.exists():
            pending, total = _checklist_stats(tasks_md)
            state = "CONVERGED" if pending == 0 else f"IN_PROGRESS({pending}/{total} pending)"
        else:
            state = "MISSING tasks.md"
        print(f"  {pkg.name}: {state}")


def package_status(pkg_name: str) -> None:
    pkg = LC_ROOT / "docs" / "sdt" / pkg_name
    if not pkg.is_dir():
        sys.exit(f"[sdt_init] 任务包不存在: {pkg}")
    for f in ("spec.md", "plan.md", "tasks.md"):
        mark = "✓" if (pkg / f).exists() else "✗"
        print(f"  {mark} {f}")
    tasks_md = pkg / "tasks.md"
    if tasks_md.exists():
        pending, total = _checklist_stats(tasks_md)
        print(f"  tasks: {total - pending}/{total} done", "→ CONVERGED" if pending == 0 else "→ 未收敛")


def main() -> None:
    ap = argparse.ArgumentParser(description="SDT 三工件脚手架")
    ap.add_argument("name", nargs="?", help="任务名（生成任务包）")
    ap.add_argument("--list", action="store_true", help="列出任务包及收敛状态")
    ap.add_argument("--status", metavar="DIR", help="查看指定任务包状态")
    args = ap.parse_args()

    if args.list:
        list_packages()
    elif args.status:
        package_status(args.status)
    elif args.name:
        create_package(args.name)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

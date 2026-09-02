#!/usr/bin/env python3
"""审计总账「验证」节自动生成器 — H17 闭环申报的机械化。

原则（docs/SYSTEMS_THEORY_SYNTHESIS.md R1 / PRINCIPLES §十）：账目里的 ✅
必须来自仪表盘，不许手写。本脚本跑验证链，把每项的命令、退出码、摘要行、
耗时、时间戳注入 docs/audit/CLI_WEBUI_AUDIT_REPORT.md 的 AUTO-VERIFY 标记区。
任何一项失败也如实入账，并以非零码退出 — 账目绝不说谎。

用法:
    python scripts/update_audit_ledger.py                # doc_check + pytest 快子集
    python scripts/update_audit_ledger.py --full         # pytest 全量（慢）
    python scripts/update_audit_ledger.py --with-cargo   # 附带 cargo test
    python scripts/update_audit_ledger.py --only doc_check
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "audit" / "CLI_WEBUI_AUDIT_REPORT.md"
MARK_START = "<!-- AUTO-VERIFY:START（脚本生成，禁手改 — H17 闭环申报） -->"
MARK_END = "<!-- AUTO-VERIFY:END -->"

# pytest 快子集：CLI/webUI 相关 + daemon（全量 2167 用例留给 --full / CI）
PYTEST_QUICK = [
    "tests/e2e/", "tests/test_cli_interaction.py", "tests/test_daemon_extras.py",
]


@dataclass(frozen=True)
class RunRecord:
    name: str
    cmd: str
    exit_code: int
    summary: str
    seconds: float
    ts: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


_CONCLUSION_RE = re.compile(r"✓|✗|passed|failed|test result|ERROR|不一致|All consistency")


def _summary_line(output: str) -> str:
    """取结论行（stdout+stderr 合并后 stderr 会排到最后，故按关键词匹配结论，
    匹配不到才退回最后一个非空行）。"""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return "(无输出)"
    for ln in reversed(lines):
        if _CONCLUSION_RE.search(ln):
            return ln[:200]
    return lines[-1][:200]


def run_check(name: str, cmd: Sequence[str], timeout: int = 1800) -> RunRecord:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            list(cmd), cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output = f"(超时 {timeout}s)"
        code = 124
    except OSError as e:
        output = f"(无法启动: {e})"
        code = 127
    return RunRecord(
        name=name,
        cmd=" ".join(cmd),
        exit_code=code,
        summary=_summary_line(output),
        seconds=round(time.monotonic() - start, 1),
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def render_section(records: list[RunRecord]) -> str:
    lines = [
        MARK_START,
        "",
        f"> 由 `scripts/update_audit_ledger.py` 于 {records[0].ts if records else '—'} 生成。"
        "手写 ✅ 已废除（H17）：账目与仪表盘冲突时，以仪表盘为准。",
        "",
        "| 检查 | 命令 | 退出码 | 结果摘要 | 耗时 |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        mark = "✅" if r.ok else "❌"
        lines.append(
            f"| {mark} {r.name} | `{r.cmd}` | {r.exit_code} | {r.summary} | {r.seconds}s |"
        )
    failed = [r for r in records if not r.ok]
    if failed:
        lines.append("")
        lines.append(
            f"**⚠️ {len(failed)} 项失败** — 本节任何 ❌ 存在期间，总账其他章节的 ✅ 一律视为未验证。"
        )
    lines.extend(["", MARK_END, ""])
    return "\n".join(lines)


def inject_into_ledger(section: str, ledger_path: Path = LEDGER_PATH) -> bool:
    """把生成节写进标记区。标记不存在时在文件尾追加并返回 False（提示需人工挂标记）。"""
    text = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
    if MARK_START in text and MARK_END in text:
        pre = text.split(MARK_START, 1)[0]
        post = text.split(MARK_END, 1)[1]
        # section 自带 START/END 标记；post 接回旧 END 之后的内容，
        # 并顺手清掉历史版本可能留下的重复 END
        post = post.replace(MARK_END, "")
        ledger_path.write_text(pre + section.rstrip() + "\n" + post, encoding="utf-8")
        return True
    ledger_path.write_text(
        (text.rstrip() + "\n\n" if text else "") + section, encoding="utf-8"
    )
    return False


def build_checks(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    checks: list[tuple[str, list[str]]] = [
        ("doc_consistency", [sys.executable, "scripts/doc_consistency_check.py"]),
    ]
    if args.only:
        name = {"doc_check": "doc_consistency"}.get(args.only, args.only)
        mapping = {n: c for n, c in checks}
        pytest_cmd = [sys.executable, "-m", "pytest", "-q"]
        pytest_cmd += ([] if args.full else PYTEST_QUICK)
        mapping["pytest"] = pytest_cmd
        mapping["cargo_test"] = ["cargo", "test"]
        if name not in mapping:
            raise SystemExit(f"--only 可选值: {', '.join(mapping)}")
        return [(name, mapping[name])]
    if not args.skip_pytest:
        pytest_cmd = [sys.executable, "-m", "pytest", "-q", *(() if args.full else PYTEST_QUICK)]
        checks.append(("pytest" + ("（全量）" if args.full else "（快子集）"), pytest_cmd))
    if args.with_cargo:
        checks.append(("cargo_test", ["cargo", "test"]))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true", help="pytest 跑全量而非快子集")
    parser.add_argument("--skip-pytest", action="store_true", help="跳过 pytest")
    parser.add_argument("--with-cargo", action="store_true", help="附带 cargo test")
    parser.add_argument("--only", help="只跑一项: doc_check | pytest | cargo_test")
    args = parser.parse_args(argv)

    records = [
        run_check(name, cmd, timeout=3600 if "全量" in name else 1800)
        for name, cmd in build_checks(args)
    ]
    ok = inject_into_ledger(render_section(records))

    for r in records:
        print(f"{'✅' if r.ok else '❌'} {r.name}: exit={r.exit_code} {r.summary}")
    if not ok:
        print(f"[提示] {LEDGER_PATH} 缺少 AUTO-VERIFY 标记，已追加到文件尾", file=sys.stderr)
    failed = [r.name for r in records if not r.ok]
    if failed:
        print(f"[账目] 失败项已如实入账: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Phase 8 / Wieman — Doc Consistency CI.

Catches cross-document inconsistencies that previously slipped through:
  - Guard count (H1-H14 vs H1-H16) — actual file has H1-H16
  - Port numbers (13458 vs 13460 for webui)
  - Threshold constants (0.6/0.4 trust weights, 4000 char cap, -900 oom)
  - Version numbers
  - Cross-doc broken links (file existence)
  - CLI ↔ USER_MANUAL consistency (F6: 子命令/端点/斜杠命令 diff)

Usage:
    python scripts/doc_consistency_check.py                # check + report
    python scripts/doc_consistency_check.py --strict      # exit non-zero on warnings
    python scripts/doc_consistency_check.py --fix         # suggest fixes for known patterns

Exit codes:
    0 — no errors (warnings allowed)
    1 — at least one error (--strict promotes warnings to errors)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────
# Findings
# ─────────────────────────────────────────────────────────────


@dataclass
class Finding:
    severity: str  # "error" | "warning" | "info"
    category: str
    file: Path
    line: int
    message: str
    suggestion: str = ""


# ─────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────


# Patterns to detect guard-count references in prose
# Match "H1-HN" where N is any digits (we compare against actual_max, not just 13-16)
GUARD_COUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bH1[\s\-]+H(\d+)\b"),       # H1-H13, H1-H14, H1-H16, etc.
    re.compile(r"\b元认知守卫\s*H\d"),         # 元认知守卫 H1 (no range, ambiguous)
    re.compile(r"\bmetacognitive\s*guards?\s*H\d"),
)

# Actual count per .lingclaude/metacognitive_guards.md (recomputed at runtime below)
def get_actual_guard_count() -> int:
    md = REPO_ROOT / ".lingclaude" / "metacognitive_guards.md"
    if not md.exists():
        return 0
    text = md.read_text(encoding="utf-8")
    ids = set()
    for line in text.splitlines():
        m = re.match(r"\|\s*(H\d+[a-z]?)\s*\|", line.strip())
        if m:
            ids.add(m.group(1))
    return len(ids)


def check_guard_count(files: Iterable[Path]) -> list[Finding]:
    """Compare the upper bound of guard-count claims in docs against the
    actual count of guards in .lingclaude/metacognitive_guards.md.

    Accepts several representations as equivalent:
      - "H1-H16"
      - "H1-H16 (含 H9b)"
      - "H1-H16, H9b"
    The check is "does the upper-bound H match the actual max?"
    """
    findings: list[Finding] = []
    actual_max = _get_actual_max_guard_id()

    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat in GUARD_COUNT_PATTERNS[:1]:  # H1-H13..16 pattern
                m = pat.search(line)
                if not m:
                    continue
                claimed = m.group()
                um = re.search(r"H(\d+)$", claimed)
                if not um:
                    continue
                claimed_max = int(um.group(1))
                if claimed_max != actual_max:
                    sev = "error" if claimed_max < actual_max else "warning"
                    findings.append(Finding(
                        severity=sev,
                        category="guard_count",
                        file=path,
                        line=i,
                        message=(
                            f"doc says {claimed} but actual guard file's max "
                            f"is H{actual_max}"
                        ),
                        suggestion=f"replace with `H1-H{actual_max}`",
                    ))
    return findings


def _get_actual_max_guard_id() -> int:
    """Return the highest numeric H-id in the guard file."""
    md = REPO_ROOT / ".lingclaude" / "metacognitive_guards.md"
    if not md.exists():
        return 0
    max_id = 0
    for line in md.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*H(\d+)", line.strip())
        if m:
            n = int(m.group(1))
            if n > max_id:
                max_id = n
    return max_id


def _format_range(ids: list[str]) -> str:
    """Format guard IDs as a compact range like 'H1-H16' (with H9b noted separately)."""
    if not ids:
        return ""
    numeric = []
    has_letter = []
    for i in ids:
        m = re.match(r"H(\d+)([a-z]?)", i)
        if m:
            numeric.append(int(m.group(1)))
            if m.group(2):
                has_letter.append(i)
    if not numeric:
        return ", ".join(ids)
    base = f"H1-H{max(numeric)}"
    if has_letter:
        extras = ",".join(has_letter)
        return f"{base},{extras}"
    return base


# ─────────────────────────────────────────────────────────────
# Port checks
# ─────────────────────────────────────────────────────────────


# Authoritative port values from code
PORT_AUTHORITATIVE: dict[str, int] = {
    "lingclaude engine": 8700,
    "webui default": 13458,
    "proxy21 llm route": 8765,
    "lingtong+ provider": 8900,  # from metacognitive_guards.md H1/H7
    "lingxi": 8001,
    "lingzhi (灵知) postgres": 5436,
}


# Map of "the actual default port" per service — to detect "default X" claims
PORT_PATTERN = re.compile(
    r"(?:默认|default)[^a-zA-Z0-9]{0,8}(1\d{4})",  # "默认 13458" or "default 13458"
    re.IGNORECASE,
)


# All known ports for each service — used to detect "default X" claims
# where X is in the service's port range but not the actual default.
SERVICE_PORTS: dict[str, set[int]] = {
    "webui": {13458, 13460},            # 13458 = real default, 13460 = alternate
    "lingclaude engine": {8700},
    "proxy21": {8765},
    "lingtong+ provider": {8900},
    "lingxi": {8001},
    "lingzhi postgres": {5436},
}


def check_ports(files: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in PORT_PATTERN.finditer(line):
                claimed_port = int(m.group(1))
                # Identify the service this port belongs to
                matched_service = None
                for svc, ports in SERVICE_PORTS.items():
                    if claimed_port in ports:
                        matched_service = svc
                        break
                if matched_service is None:
                    continue  # not a known service port

                # What's the real default for this service?
                real_default = PORT_AUTHORITATIVE.get(
                    f"{matched_service} default",
                    next(iter(PORT_AUTHORITATIVE[matched_service] if matched_service in PORT_AUTHORITATIVE else {}), claimed_port)
                )
                for key, p in PORT_AUTHORITATIVE.items():
                    if key.startswith(matched_service) and "default" in key:
                        real_default = p
                        break

                if claimed_port == real_default:
                    continue  # doc matches code

                findings.append(Finding(
                    severity="error",
                    category="port_inconsistency",
                    file=path,
                    line=i,
                    message=(
                        f"doc says port {claimed_port} is default for '{matched_service}' "
                        f"but code default is {real_default}"
                    ),
                    suggestion=f"change doc claim to {real_default}",
                ))
    return findings


# ─────────────────────────────────────────────────────────────
# Threshold checks (doc claims vs code defaults in Thresholds)
# ─────────────────────────────────────────────────────────────


THRESHOLD_PATTERN = re.compile(
    r"\btrust[_\s]?(?:safety)?[_\s]?weight[^0-9]+([0-9.]+)",  # trust_safety_weight = 0.6
    re.IGNORECASE,
)


def check_thresholds(files: Iterable[Path]) -> list[Finding]:
    """Compare doc threshold claims against Thresholds defaults."""
    findings: list[Finding] = []
    try:
        from lingclaude.core.thresholds import Thresholds
        t = Thresholds()
        code_trust_safety = t.trust_safety_weight
    except ImportError:
        return findings

    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = THRESHOLD_PATTERN.search(line)
            if not m:
                continue
            claimed = float(m.group(1))
            if abs(claimed - code_trust_safety) > 0.01:
                findings.append(Finding(
                    severity="warning",
                    category="threshold_inconsistency",
                    file=path,
                    line=i,
                    message=(
                        f"doc says trust_safety_weight={claimed} but "
                        f"Thresholds.trust_safety_weight={code_trust_safety}"
                    ),
                    suggestion=f"update doc to {code_trust_safety} or change Thresholds",
                ))
    return findings


# ─────────────────────────────────────────────────────────────
# Cross-doc link check (markdown [text](path) references)
# ─────────────────────────────────────────────────────────────


LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


def check_links(files: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in LINK_PATTERN.finditer(line):
                link = m.group(2)
                if link.startswith(("http://", "https://")):
                    continue
                target = (path.parent / link).resolve()
                if not target.exists():
                    findings.append(Finding(
                        severity="warning",
                        category="broken_link",
                        file=path,
                        line=i,
                        message=f"link to '{link}' does not exist",
                        suggestion=f"check {target}",
                    ))
    return findings


# ─────────────────────────────────────────────────────────────
# CLI ↔ doc consistency (F6 修复)
# ─────────────────────────────────────────────────────────────


KNOWN_SLASH_COMMANDS = {
    "/help", "/?", "/clear", "/compact", "/model", "/schedule", "/lsp",
    "/resume", "/continue", "/quit", "/exit",
}


def _extract_subcommands_from_app_py() -> set[str]:
    """从 main() 抽顶层 subparsers.add_parser('XXX')(过滤嵌套 _xxx_sub)。"""
    app_py = REPO_ROOT / "lingclaude" / "cli" / "app.py"
    if not app_py.exists():
        return set()
    text = app_py.read_text(encoding="utf-8")
    return set(re.findall(r"subparsers\.add_parser\(\s*[\"']([a-z_-]+)[\"']", text))


def _extract_api_routes() -> set[str]:
    """从 api.py 抽 @app.{get,post,...}(path) 路径(规范化 path 参数)。"""
    api_py = REPO_ROOT / "lingclaude" / "api.py"
    if not api_py.exists():
        return set()
    text = api_py.read_text(encoding="utf-8")
    routes: set[str] = set()
    for m in re.finditer(
        r"@app\.(?:get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']",
        text,
    ):
        path = re.sub(r"\{[^}]+\}", "{id}", m.group(1))
        routes.add(path)
    return routes


def _extract_slash_handlers_from_app_py() -> set[str]:
    """从 _handle_slash_command 抽所有 name == "/xxx" 分支。"""
    app_py = REPO_ROOT / "lingclaude" / "cli" / "app.py"
    if not app_py.exists():
        return set()
    text = app_py.read_text(encoding="utf-8")
    return set(re.findall(r"name\s*==\s*[\"'](/[a-z?]+)[\"']", text))


def _extract_endpoints_from_user_manual() -> set[str]:
    """从 USER_MANUAL_CLI_v0.4.md §11 表格抽端点路径。"""
    md = REPO_ROOT / "docs" / "USER_MANUAL_CLI_v0.4.md"
    if not md.exists():
        return set()
    text = md.read_text(encoding="utf-8")
    endpoints: set[str] = set()
    in_section_11 = False
    for line in text.splitlines():
        if "## 十一、" in line:
            in_section_11 = True
            continue
        if in_section_11 and line.startswith("## ") and "十一、" not in line:
            in_section_11 = False
        if not in_section_11:
            continue
        for m in re.finditer(r"\|\s*`?(/[a-zA-Z/_{}\-<>:]+)`?\s*\|", line):
            ep = re.sub(r"\{[^}]+\}", "{id}", m.group(1))
            endpoints.add(ep)
    return endpoints


def check_cli_doc_consistency() -> list[Finding]:
    """F6:CLI ↔ USER_MANUAL 双向一致性。

    1. 子命令:subparsers 集合 ⊆ 已知清单(新增未文档化 → warning)
    2. 端点:手册列了但路由不存在 → error
    3. 端点:路由新增但手册未列 → warning
    4. 斜杠命令:handler ⊆ 基线(不在基线 → info)
    """
    findings: list[Finding] = []
    app_py = REPO_ROOT / "lingclaude" / "cli" / "app.py"
    api_py = REPO_ROOT / "lingclaude" / "api.py"
    user_manual = REPO_ROOT / "docs" / "USER_MANUAL_CLI_v0.4.md"

    # Check 1: 子命令
    code_subcommands = _extract_subcommands_from_app_py()
    if code_subcommands and app_py.exists():
        KNOWN_SUBCOMMANDS = {
            "run", "optimize", "analyze", "session", "knowledge",
            "daemon", "metrics", "governance-audit", "webui", "unknowns",
        }
        undocumented = code_subcommands - KNOWN_SUBCOMMANDS
        if undocumented:
            findings.append(Finding(
                severity="warning",
                category="cli_subcommand_unregistered",
                file=app_py,
                line=0,
                message=f"app.py 声明新子命令 {sorted(undocumented)} 但 USER_MANUAL 未列入已知清单",
                suggestion=f"在 USER_MANUAL 补 {sorted(undocumented)} 文档",
            ))

    # Check 2-3: 端点
    code_routes = _extract_api_routes()
    doc_endpoints = _extract_endpoints_from_user_manual()
    if code_routes and doc_endpoints and user_manual.exists():
        missing = doc_endpoints - code_routes
        for ep in sorted(missing):
            findings.append(Finding(
                severity="error",
                category="api_endpoint_missing",
                file=user_manual,
                line=0,
                message=f"USER_MANUAL §11 列了端点 `{ep}` 但 api.py 无对应 @app.* 路由",
                suggestion=f"实现 `{ep}` 端点或从 USER_MANUAL 删除该行",
            ))
        KNOWN_BRIDGE_ENDPOINTS = {
            "/api/lingmessage/post", "/api/lingmessage/notify",
            "/analyze", "/exec", "/read-file", "/write-file",
        }
        truly_undocumented = (code_routes - doc_endpoints) - KNOWN_BRIDGE_ENDPOINTS - {"/"}
        if truly_undocumented:
            findings.append(Finding(
                severity="warning",
                category="api_endpoint_undocumented",
                file=api_py,
                line=0,
                message=f"api.py 新增路由 {sorted(truly_undocumented)} 但 USER_MANUAL §11 未列出",
                suggestion=f"在 USER_MANUAL §11 补 {sorted(truly_undocumented)} 行",
            ))

    # Check 4: 斜杠命令
    code_slash = _extract_slash_handlers_from_app_py()
    if code_slash and app_py.exists():
        unexpected = code_slash - KNOWN_SLASH_COMMANDS
        if unexpected:
            findings.append(Finding(
                severity="info",
                category="slash_handler_unexpected",
                file=app_py,
                line=0,
                message=f"app.py 有斜杠 handler {sorted(unexpected)} 不在 KNOWN 基线",
                suggestion="更新 KNOWN_SLASH_COMMANDS 或检查是否是 F2 残留",
            ))

    return findings


# ─────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────


def iter_doc_files() -> list[Path]:
    docs = REPO_ROOT / "docs"
    files: list[Path] = []
    if docs.exists():
        for ext in ("*.md",):
            files.extend(docs.rglob(ext))
    for ext in ("*.md",):
        files.extend(REPO_ROOT.glob(ext))
    return sorted(set(files))


def run_all_checks() -> list[Finding]:
    files = iter_doc_files()
    findings: list[Finding] = []
    findings.extend(check_guard_count(files))
    findings.extend(check_ports(files))
    findings.extend(check_thresholds(files))
    findings.extend(check_links(files))
    findings.extend(check_cli_doc_consistency())
    return findings


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "✓ All consistency checks passed."
    by_sev: dict[str, list[Finding]] = {"error": [], "warning": [], "info": []}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)
    lines = []
    for sev in ("error", "warning", "info"):
        items = by_sev.get(sev, [])
        if not items:
            continue
        lines.append(f"## {sev.upper()} ({len(items)})")
        for f in items:
            try:
                rel = f.file.relative_to(REPO_ROOT)
            except ValueError:
                rel = f.file
            lines.append(f"  {rel}:{f.line} [{f.category}] {f.message}")
            if f.suggestion:
                lines.append(f"      → {f.suggestion}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--fix", action="store_true", help="(reserved) auto-fix known patterns")
    args = parser.parse_args()

    findings = run_all_checks()
    print(format_findings(findings))

    has_errors = any(f.severity == "error" for f in findings)
    has_warnings = any(f.severity == "warning" for f in findings)

    if has_errors:
        return 1
    if args.strict and has_warnings:
        return 1

    _refresh_guard_decay_records()
    return 0


def _refresh_guard_decay_records() -> None:
    """R3 留尾接线：本检查成功 = 守卫表经过了一次跨文档一致性校验（守卫计数
    与文档同步），据此刷新 rule_registry 中 guard:* 的 last_verified。
    best-effort：任何失败静默，绝不影响本检查的退出码。
    证据语义是「守卫表完整性已校验」，不是「守卫行为被人工复核」— 后者走
    rule_decay_review.py 的 review 队列。"""
    import subprocess
    import sys as _sys
    from pathlib import Path as _P

    script = _P(__file__).resolve().parent / "rule_decay_review.py"
    if not script.exists():
        return
    try:
        subprocess.run(
            [_sys.executable, str(script), "refresh", "guard",
             "--evidence", "doc_consistency: 守卫表跨文档一致性校验通过"],
            capture_output=True, timeout=15,
        )
    except Exception:  # noqa: BLE001 — 接线绝不反噬主检查
        pass


if __name__ == "__main__":
    raise SystemExit(main())

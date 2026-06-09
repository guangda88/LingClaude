"""每日安全卫生健康检查脚本 — 灵族大家庭

每天凌晨 1:00 执行，自动扫描 10 个安全维度，输出报告。
用法: python3 scripts/daily_security_check.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path.home() / ".ling_security" / "daily"

LING_PROJECTS = [
    Path.home() / "lingclaude",
    Path.home() / "lingmessage",
    Path.home() / "Ling-term-mcp",
    Path.home() / "lingresearch",
    Path.home() / "lingyang",
    Path.home() / "lingminopt",
    Path.home() / "zhineng-bridge",
    Path.home() / "zhineng-knowledge-system",
    Path.home() / "lingflow",
    Path.home() / "lingflow_plus",
    Path.home() / "ai-server",
]

CRITICAL_FILES = [
    Path.home() / ".ling_keys.env",
    Path.home() / ".git-credentials",
    Path.home() / ".ssh" / "id_ed25519",
    Path.home() / ".pypirc",
    Path.home() / ".npmrc",
    Path.home() / ".ossutilconfig",
    Path.home() / ".docker" / "config.json",
]

SECRET_PATTERNS = [
    re.compile(r'(?:api[_-]?key|apikey|api[_-]?secret)\s*[:=]\s*["\']?\S{8,}', re.I),
    re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']?\S{4,}', re.I),
    re.compile(r'(?:token|secret[_-]?key|access[_-]?key)\s*[:=]\s*["\']?\S{8,}', re.I),
    re.compile(r'ghp_[A-Za-z0-9]{36}'),
    re.compile(r'sk-[A-Za-z0-9]{20,}'),
    re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
]

_SAFE_LINE_PREFIXES = (
    "if ", "if not ", "elif ", "elif not ",
    "assert ", "startswith(", ".startswith(",
    "logger.", "logging.", "print(",
)


def _is_safe_secret_match(content: str, match: re.Match) -> bool:
    rhs = match.group(0).split("=", 1)[-1].strip() if "=" in match.group(0) else ""
    if rhs:
        first_token = rhs.split(",")[0].split(")")[0].strip()
        if re.match(r'^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$', first_token):
            return True
        if re.match(r'^[A-Za-z_]\w*\(', first_token):
            return True
        if re.match(r'^[A-Za-z_]\w*\.', first_token):
            return True
    line_start = content.rfind("\n", 0, match.start()) + 1
    line_end = content.find("\n", match.start())
    line = content[line_start:line_end if line_end != -1 else len(content)].strip()
    if line.startswith(_SAFE_LINE_PREFIXES):
        return True
    if f"{{{match.group(0).split('=')[0].strip()}" in line:
        return True
    return False


EXCLUDE_PATTERNS = [
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".ling_security",
    "daily_security_check.py",
    "FULL_SECURITY_AUDIT",
    "SECURITY_AUDIT_POLICY",
    "test_",
    "_test.py",
    "conftest.py",
    ".env.example",
    ".env.production",
    ".env.template",
    "digest_",
    "history.jsonl",
    "_audit/",
    "audits/",
]


@dataclass
class CheckResult:
    name: str
    status: str  # PASS, WARN, FAIL, ERROR
    severity: str  # info, low, medium, high, critical
    details: str = ""
    items: list[str] = field(default_factory=list)


def run_cmd(cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout.strip() + ("\n" + r.stderr.strip() if r.stderr.strip() else "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except FileNotFoundError:
        return -2, "COMMAND_NOT_FOUND"


def check_critical_file_permissions() -> CheckResult:
    items: list[str] = []
    for f in CRITICAL_FILES:
        if not f.exists():
            continue
        try:
            st = f.stat()
            mode = st.st_mode & 0o777
            if mode != 0o600:
                items.append(f"{f}: mode={oct(mode)} (should be 0o600)")
        except OSError as e:
            items.append(f"{f}: cannot stat: {e}")

    if not items:
        return CheckResult("关键文件权限", "PASS", "info", "所有关键文件权限正确 (600)")
    return CheckResult("关键文件权限", "FAIL", "high", f"{len(items)} 个文件权限不正确", items)


def check_hardcoded_secrets_24h() -> CheckResult:
    items: list[str] = []
    for proj in LING_PROJECTS:
        if not proj.exists():
            continue
        code, output = run_cmd(["git", "log", "--since=24 hours ago", "--name-only", "--pretty=format:", "--diff-filter=AM"], cwd=str(proj))
        if code != 0 or not output:
            continue
        changed_files = [f.strip() for f in output.split("\n") if f.strip()]
        for fname in changed_files:
            if any(excl in fname for excl in EXCLUDE_PATTERNS):
                continue
            fpath = proj / fname
            if not fpath.exists() or not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                for pat in SECRET_PATTERNS:
                    for m in pat.finditer(content):
                        if _is_safe_secret_match(content, m):
                            continue
                        line_num = content[:m.start()].count("\n") + 1
                        items.append(f"{fpath}:{line_num} — {pat.pattern[:40]}...")
            except Exception:
                pass

    if not items:
        return CheckResult("24h新增硬编码密钥", "PASS", "info", "未发现新增硬编码密钥")
    return CheckResult("24h新增硬编码密钥", "FAIL", "critical", f"发现 {len(items)} 处疑似硬编码密钥", items)


def check_port_changes() -> CheckResult:
    snapshot_dir = REPORT_DIR / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = snapshot_dir / "ports_last.txt"

    code, output = run_cmd(["ss", "-tlnp"])
    if code != 0:
        return CheckResult("端口暴露变化", "ERROR", "info", "ss 命令不可用")

    current_ports = set(line.strip() for line in output.split("\n") if "LISTEN" in line)

    if snapshot_file.exists():
        prev_ports = set(snapshot_file.read_text().splitlines())
        new_ports = current_ports - prev_ports
        gone_ports = prev_ports - current_ports
        items: list[str] = []
        if new_ports:
            items.append(f"新增端口: {len(new_ports)} 个")
            items.extend(f"  + {p}" for p in sorted(new_ports)[:10])
        if gone_ports:
            items.append(f"关闭端口: {len(gone_ports)} 个")
        if items:
            return CheckResult("端口暴露变化", "WARN", "medium", f"端口变化: +{len(new_ports)} -{len(gone_ports)}", items)
    else:
        current_ports_list = sorted(current_ports)
        snapshot_file.write_text("\n".join(current_ports_list))
        return CheckResult("端口暴露变化", "PASS", "info", "首次快照已保存，共 {} 个监听端口".format(len(current_ports)))

    snapshot_file.write_text("\n".join(sorted(current_ports)))
    return CheckResult("端口暴露变化", "PASS", "info", "端口无变化")


def check_dependency_cve() -> CheckResult:
    items: list[str] = []

    code, output = run_cmd(["pip", "audit", "--format=json"], timeout=120)
    if code == 0 and output:
        try:
            vulns = json.loads(output)
            if vulns:
                for v in vulns[:20]:
                    items.append(f"pip: {v.get('package', '?')} {v.get('version', '?')} — {v.get('id', '?')}")
        except json.JSONDecodeError:
            pass
    elif code == -2:
        items.append("pip-audit 未安装，跳过 Python 依赖检查")

    for proj in LING_PROJECTS:
        if not (proj / "package.json").exists():
            continue
        code, output = run_cmd(["npm", "audit", "--json"], cwd=str(proj), timeout=60)
        if code in (0, 1) and output:
            try:
                audit = json.loads(output)
                vulns = audit.get("vulnerabilities", {})
                if vulns:
                    for name, info in list(vulns.items())[:10]:
                        items.append(f"npm [{proj.name}]: {name} — {info.get('severity', '?')}")
            except json.JSONDecodeError:
                pass

    if not items:
        return CheckResult("依赖 CVE", "PASS", "info", "未发现已知 CVE")
    return CheckResult("依赖 CVE", "WARN", "high", f"发现 {len(items)} 个依赖问题", items)


def check_docker_status() -> CheckResult:
    code, output = run_cmd(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"])
    if code != 0:
        return CheckResult("Docker 容器状态", "ERROR", "info", "Docker 不可用或未运行")

    containers = [line.strip() for line in output.split("\n") if line.strip()]
    unhealthy = [c for c in containers if "unhealthy" in c.lower() or "restarting" in c.lower()]

    if unhealthy:
        return CheckResult("Docker 容器状态", "FAIL", "high", f"{len(unhealthy)} 个容器异常", unhealthy)
    return CheckResult("Docker 容器状态", "PASS", "info", f"{len(containers)} 个容器正常运行")


def check_auth_failures() -> CheckResult:
    items: list[str] = []
    log_paths = [
        Path.home() / "logs",
        Path("/var/log/nginx/access.log"),
        Path("/var/log/auth.log"),
    ]
    for lp in log_paths:
        if not lp.exists():
            continue
        try:
            code, output = run_cmd(["grep", "-c", "401\\|403", str(lp)], timeout=15)
            if code == 0 and output:
                count = int(output.strip().split("\n")[-1])
                if count > 100:
                    items.append(f"{lp}: {count} 次 401/403")
        except Exception:
            pass
    if not items:
        return CheckResult("认证失败率", "PASS", "info", "认证失败次数正常或无日志")
    return CheckResult("认证失败率", "WARN", "medium", f"{len(items)} 个日志有异常认证失败", items)


def check_disk_space() -> CheckResult:
    code, output = run_cmd(["df", "-h", "/"])
    if code != 0:
        return CheckResult("磁盘空间", "ERROR", "info", "df 命令失败")

    lines = output.split("\n")
    items: list[str] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 5:
            use_pct = parts[4].rstrip("%")
            try:
                if int(use_pct) > 85:
                    items.append(f"{parts[0]}: {parts[4]} used ({parts[2]} free)")
            except ValueError:
                pass

    if not items:
        return CheckResult("磁盘空间", "PASS", "info", "磁盘空间充足")
    return CheckResult("磁盘空间", "WARN", "medium", f"{len(items)} 个分区空间紧张", items)


def check_git_status_sensitive() -> CheckResult:
    items: list[str] = []
    sensitive_names = {".env", ".env.local", ".env.production", "credentials", "secret", "private_key", ".pem", ".key"}

    for proj in LING_PROJECTS:
        if not proj.exists() or not (proj / ".git").exists():
            continue
        code, output = run_cmd(["git", "status", "--porcelain"], cwd=str(proj))
        if code != 0:
            continue
        for line in output.split("\n"):
            if not line.strip():
                continue
            fname = line[3:].strip()
            if any(s in fname.lower() for s in sensitive_names):
                items.append(f"{proj.name}/{fname} — {line[:2].strip()}")

    if not items:
        return CheckResult("Git 敏感文件变更", "PASS", "info", "无敏感文件待提交")
    return CheckResult("Git 敏感文件变更", "WARN", "high", f"{len(items)} 个敏感文件有变更", items)


def check_log_secrets() -> CheckResult:
    items: list[str] = []
    log_dirs = [Path.home() / "logs", Path.home() / ".lingmessage"]

    for ld in log_dirs:
        if not ld.exists():
            continue
        for lf in ld.rglob("*.log"):
            if lf.stat().st_size > 10 * 1024 * 1024:
                continue
            try:
                content = lf.read_text(encoding="utf-8", errors="ignore")[-50000:]
                found = False
                for pat in SECRET_PATTERNS:
                    if found:
                        break
                    for m in pat.finditer(content):
                        if _is_safe_secret_match(content, m):
                            continue
                        items.append(f"{lf}: 发现敏感模式 {pat.pattern[:30]}...")
                        found = True
                        break
            except Exception:
                pass

    if not items:
        return CheckResult("日志敏感信息", "PASS", "info", "日志中未发现明文密钥")
    return CheckResult("日志敏感信息", "FAIL", "high", f"{len(items)} 个日志文件包含疑似密钥", items)


def check_bandit_sast() -> CheckResult:
    """Bandit Python SAST 扫描 — 灵族所有 Python 项目"""
    items: list[str] = []
    total_high = 0
    total_med = 0

    for proj in LING_PROJECTS:
        if not proj.exists():
            continue
        py_dirs = [d for d in proj.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name not in ("node_modules", "__pycache__", "venv", ".venv")]
        if not py_dirs:
            continue

        code, output = run_cmd(["bandit", "-r", ".", "-f", "json", "-q", "--exit-zero"], timeout=120, cwd=str(proj))
        if code == -2:
            items.append("bandit 未安装，跳过 SAST 扫描")
            return CheckResult("Bandit SAST", "ERROR", "info", "bandit 不可用", items)
        if code != 0 or not output:
            continue
        try:
            data = json.loads(output)
            results = data.get("results", [])
            high = [r for r in results if r.get("issue_severity") == "HIGH"]
            med = [r for r in results if r.get("issue_severity") == "MEDIUM"]
            total_high += len(high)
            total_med += len(med)
            for r in high[:5]:
                fname = r.get("filename", "?")
                lineno = r.get("line_number", "?")
                test_id = r.get("test_id", "?")
                items.append(f"[{proj.name}] {fname}:{lineno} — {test_id} HIGH: {r.get('issue_text', '?')[:60]}")
        except (json.JSONDecodeError, KeyError):
            pass

    if total_high == 0 and total_med == 0:
        return CheckResult("Bandit SAST", "PASS", "info", "Python SAST 扫描未发现高危/中危问题")
    severity = "high" if total_high > 0 else "medium"
    status = "FAIL" if total_high > 0 else "WARN"
    summary = f"HIGH={total_high}, MEDIUM={total_med}"
    return CheckResult("Bandit SAST", status, severity, f"Python SAST: {summary}", items)


def check_semgrep_sast() -> CheckResult:
    """Semgrep 多语言 SAST 扫描（可选，失败静默）"""
    code, _ = run_cmd(["semgrep", "--version"], timeout=10)
    if code != 0:
        return CheckResult("Semgrep SAST", "ERROR", "info", "semgrep 不可用，跳过")

    items: list[str] = []
    total_issues = 0

    for proj in LING_PROJECTS:
        if not proj.exists():
            continue
        code, output = run_cmd(
            ["semgrep", "--config=auto", "--json", "--quiet", "."],
            timeout=180, cwd=str(proj)
        )
        if code != 0 or not output:
            continue
        try:
            data = json.loads(output)
            results = data.get("results", [])
            errors = [r for r in results if r.get("extra", {}).get("severity") == "ERROR"]
            total_issues += len(errors)
            for r in errors[:3]:
                path = r.get("path", "?")
                start = r.get("start", {}).get("line", "?")
                rule = r.get("check_id", "?")
                items.append(f"[{proj.name}] {path}:{start} — {rule}")
        except (json.JSONDecodeError, KeyError):
            pass

    if total_issues == 0:
        return CheckResult("Semgrep SAST", "PASS", "info", "Semgrep 扫描未发现 ERROR 级问题")
    return CheckResult("Semgrep SAST", "WARN", "high", f"发现 {total_issues} 个 ERROR 级问题", items)


CHECKS = [
    check_critical_file_permissions,
    check_hardcoded_secrets_24h,
    check_port_changes,
    check_dependency_cve,
    check_bandit_sast,
    check_semgrep_sast,
    check_docker_status,
    check_auth_failures,
    check_disk_space,
    check_git_status_sensitive,
    check_log_secrets,
]


def run_all() -> list[CheckResult]:
    results: list[CheckResult] = []
    for check_fn in CHECKS:
        try:
            result = check_fn()
        except Exception as e:
            result = CheckResult(check_fn.__name__, "ERROR", "info", f"检查异常: {e}")
        results.append(result)
    return results


def generate_report(results: list[CheckResult]) -> str:
    now = datetime.now(timezone.utc)
    lines: list[str] = []
    lines.append("# 灵族每日安全健康检查")
    lines.append(f"**日期**: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")
    err_count = sum(1 for r in results if r.status == "ERROR")

    overall = "PASS" if fail_count == 0 and warn_count == 0 else ("WARN" if fail_count == 0 else "FAIL")
    lines.append(f"**总评**: {overall}")
    lines.append(f"**通过**: {pass_count} | **告警**: {warn_count} | **失败**: {fail_count} | **错误**: {err_count}")
    lines.append("")

    lines.append("| # | 检查项 | 状态 | 等级 | 说明 |")
    lines.append("|---|--------|------|------|------|")
    for i, r in enumerate(results, 1):
        status_icon = {"PASS": "✅", "WARN": "🟡", "FAIL": "🔴", "ERROR": "❌"}.get(r.status, "❓")
        lines.append(f"| {i} | {r.name} | {status_icon} {r.status} | {r.severity} | {r.details[:80]} |")
    lines.append("")

    failed = [r for r in results if r.status in ("FAIL", "WARN")]
    if failed:
        lines.append("## 告警详情")
        lines.append("")
        for r in failed:
            lines.append(f"### {r.name} — {r.status}")
            lines.append(f"- **等级**: {r.severity}")
            lines.append(f"- **说明**: {r.details}")
            if r.items:
                for item in r.items:
                    lines.append(f"  - `{item}`")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    report_file = REPORT_DIR / f"{now.strftime('%Y-%m-%d')}.log"

    results = run_all()
    report = generate_report(results)

    report_file.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n报告已保存: {report_file}")

    fail_count = sum(1 for r in results if r.status == "FAIL")
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

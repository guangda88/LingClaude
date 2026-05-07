#!/usr/bin/env python3
"""灵族 systemd unit 文件 linter — 检查灵族安全规范合规性

规范来源: 事故报告 LC-INCIDENT-20260507 (lingflow-webui 重启风暴)
必须满足:
  1. StartLimitIntervalSec + StartLimitBurst 存在
  2. Restart != always
  3. RestartSec >= 10
  4. 如有端口绑定，在注释中声明
"""

import sys
import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class LintResult:
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


REQUIRED_RATE_LIMIT = {"StartLimitIntervalSec", "StartLimitBurst"}
FORBIDDEN_RESTART = "always"
MIN_RESTART_SEC = 10


def parse_unit(path: Path) -> dict[str, dict[str, str]]:
    """Parse a systemd unit file into {section: {key: value}}."""
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            sections[current] = sections.get(current, {})
        elif "=" in stripped and not stripped.startswith("#"):
            key, _, value = stripped.partition("=")
            sections.setdefault(current, {})[key.strip()] = value.strip()
    return sections


def extract_ports(path: Path) -> list[str]:
    """Extract port numbers from ExecStart or host:port patterns."""
    text = path.read_text(encoding="utf-8", errors="replace")
    ports: list[str] = []
    for m in re.finditer(r"(?:--port[= ]|port[= ])(\d{2,5})", text):
        ports.append(m.group(1))
    for m in re.finditer(r"(?:0\.0\.0\.0|127\.0\.0\.1|localhost):(\d{2,5})", text):
        if m.group(1) not in ports:
            ports.append(m.group(1))
    return ports


def lint_file(path: Path) -> LintResult:
    result = LintResult(path=str(path))
    sections = parse_unit(path)

    unit = sections.get("Unit", {})
    service = sections.get("Service", {})

    # Rule 1: Rate limit fields
    for key in REQUIRED_RATE_LIMIT:
        if key not in unit and key not in service:
            result.errors.append(f"缺少 {key} — 必须设置速率限制防止重启风暴")

    # Rule 2: Restart must not be 'always'
    restart = service.get("Restart", "")
    if restart == FORBIDDEN_RESTART:
        result.errors.append("Restart=always 禁止使用 — 已导致重启风暴事故，请改为 on-failure")

    # Rule 3: RestartSec >= 10
    restart_sec = service.get("RestartSec", "")
    if restart_sec:
        try:
            if int(restart_sec) < MIN_RESTART_SEC:
                result.warnings.append(f"RestartSec={restart_sec} 偏低，建议 >= {MIN_RESTART_SEC}")
        except ValueError:
            pass

    # Rule 4: Port declaration
    ports = extract_ports(path)
    if ports:
        text = path.read_text(encoding="utf-8", errors="replace")
        for p in ports:
            if f"# Port: {p}" not in text and f"# Port:{{{{" not in text:
                result.warnings.append(f"端口 {p} 未在注释中声明 (# Port: {p})，请同步更新 PORT_REGISTRY.md")

    # Rule 5: oneshot services are exempt from restart checks
    is_oneshot = service.get("Type") == "oneshot"

    if is_oneshot:
        result.errors.clear()
        result.warnings.clear()

    return result


def main() -> int:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    targets: list[Path] = []

    if sys.argv[1:]:
        for arg in sys.argv[1:]:
            p = Path(arg)
            if p.exists():
                targets.append(p)
            else:
                print(f"文件不存在: {arg}", file=sys.stderr)
    else:
        targets = sorted(unit_dir.glob("*.service"))

    if not targets:
        print("未找到 service 文件", file=sys.stderr)
        return 1

    total_ok = 0
    total_err = 0
    total_warn = 0

    for path in targets:
        r = lint_file(path)
        name = path.name
        if r.ok:
            total_ok += 1
            status = "PASS"
        else:
            total_err += 1
            status = "FAIL"

        total_warn += len(r.warnings)

        prefix = "✓" if r.ok else "✗"
        line = f"{prefix} {status} {name}"
        if r.warnings:
            line += f" ({len(r.warnings)} warn)"
        print(line)

        for e in r.errors:
            print(f"  ERROR: {e}")
        for w in r.warnings:
            print(f"  WARN:  {w}")

    print(f"\n结果: {total_ok} pass, {total_err} fail, {total_warn} warnings")
    return 1 if total_err > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

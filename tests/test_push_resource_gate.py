# -*- coding: utf-8 -*-
"""pre_push_resource_gate.sh 三态退出码测试（债单④"资源紧张状态"模拟锚）。

通过 MEMINFO_FILE/MIN_AVAIL_KB 测试缝注入，不碰真实 /proc/meminfo。
"""
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "scripts" / "pre_push_resource_gate.sh"


def _run(meminfo_text: str, min_kb: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    fake = Path(os.environ.get("TMPDIR", "/tmp")) / f"meminfo_{os.getpid()}_{id(meminfo_text)}"
    fake.write_text(meminfo_text, encoding="utf-8")
    env["MEMINFO_FILE"] = str(fake)
    if min_kb is not None:
        env["MIN_AVAIL_KB"] = min_kb
    try:
        return subprocess.run(
            ["bash", str(GATE)], env=env, capture_output=True, text=True, timeout=10
        )
    finally:
        fake.unlink(missing_ok=True)


def test_gate_executable():
    assert stat.S_IXUSR & GATE.stat().st_mode, "门脚本必须可执行（lefthook 直接 bash 调用）"


def test_abundant_pass():
    # MemAvailable=8G ≥ 4G → 0 放行
    r = _run("MemTotal:       16384000 kB\nMemAvailable:    8388608 kB\n")
    assert r.returncode == 0
    assert "OK" in r.stderr + r.stdout


def test_tight_blocks():
    # MemAvailable=2G < 4G → 1 结构性挡门（09-24 事故形态）
    r = _run("MemTotal:       16384000 kB\nMemAvailable:    2097152 kB\n")
    assert r.returncode == 1
    assert "FAIL" in r.stderr


def test_missing_meminfo_warns_but_passes():
    # /proc 不可读（非 Linux）→ 2 保守放行 + 告警
    r = _run("", min_kb="4194304")
    assert r.returncode == 2


def test_threshold_override_low():
    # 阈值可覆盖：2G 可用 + 阈值降到 1G → 放行（验证缝本身有效）
    r = _run(
        "MemTotal:       16384000 kB\nMemAvailable:    2097152 kB\n",
        min_kb="1048576",
    )
    assert r.returncode == 0


def test_lefthook_wired():
    # lefthook.yml 必须在 full-pytest 之前挂资源门（priority 18 < 20）
    yml = (REPO / "lefthook.yml").read_text(encoding="utf-8")
    assert "pre-push-resource-precheck" in yml
    assert "pre_push_resource_gate.sh" in yml
    pre = yml.index("pre-push-resource-precheck")
    gate = yml.index("priority: 18")
    full = yml.index("full-pytest:")
    # 2026-10-08 网关化: full-pytest 接 scripts/pre_push_gate.sh,
    # 超时护栏(--timeout=300)与总预算(2700s)随命令移入网关脚本内部,
    # 不再要求 lefthook run 行自带 timeout。两处都验证,防单边回退。
    gw = (REPO / "scripts" / "pre_push_gate.sh").read_text(encoding="utf-8")
    assert "--timeout=300" in gw, "网关内 pytest 必须带用例级超时护栏"
    assert "2700" in gw, "网关必须有总预算自收割(默认 2700s)"
    assert pre < gate < full, "顺序应为 资源门(18) → full-pytest(20 经网关)"

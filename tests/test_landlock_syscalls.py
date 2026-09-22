"""Landlock syscall 号与真沙箱行为钉死（2026-09-22，lc 审计 ③ 项）。

背景（本文件要防再犯的事故）：
- helper 数据表曾写 459/460/461 (x86_64) / 476/477/478 (aarch64)，probe 常量写
  459/460，docstring 又写 436 —— 三处漂移互不一致，且全部错误；
- 正确值 444/445/446（两架构同号）由内核 uapi 头文件实证：
  /usr/include/asm/unistd_64.h: __NR_landlock_create_ruleset 444 等；
- 号码错不会让任何旧测试变红（probe 只会静默 False → noop），16 个测试假绿，
  Landlock 在 6.8 内核上被静默降级——「号码必须与 uapi 头文件一致」因此入测试；
- probe 曾把 flags 位（VERSION=1<<0）误作 abi 传入、并依赖 empty-attr 探测
  （handled=0 触发 ENOMSG，uapi 文档化行为）——现钉死为 uapi 标准 VERSION 探测
  且无 prctl 副作用；
- helper 曾用伪造结构体（parent_devno/parent_ino，真字段是 parent_fd）+
  fail-open 无告警 —— 现以「真跑通」行为测试钉死：限内可写、限外必拒、
  失败必告警（stderr），杜绝「声称沙箱化实际裸奔」。
"""
from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

import lingclaude.engine._landlock_helper as lh
import lingclaude.engine.sandbox_provider as sp


@pytest.fixture(autouse=True)
def _reset_landlock_probe_cache():
    """每测前后复位探针缓存（防跨文件/跨测试缓存污染，顺序无关性）。"""
    saved = sp._landlock_probe_cache
    sp._landlock_probe_cache = None
    yield
    sp._landlock_probe_cache = saved


# ── syscall 号与 uapi 头文件一致性（防再犯核心） ──────────────────────

_UAPI_INCLUDES = [
    "/usr/include/asm/unistd_64.h",
    "/usr/include/x86_64-linux-gnu/asm/unistd_64.h",
]
_UAPI_AARCH64_INCLUDES = [
    "/usr/include/aarch64-linux-gnu/asm/unistd_64.h",
]


def _read_nr(path: str, name: str) -> int | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(rf"#define\s+{name}\s+(\d+)", text)
    return int(m.group(1)) if m else None


def test_helper_syscall_numbers_match_uapi_x86_64():
    """helper 数据表 x86_64 号必须与 uapi 头文件一致（444/445/446）。"""
    hdr = next((h for h in _UAPI_INCLUDES if _read_nr(h, "__NR_landlock_create_ruleset")), None)
    if hdr is None:
        pytest.skip("uapi 头文件不可用（非 Linux 构建机）")
    expect = {
        "create_ruleset": _read_nr(hdr, "__NR_landlock_create_ruleset"),
        "add_rule": _read_nr(hdr, "__NR_landlock_add_rule"),
        "restrict_self": _read_nr(hdr, "__NR_landlock_restrict_self"),
    }
    assert expect["create_ruleset"] == 444  # 钉死：头文件若变，此处先红
    assert lh._SYSCALLS["x86_64"] == expect


def test_helper_syscall_numbers_match_uapi_aarch64():
    """aarch64 与 x86_64 同号（uapi 实证），数据表两架构一致。"""
    hdr = next((h for h in _UAPI_AARCH64_INCLUDES if _read_nr(h, "__NR_landlock_create_ruleset")), None)
    if hdr is not None:
        assert _read_nr(hdr, "__NR_landlock_create_ruleset") == 444
    assert lh._SYSCALLS["aarch64"] == lh._SYSCALLS["x86_64"]
    assert lh._SYSCALLS["aarch64"]["create_ruleset"] == 444


def test_provider_probe_pins_syscall_444():
    """provider 探测必须使用 444（曾漂移为 459/460/436 的三处之一）。"""
    src = inspect.getsource(sp._landlock_probe)
    assert "= 444" in src
    assert "459" not in src and "436" not in src


def test_probe_is_version_probe_without_prctl_side_effect():
    """探测用 uapi 标准 VERSION 手法（attr=NULL, size=0, flags=1），无 prctl 副作用。

    旧实现曾 (a) 把 flags 位当 abi 传入、(b) 依赖 empty-attr 探测（handled=0
    → ENOMSG）、(c) prctl(NO_NEW_PRIVS) 留进程级副作用。
    """
    src = inspect.getsource(sp._landlock_probe)
    assert "prctl" not in src
    assert "VERSION" in src


def test_helper_abi_probe_passes_version_flag():
    """helper 的 ABI 探测必须传 flags=VERSION(1<<0)，而非把 abi 当 flags。"""
    class _FakeSyscall:
        restype = None
        argtypes = None

        def __init__(self, sink: list) -> None:
            self._sink = sink

        def __call__(self, *args):  # noqa: ANN002 — 泛化捕获
            self._sink.append(args)
            return 4  # 模拟内核返回最高 ABI=4

    class FakeLibc:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.syscall = _FakeSyscall(self.calls)

    fake = FakeLibc()
    abi = lh._landlock_abi(fake, 444)  # type: ignore[arg-type]
    assert abi == 4
    raw = fake.calls[0]
    assert len(raw) == 4
    nr, attr, size, flags = (v.value if hasattr(v, "value") else v for v in raw)
    assert nr == 444
    assert attr in (None, 0)  # attr=NULL（ctypes 包装后 value 可能为 None）
    assert size == 0
    assert flags == 1  # LANDLOCK_CREATE_RULESET_VERSION


def test_helper_write_bit_semantics():
    """写沙箱管辖位 = 写系位，不含 EXECUTE/READ_*（否则白名单外连 bash 都跑不了）。"""
    expected = (1 << 1) | (0x1F << 4)  # WRITE_FILE | REMOVE_*/MAKE_* 系
    assert lh._FS_WRITE_BASE == expected
    assert not (lh._FS_WRITE_BASE & 1)          # 不含 EXECUTE
    assert not (lh._FS_WRITE_BASE & (0b1100))   # 不含 READ_FILE/READ_DIR


# ── 探测结果契约（跨平台行为钉死） ────────────────────────────────────

def test_probe_cache_consistency():
    sp._landlock_probe_cache = None
    a = sp._landlock_probe()
    b = sp._landlock_probe()
    assert a == b


def test_probe_non_linux_false():
    saved = sp._landlock_probe_cache
    sp._landlock_probe_cache = None
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "platform", "darwin")
            sp._landlock_probe_cache = None
            ok, reason = sp._landlock_probe()
        assert ok is False
        assert reason
    finally:
        sp._landlock_probe_cache = saved


def test_probe_true_on_lsm_enabled_kernel():
    """本机 6.8 内核（lsm 含 landlock）探针必须 True——防「假不可用」回归。"""
    try:
        lsm = Path("/sys/kernel/security/lsm").read_text(encoding="utf-8")
    except OSError:
        pytest.skip("本机无 /sys/kernel/security/lsm（非启用 LSM 的 Linux）")
    if "landlock" not in lsm:
        pytest.skip("本机 LSM 未启用 landlock")
    sp._landlock_probe_cache = None
    ok, reason = sp._landlock_probe()
    assert ok is True, f"LSM 已启用 landlock 但探测失败: {reason}"


# ── 真沙箱行为（helper 子进程实证，沙箱真验的常驻形态） ──────────────

def _helper_cmd(writable: str, script: str) -> list[str]:
    helper = Path(lh.__file__).resolve()
    return ["python3", str(helper), "--writable", writable,
            "--", "/bin/bash", "-c", script]


def test_helper_true_sandbox_write_enforcement(tmp_path):
    """限内可写、限外必拒、且必须 truthfully restricted（无 fail-open 告警）。

    这是「声称沙箱化实际裸奔」的直接反测：旧实现结构体伪造导致 restrict
    从未生效，本测试在修复前的代码上会以 W-out:OK + fail-open 告警双红。
    """
    sp._landlock_probe_cache = None
    ok, reason = sp._landlock_probe()
    if not ok:
        pytest.skip(f"本机 Landlock 不可用: {reason}")
    inbox = tmp_path / "in"
    outside = tmp_path / "out"
    inbox.mkdir()
    outside.mkdir()
    r = subprocess.run(
        _helper_cmd(str(inbox), f"echo in > {inbox}/a.txt && echo out > {outside}/b.txt"),
        capture_output=True, text=True, timeout=30,
    )
    assert (inbox / "a.txt").exists(), f"白名单内写入失败: {r.stderr}"
    assert not (outside / "b.txt").exists(), "白名单外写入未被拒绝——沙箱未生效！"
    assert "不受 Landlock 约束" not in r.stderr, "helper 报告 fail-open——沙箱未生效！"


def test_helper_fail_open_warns_loudly(tmp_path):
    """Landlock 不可用路径必须 fail-open 但大声告警（stderr 提示，不静默裸奔）。"""
    r = subprocess.run(
        _helper_cmd("/nonexistent_ll_probe_xyz", "exit 0"),
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, "fail-open 必须继续执行目标命令"
    assert "不受 Landlock 约束" in r.stderr, "fail-open 必须显式告警（J5 可见性）"


def test_provider_wrap_uses_helper_when_available(tmp_path):
    """available 时 wrap 产出 helper 命令行（含 --writable 与 bash -c 透传）。"""
    saved = sp._landlock_probe_cache
    sp._landlock_probe_cache = (True, None)
    try:
        p = sp.LandlockSandboxProvider()
        out = p.wrap("echo hi", working_dir=tmp_path)
        assert "_landlock_helper.py" in out
        assert "--writable" in out
        assert "/bin/bash -c" in out
    finally:
        sp._landlock_probe_cache = saved

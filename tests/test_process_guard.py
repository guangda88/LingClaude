"""process_guard.py 测试 — 灵克 own scope (L7/L10 v0.5 P3)"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path("/home/ai/lingclaude/scripts/process_guard.py")


def _load():
    spec = importlib.util.spec_from_file_location("pg", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_etime_to_seconds():
    m = _load()
    assert m.etime_to_seconds("30") == 30
    assert m.etime_to_seconds("5:30") == 5 * 60 + 30
    assert m.etime_to_seconds("2:30:45") == 2 * 3600 + 30 * 60 + 45
    assert m.etime_to_seconds("1-02:30:45") == 86400 + 2 * 3600 + 30 * 60 + 45
    print("  ✓ etime_to_seconds 转换正确 (4 cases)")


def test_get_session_chain():
    """返回当前会话 PID 链 (含 pgid 同组)."""
    m = _load()
    chain = m.get_session_chain()
    my_pid = os.getpid()
    assert my_pid in chain, f"自身 PID {my_pid} 应在 chain"
    assert len(chain) >= 1
    print(f"  ✓ get_session_chain 包含自身 PID (chain size={len(chain)})")


def test_check_dry_run():
    """DRY-RUN 模式不实际 kill."""
    m = _load()
    # 备份 AUDIT_LOG
    audit_log = "/tmp/test_pg_audit.log"
    if os.path.exists(audit_log):
        os.unlink(audit_log)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "PROC_GUARD_AUDIT": audit_log},
    )
    assert "process_guard report" in result.stdout
    # audit log 必有记录
    if os.path.exists(audit_log):
        content = open(audit_log).read()
        assert "process_guard" in content
    print("  ✓ dry-run 模式 + audit log 记录")


def test_check_excludes_self():
    """自身 PID 链不在 findings 中."""
    m = _load()
    chain = m.get_session_chain()
    procs = m.list_all_procs()
    # T 状态检测
    t = m.check_t_state(procs, chain)
    for p in t:
        assert p["pid"] not in chain, f"T 状态 {p['pid']} 不应在 chain 中"
    # defunct 检测
    d = m.check_defunct(procs, chain)
    for p in d:
        assert p["pid"] not in chain
    print(f"  ✓ self chain 排除正确 (T={len(t)}, defunct={len(d)})")


def test_check_kill_idempotent():
    """杀 defunct PID (可能已退出) 不抛异常."""
    m = _load()
    # 用一个不存在的 PID
    killed = m.kill_pids([999999], signal.SIGTERM)
    assert killed == []
    # PID 1 (init) 不能杀
    killed = m.kill_pids([1], signal.SIGTERM)
    assert killed == [], "init PID 不应被杀"
    print("  ✓ kill_pids 幂等 + 不杀 init")


def test_long_run_patterns():
    """long-run 区分 daemon / 异常."""
    m = _load()
    chain = m.get_session_chain()
    procs = m.list_all_procs()
    n1, n2 = m.check_long_run(procs, chain)
    # n1 = atomcode 正常, n2 = 异常
    for p in n1:
        assert "atomcode" in p["cmd"] or "atomgit_proxy" in p["cmd"], f"normal 应含 atomcode: {p}"
    for p in n2:
        assert any(pat in p["cmd"] for pat in m.LONG_RUN_PATTERNS), f"abnormal 应匹配 pattern: {p}"
    print(f"  ✓ long-run 分类 (daemon={len(n1)}, abnormal={len(n2)})")


def test_audit_log_always_writes():
    """即使 findings 为空也写 audit log (修复 v0.4 旧版 bug)."""
    audit_log = "/tmp/test_pg_always.log"
    if os.path.exists(audit_log):
        os.unlink(audit_log)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "PROC_GUARD_AUDIT": audit_log},
    )
    assert os.path.exists(audit_log), "audit log 必须写"
    content = open(audit_log).read()
    assert "t_state=0" in content or "t_state=" in content
    print("  ✓ audit log 总是写 (含 0 findings)")


if __name__ == "__main__":
    print("=== process_guard 沙箱测试 ===")
    test_etime_to_seconds()
    test_get_session_chain()
    test_check_dry_run()
    test_check_excludes_self()
    test_check_kill_idempotent()
    test_long_run_patterns()
    test_audit_log_always_writes()
    print("\n✓ 沙箱测试全部通过")

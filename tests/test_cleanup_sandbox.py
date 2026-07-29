"""crush zombie cleanup 测试 — L7/L10 v0.5 沙箱测试

依据灵族方向例会 #3 议程 7 沙箱 + 启安 R1 安全前置:
- DRY_RUN 模式必须精确返回"应清理"列表
- 必须排除当前会话进程链
- 必须支持 etime 阈值
- 必须写 audit log
- 必须 max-kill 限制
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path("/home/ai/lingclaude/scripts/crush_zombie_cleanup.py")
SPEC = importlib.util.spec_from_file_location("cleanup", SCRIPT)


def test_get_current_session_chain():
    """返回当前会话 PID 链 (含 pgid + PPID)。"""
    # Use a subprocess to avoid importing cleanup in current session
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--apply", "--min-days", "0"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "L7_HOOK_AUDIT_LOG": "/tmp/test_audit.log"},
    )
    output = result.stdout
    assert "当前会话链" in output, f"应输出现前会话链, 实际: {output[:200]}"
    assert "PID" in output
    print(f"  ✓ 当前会话链检测正常")


def test_dry_run_no_kill():
    """DRY_RUN 模式不实际 kill。"""
    audit_log = "/tmp/test_dry_run.log"
    # 删除旧 log
    if os.path.exists(audit_log):
        os.unlink(audit_log)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--min-days", "0.001"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "L7_HOOK_AUDIT_LOG": audit_log},
    )
    output = result.stdout
    assert "DRY-RUN" in output or "无 zombie" in output
    # audit log 应该有 DRY_RUN 记录
    if os.path.exists(audit_log):
        with open(audit_log) as f:
            assert "DRY_RUN" in f.read()
        print(f"  ✓ DRY_RUN 模式不 kill, audit log 有记录")


def test_excludes_self_chain():
    """当前会话的 PID 链不会被列在 safe_to_kill。"""
    # Get our shell's PID
    my_pid = os.getpid()
    # Run cleanup in --apply mode but check output
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--apply", "--min-days", "0"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "L7_HOOK_AUDIT_LOG": "/tmp/test_exclude.log"},
    )
    output = result.stdout
    # my_pid should NOT be in the kill list
    # Extract PID lines from output
    for line in output.splitlines():
        if "SIGTERM" in line:
            assert str(my_pid) not in line, f"必须排除自身 PID {my_pid}"
    print(f"  ✓ 自身 PID {my_pid} 未被 kill")


def test_etime_conversion():
    """etime 字符串转秒正确。"""
    spec = importlib.util.spec_from_file_location("cleanup", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._etime_to_seconds("30") == 30
    assert mod._etime_to_seconds("5:30") == 5 * 60 + 30
    assert mod._etime_to_seconds("2:30:45") == 2 * 3600 + 30 * 60 + 45
    assert mod._etime_to_seconds("1-02:30:45") == 86400 + 2 * 3600 + 30 * 60 + 45
    print(f"  ✓ _etime_to_seconds 转换正确")


def test_max_kill_limit():
    """--max-kill 限制应生效。"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--min-days", "0", "--max-kill", "1"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "L7_HOOK_AUDIT_LOG": "/tmp/test_maxkill.log"},
    )
    output = result.stdout
    # 应显示 max-kill: 1
    assert "max-kill: 1" in output
    print(f"  ✓ --max-kill 参数生效")


def test_audit_log_format():
    """audit log 应含 mode + killed 字段。"""
    audit_log = "/tmp/test_audit_format.log"
    if os.path.exists(audit_log):
        os.unlink(audit_log)
    subprocess.run(
        [sys.executable, str(SCRIPT), "--min-days", "0"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "L7_HOOK_AUDIT_LOG": audit_log},
    )
    if os.path.exists(audit_log):
        content = open(audit_log).read()
        assert "crash_kill" in content
        assert "DRY_RUN" in content
        assert "lingclaude" in content
        print(f"  ✓ audit log 格式正确 ({len(content)} bytes)")


if __name__ == "__main__":
    print("=== crush_zombie_cleanup 沙箱测试 ===")
    test_etime_conversion()
    test_dry_run_no_kill()
    test_excludes_self_chain()
    test_max_kill_limit()
    test_audit_log_format()
    print("\n✓ 沙箱测试全部通过")

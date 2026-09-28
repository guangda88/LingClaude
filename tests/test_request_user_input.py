"""P0-3b: request_user_input 非交互防卡死回归测试。

远程 LACP 调用本机时 stdin 是管道，input() 若无保护会永久阻塞，
导致整条 agent loop 卡死。本文件锁定非 TTY 分支行为。
"""
from __future__ import annotations

import io
import sys

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.engine.coding import CodingRuntime


@pytest.fixture()
def handler():
    rt = CodingRuntime(lingclaudeConfig())
    return rt._request_user_input_handler


def _make_tty_stdout(monkeypatch):
    """把 stdout 伪装成真实 tty —— 绕过 A 方案的 TUI 死锁熔断。

    A 方案（2026-09-28）按「stdout 非 tty」判定 TUI 活跃并熔断；CI/pytest
    捕获下 stdout 非 tty，会拦截所有用例。须在测试体内（捕获已稳定后）
    patch，故不在 fixture 里做（fixture 时机与 pytest 全局捕获竞争，曾失效）。
    """
    fake_out = io.StringIO()
    fake_out.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdout", fake_out)
    return fake_out


def test_no_tty_returns_pending_not_block(handler, monkeypatch):
    """stdin 为管道且无数据时：立即返回 pending，绝不阻塞。"""
    _make_tty_stdout(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # StringIO.isatty() -> False
    result = handler(header="确认", question="继续吗？", mode="single")
    assert result["ok"] is False
    assert result["pending"] is True
    assert result["answer"] is None
    assert "继续吗" in result["prompt"]


def test_no_tty_includes_rendered_options(handler, monkeypatch):
    """pending 结果携带已渲染的选项 prompt，供上层（如 LACP remote）转交前端。"""
    _make_tty_stdout(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    opts = [{"label": "方案 A"}, {"label": "方案 B"}]
    result = handler(question="选一个", mode="single", options=opts)
    assert result["pending"] is True
    assert "1. 方案 A" in result["prompt"]
    assert "2. 方案 B" in result["prompt"]


def test_no_tty_stdin_none_guard(handler, monkeypatch):
    """stdin 被替换为 None（detached 进程）时同样安全返回。"""
    _make_tty_stdout(monkeypatch)
    monkeypatch.setattr(sys, "stdin", None)
    result = handler(question="任何问题")
    assert result["pending"] is True
    assert result["ok"] is False


def test_tty_path_reads_stdin(handler, monkeypatch):
    """有 TTY 时保持原交互语义：读入并解析选项编号。"""
    _make_tty_stdout(monkeypatch)
    fake_tty = io.StringIO("2\n")
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_tty)
    opts = [{"label": "甲"}, {"label": "乙"}]
    result = handler(question="选一个", mode="single", options=opts)
    assert result["ok"] is True
    assert result["answer"] == "乙"


def test_tty_path_eof_is_cancelled(handler, monkeypatch):
    """TTY 分支 EOF：取消语义不变（ok=False, pending=False）。"""
    _make_tty_stdout(monkeypatch)
    fake_tty = io.StringIO("")
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_tty)
    result = handler(question="选一个")
    assert result["ok"] is False
    assert result.get("pending") is False


# ── A 方案专项：TUI 死锁熔断（2026-09-28）────────────────────────────────────


def test_tui_mode_degrades_not_blocks(handler, monkeypatch):
    """A 方案：stdout 非 tty（TUI 活跃指纹）→ 熔断降级，绝不走 input()。

    复现隔壁进程 1170371 死锁场景：full_tui 下 stdin 仍是 tty（isatty=True），
    若无熔断会走 input() C 级阻塞、SIGINT 无效。熔断后返回 degraded 提示。
    """
    # stdout 非 tty（模拟 _StdoutProxy / pytest 捕获），stdin 是 tty（模拟 full_tui）
    fake_in = io.StringIO("1\n")
    fake_in.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_in)
    # stdout 保持 pytest 默认（非 tty），不调用 _make_tty_stdout
    result = handler(question="选一个", mode="single", options=[{"label": "甲"}])
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["pending"] is False
    assert "TUI" in result["error"] or "死锁" in result["error"]
    assert "文字回复" in result["error"]


def test_tui_mode_records_incident_rule(handler, monkeypatch, tmp_path):
    """A 方案：熔断时落 LearnedRule（category=TOOL_ERROR）——fail-soft 不阻断。"""
    calls = []

    class _FakeKB:
        def add_rule(self, rule):
            calls.append(rule)
            from lingclaude.core.result import Ok
            return Ok(True)

        def close(self):
            pass

    monkeypatch.setattr(
        "lingclaude.self_optimizer.learner.knowledge.KnowledgeBase",
        lambda *a, **k: _FakeKB(),
    )
    fake_in = io.StringIO("1\n")
    fake_in.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_in)
    handler(question="q", mode="single")
    assert len(calls) == 1
    rule = calls[0]
    assert rule.category.value == "tool_error"
    assert "request_user_input" in rule.description
    assert "TUI" in rule.description or "tui" in rule.description


def test_tui_mode_incident_record_failure_does_not_raise(handler, monkeypatch):
    """A 方案：落库异常静默吞掉，不反噬工具返回。"""
    monkeypatch.setattr(
        "lingclaude.self_optimizer.learner.knowledge.KnowledgeBase",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    fake_in = io.StringIO("1\n")
    fake_in.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_in)
    result = handler(question="q", mode="single")
    assert result["degraded"] is True  # 仍正常返回降级结果


# ── B 方案专项：TTY 路径超时兜底（2026-09-28）────────────────────────────────


def test_tty_timeout_defaults_to_first_option(handler, monkeypatch):
    """B 方案：TTY 输入超时 → 降级为默认答案（选项 1），不挂起、不 raise。"""
    import lingclaude.engine.coding as coding_mod

    _make_tty_stdout(monkeypatch)
    fake_tty = io.StringIO("")  # 无内容，isatty True
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_tty)
    # 强制 select 超时返回（无 fd 回退 input 会立刻 EOF，故直接 stub 读行函数）
    monkeypatch.setattr(coding_mod, "_read_line_with_timeout", lambda s, t: None)
    monkeypatch.setattr(
        "lingclaude.self_optimizer.learner.knowledge.KnowledgeBase",
        lambda *a, **k: type("K", (), {"add_rule": lambda self, r: None, "close": lambda self: None})(),
    )
    opts = [{"label": "默认项"}, {"label": "其它"}]
    result = handler(question="选一个", mode="single", options=opts)
    assert result["ok"] is True
    assert result["answer"] == "默认项"
    assert result["timed_out"] is True
    assert result["degraded"] is True


def test_user_input_timeout_env_override(monkeypatch):
    """B 方案：LINGCLAUDE_USER_INPUT_TIMEOUT 环境变量可调超时。"""
    from lingclaude.engine.coding import _user_input_timeout

    monkeypatch.setenv("LINGCLAUDE_USER_INPUT_TIMEOUT", "5")
    assert _user_input_timeout() == 5.0
    monkeypatch.setenv("LINGCLAUDE_USER_INPUT_TIMEOUT", "not_a_number")
    assert _user_input_timeout() == 120.0
    monkeypatch.setenv("LINGCLAUDE_USER_INPUT_TIMEOUT", "0")
    import math

    assert math.isinf(_user_input_timeout())  # <=0 → 不超时

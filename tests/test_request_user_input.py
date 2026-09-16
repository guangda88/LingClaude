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


def test_no_tty_returns_pending_not_block(handler, monkeypatch):
    """stdin 为管道且无数据时：立即返回 pending，绝不阻塞。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # StringIO.isatty() -> False
    result = handler(header="确认", question="继续吗？", mode="single")
    assert result["ok"] is False
    assert result["pending"] is True
    assert result["answer"] is None
    assert "继续吗" in result["prompt"]


def test_no_tty_includes_rendered_options(handler, monkeypatch):
    """pending 结果携带已渲染的选项 prompt，供上层（如 LACP remote）转交前端。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    opts = [{"label": "方案 A"}, {"label": "方案 B"}]
    result = handler(question="选一个", mode="single", options=opts)
    assert result["pending"] is True
    assert "1. 方案 A" in result["prompt"]
    assert "2. 方案 B" in result["prompt"]


def test_no_tty_stdin_none_guard(handler, monkeypatch):
    """stdin 被替换为 None（detached 进程）时同样安全返回。"""
    monkeypatch.setattr(sys, "stdin", None)
    result = handler(question="任何问题")
    assert result["pending"] is True
    assert result["ok"] is False


def test_tty_path_reads_stdin(handler, monkeypatch):
    """有 TTY 时保持原交互语义：读入并解析选项编号。"""
    fake_tty = io.StringIO("2\n")
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_tty)
    opts = [{"label": "甲"}, {"label": "乙"}]
    result = handler(question="选一个", mode="single", options=opts)
    assert result["ok"] is True
    assert result["answer"] == "乙"


def test_tty_path_eof_is_cancelled(handler, monkeypatch):
    """TTY 分支 EOF：取消语义不变（ok=False, pending=False）。"""
    fake_tty = io.StringIO("")
    fake_tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", fake_tty)
    result = handler(question="选一个")
    assert result["ok"] is False
    assert result.get("pending") is False

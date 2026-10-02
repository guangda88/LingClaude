"""task_contract 单测 + /quit 退出闸接线测试（M2，2026-10-02）。

覆盖面：
- 契约 CRUD：new/add/done 序号语义/clear=删文件
- 持久化：写盘再读一致（tmp_path 隔离）
- exit_gate：未清→block+报告 / 全清→放行 / force→放行 / 坏文件→放行（fail-open）
- /quit 接线：未清时 consume 但不置 quit_requested；force 置位
- /contract 插件：register 契约 + new/done/clear 全流程（capsys 捕获输出）

hermetic：LC_CONTRACT_FILE 指 tmp_path。
"""
from __future__ import annotations

import json

import pytest

from lingclaude.core import task_contract as tc


@pytest.fixture()
def contract_env(tmp_path, monkeypatch):
    f = tmp_path / "contract" / "active_contract.json"
    monkeypatch.setenv(tc.ENV_CONTRACT_FILE, str(f))
    return f


# ── CRUD ─────────────────────────────────────────────────────────

def test_new_and_load(contract_env):
    items = tc.contract_new(["重构 module X", "回归全绿"])
    assert len(items) == 2 and all(not it["done"] for it in items)
    assert tc.load_contract() == items  # 持久化 roundtrip


def test_add_and_done(contract_env):
    tc.contract_new(["a"])
    tc.contract_add("b")
    assert tc.contract_done(2) is True
    assert tc.contract_done(9) is False  # 越界
    assert tc.load_contract()[1]["done"] is True


def test_clear_deletes_file(contract_env):
    tc.contract_new(["a"])
    assert contract_env.exists()
    tc.contract_clear()
    assert not contract_env.exists()
    assert tc.load_contract() == []


def test_bad_file_fails_open(contract_env):
    contract_env.parent.mkdir(parents=True, exist_ok=True)
    contract_env.write_text("{broken json", encoding="utf-8")
    assert tc.load_contract() == []          # 门放行语义
    blocked, _ = tc.exit_gate()
    assert blocked is False


# ── exit_gate ────────────────────────────────────────────────────

def test_exit_gate_blocks_with_report(contract_env):
    tc.contract_new(["收尾项1", "收尾项2"])
    tc.contract_done(1)
    blocked, report = tc.exit_gate()
    assert blocked is True
    assert "收尾项2" in report and "/quit force" in report


def test_exit_gate_passes_when_clear(contract_env):
    tc.contract_new(["已验收项"])
    tc.contract_done(1)
    blocked, report = tc.exit_gate()
    assert blocked is False and report == ""


def test_exit_gate_force(contract_env):
    tc.contract_new(["就是不验收"])
    blocked, report = tc.exit_gate(force=True)
    assert blocked is False
    assert tc.pending_items()  # force 不动契约，下次进入仍可见


# ── /quit 接线 ───────────────────────────────────────────────────

def _fresh_processor():
    from lingclaude.cli.commands import SlashCommandProcessor

    return SlashCommandProcessor.__new__(SlashCommandProcessor)  # 只用 quit_requested 属性面


def test_quit_blocked_by_contract(contract_env, capsys):
    tc.contract_new(["未清项"])
    proc = _fresh_processor()
    proc.quit_requested = False
    from lingclaude.cli.commands import SlashCommandProcessor

    consumed = SlashCommandProcessor.handle(proc, "/quit")
    assert consumed is True
    assert proc.quit_requested is False   # 被拦：不退场
    assert "未清项" in capsys.readouterr().out


def test_quit_force_passes(contract_env):
    tc.contract_new(["未清项"])
    proc = _fresh_processor()
    proc.quit_requested = False
    from lingclaude.cli.commands import SlashCommandProcessor

    assert SlashCommandProcessor.handle(proc, "/quit force") is True
    assert proc.quit_requested is True    # force 放行


def test_quit_normal_passes(contract_env):
    proc = _fresh_processor()
    proc.quit_requested = False
    from lingclaude.cli.commands import SlashCommandProcessor

    assert SlashCommandProcessor.handle(proc, "/quit") is True
    assert proc.quit_requested is True    # 无契约零分叉


# ── /contract 插件 ───────────────────────────────────────────────

def test_contract_plugin_register_and_flow(contract_env, capsys):
    from lingclaude.cli.slash_plugins.contract import contract_cmd, register

    captured = {}
    register(lambda n, f, d, **k: captured.update({n: d}))
    assert "/contract" in captured

    contract_cmd(None, "new 项A 项B")
    assert "项A" in capsys.readouterr().out
    contract_cmd(None, "done 1")
    contract_cmd(None, "done 2")
    contract_cmd(None, "")  # status：全清
    assert "全清" in capsys.readouterr().out
    contract_cmd(None, "clear")
    assert not contract_env.exists()

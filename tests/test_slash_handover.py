"""/handover 插件单测（2026-10-10）。

覆盖：注册契约 / 骨架机械采集 / todo 降级 / prompt 落盘防二次解析。
不碰真实 tmux 与 LLM（_launch_successor 的真实验证由端到端手工验收承担，
2026-10-10 handover_222726 实测 PASS）。
"""
from __future__ import annotations

import json
from pathlib import Path

from lingclaude.cli.commands import SLASH_REGISTRY
from lingclaude.cli.slash_plugins.handover import (
    _collect_todo_snapshot,
    _write_skeleton,
    handover_cmd,
    register,
)


class _Item:
    def __init__(self, d: dict) -> None:
        self._d = d

    def to_dict(self) -> dict:
        return self._d


class _Store:
    def list(self, status=None, tags=None):  # noqa: ARG002 — 测试桩
        return [
            _Item({"id": "t1", "status": "in_progress", "content": "进行中任务"}),
            _Item({"id": "t2", "status": "pending", "content": "待办任务"}),
            _Item({"id": "t3", "status": "completed", "content": "已完成不应出现"}),
        ]


class _Runtime:
    _todo_store = _Store()


class _Engine:
    _runtime = _Runtime()


def test_register_contract() -> None:
    """注册契约：add() 后 SLASH_REGISTRY 有条目且 desc 非空。"""
    seen: list[tuple] = []
    register(lambda n, h, d, **k: seen.append((n, h, d)))
    assert seen and seen[0][0] == "/handover"
    assert SLASH_REGISTRY.get("/handover") is not None


def test_todo_snapshot_filters_completed() -> None:
    """todo 采集：completed 被过滤，pending/in_progress 保留。"""
    out = _collect_todo_snapshot(_Engine())
    ids = {t["id"] for t in out}
    assert ids == {"t1", "t2"}


def test_todo_snapshot_degrades_without_runtime() -> None:
    """engine 无 _runtime/_todo_store → 空列表不抛异常。"""
    class Bare:
        pass

    assert _collect_todo_snapshot(Bare()) == []


def test_skeleton_contains_mechanical_facts(tmp_path: Path) -> None:
    """骨架文档：beacon 事实逐项在位，无占位幻觉。"""
    beacon = {
        "session_id": "sid123", "turns": 42,
        "last_user_input": "做完 /handover",
        "input_tokens": 1000,
    }
    todos = [{"id": "t1", "status": "in_progress", "content": "进行中任务"}]
    doc = tmp_path / "h.md"
    _write_skeleton(doc, beacon, todos, "/proj")
    text = doc.read_text(encoding="utf-8")
    for key in ("sid123", "42", "1000", "做完 /handover", "进行中任务", "继任会话在此补全"):
        assert key in text, f"missing {key}"
    assert "已完成不应出现" not in text


def test_cmd_invalid_arg_rejected(capsys) -> None:
    """/handover 带参数 → 用法提示，不落盘不拉会话。"""
    class P:
        engine = _Engine()

    handover_cmd(P(), "extra")
    assert "用法" in capsys.readouterr().out


def test_beacon_snapshot_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """_collect_beacon_snapshot：写读回环 + 异常降级。"""
    from lingclaude.cli.slash_plugins.handover import _collect_beacon_snapshot
    from lingclaude.engine import kill_beacon as KB

    class BEngine:
        session_id = "rt123"
        _messages = ["u", "a"]
        _conversation: list = []
        get_stats = lambda self: {}  # noqa: E731

    monkeypatch.setattr(KB, "_beacons_root", lambda: tmp_path)
    data = _collect_beacon_snapshot(BEngine(), "/proj")
    assert data.get("session_id") == "rt123"
    raw = json.loads((tmp_path / "proj" / "rt123.json").read_text(encoding="utf-8"))
    assert raw["session_id"] == "rt123"

    class Boom:
        session_id = "x"

        def __getattr__(self, k):  # noqa: ANN204
            raise RuntimeError("boom")

    assert _collect_beacon_snapshot(Boom(), "/proj") == {}


# ---- v2 进程内换班（2026-10-11）----


class _FakeProc:
    """mock processor：submit/clear 记录调用，engine 可注入。"""

    def __init__(self, engine, with_submit=True):
        self.engine = engine
        self.submit_calls: list[str] = []
        self.clear_calls = 0
        if with_submit:
            self.submit = lambda t: self.submit_calls.append(t)

    def _cmd_clear(self):
        self.clear_calls += 1


def test_v2_inplace_submit_and_clear(tmp_path: Path, monkeypatch) -> None:
    """v2 主流程：落盘→_cmd_clear 被调→submit 注入含文档路径的续作指令。"""
    from lingclaude.cli.slash_plugins import handover as ho

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ho, "_collect_beacon_snapshot", lambda e, p: {})
    proc = _FakeProc(_Engine())
    ho.handover_cmd(proc, "")
    assert proc.clear_calls == 1
    assert len(proc.submit_calls) == 1
    body = proc.submit_calls[0]
    assert "交接文档" in body and ".md" in body and "下一步计划" in body
    # 文档确实落盘
    docs = list((tmp_path / "docs" / "handover").glob("*_relay_handover.md"))
    assert len(docs) == 1


def test_v2_submit_unavailable_degrades(tmp_path: Path, monkeypatch, capsys) -> None:
    """submit 不可用 → 退化为纯 clear（engine.reset 兜底），不抛异常。"""
    from lingclaude.cli.slash_plugins import handover as ho

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ho, "_collect_beacon_snapshot", lambda e, p: {})
    proc = _FakeProc(_Engine(), with_submit=False)
    ho.handover_cmd(proc, "")
    assert proc.submit_calls == []
    out = capsys.readouterr().out
    assert "submit 通道不可用" in out


def test_v2_auto_latch_single_fire() -> None:
    """自动换班幂等闩：首调用执行，二次调用直接短路。"""
    from lingclaude.cli.slash_plugins import handover as ho

    calls = []

    class _Ctx:
        processor = None

    ho._AUTO_STATE["done"] = False
    # processor=None → 不置闩直接返回（通道缺席不永久闩死，轻量重检无害）
    ho._auto_handover_if_needed(_Ctx())
    assert ho._AUTO_STATE["done"] is False
    assert calls == []


def test_v2_invalid_mode_rejected(capsys) -> None:
    """未知参数 → 用法提示。"""
    from lingclaude.cli.slash_plugins import handover as ho

    ho.handover_cmd(_FakeProc(_Engine()), "bogus")
    out = capsys.readouterr().out
    assert "用法" in out

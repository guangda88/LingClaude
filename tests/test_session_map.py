"""session_map 单测：三源映射 / 配对 / 脱敏契约 / 幂等水位 / HTML 渲染。

狗粮说明：真实回放对象是本会话数据（journal/rollout 布局实测于 2026-10-29），
单测全部使用 tmp 沙箱注入（ monkeypatch 模块常量），零写真实 .lingclaude/。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "session_map.py"
_SPEC = importlib.util.spec_from_file_location("session_map", _SCRIPT)
sm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sm)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    jd = tmp_path / "journals"
    rd = tmp_path / "rollouts"
    ld = tmp_path / "ledger"
    for d in (jd, rd, ld):
        d.mkdir(parents=True)
    monkeypatch.setattr(sm, "_JOURNAL_DIR", jd)
    monkeypatch.setattr(sm, "_ROLLOUT_DIR", rd)
    monkeypatch.setattr(sm, "_LEDGER_DIR", ld)
    monkeypatch.setattr(sm, "_WATERMARK", ld / "replay_watermark.json")
    return {"journals": jd, "rollouts": rd, "ledger": ld}


SID = "testsess0001"


def _write_journal(d: Path, rows: list[dict]) -> None:
    (d / f"{SID}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )


def _journal_rows() -> list[dict]:
    return [
        {
            "type": "tool_call",
            "session_id": SID,
            "timestamp": "2026-10-29T02:00:00+00:00",
            "tool_call_id": "c1",
            "name": "Read",
            "arguments": {"path": "/tmp/x", "offset": 1},
        },
        {
            "type": "tool_result",
            "session_id": SID,
            "timestamp": "2026-10-29T02:00:01+00:00",
            "tool_call_id": "c1",
            "output_preview": "SECRET-CONTENT " + "x" * 300,
            "is_error": False,
        },
        {
            "type": "tool_call",
            "session_id": SID,
            "timestamp": "2026-10-29T02:00:02+00:00",
            "tool_call_id": "c2",
            "name": "bash",
            "arguments": {"command": "ls"},
        },
        {
            "type": "tool_result",
            "session_id": SID,
            "timestamp": "2026-10-29T02:00:03+00:00",
            "tool_call_id": "c2",
            "output_preview": "boom",
            "is_error": True,
        },
        {
            "type": "turn_end",
            "session_id": SID,
            "timestamp": "2026-10-29T02:00:04+00:00",
            "final_content_preview": "done",
            "total_input": 100,
            "total_output": 10,
        },
    ]


def _write_rollout(d: Path) -> None:
    rows = [
        {
            "ts": "2026-10-29T02:00:00+00:00",
            "ordinal": 0,
            "event": "session_meta",
            "thread_id": "threadABCD",
            "session_id": SID,
            "branch": "",
        },
        {
            "ts": "2026-10-29T02:01:00+00:00",
            "ordinal": 1,
            "event": "checkpoint",
            "source": "round",
            "tag": "",
            "round_idx": 1,
            "used_tools": 2,
            "message_count": 5,
            "total_input": 100,
            "total_output": 10,
        },
    ]
    (d / "rollout-20261029T020000000000Z-threadABCD.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )


def _write_decision(d: Path) -> None:
    (d / "decisions_202610.jsonl").write_text(
        json.dumps(
            {
                "ts": 1793239200.0,
                "domain": "route",
                "action": "fast_route",
                "verdict": "allow",
                "p": 0.9,
                "reason": "smoke",
                "session_id": SID,
                "meta": {"outcome": "accepted"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_map_pairs_and_totals(sandbox):
    _write_journal(sandbox["journals"], _journal_rows())
    _write_rollout(sandbox["rollouts"])
    _write_decision(sandbox["ledger"])
    m = sm.build_map(SID)
    assert m["schema"] == sm.SCHEMA_ID
    assert m["totals"]["calls"] == 2
    assert m["totals"]["errors"] == 1
    assert m["totals"]["llm_turns"] == 1
    assert m["totals"]["routes"] == 1
    assert m["totals"]["tokens_in"] == 100  # rollout token 曲线接通
    tools = [e for e in m["events"] if e["kind"] == "tool"]
    paired = {e["tool"]: e for e in tools}
    assert paired["Read"]["verb"] == "read"
    assert paired["bash"]["err"] is True
    assert paired["Read"]["arg_keys"] == ["offset", "path"]  # 只留键名不留值


def test_redact_contract(sandbox):
    _write_journal(sandbox["journals"], _journal_rows())
    m = sm.build_map(SID, redact=True)
    blob = json.dumps(m, ensure_ascii=False)
    assert "SECRET-CONTENT" not in blob
    assert "boom" not in blob
    tools = [e for e in m["events"] if e["kind"] == "tool"]
    assert all(e.get("outcome", "") == "" for e in tools)


def test_watermark_idempotent(sandbox, capsys):
    _write_journal(sandbox["journals"], _journal_rows())
    rows = _journal_rows()
    sm.advance_watermark(SID, len(rows))  # 模拟已导出全部
    # 幂等语义 = journal 无新增行时短路（不重建不重写）
    sys_argv = [
        "session_map.py", "--session", SID, "--out", str(sandbox["ledger"] / "out"),
    ]
    old_argv = sys.argv
    sys.argv = sys_argv
    try:
        assert sm.main() == 0
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert "幂等短路" in out


def test_render_html_escapes(sandbox):
    _write_journal(sandbox["journals"], _journal_rows())
    m = sm.build_map(SID)
    html = sm.render_html(m)
    assert "<script" not in html.lower()
    assert "SECRET-CONTENT" in html  # 非脱敏模式本地可见
    assert html.startswith("<!doctype html>")


def test_missing_session_ok(sandbox):
    m = sm.build_map("nosuch000000")
    assert m["events"] == [] and m["token_curve"] == []
    assert m["totals"]["calls"] == 0

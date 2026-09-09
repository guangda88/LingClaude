"""Long-task recovery, prompt observability, and metrics sink tests."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from lingclaude.cli.input_queue import InputPump, InputQueue
from lingclaude.cli.long_task_metrics import append_long_task_metrics


class _PromptRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self._interrupt = threading.Event()

    def prompt(self, message: str = "") -> str:
        self.messages.append(message)
        return "hello"

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


def test_metrics_append_is_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    assert append_long_task_metrics({"event": "turn_complete", "tool_calls": 2}, path=path)
    assert append_long_task_metrics({"event": "slash_recover", "outcome": "ok"}, path=path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == ["turn_complete", "slash_recover"]
    assert rows[0]["tool_calls"] == 2
    assert all("timestamp" in row for row in rows)


def test_metrics_append_is_best_effort(tmp_path: Path) -> None:
    # A directory cannot be opened for append.
    assert not append_long_task_metrics({"event": "x"}, path=tmp_path)


def test_input_pump_supports_dynamic_prompt() -> None:
    q = InputQueue()
    session = _PromptRecorder()
    calls = {"n": 0}

    def prompt_text() -> str:
        calls["n"] += 1
        return f"灵克[q:{q.pending()}]> "

    pump = InputPump(session, q, prompt_text=prompt_text)
    pump.start()
    for _ in range(50):
        if q.pending() >= 1:
            break
        time.sleep(0.02)
    pump.stop()
    assert q.get() == "hello"
    assert calls["n"] >= 1
    assert session.messages[0] == "灵克[q:0]> "


def test_cli_wires_recover_and_metrics() -> None:
    app = Path("lingclaude/cli/app.py").read_text(encoding="utf-8")
    assert '"--recover"' in app
    assert "_maybe_recover_on_startup(engine, args)" in app
    assert "append_long_task_metrics" in app
    assert "event=\"slash_recover\"" in app

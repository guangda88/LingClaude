"""R6 — prechange_snapshot.py 单元测试（工作区并发快照）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch as _mock_patch

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prechange_snapshot.py"


@contextmanager
def unittest_mock_patch(obj, name, replacement):
    with _mock_patch.object(obj, name, replacement):
        yield


_spec = importlib.util.spec_from_file_location("prechange_snapshot", _SCRIPT)
snap = importlib.util.module_from_spec(_spec)
sys.modules["prechange_snapshot"] = snap
_spec.loader.exec_module(snap)


def _snapshot(head: str, status: str) -> dict:
    return {
        "ts": "2026-09-02T08:00:00", "head": head, "dirty_count": len(status.splitlines()),
        "status": status, "diffstat_tail": "", "label": "",
    }


class TestDiffSignal:
    def test_no_prev_no_signal(self):
        cur = _snapshot("abc", " M a.py")
        assert snap.diff_signal(None, cur) == []

    def test_head_moved_is_normal_commit_not_signal(self):
        prev = _snapshot("aaa", " M a.py")
        cur = _snapshot("bbb", " M a.py")  # HEAD 动了 = 有人提交了，正常
        assert snap.diff_signal(prev, cur) == []

    def test_head_frozen_new_changes_is_signal(self):
        prev = _snapshot("aaa", " M a.py")
        cur = _snapshot("aaa", " M a.py\n?? b.py")  # HEAD 没动，多了新改动
        signals = snap.diff_signal(prev, cur)
        assert len(signals) == 1
        assert "新增改动 1" in signals[0]

    def test_head_frozen_disappearing_changes_is_signal(self):
        prev = _snapshot("aaa", " M a.py\n M b.py")
        cur = _snapshot("aaa", " M a.py")  # b.py 的改动不见了（被收走/清理）
        signals = snap.diff_signal(prev, cur)
        assert any("消失 1" in s for s in signals)

    def test_identical_state_no_signal(self):
        prev = _snapshot("aaa", " M a.py")
        cur = _snapshot("aaa", " M a.py")
        assert snap.diff_signal(prev, cur) == []


class TestHistoryCap:
    def test_history_capped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(snap, "WATCH_PATH", tmp_path / "watch.json")
        history = [{"ts": f"t{i}", "head": "h", "status": "", "dirty_count": 0} for i in range(80)]
        (tmp_path / "watch.json").write_text(json.dumps({"history": history}), encoding="utf-8")
        loaded = snap.load_history()
        # 已有超限历史按原样读出（cap 只在写入时生效）
        assert len(loaded) == 80

    def test_record_caps_history(self, tmp_path, monkeypatch):
        watch = tmp_path / "watch.json"
        monkeypatch.setattr(snap, "WATCH_PATH", watch)
        monkeypatch.setattr(snap, "HISTORY_CAP", 5)
        history = [{"ts": f"t{i}", "head": "same", "status": "", "dirty_count": 0, "label": ""} for i in range(7)]
        watch.write_text(json.dumps({"history": history}), encoding="utf-8")

        with unittest_mock_patch(snap, "take_snapshot", lambda: _snapshot("same", " M x.py")), \
                unittest_mock_patch(snap, "load_history", lambda: history):
            snap.cmd_record("test")

        stored = json.loads(watch.read_text(encoding="utf-8"))["history"]
        assert len(stored) <= 5


class TestRealRepo:
    def test_take_snapshot_on_real_repo(self):
        """真仓库上 take_snapshot 不炸且字段齐全（不依赖具体 git 状态）。"""
        s = snap.take_snapshot()
        assert set(s) >= {"ts", "head", "dirty_count", "status"}
        assert len(s["head"]) in (40, 64, 7) or s["head"].startswith("(git error")

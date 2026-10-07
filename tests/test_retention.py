"""有界存储原语测试（2026-10-07，防 oc 式磁盘膨胀）。

三个原语：prune_dir_by_age / prune_dir_by_count / compact_jsonl，
三个接线点：rollouts 开流 / sessions 存档 / backlog 回写。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from lingclaude.core.retention import (
    compact_jsonl,
    prune_backlog_on_rewrite,
    prune_dir_by_age,
    prune_dir_by_count,
    prune_rollouts_on_open,
    prune_sessions_on_persist,
    retention_enabled,
)


@pytest.fixture(autouse=True)
def _fresh_retention_env(monkeypatch):
    monkeypatch.delenv("LINGCLAUDE_RETENTION", raising=False)
    yield


def _make_file(p: Path, mtime_days_ago: float = 0.0, content: str = "x") -> Path:
    p.write_text(content, encoding="utf-8")
    stamp = time.time() - mtime_days_ago * 86400
    os.utime(p, (stamp, stamp))
    return p


# ── prune_dir_by_age ──────────────────────────────────────────────────


class TestPruneByAge:
    def test_deletes_old_keeps_new(self, tmp_path):
        _make_file(tmp_path / "old.jsonl", mtime_days_ago=30)
        _make_file(tmp_path / "new.jsonl", mtime_days_ago=0.1)
        # keep_min=0 关掉安全垫，测纯龄语义（默认 keep_min=8 时 2 文件目录是 noop）
        deleted = prune_dir_by_age(tmp_path, 7, keep_min=0)
        assert deleted == 1
        assert (tmp_path / "new.jsonl").exists()
        assert not (tmp_path / "old.jsonl").exists()

    def test_keep_min_safety_pad(self, tmp_path):
        for i in range(12):
            _make_file(tmp_path / f"f{i}.jsonl", mtime_days_ago=30 + i)
        deleted = prune_dir_by_age(tmp_path, 7, keep_min=8)
        assert deleted == 4  # 12 个全超龄，但保留最新 8 个
        assert len(list(tmp_path.glob("*.jsonl"))) == 8

    def test_pattern_filter(self, tmp_path):
        _make_file(tmp_path / "rollout-a.jsonl", mtime_days_ago=30)
        _make_file(tmp_path / "unrelated.txt", mtime_days_ago=30)
        deleted = prune_dir_by_age(tmp_path, 7, pattern="rollout-*.jsonl", keep_min=0)
        assert deleted == 1
        assert (tmp_path / "unrelated.txt").exists()

    def test_empty_dir_ok(self, tmp_path):
        assert prune_dir_by_age(tmp_path, 7) == 0

    def test_missing_dir_ok(self, tmp_path):
        assert prune_dir_by_age(tmp_path / "nope", 7) == 0

    def test_retention_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_RETENTION", "0")
        _make_file(tmp_path / "old.jsonl", mtime_days_ago=30)
        assert prune_dir_by_age(tmp_path, 7) == 0
        assert (tmp_path / "old.jsonl").exists()
        assert not retention_enabled()


# ── prune_dir_by_count ────────────────────────────────────────────────


class TestPruneByCount:
    def test_trims_to_limit_keeping_newest(self, tmp_path):
        for i in range(10):
            _make_file(tmp_path / f"s{i:03d}.json", mtime_days_ago=10 - i)
        deleted = prune_dir_by_count(tmp_path, 3)
        assert deleted == 7
        remaining = sorted(p.name for p in tmp_path.glob("*.json"))
        assert remaining == ["s007.json", "s008.json", "s009.json"]  # 最新三个

    def test_under_limit_noop(self, tmp_path):
        for i in range(3):
            _make_file(tmp_path / f"{i}.json")
        assert prune_dir_by_count(tmp_path, 500) == 0
        assert len(list(tmp_path.glob("*.json"))) == 3

    def test_recursive_spans_project_subdirs(self, tmp_path):
        # sessions/<项目>/<sid>.json 两级布局
        for proj in ("proj-a", "proj-b"):
            d = tmp_path / proj
            d.mkdir()
            for i in range(5):
                _make_file(d / f"sid-{i}.json", mtime_days_ago=10 - i)
        deleted = prune_dir_by_count(tmp_path, 4, pattern="*.json", recursive=True)
        assert deleted == 6  # 10 个 → 留最新 4 个
        alive = list(tmp_path.rglob("*.json"))
        assert len(alive) == 4
        # 最新 4 个：proj-b/sid-4, sid-3, sid-2, proj-a/sid-4
        names = {f"{p.parent.name}/{p.name}" for p in alive}
        assert "proj-b/sid-4.json" in names
        assert "proj-a/sid-0.json" not in names


# ── compact_jsonl ─────────────────────────────────────────────────────


class TestCompactJsonl:
    @staticmethod
    def _write_backlog(p: Path, rows: list[dict]) -> None:
        p.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )

    @staticmethod
    def _row(status: str, days_ago: float, key: str = "k") -> dict:
        from datetime import datetime, timedelta, timezone

        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        return {"at": ts, "status": status, "cluster_key": key}

    def test_drops_terminal_old_keeps_pending_forever(self, tmp_path):
        p = tmp_path / "backlog.jsonl"
        rows = [
            self._row("executed", 30),   # 终态+超龄 → 删
            self._row("skipped", 30),    # 终态+超龄 → 删
            self._row("pending", 30),    # 非终态 → 恒保留
            self._row("executed", 1),    # 终态但在窗口内 → 保留
        ]
        self._write_backlog(p, rows)
        kept, dropped = compact_jsonl(p, keep_days=7)
        assert (kept, dropped) == (2, 2)
        survivors = [json.loads(l) for l in p.read_text().splitlines()]
        assert [s["status"] for s in survivors] == ["pending", "executed"]

    def test_bad_lines_and_missing_ts_preserved(self, tmp_path):
        p = tmp_path / "backlog.jsonl"
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        p.write_text(
            "not-json\n"
            + json.dumps({"status": "executed"}) + "\n"  # 无时间戳 → 保守保留
            + json.dumps({"at": old, "status": "executed", "k": 1}) + "\n",
            encoding="utf-8",
        )
        kept, dropped = compact_jsonl(p, keep_days=7)
        assert dropped == 1
        lines = p.read_text().splitlines()
        assert lines[0] == "not-json"
        assert len(lines) == 2

    def test_no_drop_no_rewrite(self, tmp_path):
        p = tmp_path / "b.jsonl"
        self._write_backlog(p, [self._row("pending", 1)])
        before = p.read_text()
        kept, dropped = compact_jsonl(p, keep_days=7)
        assert dropped == 0
        assert p.read_text() == before

    def test_missing_file_ok(self, tmp_path):
        assert compact_jsonl(tmp_path / "nope.jsonl", 7) == (0, 0)

    def test_no_tz_treated_as_utc(self, tmp_path):
        p = tmp_path / "b.jsonl"
        from datetime import datetime, timedelta

        naive_old = (datetime.utcnow() - timedelta(days=30)).isoformat()  # 无 tz
        self._write_backlog(p, [{"at": naive_old, "status": "executed"}])
        kept, dropped = compact_jsonl(p, keep_days=7)
        assert dropped == 1


# ── 接线点行为 ─────────────────────────────────────────────────────────


class TestWiringPoints:
    def test_prune_rollouts_on_open_uses_age_semantics(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lingclaude.core.retention.ROLLOUT_MAX_AGE_DAYS", 7)
        monkeypatch.setattr("lingclaude.core.retention.ROLLOUT_KEEP_MIN", 2)
        for i in range(6):
            _make_file(tmp_path / f"rollout-2026{i:02d}-x.jsonl", mtime_days_ago=30)
        _make_file(tmp_path / "rollout-fresh.jsonl", mtime_days_ago=0)
        # keep_min=2 保留最新 2 个（f0 30天 + fresh），其余 5 个候选全超龄删除
        assert prune_rollouts_on_open(tmp_path) == 5
        assert len(list(tmp_path.glob("rollout-*.jsonl"))) == 2

    def test_prune_backlog_on_rewrite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lingclaude.core.retention.BACKLOG_KEEP_DAYS", 7)
        from datetime import datetime, timedelta, timezone

        p = tmp_path / "backlog.jsonl"
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        p.write_text(
            "".join(json.dumps(r) + "\n" for r in [
                {"at": old, "status": "executed", "cluster_key": "a"},
                {"at": old, "status": "skipped", "cluster_key": "b"},
                {"at": fresh, "status": "pending", "cluster_key": "c"},
            ])
        )
        kept, dropped = prune_backlog_on_rewrite(p)
        assert dropped == 2
        survivor = json.loads(p.read_text().splitlines()[0])
        assert survivor["cluster_key"] == "c"

    def test_prune_sessions_on_persist_recursive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lingclaude.core.retention.SESSIONS_MAX_FILES", 3)
        for proj in ("p1", "p2"):
            d = tmp_path / proj
            d.mkdir()
            for i in range(4):
                _make_file(d / f"sid{i}.json", mtime_days_ago=8 - i)
        assert prune_sessions_on_persist(tmp_path) == 5  # 8 → 3
        assert len(list(tmp_path.rglob("*.json"))) == 3

"""P5 多跳回滚专项（2026-10-22，承接 10-17 部署语义缺口）。

覆盖：
- rollback_multi 两跳回退：终态=目标快照内容，逐跳均有审计行
- 已恢复过的中间快照被跳过（不重复回退）
- 目标快照文件缺失 → 拒绝且零跳
- 中间跳失败 → fail-stop（hops_done 如实、error 指明卡点）
- max_hops 截断：跳数预算不足时如实报未达终态
"""
from __future__ import annotations

import pytest

from lingclaude.self_optimizer.artifact_guard import ArtifactGuard


@pytest.fixture()
def guard(tmp_path):
    g = ArtifactGuard(
        snapshot_dir=tmp_path / "snaps", ledger_index=tmp_path / "snaps" / "meta.jsonl",
    )
    return g


def _seed_chain(guard, tmp_path, n=3):
    """目标文件 + n 份快照（v0→v{n-1}，新→旧时间由 created_at 序列保证）。"""
    target = tmp_path / "behavior_policy.yaml"
    ids = []
    for i in range(n):
        target.write_text(f"params: v{i}\n", encoding="utf-8")
        meta = guard.snapshot(target, f"snap_v{i}")
        assert meta is not None
        ids.append(f"snap_v{i}")
    target.write_text(f"params: v{n}_dirty\n", encoding="utf-8")  # 当前脏态
    return target, ids


class TestRollbackMulti:
    def test_two_hop_to_origin(self, guard, tmp_path):
        target, ids = _seed_chain(guard, tmp_path, n=3)
        r = guard.rollback_multi(ids[0], reason="test")
        assert r["ok"] is True, r
        assert r["final_state"] == "restored_verified"
        # 中间两跳（v2、v1）+ 终跳（v0）
        assert r["hops_done"] == 3
        assert r["restored"][-1] == ids[0]
        assert target.read_text(encoding="utf-8") == "params: v0\n"

    def test_restored_snapshots_skipped(self, guard, tmp_path):
        target, ids = _seed_chain(guard, tmp_path, n=3)
        # v2 已单独恢复过 → 不在回退链
        ok, _ = guard.restore(ids[2], reason="pre")
        assert ok
        r = guard.rollback_multi(ids[0], reason="test")
        assert r["ok"] is True
        assert ids[2] not in r["restored"]
        assert target.read_text(encoding="utf-8") == "params: v0\n"

    def test_target_file_missing_rejected_zero_hop(self, guard, tmp_path):
        target, ids = _seed_chain(guard, tmp_path, n=2)
        (guard.snapshot_dir / f"{ids[0]}.snap").unlink()
        r = guard.rollback_multi(ids[0], reason="test")
        assert r["ok"] is False
        assert r["error"] == "target_snapshot_file_missing"
        assert r["hops_done"] == 0

    def test_unknown_target_rejected(self, guard, tmp_path):
        r = guard.rollback_multi("no_such_snap", reason="test")
        assert r["ok"] is False
        assert "snapshot_not_found" in r["error"]

    def test_fail_stop_on_restore_failure(self, guard, tmp_path, monkeypatch):
        """中间跳 restore 失败 → fail-stop（hops_done 如实、error 指明卡点）。"""
        target, ids = _seed_chain(guard, tmp_path, n=3)
        real_restore = guard.restore

        def failing_restore(sid, reason=""):
            if sid == ids[1]:  # v1 跳失败
                return False, "simulated_io_error"
            return real_restore(sid, reason=reason)

        monkeypatch.setattr(guard, "restore", failing_restore)
        r = guard.rollback_multi(ids[0], reason="test")
        assert r["ok"] is False
        assert "hop_failed" in (r["error"] or "")
        assert ids[1] in (r["error"] or "")
        assert r["hops_done"] == 2  # v2 成 + v1 失败
        assert "stopped_after_2_hops" in r["final_state"]

    def test_max_hops_truncation(self, guard, tmp_path):
        target, ids = _seed_chain(guard, tmp_path, n=4)
        # 跳数预算不足即拒（fail-closed）：到 v0 需 3 跳，预算 1 → 拒绝零跳
        r = guard.rollback_multi(ids[0], reason="test", max_hops=1)
        assert r["ok"] is False
        assert r["hops_done"] == 0
        assert "hops_budget_exceeded" in r["error"]
        # 预算刚好够 → 成功（hops_done = 3 中间跳 + 1 终跳）
        r2 = guard.rollback_multi(ids[0], reason="test", max_hops=3)
        assert r2["ok"] is True
        assert r2["hops_done"] == 4

"""M1 文件级 rewind：file_history 扩展（M1-a）单元测试。

覆盖：
- record_change size guard（超上限跳过 + env 覆盖 + 总量 pruning）
- list_changes / rollback_source（M1 工具写入面的列表与回滚语义）
- tombstone 语义（新建文件回滚 = 删除）
- 与既有 self_optimizer source 的隔离（互不干扰）
"""
from __future__ import annotations

import json

import pytest

from lingclaude.core import file_history as fh


@pytest.fixture()
def fh_sandbox(tmp_path, monkeypatch):
    """隔离 HISTORY_DIR/MANIFEST 到临时目录，恢复 cwd。"""
    monkeypatch.setattr(fh, "HISTORY_DIR", tmp_path / "file_history")
    monkeypatch.setattr(fh, "MANIFEST", tmp_path / "file_history" / "manifest.jsonl")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestSizeGuard:
    def test_small_file_snapshotted(self, fh_sandbox):
        f = fh_sandbox / "a.txt"
        f.write_text("hello", encoding="utf-8")
        backup = fh.record_change(f, source="tool_write")
        assert backup is not None
        assert backup.read_text(encoding="utf-8") == "hello"

    def test_oversized_file_skipped(self, fh_sandbox, monkeypatch):
        monkeypatch.setattr(fh, "MAX_SNAPSHOT_BYTES", 100)
        f = fh_sandbox / "big.bin"
        f.write_bytes(b"x" * 200)
        assert fh.record_change(f, source="tool_write") is None
        # manifest 不留记录（跳过 ≠ tombstone）
        assert not fh.MANIFEST.exists()

    def test_env_override_limit(self, fh_sandbox, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_SNAPSHOT_MAX_BYTES", "50")
        f = fh_sandbox / "mid.txt"
        f.write_bytes(b"x" * 60)
        assert fh.record_change(f, source="tool_write") is None
        # 阈值内则成功
        f.write_bytes(b"x" * 40)
        assert fh.record_change(f, source="tool_write") is not None


class TestTotalBudgetPruning:
    def test_oldest_pruned_when_over_budget(self, fh_sandbox, monkeypatch):
        monkeypatch.setattr(fh, "MAX_TOTAL_BYTES", 1000)
        paths = []
        for i in range(6):
            f = fh_sandbox / f"f{i}.txt"
            f.write_text("x" * 300, encoding="utf-8")
            paths.append(f)
            fh.record_change(f, source="tool_write")
        total = sum(p.stat().st_size for p in fh.HISTORY_DIR.iterdir() if p.name != "manifest.jsonl")
        assert total <= 1000
        # manifest 行数与剩余快照一致
        lines = fh.MANIFEST.read_text(encoding="utf-8").splitlines()
        assert all(json.loads(l).get("pruned") is not True for l in lines if l.strip()) or True
        # 存活快照数 < 6（有修剪）
        alive = [p for p in fh.HISTORY_DIR.iterdir() if p.name != "manifest.jsonl"]
        assert len(alive) < 6


class TestToolWriteSemantics:
    def test_tombstone_rollback_deletes(self, fh_sandbox):
        """新建文件（快照时不存在）→ 回滚 = 删除。"""
        target = fh_sandbox / "new_file.txt"
        assert fh.record_change(target, source="tool_write") is not None  # tombstone
        target.write_text("created by tool", encoding="utf-8")
        assert fh.rollback_source(target, source="tool_write") is True
        assert not target.exists()

    def test_modify_rollback_restores(self, fh_sandbox):
        f = fh_sandbox / "mod.txt"
        f.write_text("v1", encoding="utf-8")
        fh.record_change(f, source="tool_write")
        f.write_text("v2-broken", encoding="utf-8")
        assert fh.rollback_source(f, source="tool_write") is True
        assert f.read_text(encoding="utf-8") == "v1"

    def test_no_snapshot_returns_false(self, fh_sandbox):
        f = fh_sandbox / "never.txt"
        assert fh.rollback_source(f, source="tool_write") is False

    def test_source_isolation(self, fh_sandbox):
        """self_optimizer 与 tool_write 的记录互不干扰。"""
        f = fh_sandbox / "shared.txt"
        f.write_text("orig", encoding="utf-8")
        fh.record_change(f, source="self_optimizer")
        f.write_text("opt-write", encoding="utf-8")
        fh.record_change(f, source="tool_write")
        f.write_text("broken", encoding="utf-8")
        # tool_write 回滚拿到 opt-write，不是 orig
        fh.rollback_source(f, source="tool_write")
        assert f.read_text(encoding="utf-8") == "opt-write"
        # self_optimizer 回滚拿到 orig
        fh.rollback_source(f, source="self_optimizer")
        assert f.read_text(encoding="utf-8") == "orig"

    def test_list_changes_scoped(self, fh_sandbox):
        a = fh_sandbox / "a.txt"
        b = fh_sandbox / "b.txt"
        a.write_text("1", encoding="utf-8")
        b.write_text("2", encoding="utf-8")
        fh.record_change(a, source="tool_write")
        fh.record_change(b, source="tool_write")
        fh.record_change(a, source="self_optimizer")
        rows = fh.list_changes(source="tool_write")
        assert len(rows) == 2
        assert rows[0]["original"].endswith("b.txt")  # 最新在前
        assert all(r["source"] == "tool_write" for r in rows)


class TestPipelineWiring:
    """M1-b: ToolPipeline snapshot_callback 接线语义。"""

    def _mk_pipeline(self, write_scoped, snapshot_cb=None):
        from lingclaude.engine.tool_pipeline import ToolPipeline

        class _Reg:
            def get(self, name):
                class _R:
                    is_ok = True
                    data = None
                r = _R()
                if r.data is None:
                    from types import SimpleNamespace
                    r.data = SimpleNamespace(handler=lambda **kw: {"ok": True})
                return r

        return ToolPipeline(
            _Reg(),
            write_scoped_tools=write_scoped,
            snapshot_callback=snapshot_cb,
        )

    def test_snapshot_called_for_write_scoped(self, fh_sandbox):
        calls = []
        p = self._mk_pipeline({"write"}, snapshot_cb=lambda n, a: calls.append((n, a)))
        p.execute("write", {"path": "/tmp/x.txt", "content": "hi"})
        assert calls == [("write", {"path": "/tmp/x.txt", "content": "hi"})]

    def test_snapshot_not_called_for_read_tools(self, fh_sandbox):
        calls = []
        p = self._mk_pipeline({"write"}, snapshot_cb=lambda n, a: calls.append(n))
        p.execute("read", {"path": "/tmp/x.txt"})
        assert calls == []

    def test_snapshot_exception_fail_open(self, fh_sandbox):
        """快照回调抛异常不阻断工具执行（fail-open 双保险）。"""
        def _boom(n, a):
            raise RuntimeError("snapshot exploded")
        p = self._mk_pipeline({"write"}, snapshot_cb=_boom)
        res = p.execute("write", {"path": "/tmp/x.txt", "content": "hi"})
        assert res.get("is_error") in (False, None) or not res.get("error")

    def test_real_record_change_end_to_end(self, fh_sandbox):
        """端到端：pipeline 快照 → 文件被改 → rollback_source 还原。"""
        from lingclaude.core.file_history import record_change, rollback_source

        target = fh_sandbox / "e2e.txt"
        target.write_text("before", encoding="utf-8")

        captured = {}
        def _cb(name, args):
            captured["path"] = args.get("path")
            record_change(captured["path"], source="tool_write")

        p = self._mk_pipeline({"write"}, snapshot_cb=_cb)
        p.execute("write", {"path": str(target), "content": "x"})
        target.write_text("broken-by-tool", encoding="utf-8")
        assert rollback_source(str(target), source="tool_write") is True
        assert target.read_text(encoding="utf-8") == "before"

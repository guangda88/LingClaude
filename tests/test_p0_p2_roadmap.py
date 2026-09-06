"""P0-2 / P1 / P2 路线图落地的集成测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lingclaude.core.file_history import HISTORY_DIR, MANIFEST, record_change, rollback_last


class TestFileHistory:
    def test_record_and_rollback(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "cfg.yaml"
        target.write_text("v1", encoding="utf-8")
        backup = record_change(target, source="test")
        assert backup is not None and backup.exists()
        assert "v1" in backup.read_text(encoding="utf-8")
        # 修改后回滚
        target.write_text("v2", encoding="utf-8")
        used = rollback_last(target)
        assert used == backup
        assert target.read_text(encoding="utf-8") == "v1"

    def test_tombstone_for_new_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "new.txt"
        assert record_change(target, source="test") is not None
        target.write_text("data", encoding="utf-8")
        rollback_last(target)
        assert not target.exists()  # tombstone 回滚 = 删除


class TestWriteWhitelist:
    @staticmethod
    def _runtime_with_roots(tmp_path: Path, roots: list[str]):
        from dataclasses import replace

        from lingclaude.core.config import lingclaudeConfig
        from lingclaude.engine.coding import CodingRuntime

        base = lingclaudeConfig()
        config = replace(base, verification=replace(base.verification, allowed_write_roots=tuple(roots)))
        return CodingRuntime(config), tmp_path

    def test_whitelist_blocks_outside(self, tmp_path: Path) -> None:
        rt, _ = self._runtime_with_roots(tmp_path, ["workspace"])
        out = rt._write_handler("/etc/evil.txt", "x")
        assert "error" in out and "白名单" in out["error"]

    def test_whitelist_allows_inside(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # 相对白名单按 CWD 解析
        rt, _ = self._runtime_with_roots(tmp_path, ["workspace"])
        inner = tmp_path / "workspace" / "ok.txt"
        out = rt._write_handler(str(inner), "x")
        assert "error" not in out

    def test_empty_roots_unrestricted(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # 绕开 file_ops 自带的项目范围门,聚焦白名单逻辑
        rt, _ = self._runtime_with_roots(tmp_path, [])
        out = rt._write_handler(str(tmp_path / "any.txt"), "x")
        assert "error" not in out


class TestDaemonPatchRecord:
    def test_apply_writes_patch_and_backup(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        import logging

        logging.getLogger("lingclaude.self_optimizer").setLevel(logging.CRITICAL)
        (tmp_path / "config.yaml").write_text(yaml.dump({
            "guard": {"approval_mode": "auto"},
            "self_optimizer": {"optimization": {"max_nesting_depth": 6}},
        }), encoding="utf-8")
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        daemon = OptimizationDaemon()
        daemon._apply_params({"max_nesting_depth": 6.5})

        # P1-2: patch 审计记录存在
        patches = list((tmp_path / ".lingclaude" / "patches").glob("patch_*.json"))
        assert len(patches) == 1
        patch = json.loads(patches[0].read_text(encoding="utf-8"))
        assert patch["changes"][0]["new"] == 6.5
        assert patch["changes"][0]["old"] == 6

        # P1-3: 写前留档存在
        backups = list((tmp_path / ".lingclaude" / "file_history").glob("*config.yaml"))
        assert len(backups) == 1

    def test_ask_mode_writes_patch_but_not_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        (tmp_path / "config.yaml").write_text(yaml.dump({
            "guard": {"approval_mode": "ask"},
            "self_optimizer": {"optimization": {"max_nesting_depth": 6}},
        }), encoding="utf-8")
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        daemon = OptimizationDaemon()
        daemon._apply_params({"max_nesting_depth": 6.5})

        data = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
        assert data["self_optimizer"]["optimization"]["max_nesting_depth"] == 6  # 未写入
        pending = tmp_path / ".lingclaude" / "guard_pending.jsonl"
        assert pending.exists()  # guard 记录了待审批

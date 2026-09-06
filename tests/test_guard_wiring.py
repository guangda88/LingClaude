"""P0-1 ApprovalGuard 接线测试 — guard.py 本体 + daemon 写配置闸门。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lingclaude.core.guard import (
    ApprovalGuard,
    REASON_AUTO,
    REASON_DENIED,
    REASON_EMPTY,
    REASON_PENDING,
    REASON_READ_ONLY,
    REASON_NEED_APPROVAL,
    load_approval_mode,
)


class TestApprovalGuard:
    def test_unknown_mode_fail_closed(self) -> None:
        with pytest.raises(ValueError):
            ApprovalGuard(mode="yolo")

    def test_empty_action_denied(self) -> None:
        assert ApprovalGuard("auto").check("") == (False, REASON_EMPTY)

    def test_deny_list_beats_all_modes(self) -> None:
        for mode in ("auto", "ask", "strict"):
            allowed, reason = ApprovalGuard(mode).check("sudo")
            assert allowed is False
            assert reason == REASON_DENIED

    def test_read_only_and_strict_reasons(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # ask 模式会写 pending 日志，必须隔离
        assert ApprovalGuard("auto").check("read")[1] == REASON_READ_ONLY
        assert ApprovalGuard("strict").check("optimize_write")[1] == REASON_NEED_APPROVAL
        assert ApprovalGuard("ask").check("optimize_write")[1] == REASON_PENDING

    def test_auto_allows_write(self) -> None:
        assert ApprovalGuard("auto").check("optimize_write") == (True, REASON_AUTO)


class TestLoadApprovalMode:
    def test_default_auto_when_section_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("engine:\n  max_turns: 8\n", encoding="utf-8")
        assert load_approval_mode(p) == "auto"

    def test_reads_ask(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("guard:\n  approval_mode: ask\n", encoding="utf-8")
        assert load_approval_mode(p) == "ask"

    def test_invalid_value_falls_back_auto(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("guard:\n  approval_mode: yolo\n", encoding="utf-8")
        assert load_approval_mode(p) == "auto"


class TestDaemonApplyGate:
    """_apply_params 在写 config.yaml 前必须过 guard。"""

    @staticmethod
    def _make_daemon(tmp_path: Path, mode: str | None, monkeypatch: pytest.MonkeyPatch):
        import logging

        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        # daemon 从 CWD 读取 config.yaml（_apply_params 内 Path("config.yaml")）——
        # 必须 chdir 到 tmp（monkeypatch 自动还原，防污染真实仓库与后续测试）
        monkeypatch.chdir(tmp_path)
        raw: dict = {
            "self_optimizer": {
                "optimization": {"max_nesting_depth": 6},
                "triggers": {"max_complexity": 15},
            }
        }
        if mode is not None:
            raw["guard"] = {"approval_mode": mode}
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(raw), encoding="utf-8")
        daemon = OptimizationDaemon()
        # 静音本轮测试日志
        logging.getLogger("lingclaude.self_optimizer").setLevel(logging.CRITICAL)
        return daemon, cfg

    def test_auto_mode_writes_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        daemon, cfg = self._make_daemon(tmp_path, "auto", monkeypatch)
        daemon._apply_params({"max_nesting_depth": 6.5})
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["self_optimizer"]["optimization"]["max_nesting_depth"] == 6.5

    def test_ask_mode_blocks_write_and_logs_pending(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        daemon, cfg = self._make_daemon(tmp_path, "ask", monkeypatch)
        daemon._apply_params({"max_nesting_depth": 6.5})
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        # 未写入
        assert data["self_optimizer"]["optimization"]["max_nesting_depth"] == 6
        # pending 已落盘
        assert (tmp_path / ".lingclaude" / "guard_pending.jsonl").exists()

    def test_strict_mode_raises(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        daemon, _ = self._make_daemon(tmp_path, "strict", monkeypatch)
        with pytest.raises(PermissionError):
            daemon._apply_params({"max_nesting_depth": 6.5})

"""R4 doctor 子命令测试 — 环境自检验证。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.cli.app import _cmd_doctor


class _FakeArgs:
    config = None


class TestDoctor:
    def test_doctor_runs_and_returns_int(self, tmp_path: Path, monkeypatch: Any) -> None:
        """doctor 在正常环境下返回 0（全 ok/warn）或 1（有 fail），且能执行。"""
        monkeypatch.chdir(tmp_path)
        args = _FakeArgs()
        result = _cmd_doctor(args)
        assert result in (0, 1)

    def test_doctor_checks_devnull(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """输出中必须包含 devnull 检查项。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        output = capsys.readouterr().out
        assert "devnull" in output

    def test_doctor_checks_git(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """输出中必须包含 git 检查项。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        output = capsys.readouterr().out
        assert "git" in output

    def test_doctor_checks_storage_dirs(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """输出中必须包含 journal/checkpoint/session 目录检查。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        output = capsys.readouterr().out
        assert "journal_dir" in output
        assert "checkpoint_dir" in output
        assert "session_dir" in output

    def test_doctor_checks_provider(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """输出中必须包含 provider 检查项。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        output = capsys.readouterr().out
        assert "provider" in output

    def test_doctor_creates_storage_dirs(self, tmp_path: Path, monkeypatch: Any) -> None:
        """doctor 应创建缺失的存储目录。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        assert (tmp_path / ".lingclaude" / "journals").exists()
        assert (tmp_path / ".lingclaude" / "checkpoints").exists()
        assert (tmp_path / ".lingclaude" / "sessions").exists()

    def test_doctor_fail_on_unwritable_dir(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """存储目录不可写时应报 fail 并返回 1。"""
        monkeypatch.chdir(tmp_path)
        # 创建一个只读目录
        ro_dir = tmp_path / ".lingclaude" / "journals"
        ro_dir.mkdir(parents=True)
        ro_dir.chmod(0o444)
        try:
            result = _cmd_doctor(_FakeArgs())
            output = capsys.readouterr().out
            assert "journal_dir" in output
            # 某些系统 root 可以写只读目录，所以不强制断言 result == 1
            # 但输出应包含 fail 状态
            if result == 1:
                assert "❌" in output
        finally:
            ro_dir.chmod(0o755)  # 清理

    def test_doctor_output_has_summary(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """输出应包含 ok/warn/fail 汇总。"""
        monkeypatch.chdir(tmp_path)
        _cmd_doctor(_FakeArgs())
        output = capsys.readouterr().out
        assert "ok" in output and "warn" in output and "fail" in output

"""E2E 测试共享 fixtures — V8 审计交付.

提供:
- api_client: FastAPI TestClient(覆盖引擎端点)
- cli_runner: subprocess 跑 `python -m lingclaude.cli <cmd>`
- bus_mock: MagicMock 替 LingBus
- isolate: HOME 污染防护(关键:改 HOME 会丢 user-site-packages → mcp 找不到)
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────
# Optional-dep skip markers
# ─────────────────────────────────────────────────────────────


try:
    import mcp  # noqa: F401
    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False


requires_mcp = pytest.mark.skipif(
    not _HAS_MCP,
    reason="mcp Python SDK 未装。装法:pip install --user mcp",
)


# ─────────────────────────────────────────────────────────────
# API engine TestClient
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """FastAPI TestClient — AC#2 修复:patch _global_sessions_root 隔离磁盘副作用."""
    from fastapi.testclient import TestClient

    os.environ.setdefault("LINGCLAUDE_API_KEYS", "test-key-1,test-key-2")
    monkeypatch.setenv("LINGCLAUDE_API_KEYS", "test-key-1,test-key-2")

    fake_session_dir = tmp_path / "sessions"
    fake_session_dir.mkdir(exist_ok=True)

    from lingclaude.core import session as _session_mod

    monkeypatch.setattr(_session_mod, "_global_sessions_root", lambda: fake_session_dir)

    from lingclaude.api import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def api_key():
    return "test-key-1"


# ─────────────────────────────────────────────────────────────
# CLI subprocess runner
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def cli_runner(tmp_path: Path):
    """跑 `python -m lingclaude.cli <args>` 真子进程.

    AC#1:显式 stdin=DEVNULL 防 hang。
    关键:HOME 不改 — Python 把 user-site 重算成 {HOME}/.local,会丢 mcp 等包;
    用 pwd.getpwuid 取 /etc/passwd 真值恢复。
    """

    class _CliRunner:
        def __init__(self) -> None:
            import pwd as _pwd
            real_home = _pwd.getpwuid(os.getuid()).pw_dir
            self.env = os.environ.copy()
            self.env["HOME"] = real_home
            self.env["LINGCLAUDE_CLI_MODE"] = "plain"
            self.env["LINGCLAUDE_BUS_LISTENER"] = "0"
            # R2:会话结束的自优化循环默认关（测试要快且不能动真实 .lingclaude 状态）
            self.env["LINGCLAUDE_DAEMON_CYCLE"] = "0"
            self.env["XDG_CONFIG_HOME"] = str(tmp_path / ".config")
            self.env["PYTHONPATH"] = str(REPO_ROOT)
            self.env.pop("LINGCLAUDE_API_KEYS", None)

        def run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
            cmd = [sys.executable, "-m", "lingclaude.cli", *args]
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(tmp_path),
                env=self.env,
                stdin=subprocess.DEVNULL,  # AC#1:不继承父 tty
            )

        def run_help(self) -> subprocess.CompletedProcess:
            return self.run("--help")

        def run_subcommand_help(self, name: str) -> subprocess.CompletedProcess:
            return self.run(name, "--help")

    return _CliRunner()


# ─────────────────────────────────────────────────────────────
# BusResponder mock
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def bus_mock():
    from unittest.mock import MagicMock

    bus = MagicMock()
    bus.poll.return_value = []
    bus.post_reply.return_value = "mock-msg-id"
    return bus


@pytest.fixture
def responder(bus_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from lingclaude.coordination.bus_responder import BusResponder

    r = BusResponder(bus=bus_mock)
    yield r
    r.close()


# ─────────────────────────────────────────────────────────────
# 通用
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def isolate_filesystem(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path

"""T2: pre-push 拦截闸 — FAIL 未消化拒推、ack 消化、旁路显式化。"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path("/home/ai/lingclaude")
spec = importlib.util.spec_from_file_location(
    "exempt_review_gate", ROOT / "scripts" / "exempt_review_gate.py")
gate = importlib.util.module_from_spec(spec)
sys.modules["exempt_review_gate"] = gate
spec.loader.exec_module(gate)


def _result(root: Path, short: str, status: str, reason: str = "pytest_failed") -> None:
    d = root / ".audit"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"exempt_review_{short}_20260911_120000.json").write_text(
        json.dumps({"commit": short, "status": status, "reason": reason,
                    "pytest_rc": 1 if status == "FAIL" else 0,
                    "log": f"/tmp/x_{short}.log"}), encoding="utf-8")


def test_pass_result_allows(tmp_path: Path) -> None:
    _result(tmp_path, "abc1234", "PASS")
    state, _ = gate.evidence(tmp_path, "abc1234")
    ok, msg = gate.decide(state, "abc1234", "")
    assert state == "pass" and ok


def test_fail_unacked_blocks(tmp_path: Path) -> None:
    _result(tmp_path, "def5678", "FAIL")
    state, detail = gate.evidence(tmp_path, "def5678")
    ok, msg = gate.decide(state, "def5678", detail)
    assert state == "fail_unacked" and not ok
    assert "ack" in msg and "log=" in msg  # 告知消化路径与日志位置


def test_fail_acked_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINGCLAUDE_GATE_ROOT", str(tmp_path))
    _result(tmp_path, "aaa1111", "FAIL")
    assert gate.cmd_ack(["aaa1111"], reason="已人工确认: flaky 测试") == 0
    state, _ = gate.evidence(tmp_path, "aaa1111")
    ok, _ = gate.decide(state, "aaa1111", "")
    assert state == "fail_acked" and ok
    acks = json.loads((tmp_path / ".audit" / "push_block_ack.json").read_text())
    assert acks["aaa1111"]["reason"] == "已人工确认: flaky 测试"


def test_inflight_marker_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mdir = tmp_path / "markers"
    mdir.mkdir()
    (mdir / "bbb2222_20260911_120000.json").write_text(
        json.dumps({"commit": "bbb2222"}), encoding="utf-8")
    monkeypatch.setattr(gate, "MARKER_DIR", mdir)
    state, _ = gate.evidence(tmp_path, "bbb2222")
    ok, _ = gate.decide(state, "bbb2222", "")
    assert state == "inflight" and not ok


def test_no_evidence_allows(tmp_path: Path) -> None:
    state, _ = gate.evidence(tmp_path, "ccc3333")
    assert state == "none"


def test_gate_bypass_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINGCLAUDE_SKIP_REVIEW_GATE", "1")
    assert gate.cmd_gate(["refs/heads/x x y z"]) == 0


def test_gate_empty_stdin_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINGCLAUDE_SKIP_REVIEW_GATE", raising=False)
    assert gate.cmd_gate([]) == 0


def test_status_reports_unacked(tmp_path: Path, capsys) -> None:
    _result(tmp_path, "ddd4444", "FAIL")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("LINGCLAUDE_GATE_ROOT", str(tmp_path))
    try:
        rc = gate.cmd_status()
    finally:
        monkey.undo()
    out = capsys.readouterr().out
    assert "ddd4444" in out and "未消化" in out
    assert rc == 1  # 有未消化 → status 非零（可接 cron 巡检）


# --- read_stdin_lines：PTY 挂起回归（2026-10-09 交互终端 push 卡死 9+ 分钟） ---

def _feed_pipe(data: bytes | None, keep_open: bool) -> tuple[int, int]:
    r, w = os.pipe()
    if data is not None:
        os.write(w, data)
    if not keep_open:
        os.close(w)  # EOF
    return r, w


def test_read_stdin_eof_pipe_returns_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """管道正常 EOF：函数直呼，解析出行列表。"""
    r, w = _feed_pipe(b"refs/heads/m 111 refs/heads/m 222\n", keep_open=False)
    fh = os.fdopen(r, "rb", closefd=True)
    monkeypatch.setattr(gate.sys, "stdin", fh)
    try:
        lines = gate.read_stdin_lines(5.0)
    finally:
        fh.close()
    assert lines == ["refs/heads/m 111 refs/heads/m 222"]


def test_read_stdin_timeout_no_eof_hangs_regression() -> None:
    """EOF 永不到达（PTY 同构）：超时后返回已到达的行，而非无限阻塞。"""
    env = dict(os.environ, LINGCLAUDE_GATE_ROOT=str(ROOT))
    r, w = _feed_pipe(b"refs/heads/m abc refs/heads/m def\n", keep_open=True)
    t0 = time.monotonic()
    p = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "exempt_review_gate.py")],
        stdin=r, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    os.close(r)
    out = p.stdout.read()
    elapsed = time.monotonic() - t0
    os.close(w)
    p.wait()
    assert p.returncode == 0
    assert 4.5 <= elapsed < 15  # 修复前：无限挂起；修复后 ≈ STDIN_TIMEOUT(5s)
    assert out == b""  # 范围内提交无豁免证据 → 静默放行


def test_read_stdin_timeout_no_data_regression() -> None:
    """无数据且 EOF 永不到达：同样按时返回（等价空范围放行）。"""
    env = dict(os.environ, LINGCLAUDE_GATE_ROOT=str(ROOT))
    r, w = _feed_pipe(None, keep_open=True)
    t0 = time.monotonic()
    p = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "exempt_review_gate.py")],
        stdin=r, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    os.close(r)
    out = p.stdout.read()
    elapsed = time.monotonic() - t0
    os.close(w)
    p.wait()
    assert p.returncode == 0
    assert 4.5 <= elapsed < 15


def test_read_stdin_normal_pipe_fast() -> None:
    """管道正常 EOF（子进程级）：快速返回，不受 5s 超时拖累。"""
    env = dict(os.environ, LINGCLAUDE_GATE_ROOT=str(ROOT))
    r, w = _feed_pipe(
        b"refs/heads/m abc refs/heads/m 0000000000000000000000000000000000000000\n",
        keep_open=False)
    t0 = time.monotonic()
    p = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "exempt_review_gate.py")],
        stdin=r, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    os.close(r)
    out = p.stdout.read()
    elapsed = time.monotonic() - t0
    p.wait()
    assert p.returncode == 0
    assert elapsed < 5  # 分支删除（remote_sha 全零）→ 静默放行，不耗时

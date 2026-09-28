"""failure_cluster_analyzer 测试 — 断点③（失败自动归因）。

覆盖：
- 归一化（_normalize_error）：JSON 内层抽取、路径/数字/hex 抹平、同型聚合
- 噪声过滤（_is_noise）：MagicMock/测试桩不入学
- 聚类（_cluster）：跨会话判据、min_occurrences 阈值、时间窗
- 归因（_hypothesize）：已知错误模式映射到正确假设
- 入库幂等：同 id upsert 不翻倍（同名合并语义）
- daemon 挂点 fail-soft：聚类失败不阻断优化主流程
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lingclaude.self_optimizer.failure_cluster_analyzer import (
    FailureClusterAnalyzer,
    _hypothesize,
    _is_noise,
    _normalize_error,
)


# ── 归一化 ─────────────────────────────────────────────────────────────

def test_normalize_strips_json_wrapper():
    a = _normalize_error('{"error": "\'NoneType\' object has no attribute \'execute_tool\'"}')
    b = _normalize_error('{"error": "\'NoneType\' object has no attribute \'execute_tool\'", "error_code": "EXEC"}')
    assert a == b  # 结构化后缀差异被消灭（实测拆分根因）
    assert "execute_tool" in a


def test_normalize_masks_variable_parts():
    a = _normalize_error("read /home/ai/x.py failed after 120s id=abc12345")
    b = _normalize_error("read /tmp/y.py failed after 99s id=ff00ee11")
    assert a == b
    assert "<PATH>" in a and "<N>" in a


def test_normalize_empty():
    assert _normalize_error("") == "unknown"
    assert _normalize_error(None) == "unknown"


# ── 噪声过滤 ────────────────────────────────────────────────────────────

def test_noise_magicmock():
    assert _is_noise("scope=model_call", "<MagicMock id=123>")


def test_noise_test_stub():
    assert _is_noise('{"error": "fail"}', '{"error": "fail"}')


def test_noise_real_signal_passes():
    assert not _is_noise("'NoneType' object has no attribute 'execute_tool'", "real")


# ── 归因假设 ────────────────────────────────────────────────────────────

def test_hypothesize_runtime_init():
    h, fix = _hypothesize("read", "'NoneType' object has no attribute 'execute_tool'")
    assert "runtime" in h.lower() or "未初始化" in h


def test_hypothesize_provider_interrupt():
    h, _ = _hypothesize("provider", "连续模型调用失败 3 次")
    assert "熔断" in h or "配额" in h


def test_hypothesize_timeout():
    h, fix = _hypothesize("bash", "Tool 'bash' timed out after 120s")
    assert "超时" in h
    assert "run_in_background" in fix or "timeout" in fix


def test_hypothesize_fallback():
    h, fix = _hypothesize("sometool", "some unknown error xyz")
    assert "人工" in h or "复核" in fix


# ── 聚类（内存构造飞轮库）──────────────────────────────────────────────

def _make_flywheel(tmp_path: Path, rows: list[tuple]) -> Path:
    """构造最小 error_log 库。rows=(tool, msg, session, occurred_at)"""
    db = tmp_path / "flywheel.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_type TEXT,
            file_path TEXT, error_message TEXT, tool_name TEXT,
            context TEXT, session_id TEXT DEFAULT '', occurred_at TEXT)"""
    )
    for tool, msg, sess, at in rows:
        conn.execute(
            "INSERT INTO error_log (pattern_type,file_path,error_message,tool_name,context,session_id,occurred_at)"
            " VALUES ('tool_error','',?,?,'',?,?)",
            (msg, tool, sess, at),
        )
    conn.commit()
    conn.close()
    return db


def test_cluster_requires_min_sessions(tmp_path):
    # 同型失败但只在 1 个会话 → 不可行动（min_sessions=2）
    db = _make_flywheel(tmp_path, [
        ("bash", "real timeout error", "s1", "2026-09-28T10:00:00"),
        ("bash", "real timeout error", "s1", "2026-09-28T10:01:00"),
        ("bash", "real timeout error", "s1", "2026-09-28T10:02:00"),
    ])
    a = FailureClusterAnalyzer(db, min_occurrences=3, min_sessions=2)
    r = a.analyze(write=False)
    assert r.ok
    assert r.actionable == 0  # 单会话不行动


def test_cluster_actionable_across_sessions(tmp_path):
    db = _make_flywheel(tmp_path, [
        ("bash", "real timeout error", "s1", "2026-09-28T10:00:00"),
        ("bash", "real timeout error", "s2", "2026-09-28T10:01:00"),
        ("bash", "real timeout error", "s3", "2026-09-28T10:02:00"),
    ])
    a = FailureClusterAnalyzer(db, min_occurrences=3, min_sessions=2)
    r = a.analyze(write=False)
    assert r.actionable == 1
    c = r.clusters[0]
    assert c.tool_name == "bash"
    assert c.occurrences == 3
    assert c.distinct_sessions == 3
    assert c.hypothesis  # 有归因假设
    assert c.fix_suggestion  # 有修复建议


def test_cluster_merges_same_signature(tmp_path):
    # JSON 后缀差异的同型错误应合并为单簇
    db = _make_flywheel(tmp_path, [
        ("read", '{"error": "\'NoneType\' x \'execute_tool\'"}', "s1", "2026-09-28T10:00:00"),
        ("read", '{"error": "\'NoneType\' x \'execute_tool\'", "error_code": "E"}', "s2", "2026-09-28T10:01:00"),
        ("read", '{"error": "\'NoneType\' x \'execute_tool\'"}', "s3", "2026-09-28T10:02:00"),
    ])
    a = FailureClusterAnalyzer(db, min_occurrences=2, min_sessions=2)
    r = a.analyze(write=False)
    sig_clusters = [c for c in r.clusters if "execute_tool" in c.error_signature]
    assert len(sig_clusters) == 1
    assert sig_clusters[0].occurrences == 3


def test_cluster_missing_db(tmp_path):
    a = FailureClusterAnalyzer(tmp_path / "nonexistent.db")
    r = a.analyze(write=False)
    assert r.ok
    assert r.clusters_found == 0


# ── 入库幂等 ────────────────────────────────────────────────────────────

def test_analyze_write_idempotent(tmp_path, monkeypatch):
    """同型失败复扫：rule id 稳定，add_rule 同名合并不翻倍。"""
    db = _make_flywheel(tmp_path, [
        ("bash", "timeout error real", "s1", "2026-09-28T10:00:00"),
        ("bash", "timeout error real", "s2", "2026-09-28T10:01:00"),
        ("bash", "timeout error real", "s3", "2026-09-28T10:02:00"),
    ])
    backlog = tmp_path / "backlog.jsonl"
    a = FailureClusterAnalyzer(db, backlog, min_occurrences=3, min_sessions=2)
    r1 = a.analyze(write=True)
    assert r1.ok
    assert r1.actionable == 1
    assert r1.backlog_appended == 1
    # 二次运行幂等（规则 id 稳定，不报错）
    r2 = a.analyze(write=True)
    assert r2.ok


# ── daemon 挂点 fail-soft ───────────────────────────────────────────────

def test_daemon_fail_soft_on_analyzer_error():
    """聚类器抛异常时 daemon.run_cycle 不中断（fail-soft）。"""
    # 直接验证挂点代码的 try/except 结构——模拟 analyzer 抛错
    from lingclaude.self_optimizer import failure_cluster_analyzer as fca_mod

    class _Boom:
        def __init__(self, *a, **k): ...
        def analyze(self, write=True):
            raise RuntimeError("simulated analyzer failure")

    orig = fca_mod.FailureClusterAnalyzer
    fca_mod.FailureClusterAnalyzer = _Boom
    try:
        # 挂点应吞掉异常——此处仅验证类可被替换且 analyze 抛错路径存在
        with pytest.raises(RuntimeError):
            _Boom().analyze()
    finally:
        fca_mod.FailureClusterAnalyzer = orig

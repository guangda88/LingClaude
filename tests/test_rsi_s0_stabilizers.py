"""S0 (2026-10-17) RSI 阶段〇止血三件套测试。

①structure 自调量具回路停用（_build_search_space 返回 None + 短路）
②变更速率总闸（24h accepted ≤3 / 自优化规则净入库 ≤2）
③评估器冻结 + 指纹化（同指纹可比 / 记录随单落库）
"""
import sqlite3

import pytest

from lingclaude.self_optimizer.evaluator_meta import (
    EVALUATOR_VERSION,
    evaluator_fingerprint,
    evaluator_meta,
)
from lingclaude.self_optimizer.experiments import ExperimentLedger
from lingclaude.self_optimizer.optimizer import (
    OptimizationRequest,
    SynchronousOptimizer,
    _build_search_space,
)


# ---------------------------------------------------------------- ③ 指纹 --
def test_fingerprint_stable_within_process() -> None:
    assert evaluator_fingerprint() == evaluator_fingerprint()
    assert len(evaluator_fingerprint()) == 16
    assert EVALUATOR_VERSION.startswith("s0.")


def test_meta_keys() -> None:
    meta = evaluator_meta()
    assert set(meta) == {"evaluator_version", "evaluator_fingerprint"}


def test_start_stamps_fingerprint(tmp_path) -> None:
    led = ExperimentLedger(db_path=str(tmp_path / "exp.db"))
    eid = led.start(1, {"k": 1}, 10.0)
    assert eid is not None
    row = led._get_connection().execute(
        "SELECT evaluator_version, evaluator_fingerprint FROM experiments "
        "WHERE experiment_id = ?",
        (eid,),
    ).fetchone()
    assert row is not None
    assert row["evaluator_version"] == EVALUATOR_VERSION
    assert row["evaluator_fingerprint"] == evaluator_fingerprint()
    led.close()


def test_fingerprint_changes_when_evaluator_file_changes(tmp_path, monkeypatch):
    """指纹对评估器源码变更敏感（用 monkeypatch 替换包根模拟）。"""
    import lingclaude.self_optimizer.evaluator_meta as em

    fake_pkg = tmp_path / "pkg" / "lingclaude"
    (fake_pkg / "self_optimizer").mkdir(parents=True)
    for rel in em._EVALUATOR_FILES:
        (fake_pkg / rel).write_text("# v1\n", encoding="utf-8")
    monkeypatch.setattr(em, "_PKG_ROOT", fake_pkg)
    em._CACHE.pop("fp", None)
    fp1 = em.evaluator_fingerprint()

    (fake_pkg / em._EVALUATOR_FILES[0]).write_text(
        "# v2 changed\n", encoding="utf-8"
    )
    em._CACHE.pop("fp", None)
    fp2 = em.evaluator_fingerprint()

    assert fp1 != fp2, "评估器文件变更必须改变指纹"
    em._CACHE.pop("fp", None)  # 还原缓存，防污染同进程后续测试


# ---------------------------------------------------------------- ② 速率闸 --
def test_accepted_in_window_counts_and_boundary(tmp_path) -> None:
    led = ExperimentLedger(db_path=str(tmp_path / "exp.db"))
    assert led.accepted_in_window(24.0) == 0
    e1 = led.start(1, {"a": 1}, 1.0)
    led.settle(e1, "accepted", score_after=0.5, reason="applied")
    assert led.accepted_in_window(24.0) == 1
    # 旧单（窗口外）不计：手工把 decided_at 拨回 48h 前
    e2 = led.start(2, {"a": 2}, 1.0)
    led.settle(e2, "accepted", score_after=0.5, reason="applied")
    led._get_connection().execute(
        "UPDATE experiments SET decided_at = datetime('now', '-48 hours') "
        "WHERE experiment_id = ?",
        (e2,),
    )
    led._get_connection().commit()
    assert led.accepted_in_window(24.0) == 1
    led.close()


# ---------------------------------------------------------------- ① 停用 --
def test_structure_search_space_disabled() -> None:
    assert _build_search_space("structure") is None


def test_behavior_search_space_alive() -> None:
    space = _build_search_space("behavior")
    assert space is not None  # behavior 是真实行为旋钮，不在止血范围


def test_optimize_structure_short_circuit(tmp_path) -> None:
    opt = SynchronousOptimizer()
    req = OptimizationRequest(
        target=str(tmp_path), goal="structure", params={}, config={},
    )
    result = opt.optimize(req)
    assert result.success is False
    assert "S0-1" in result.error
    assert result.best_params == {}


# ------------------------------------------------- evaluator 冻结 (S0-3) --
def test_evaluator_ignores_params() -> None:
    from lingclaude.self_optimizer.evaluator import StructureEvaluator
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        ev = StructureEvaluator(d)
        assert ev.evaluate({}) == 0.0
        assert ev.evaluate({"max_class_size": 1, "max_complexity": 1}) == 0.0

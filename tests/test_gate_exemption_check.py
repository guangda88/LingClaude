#!/usr/bin/env python3
"""gate_exemption_check 单测（2026-10-28，门禁 v2.0 已知失败基线豁免机制）。

覆盖矩阵：
  1. 失败集 ⊆ 基线           → exit 0（豁免放行）
  2. 失败集含新红            → exit 1（棘轮拒绝，列出 NEW-RED）
  3. 基线文件缺失            → exit 2（回退严格语义）
  4. 基线损坏 JSON           → exit 2
  5. lastfailed 损坏         → exit 2
  6. RC=1 无失败记录         → exit 3（疑收集错误，不可豁免）
  7. lastfailed 全等于基线   → exit 0
  8. 用例 id 精确匹配语义    → 前缀/相似 id 不算命中
  9. 入库基线文件本身可解析  → 数据面健全
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "gate_exemption_check.py"
BASELINE = REPO / "data" / "arch_ledger" / "test_baselines" / "push_gate_known_failures.json"

spec = importlib.util.spec_from_file_location("gate_exemption_check", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run_check(baseline_path, lastfailed_obj):
    argv_backup = sys.argv
    stdin_backup = sys.stdin
    try:
        sys.argv = ["gate_exemption_check.py", str(baseline_path)]
        sys.stdin = io.StringIO(json.dumps(lastfailed_obj))
        return mod.main()
    finally:
        sys.argv = argv_backup
        sys.stdin = stdin_backup


def _base_entries(ids):
    return [{"test_id": t, "reason": "x", "verified_head": "e347663"} for t in ids]


def test_subset_of_baseline_passes(tmp_path):
    b = tmp_path / "base.json"
    b.write_text(json.dumps({"entries": _base_entries(["tests/a.py::t1", "tests/a.py::t2"])}), encoding="utf-8")
    assert run_check(b, {"tests/a.py::t1": ["E"]}) == 0
    assert run_check(b, {"tests/a.py::t1": ["E"], "tests/a.py::t2": ["E"]}) == 0


def test_new_red_rejected(tmp_path):
    b = tmp_path / "base.json"
    b.write_text(json.dumps({"entries": _base_entries(["tests/a.py::t1"])}), encoding="utf-8")
    rc = run_check(b, {"tests/a.py::t1": ["E"], "tests/b.py::new_red": ["E"]})
    assert rc == 1


def test_missing_baseline_falls_back_strict(tmp_path):
    assert run_check(tmp_path / "nope.json", {"tests/a.py::t1": ["E"]}) == 2


def test_corrupt_baseline_falls_back_strict(tmp_path):
    b = tmp_path / "base.json"
    b.write_text("{not json", encoding="utf-8")
    assert run_check(b, {"tests/a.py::t1": ["E"]}) == 2


def test_corrupt_lastfailed_falls_back_strict(tmp_path, capsys):
    b = tmp_path / "base.json"
    b.write_text(json.dumps({"entries": _base_entries(["tests/a.py::t1"])}), encoding="utf-8")
    argv_backup, stdin_backup = sys.argv, sys.stdin
    try:
        sys.argv = ["x", str(b)]
        sys.stdin = io.StringIO("not json at all")
        rc = mod.main()
    finally:
        sys.argv, sys.stdin = argv_backup, stdin_backup
    assert rc == 2
    assert "lastfailed 不可读" in capsys.readouterr().out


def test_rc1_without_failures_rejected(tmp_path, capsys):
    b = tmp_path / "base.json"
    b.write_text(json.dumps({"entries": _base_entries(["tests/a.py::t1"])}), encoding="utf-8")
    rc = run_check(b, {})
    assert rc == 3
    assert "疑收集错误" in capsys.readouterr().out


def test_exact_match_semantics(tmp_path):
    b = tmp_path / "base.json"
    b.write_text(json.dumps({"entries": _base_entries(["tests/a.py::TestClass::t1"])}), encoding="utf-8")
    # 前缀相似 / 类级近似 / 参数化变体 都不算命中
    tricky = {
        "tests/a.py::TestClass": ["E"],
        "tests/a.py::TestClass::t1[param0]": ["E"],
        "tests/a.py::xTestClass::t1": ["E"],
        "tests/a.py::TestClass::t10": ["E"],
    }
    for tid, err in tricky.items():
        assert run_check(b, {tid: err}) == 1, tid


def test_repo_baseline_parses_and_nonempty():
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    entries = data["entries"]
    assert entries, "基线不得为空提交（空基线=无意义机制启用）"
    for e in entries:
        assert e["test_id"].startswith("tests/"), e["test_id"]
        assert e.get("reason"), f"缺 reason: {e['test_id']}"
        assert e.get("verified_head"), f"缺 verified_head: {e['test_id']}"


def test_repo_baseline_ids_unique():
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    ids = [e["test_id"] for e in data["entries"]]
    assert len(ids) == len(set(ids)), "基线存在重复 test_id"

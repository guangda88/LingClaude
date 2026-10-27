"""evals_labeler 单元测试：打标分型 / 幂等去重 / corrections 喂食。

隔离纪律：_LEDGER_DIR/_WATERMARK 是模块级常量 → monkeypatch 打模块属性指向
tmp_path；DataFlywheel 用显式 db_path 指向 tmp（data_flywheel 自带真实库防误写）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# scripts/ 非 import 包 → spec_from_file_location 加载（test_cleanup_sandbox 同款惯例）
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "evals_labeler.py"
_SPEC = importlib.util.spec_from_file_location("evals_labeler", _SCRIPT)
el = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(el)


@pytest.fixture()
def tmp_ledger(tmp_path, monkeypatch):
    d = tmp_path / "arch_ledger"
    d.mkdir()
    monkeypatch.setattr(el, "_LEDGER_DIR", d)
    monkeypatch.setattr(el, "_WATERMARK", d / "evals_watermark.json")
    return d


def _seed_decision(d, ts, domain="route", action="fast_route", verdict="allow", p=0.9):
    line = json.dumps(
        {"ts": ts, "ts_iso": "2026-10-27T10:00:00", "domain": domain, "action": action,
         "verdict": verdict, "reason": "t", "session_id": "s1", "p": p, "meta": {}}
    )
    (d / "decisions_202610.jsonl").open("a", encoding="utf-8").write(line + "\n")


class TestLabelOf:
    def test_rules(self):
        assert el.label_of({"verdict": "gray", "p": 0.5}) == "uncertain"
        assert el.label_of({"verdict": "allow", "p": 0.5}) == "uncertain"
        assert el.label_of({"verdict": "allow", "p": 0.969}) == "confident_allow"
        assert el.label_of({"verdict": "deny", "p": 0.044}) == "likely_overkill"
        assert el.label_of({"verdict": "deny", "p": 0.4}) == "uncertain"  # p∈灰带优先
        assert el.label_of({"verdict": "deny", "p": 0.8}) == "confident_deny"
        assert el.label_of({"verdict": "weird", "p": 0.9}).startswith("unlabeled_")

    def test_bad_p_defaults(self):
        assert el.label_of({"verdict": "allow", "p": None}) == "uncertain"


class TestPipeline:
    def test_label_and_watermark(self, tmp_ledger):
        _seed_decision(tmp_ledger, 1000.0, verdict="allow", p=0.969)
        _seed_decision(tmp_ledger, 2000.0, verdict="gray", p=0.5)
        rc = el.main.__wrapped__() if hasattr(el.main, "__wrapped__") else None
        # 直接跑 CLI 逻辑（模拟参数）
        import sys

        sys.argv = ["evals_labeler.py", "--month", "202610", "--since-days", "0"]
        assert el.main() == 0
        out = tmp_ledger / "evals_202610.jsonl"
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert {r["label"] for r in rows} == {"confident_allow", "uncertain"}
        wm = json.loads((tmp_ledger / "evals_watermark.json").read_text())
        assert wm["last_ts"] == 2000.0
        # 幂等：水位推进后重跑 → 无新增
        assert el.main() == 0
        rows2 = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows2) == 2

    def test_dedup_rescans_old_window(self, tmp_ledger):
        # 水位存在但台账缺条目（如手工删除）→ 重扫旧窗口靠键去重补齐
        _seed_decision(tmp_ledger, 1000.0)
        import sys

        sys.argv = ["evals_labeler.py", "--month", "202610", "--since-days", "0"]
        el.main()
        (tmp_ledger / "evals_202610.jsonl").write_text("")  # 清空台账但留水位
        el.main()
        rows = [json.loads(x) for x in (tmp_ledger / "evals_202610.jsonl").read_text().splitlines()]
        assert len(rows) == 1  # 键去重后只补回这条

    def test_feed_corrections_tmp_db(self, tmp_ledger, tmp_path):
        _seed_decision(tmp_ledger, 1000.0, verdict="gray", p=0.36)
        _seed_decision(tmp_ledger, 1001.0, verdict="allow", p=0.9)
        db = tmp_path / "fw.db"
        import sys

        sys.argv = [
            "evals_labeler.py", "--month", "202610", "--since-days", "0",
            "--feed-corrections", "5", "--db", str(db),
        ]
        assert el.main() == 0
        import sqlite3

        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT count(*) FROM corrections WHERE source='evals_labeler'"
        ).fetchone()[0]
        conn.close()
        assert n == 1  # 只喂了 1 条 uncertain

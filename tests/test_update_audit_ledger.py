"""R1 — update_audit_ledger.py 单元测试（H17 闭环申报的机械化）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update_audit_ledger.py"
_spec = importlib.util.spec_from_file_location("update_audit_ledger", _SCRIPT)
ledger_mod = importlib.util.module_from_spec(_spec)
sys.modules["update_audit_ledger"] = ledger_mod
_spec.loader.exec_module(ledger_mod)


def _rec(name: str, code: int, summary: str = "ok") -> ledger_mod.RunRecord:
    return ledger_mod.RunRecord(
        name=name, cmd=f"echo {name}", exit_code=code, summary=summary,
        seconds=0.1, ts="2026-09-02 07:00",
    )


class TestSummaryLine:
    def test_prefers_conclusion_over_trailing_stderr_log(self):
        # stdout 结论在前、stderr 日志在后（subprocess 合并输出时的真实顺序）
        output = "✓ All consistency checks passed.\n[07:00:00] INFO 灵知 FactVerifier 可用"
        assert "consistency" in ledger_mod._summary_line(output)

    def test_falls_back_to_last_line(self):
        assert ledger_mod._summary_line("hello\nworld") == "world"
        assert ledger_mod._summary_line("") == "(无输出)"


class TestRender:
    def test_ok_records_render_checkmarks(self):
        section = ledger_mod.render_section([_rec("a", 0), _rec("b", 0)])
        assert "✅ a" in section and "✅ b" in section
        assert "⚠️" not in section
        assert section.index(ledger_mod.MARK_START) < section.index(ledger_mod.MARK_END)

    def test_failure_recorded_honestly_and_flagged(self):
        section = ledger_mod.render_section([_rec("a", 0), _rec("b", 1, "boom")])
        assert "❌ b" in section
        assert "| 1 |" in section  # 退出码如实
        assert "⚠️ 1 项失败" in section


class TestInject:
    def test_replaces_marked_region_without_duplicates(self, tmp_path):
        p = tmp_path / "ledger.md"
        p.write_text(
            "# 账目\n\n"
            f"{ledger_mod.MARK_START}\n旧记录\n{ledger_mod.MARK_END}\n\n## 尾节\n",
            encoding="utf-8",
        )
        ok = ledger_mod.inject_into_ledger(
            ledger_mod.render_section([_rec("a", 0)]), ledger_path=p,
        )
        assert ok
        text = p.read_text(encoding="utf-8")
        assert "旧记录" not in text
        assert "## 尾节" in text
        assert text.count(ledger_mod.MARK_START) == 1
        assert text.count(ledger_mod.MARK_END) == 1

    def test_repairs_duplicate_end_markers(self, tmp_path):
        p = tmp_path / "ledger.md"
        p.write_text(
            f"{ledger_mod.MARK_START}\n旧\n{ledger_mod.MARK_END}\n{ledger_mod.MARK_END}\n",
            encoding="utf-8",
        )
        ledger_mod.inject_into_ledger(
            ledger_mod.render_section([_rec("a", 0)]), ledger_path=p,
        )
        assert p.read_text(encoding="utf-8").count(ledger_mod.MARK_END) == 1

    def test_appends_when_markers_missing(self, tmp_path):
        p = tmp_path / "ledger.md"
        p.write_text("# 账目\n", encoding="utf-8")
        ok = ledger_mod.inject_into_ledger(
            ledger_mod.render_section([_rec("a", 0)]), ledger_path=p,
        )
        assert ok is False
        assert ledger_mod.MARK_START in p.read_text(encoding="utf-8")


class TestRunCheck:
    def test_captures_exit_code_and_summary(self):
        r = ledger_mod.run_check("t", [sys.executable, "-c", "print('✓ done')"])
        assert r.ok and "done" in r.summary

    def test_failure_exit_code_recorded(self):
        r = ledger_mod.run_check("t", [sys.executable, "-c", "raise SystemExit(3)"])
        assert not r.ok and r.exit_code == 3

    def test_missing_binary_reported_not_raised(self):
        r = ledger_mod.run_check("t", ["definitely-not-a-binary-xyz"])
        assert not r.ok


class TestLedgerLiveFile:
    def test_real_ledger_has_markers(self):
        """真实总账必须挂好标记区（H17：验证节由脚本接管）。"""
        assert ledger_mod.LEDGER_PATH.exists()
        text = ledger_mod.LEDGER_PATH.read_text(encoding="utf-8")
        assert ledger_mod.MARK_START in text
        assert text.count(ledger_mod.MARK_END) == 1

    def test_only_accepts_known_names(self):
        with pytest.raises(SystemExit):
            ledger_mod.build_checks(
                type("A", (), {"full": False, "skip_pytest": False,
                               "with_cargo": False, "only": "nope"})()
            )

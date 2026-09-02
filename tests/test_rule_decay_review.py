"""R3 — rule_decay_review.py 单元测试（规则衰减复核：登记/复核/标记）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rule_decay_review.py"
_spec = importlib.util.spec_from_file_location("rule_decay_review", _SCRIPT)
rdr = importlib.util.module_from_spec(_spec)
sys.modules["rule_decay_review"] = rdr
_spec.loader.exec_module(rdr)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把登记册与规则源全部指到 tmp——真实登记册不被测试污染。"""
    linggit_dir = tmp_path / "linggit" / "rules"
    linggit_dir.mkdir(parents=True)
    (linggit_dir / "review_rules.yaml").write_text(
        "checks:\n"
        "  - type: pattern\n"
        "    pattern: 'eval\\('\n"
        "    description: \"eval() 使用\"\n"
        "  - type: pattern\n"
        "    pattern: 'os\\.system\\('\n"
        "    description: \"os.system() 使用\"\n",
        encoding="utf-8",
    )
    lc = tmp_path / ".lingclaude"
    lc.mkdir()
    (lc / "metacognitive_guards.md").write_text(
        "| # | 守卫 | 触发 | 执行 |\n"
        "|---|------|------|------|\n"
        "| H1 | 唤醒 | 新会话 | 恢复 |\n"
        "| H17 | 闭环申报 | 声明完成 | 附证据 |\n",
        encoding="utf-8",
    )
    (lc / "security_rules.md").write_text("# security rules\n", encoding="utf-8")

    monkeypatch.setitem(rdr.SOURCES, "linggit", linggit_dir / "review_rules.yaml")
    monkeypatch.setitem(rdr.SOURCES, "guards", lc / "metacognitive_guards.md")
    monkeypatch.setitem(rdr.SOURCES, "security_rules", lc / "security_rules.md")
    monkeypatch.setitem(rdr.SOURCES, "coding_rules", tmp_path / "nonexistent.md")
    monkeypatch.setitem(rdr.SOURCES, "task_protection_rules", tmp_path / "nonexistent2.md")
    monkeypatch.setitem(rdr.SOURCES, "self_driven_rules", tmp_path / "nonexistent3.md")
    monkeypatch.setitem(rdr.SOURCES, "l3_rules", tmp_path / "nonexistent4.md")
    monkeypatch.setattr(rdr, "REGISTRY_PATH", tmp_path / ".lingclaude" / "rule_registry.json")
    return tmp_path


class TestInit:
    def test_registers_all_sources(self, sandbox):
        assert rdr.cmd_init() == 0
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        ids = {r["id"] for r in reg["rules"]}
        assert "linggit:01" in ids and "linggit:02" in ids
        assert "guard:H1" in ids and "guard:H17" in ids
        assert "md:security_rules" in ids
        # 全部初始 last_verified=null（诚实：登记≠验证）
        assert all(r["last_verified"] is None for r in reg["rules"])

    def test_reinit_preserves_last_verified(self, sandbox):
        rdr.cmd_init()
        rdr.cmd_mark("guard:H17", 0)
        rdr.cmd_init()  # 二次 init 不得清掉验证时间戳
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        h17 = next(r for r in reg["rules"] if r["id"] == "guard:H17")
        assert h17["last_verified"] is not None
        # 且不产生重复条目
        assert len([r for r in reg["rules"] if r["id"] == "guard:H17"]) == 1


class TestReview:
    def test_all_unverified_appear_in_queue(self, sandbox):
        rdr.cmd_init()
        assert rdr.cmd_review(30, strict=False) == 0  # 报告模式 exit 0
        stale, _ = rdr.stale_rules(30)
        assert len(stale) == 5  # 2 linggit + 2 guards + 1 md

    def test_marked_rule_leaves_queue(self, sandbox):
        rdr.cmd_init()
        rdr.cmd_mark("guard:H1", 0)
        stale, _ = rdr.stale_rules(30)
        assert all(r["id"] != "guard:H1" for r in stale)
        assert len(stale) == 4

    def test_strict_exit_code(self, sandbox):
        rdr.cmd_init()
        assert rdr.cmd_review(30, strict=True) == 1  # 有过期 → exit 1
        # 全部标记后 strict 通过
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        for r in reg["rules"]:
            r["last_verified"] = rdr._today()
        rdr.save_registry(reg)
        assert rdr.cmd_review(30, strict=True) == 0

    def test_malformed_timestamp_treated_as_stale(self, sandbox):
        rdr.cmd_init()
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        reg["rules"][0]["last_verified"] = "not-a-date"
        rdr.save_registry(reg)
        stale, _ = rdr.stale_rules(30)
        assert any(r["id"] == reg["rules"][0]["id"] for r in stale)


class TestRefresh:
    def test_refresh_marks_only_matching_prefix_with_evidence(self, sandbox):
        rdr.cmd_init()
        assert rdr.cmd_refresh("guard", "测试接线") == 0
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        guards = [r for r in reg["rules"] if r["id"].startswith("guard:")]
        others = [r for r in reg["rules"] if not r["id"].startswith("guard:")]
        assert all(r["last_verified"] == rdr._today() for r in guards)
        assert all(r["last_verified_evidence"] == "测试接线" for r in guards)
        assert all(r["last_verified"] is None for r in others), "前缀外的规则不得被误刷"

    def test_refresh_no_match_is_safe(self, sandbox):
        rdr.cmd_init()
        assert rdr.cmd_refresh("nonexistent-prefix") == 0

    def test_refresh_leaves_queue_when_all_fresh(self, sandbox):
        rdr.cmd_init()
        rdr.cmd_refresh("linggit")
        rdr.cmd_refresh("guard")
        stale, _ = rdr.stale_rules(30)
        # 只剩 md:* 文件级规则未验证
        assert all(r["id"].startswith("md:") for r in stale)


class TestGhost:
    def test_removed_source_rule_marked_as_ghost(self, sandbox):
        rdr.cmd_init()
        # 源文件删掉 H17 → 再 init 应保留条目并标记 note
        guards = rdr.SOURCES["guards"]
        text = guards.read_text(encoding="utf-8").replace("| H17 | 闭环申报 | 声明完成 | 附证据 |\n", "")
        guards.write_text(text, encoding="utf-8")
        rdr.cmd_init()
        reg = json.loads(rdr.REGISTRY_PATH.read_text(encoding="utf-8"))
        h17 = next(r for r in reg["rules"] if r["id"] == "guard:H17")
        assert "已无此规则" in h17.get("note", "")

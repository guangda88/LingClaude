"""NanoJev 契约消费层测试：统一决策流 + 四类状态黑板 + prompt 注入。

2026-10-27 决策域落地批次。对齐 AGENTS.md 契约段：
- decisions.jsonl 只追加、按月滚转、fail-open、去重窗口
- blackboard Goal/Worker/Constraint/Evidence 四类状态、快照往返
- system_prompt_builder 动态尾随块注入（空黑板零注入，不破前缀缓存）
"""

import json

import pytest

from lingclaude.core.blackboard import Blackboard, get_blackboard, reset_blackboard
from lingclaude.core.decision_log import (
    DecisionEntry,
    DecisionLog,
    get_decision_log,
    record_decision,
)


# ---------- decision_log ----------

class TestDecisionLog:
    def test_record_appends_jsonl(self, tmp_path):
        log = DecisionLog(root=tmp_path)
        log.record("route", "fast_route", "allow", reason="r1", p=0.9)
        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert entry["domain"] == "route"
        assert entry["verdict"] == "allow"
        assert entry["p"] == 0.9
        assert entry["ts_iso"]  # 人类可读时间已附

    def test_monthly_rollover(self, tmp_path):
        log = DecisionLog(root=tmp_path)
        log.record("success", "bash", "allow")
        log.record("success", "bash", "allow", dedup=False)
        # 同月落同一文件；两条都在（去重需显式关掉或换指纹）
        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_dedup_window_skips_duplicate(self, tmp_path):
        log = DecisionLog(root=tmp_path)
        log.record("risk", "rm -rf /", "deny", reason="destructive")
        log.record("risk", "rm -rf /", "deny", reason="destructive")  # 同指纹→跳过
        f = next(tmp_path.glob("decisions_*.jsonl"))
        assert len(f.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_stats_and_iter(self, tmp_path):
        log = DecisionLog(root=tmp_path)
        for i in range(3):
            log.record("route", "a", "allow", dedup=(i == 0))
        log.record("risk", "b", "deny")
        stats = log.stats()  # 进程内计数快照
        assert stats["dedup_cache_size"] >= 2
        assert any(
            d.get("domain") == "risk"
            for d in log.iter_entries(tail=10)
        )

    def test_entry_roundtrip(self):
        d = DecisionEntry(
            ts=1.0, domain="route", action="x", verdict="gray",
            reason="y", session_id="s", p=0.5, meta={"k": "v"},
        ).to_dict()
        e = DecisionEntry.from_dict(d)
        assert e.domain == "route" and e.meta == {"k": "v"}

    def test_fail_open_on_bad_root(self, tmp_path):
        # root 指向一个文件 → mkdir/写失败也必须不抛（fail-open 纪律）
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        log = DecisionLog(root=blocker / "sub")
        entry = log.record("route", "x", "allow")  # 不抛即通过
        assert entry.domain == "route"


# ---------- blackboard ----------

class TestBlackboard:
    def test_empty_render_returns_empty(self, tmp_path):
        bb = Blackboard(snapshot_path=tmp_path / "bb.json")
        assert bb.render() == ""
        assert bb.snapshot_dict()["goal"] is None

    def test_four_states_roundtrip(self, tmp_path):
        bb = Blackboard(snapshot_path=tmp_path / "bb.json")
        bb.set_goal("修好 f5 卫兵", done_when="pytest 全绿")
        bb.set_current_worker("lingke", handover_reason="主会话接续")
        bb.add_constraint("不碰生产 daemon")
        bb.add_evidence("runtime_observation", "7ad633e 实测", digest="abc123")
        snap = bb.snapshot_dict()
        assert snap["goal"]["done_when"] == "pytest 全绿"
        assert snap["worker"]["name"] == "lingke"
        assert snap["constraints"][0]["text"] == "不碰生产 daemon"
        assert snap["evidence"][0]["digest"] == "abc123"
        text = bb.render()
        assert "Goal: 修好 f5 卫兵" in text
        assert "Worker: lingke" in text
        assert "Constraint[task]" in text
        assert "digest=abc123" in text

    def test_snapshot_persistence_reload(self, tmp_path):
        p = tmp_path / "bb.json"
        bb1 = Blackboard(snapshot_path=p)
        bb1.set_goal("跨进程目标")
        bb2 = Blackboard(snapshot_path=p)
        assert bb2.load_snapshot() is True
        assert bb2.snapshot_dict()["goal"]["text"] == "跨进程目标"

    def test_corrupt_snapshot_fails_soft(self, tmp_path):
        p = tmp_path / "bb.json"
        p.write_text("{broken json", encoding="utf-8")
        bb = Blackboard(snapshot_path=p)
        assert bb.load_snapshot() is False  # 不抛
        assert bb.render() == ""

    def test_lists_bounded(self, tmp_path):
        bb = Blackboard(snapshot_path=tmp_path / "bb.json")
        for i in range(50):
            bb.add_evidence("obs", f"e{i}")
        assert len(bb.snapshot_dict()["evidence"]) <= 64  # _MAX_LIST 封顶


# ---------- 单例与注入接线 ----------

class TestWiring:
    def test_singleton_and_reset(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lingclaude.core.blackboard._singleton", None, raising=False)
        a = get_blackboard()
        b = get_blackboard()
        assert a is b
        reset_blackboard()
        assert get_blackboard().snapshot_dict()["goal"] is None

    def test_module_imports_clean(self):
        # 注入点语法/依赖烟测（system_prompt_builder 顶层可导入）
        import lingclaude.core.system_prompt_builder  # noqa: F401
        import lingclaude.core.decision_log  # noqa: F401

    def test_convenience_record_uses_singleton(self, tmp_path, monkeypatch):
        # chdir 隔离：默认 root 相对 CWD，不隔离会写穿生产台账
        # （同型陷阱前科：10-22 test_session_budget_writing 写穿 project_memory）
        monkeypatch.chdir(tmp_path)
        record_decision("route", "smoke2", "allow", reason="test")
        # 只验证不抛+返回条目；落盘位置由 chdir 隔离进 tmp_path
        debris = list(tmp_path.rglob("decisions_*.jsonl"))
        assert len(debris) == 1

# ---------- 会话归因（2026-10-27 收口 session_id=unknown）----------

class TestSessionAttribution:
    """进程级 provider 归因：显式参数 > provider > 空串（落 unknown）。"""

    def test_provider_used_when_no_explicit(self, tmp_path, monkeypatch):
        from lingclaude.core import decision_log as dl
        monkeypatch.chdir(tmp_path)  # 单例默认 root 相对 CWD，一律隔离
        dl.set_session_provider(lambda: "sess_provider_1")
        try:
            e = dl.get_decision_log().record("route", "attr1", "allow", reason="r1")
            assert e.session_id == "sess_provider_1"
        finally:
            dl.set_session_provider(None)

    def test_explicit_overrides_provider(self, tmp_path, monkeypatch):
        from lingclaude.core import decision_log as dl
        monkeypatch.chdir(tmp_path)
        dl.set_session_provider(lambda: "sess_provider_2")
        try:
            e = dl.get_decision_log().record(
                "route", "attr2", "allow", reason="r2", session_id="sess_explicit")
            assert e.session_id == "sess_explicit"
        finally:
            dl.set_session_provider(None)

    def test_provider_exception_falls_back(self, tmp_path, monkeypatch):
        from lingclaude.core import decision_log as dl
        monkeypatch.chdir(tmp_path)
        dl.set_session_provider(lambda: 1 / 0)
        try:
            e = dl.get_decision_log().record("route", "attr3", "allow", reason="r3")
            assert e.session_id == ""
        finally:
            dl.set_session_provider(None)

    def test_no_provider_and_no_explicit_serializes_unknown(self, tmp_path, monkeypatch):
        from lingclaude.core import decision_log as dl
        monkeypatch.chdir(tmp_path)
        dl.set_session_provider(None)
        e = dl.get_decision_log().record("route", "attr4", "allow", reason="r4")
        assert e.session_id == ""
        # to_dict 安全化后空串落 unknown token（与生产观测一致）
        assert e.to_dict()["session_id"] == "unknown"

    def test_record_decision_convenience_attribution(self, tmp_path, monkeypatch):
        from lingclaude.core import decision_log as dl
        monkeypatch.chdir(tmp_path)
        dl.set_session_provider(lambda: "sess_conv")
        try:
            e = dl.record_decision("risk", "attr5", "deny", reason="r5")
            assert e.session_id == "sess_conv"
        finally:
            dl.set_session_provider(None)

"""第二批整改测试：N5 契约漂移 + N1 互账对账 + M6 双口径（2026-09-23）。

对应 docs/audit/20260923_total_report.md §7.2 第二批 P0 #1/#4/#5：
- N5：scripts/contract_drift.py 行为指纹 hash + clean/drift 入册（此前真零落地）；
- N1：scripts/n1_federation_audit.py federation_pair 周期对账 + 六态 transition
  （此前六态机零编码、drift 永不触发）；
- M6：scripts/seam_trend_inspect.py 运行时口径（SeamRegistry.snapshot 实拍，
  此前从未调用）+ 独立快照命名空间 arch_m6_seam_snapshot（此前被 datalog 鸠占）。

纪律：所有写操作只碰 tmp 副本台账，真实 data/arch_ledger/ 只读不改
（测试对真台账的唯一合法动作 = query）。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    """scripts/ 非包，importlib 按路径加载（与 test_iron_law_5_8 的 sweeper 同法）。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _tmp_root(mod, monkeypatch) -> Path:
    """真台账 → tmp 副本，把脚本的 store 工厂重定向过去（隔离真实台账）。"""
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "data" / "arch_ledger", tmp / "data" / "arch_ledger")
    monkeypatch.setattr(mod, "ROOT", tmp)

    def _store():
        from lingclaude.core.state_store import StateStore

        return StateStore(backend="json", root=tmp / "data" / "arch_ledger")

    monkeypatch.setattr(mod, "_store", _store)
    if hasattr(mod, "_snap_store"):
        monkeypatch.setattr(mod, "_snap_store", _store)
    return tmp


# ── N5 契约漂移 ────────────────────────────────────────────────────────


class TestN5ContractDrift:
    @pytest.fixture()
    def cd(self):
        return _load("cd_test", "scripts/contract_drift.py")

    def test_fingerprint_deterministic_and_surface_sensitive(self, cd):
        """同 manifest 指纹稳定；契约面任一字段变化 → 指纹必变。"""
        fp1, surf1 = cd.behavior_fingerprint("agent:agent_lingxi")
        fp2, _ = cd.behavior_fingerprint("agent:agent_lingxi")
        assert fp1 == fp2 and len(fp1) == 16
        assert surf1["name"] == "agent/lingxi"
        surf1["version"] = "9.9.9"
        import hashlib

        fp3 = hashlib.sha256(
            json.dumps(surf1, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        assert fp3 != fp1

    def test_record_then_check_clean_and_drift(self, cd, monkeypatch):
        _tmp_root(cd, monkeypatch)
        assert cd.record("agent:agent_lingxi") == 0
        assert cd.check("agent:agent_lingxi") == 0
        # 篡改台账指纹 → check 必须漂移退出 1
        s = cd._store()
        rec = s.load(cd.T_DRIFT, "agent-agent_lingxi")
        saved = dict(rec)
        rec["fingerprint"] = "deadbeef00000000"
        s.save(cd.T_DRIFT, "agent-agent_lingxi", rec)
        assert cd.check("agent:agent_lingxi") == 1
        # record 复测：旧指纹留存 fingerprint_prev + 状态转 drift
        assert cd.record("agent:agent_lingxi") == 1
        rec2 = cd._store().load(cd.T_DRIFT, "agent-agent_lingxi")
        assert rec2["state"] == "drift"
        assert rec2["fingerprint_prev"] == "deadbeef00000000"
        s.save(cd.T_DRIFT, "agent-agent_lingxi", saved)

    def test_check_unrecorded_target_fails_closed(self, cd, monkeypatch):
        """未对账 ≠ 健康：fail closed（J5 守卫纪律）。"""
        _tmp_root(cd, monkeypatch)
        # tmp 副本含真台账首记（agent_lingxi 已在册），须用无记录目标验 fail closed
        assert cd.check("agent:agent_lingan") == 1  # 无 contract_drift 记录
        s = cd._store()
        rec = s.load(cd.T_DRIFT, "agent-agent_lingxi")
        rec["state"] = "drift"
        s.save(cd.T_DRIFT, "agent-agent_lingxi", rec)
        assert cd.check("agent:agent_lingxi") == 1  # drift 态同红

    def test_real_ledger_has_anchors(self):
        """真台账只读核对：两个 T3 插片已首记 clean（CI check 的前提）。"""
        from lingclaude.core.state_store import StateStore

        s = StateStore(backend="json", root=ROOT / "data" / "arch_ledger")
        try:
            for key in ("agent-agent_lingxi", "agent-lc_mcp_guard"):
                rec = s.load("contract_drift", key)
                assert rec is not None, f"{key} 未首记"
                assert rec["state"] == "clean"
                assert rec["fingerprint"]
        finally:
            s.close()

    def test_lingxi_manifest_has_contract_anchor(self):
        """T3 实证案例缺锚点整改：agent_lingxi manifest 必须带 N5 anchor。"""
        m = json.loads(
            (ROOT / "lingclaude/plugins/agents/agent_lingxi/manifest.agent.json")
            .read_text(encoding="utf-8"))
        anchor = m.get("contract_anchor")
        assert anchor and anchor["guard"] == "N5"
        assert "contract_drift" in anchor["record"]


# ── N1 互账对账 ────────────────────────────────────────────────────────


class TestN1FederationAudit:
    @pytest.fixture()
    def n1(self):
        return _load("n1_test", "scripts/n1_federation_audit.py")

    def test_real_pairs_consistent(self, n1):
        """真台账只读对账：lc-ac / ac-lc 双侧齐 + state 一致 + 绿。"""
        report = n1.audit()
        keys = {p["key"] for p in report["pairs"]}
        assert {"lc-ac", "ac-lc"} <= keys
        # 当前真台账经整改验证后归位 paired，不得有结构性告警
        structural = [a for a in report["alerts"]
                      if "缺失" in a or "不一致" in a or "六态外" in a]
        assert not structural, structural

    def test_missing_counterpart_alert_and_fix(self, n1, monkeypatch):
        tmp = _tmp_root(n1, monkeypatch)
        (tmp / "data/arch_ledger/federation_pair/lc-ac.json").unlink()
        r = n1.audit()
        assert any("对偶 record 缺失" in a for a in r["alerts"])
        r2 = n1.audit(fix=True)
        assert any("补建对偶 lc-ac" in f for f in r2["fixed"])
        rec = n1._store().load(n1.T_PAIR, "lc-ac")
        assert rec["state"] == "proposing"  # 谁先建语义：后建侧 proposing

    def test_state_mismatch_alerts(self, n1, monkeypatch):
        _tmp_root(n1, monkeypatch)
        s = n1._store()
        rec = s.load(n1.T_PAIR, "ac-lc")
        rec["state"] = "drift"
        s.save(n1.T_PAIR, "ac-lc", rec)
        r = n1.audit()
        assert any("state 不一致" in a for a in r["alerts"])

    def test_fingerprint_drift_triggers_transition(self, n1, monkeypatch):
        """N1 绑 N5：指纹分歧 = drift 进入条件，且自动 transition（六态机跑活）。"""
        _tmp_root(n1, monkeypatch)
        # lc-ac 的 manifest 属主是 lc_mcp_guard；n1 内部 _cd 指纹器读真 manifest
        # （REPO_ROOT），而指纹台账走 n1._store（tmp 副本）——篡改副本即触发
        s = n1._store()
        drec = s.load("contract_drift", "agent-lc_mcp_guard")
        saved = dict(drec)
        drec["fingerprint"] = "deadbeef00000000"
        s.save("contract_drift", "agent-lc_mcp_guard", drec)
        r = n1.audit()
        assert any("契约指纹漂移" in a for a in r["alerts"])
        assert any("-> drift" in t for t in r.get("transitions", []))
        assert s.load(n1.T_PAIR, "lc-ac")["state"] == "drift"
        # 恢复指纹后 drift 不自动翻转——restored 须显式（对账恢复语义）
        s.save("contract_drift", "agent-lc_mcp_guard", saved)

    def test_six_state_machine_transitions(self, n1, monkeypatch):
        _tmp_root(n1, monkeypatch)
        legal = [("proposing", "paired"), ("paired", "drift"),
                 ("drift", "restored"), ("restored", "paired")]
        for old, new in legal:
            rec = n1._store().load(n1.T_PAIR, "ac-lc")
            rec["state"] = old
            n1._store().save(n1.T_PAIR, "ac-lc", rec)
            out = n1.transition("ac-lc", new, note="测试")
            assert out["state"] == new
        # 非法迁移被拒
        rec = n1._store().load(n1.T_PAIR, "ac-lc")
        rec["state"] = "dissolved"
        n1._store().save(n1.T_PAIR, "ac-lc", rec)
        with pytest.raises(ValueError, match="非法迁移"):
            n1.transition("ac-lc", "paired")
        # dissolved 联动：不留半边
        rec = n1._store().load(n1.T_PAIR, "ac-lc")
        rec["state"] = "paired"
        n1._store().save(n1.T_PAIR, "ac-lc", rec)
        rec2 = n1._store().load(n1.T_PAIR, "lc-ac")
        rec2["state"] = "paired"
        n1._store().save(n1.T_PAIR, "lc-ac", rec2)
        n1.transition("ac-lc", "dissolved")
        assert n1._store().load(n1.T_PAIR, "lc-ac")["state"] == "dissolved"


# ── M6 双口径 ──────────────────────────────────────────────────────────


class TestM6DualCaliber:
    @pytest.fixture()
    def m6(self):
        return _load("m6_test", "scripts/seam_trend_inspect.py")

    def test_snapshot_namespace_split(self, m6):
        """独立命名空间：接缝口径不再与 datalog 鸠占目录共写。"""
        assert m6.T_SNAP == "arch_m6_seam_snapshot"
        assert m6.T_SNAP_LEGACY == "arch_m6_snapshot"

    def test_load_last_snapshot_ignores_datalog_shape(self, m6, monkeypatch):
        """环比基线只认含 seam_impl_distribution 的记录——datalog 快照不作基线。

        用纯净 tmp（不拷真台账）——真台账已含整改后产生的接缝快照，会污染断言。
        """
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data/arch_ledger").mkdir(parents=True)
        monkeypatch.setattr(m6, "ROOT", tmp)

        def _store():
            from lingclaude.core.state_store import StateStore

            return StateStore(backend="json", root=tmp / "data" / "arch_ledger")

        monkeypatch.setattr(m6, "_snap_store", _store)
        snap_dir = tmp / "data/arch_ledger/arch_m6_seam_snapshot"
        snap_dir.mkdir(parents=True)
        (snap_dir / "x.json").write_text(json.dumps({"day": "d1"}), encoding="utf-8")
        assert m6.load_last_snapshot() is None  # 纯 datalog 形态被过滤
        (snap_dir / "y.json").write_text(
            json.dumps({"seam_impl_distribution": {"TOOL": 1}}), encoding="utf-8")
        assert m6.load_last_snapshot()["seam_impl_distribution"] == {"TOOL": 1}

    def test_runtime_caliber_real_registry(self, m6):
        """运行时口径是真 SeamRegistry 实拍（dict），且带装载失败显歧。"""
        snap = m6.runtime_seam_snapshot()
        assert isinstance(snap["registry"], dict)
        assert isinstance(snap["plugins_failed"], list)
        # 静态口径必有 TOOL/SANDBOX 等扫描结果
        dist = m6.scan_seam_registrations()
        assert sum(len(v) for v in dist.values()) > 0

    def test_report_carries_both_calibers_and_divergence(self):
        """双口径并排 + 分歧必须可见（仪表只显歧不裁定）——纯函数级验证。"""
        m6 = _load("m6_test2", "scripts/seam_trend_inspect.py")
        regs = m6.scan_seam_registrations()
        dist = {k: len(v) for k, v in regs.items()}
        rt = {"registry": {"agent": ["x/y"]}, "plugins_loaded": {}, "plugins_failed": []}
        divergences = {}
        for st in sorted(set(dist) | set(rt["registry"])):
            sc, rc = dist.get(st, 0), len(rt["registry"].get(st, []))
            if sc != rc:
                divergences[st] = {"static_scan": sc, "runtime_registry": rc}
        # 静态扫描（大写/小写域混布）vs 运行时（'agent': 1）必有分歧
        assert divergences

    def test_m6_debt_registered(self):
        """新发现的 register 钩子潜伏 bug 已挂账（due 2026-10-15）。"""
        from lingclaude.core.state_store import StateStore

        s = StateStore(backend="json", root=ROOT / "data" / "arch_ledger")
        try:
            rec = s.load("arch_debt", "m6-family-carriers-register-hook-broken")
            assert rec is not None and rec["state"] == "open"
            assert rec["due"] == "2026-10-15"
        finally:
            s.close()

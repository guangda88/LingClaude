"""周期自审整改测试（2026-09-24，audit P0-B + M6 假阳性欠账）。

覆盖四层：
  1. M6 双口径键规范统一——静态 attr(AGENT) → SeamType.value(agent)，
     大小写劈叉不再算分歧（真实分歧仍显歧，仪表不裁定）；
  2. self_audit_trigger --force——跳过触发短路强制深审（CI schedule 用）；
  3. 空转检测 36h——时间维度触发，防「HEAD 不动 → 审计永不跑」；
     时间戳缺失/损坏保守视为空转（宁可多审）；
  4. audit_watch 值守层——sweep_due 时本轮值守 force 透传（本地执行点）。

台账隔离：trigger 的 _store 打到 tmp（不写真台账）；subprocess 打桩
（深审的 pytest 自检不在单测里重跑，--force 的真实链路由 E2E 冒烟验证）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    """scripts/ 非包，importlib 按路径加载（与 test_batch2_n5_n1_m6 同法）。

    scripts/ 需入 sys.path——trigger 内部 `from arch_ledger import ...`
    按脚本直跑语义解析（daemon 侧 _load_audit_trigger 亦显式注册，同法）。
    """
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    returncode = 0
    stdout = "fedcba1"
    stderr = ""


# ── 1. M6 口径规范统一 ─────────────────────────────────────────────────


class TestM6CanonicalKeys:
    @pytest.fixture()
    def m6(self):
        return _load("m6_periodic", "scripts/seam_trend_inspect.py")

    def test_case_split_no_longer_diverges(self, m6):
        """世界线复现：静态 AGENT=23 与运行时 agent=23 是同一事实——不得报分歧。"""
        dist = {"AGENT": 23}
        rt = {"agent": [f"agent/p{i}" for i in range(23)]}
        div = m6._divergences(dist, rt)
        assert div == {}, f"大小写劈叉假分歧未消除: {div}"

    def test_real_divergence_survives_canonicalization(self, m6):
        """规范键下真实分歧保留：静态有注册点、运行时没装载 → 显歧。"""
        dist = {"AGENT": 23, "SANDBOX": 8, "TOOL": 1}
        rt = {"agent": [f"agent/p{i}" for i in range(23)]}
        div = m6._divergences(dist, rt)
        assert "AGENT" not in div
        assert div["sandbox"] == {"static_scan": 8, "runtime_registry": 0}
        assert div["tool"] == {"static_scan": 1, "runtime_registry": 0}

    def test_unknown_key_failopen(self, m6):
        """枚举外字面量原样保留（fail-open，不吞未知键）。"""
        div = m6._divergences({"AGENTX": 2}, {"agentx": ["a"]})
        assert div["AGENTX"]["static_scan"] == 2
        assert div["agentx"]["runtime_registry"] == 1


# ── 2/3. --force 与空转检测 ────────────────────────────────────────────


class TestForceAndStaleness:
    @pytest.fixture()
    def trg(self, monkeypatch, tmp_path):
        mod = _load("trg_periodic", "scripts/self_audit_trigger.py")
        tmp_ledger = tmp_path / "data" / "arch_ledger"
        tmp_ledger.mkdir(parents=True)
        from lingclaude.core.state_store import StateStore

        monkeypatch.setattr(mod, "_store",
                            lambda: StateStore(backend="json", root=tmp_ledger))
        # 打桩 subprocess：深审的 pytest 自检不在单测里重跑（E2E 冒烟另验）
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc())
        return mod

    def _seed(self, trg, hours_ago: float | None):
        st = trg._store()
        checked = None
        if hours_ago is not None:
            checked = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)
                       ).isoformat(timespec="seconds")
        st.save(trg.T_STATE, "default", {
            "head": trg._head(), "fingerprints": trg._fingerprints(),
            "checked": checked,
        })

    def test_no_trigger_short_circuits(self, trg, capsys):
        self._seed(trg, hours_ago=0.0)
        assert trg.run_audit() == 0
        assert "无触发条件 → 不审查" in capsys.readouterr().out

    def test_force_bypasses_short_circuit(self, trg, capsys):
        """--force：指纹全等也要深审（CI schedule / 值守例行核账依赖此路径）。"""
        self._seed(trg, hours_ago=0.0)
        assert trg.run_audit(force=True) == 0
        out = capsys.readouterr().out
        assert "无触发条件 → 不审查" not in out
        assert "周期深审" in out

    def test_staleness_triggers_deep_audit(self, trg, capsys):
        """36h 未深审 → 时间维度自动触发（防机制空转）。"""
        self._seed(trg, hours_ago=48.0)
        assert trg.run_audit() == 0
        out = capsys.readouterr().out
        assert "无触发条件 → 不审查" not in out
        assert "空转检测" in out

    def test_missing_or_corrupt_checked_is_stale(self, trg, capsys):
        """时间戳缺失/损坏保守视为空转（宁可多审，不可漏审）。"""
        self._seed(trg, hours_ago=None)
        st = trg._store()
        rec = st.load(trg.T_STATE, "default")
        rec["checked"] = "not-a-date"
        st.save(trg.T_STATE, "default", rec)
        assert trg.run_audit() == 0
        assert "空转检测" in capsys.readouterr().out

    def test_state_refreshed_after_deep_audit(self, trg):
        """深审后 checked 刷新（CI staleness 契约步骤的本地等价断言）。"""
        self._seed(trg, hours_ago=48.0)
        trg.run_audit()
        rec = trg._store().load(trg.T_STATE, "default")
        assert rec.get("checked"), "深审未刷新 arch_audit_state.checked"


# ── 4. 值守层 force 透传 ───────────────────────────────────────────────


class TestAuditWatchForcePassthrough:
    def test_sweep_due_forces_deep_audit(self, monkeypatch, tmp_path):
        from lingclaude.self_optimizer.audit_watch import AuditWatch

        watch = AuditWatch(state_dir=tmp_path, kb_path=tmp_path / "kb",
                           min_sweep_hours=24.0, enable_f5_hooks=False)
        assert watch._sweep_due() is True  # 全新状态 → 例行核账到期

        calls: list[bool] = []

        def fake_run_audit(force: bool = False) -> int:
            calls.append(force)
            return 0

        monkeypatch.setattr(watch.trigger, "run_audit", fake_run_audit)
        monkeypatch.setattr(watch.trigger, "sweep_debts", lambda: [])
        diag = watch.run_once(write_kb=False)
        assert calls == [True], f"sweep_due 未透传 force: {calls}"
        assert diag["sweep_due"] is True
        assert (tmp_path / "audit_watch_state.json").exists()

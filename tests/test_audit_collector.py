"""LINGKERNEL_v1 D1 - AuditCollector 测试。"""

from __future__ import annotations

from lingclaude.core.audit_collector import AuditCollector, AuditResult, DegradationAlert


class FakeL5:
    def __init__(self, rewrite_to=None, fail=False):
        self.rewrite_to = rewrite_to
        self.fail = fail
        self.calls = []

    def audit(self, prompt, output):
        self.calls.append((prompt, output))
        if self.fail:
            raise RuntimeError("l5 down")
        return self.rewrite_to if self.rewrite_to is not None else output


class FakeDegradation:
    def __init__(self, alerts=None):
        self._alerts = alerts or []

    def check(self, prompt, output):
        return self._alerts

    def health(self):
        return {"status": "ok", "alerts": len(self._alerts)}


# ---- L5 ----

def test_l5_absent_passthrough():
    ac = AuditCollector()
    r = ac.apply_l5("p", "out")
    assert r.output == "out"
    assert r.rewritten is False


def test_l5_rewrite():
    ac = AuditCollector(l5_orchestrator=FakeL5(rewrite_to="clean"))
    r = ac.apply_l5("p", "bad")
    assert r.output == "clean"
    assert r.rewritten is True


def test_l5_same_output_not_marked_rewritten():
    ac = AuditCollector(l5_orchestrator=FakeL5(rewrite_to="same"))
    r = ac.apply_l5("p", "same")
    assert r.rewritten is False


def test_l5_failure_swallowed():
    ac = AuditCollector(l5_orchestrator=FakeL5(fail=True))
    r = ac.apply_l5("p", "out")
    assert r.output == "out"  # 原样
    assert any("l5-error" in n for n in r.notes)


def test_l5_history_recorded():
    ac = AuditCollector(l5_orchestrator=FakeL5())
    ac.apply_l5("p1", "o1")
    ac.apply_l5("p2", "o2")
    assert len(ac.history) == 2
    ac.clear_history()
    assert ac.history == []


# ---- degradation ----

def test_degradation_absent():
    ac = AuditCollector()
    assert ac.check_degradation("p", "o") == []
    assert ac.degradation_health() == {"status": "not-configured"}


def test_degradation_alerts():
    alerts = [{"kind": "loop", "message": "repeat detected"}]
    ac = AuditCollector(degradation_detector=FakeDegradation(alerts))
    out = ac.check_degradation("p", "o")
    assert out == [DegradationAlert(kind="loop", message="repeat detected")]


def test_degradation_health():
    ac = AuditCollector(degradation_detector=FakeDegradation(alerts=[{"kind": "x", "message": ""}]))
    assert ac.degradation_health() == {"status": "ok", "alerts": 1}


# ---- behavior gate ----

def test_behavior_gate_blocks():
    ac = AuditCollector()
    r = ac.check_behavior_gate("please run rm -rf / now", rules=["block:rm -rf"])
    assert r is not None and "rm -rf" in r


def test_behavior_gate_passes_clean():
    ac = AuditCollector()
    assert ac.check_behavior_gate("hello world", rules=["block:rm -rf"]) is None


def test_behavior_gate_no_rules():
    ac = AuditCollector()
    assert ac.check_behavior_gate("rm -rf") is None


def test_behavior_gate_warn_not_block():
    """warn 规则不拦截。"""
    ac = AuditCollector()
    assert ac.check_behavior_gate("sudo ls", rules=["warn:sudo"]) is None


def test_audit_result_defaults():
    r = AuditResult(output="x")
    assert r.rewritten is False
    assert r.alerts == []
    assert r.notes == []
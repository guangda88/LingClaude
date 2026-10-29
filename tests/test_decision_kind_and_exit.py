"""第2批 ⑥⑦ 契约测试：DecisionKind 类型化 + success_gate 公共出口。

钉住的语义（2026-10-29，五方评审执行序列第2批）：
1. DecisionDomain 不再悬空（__all__ 导出且可实例化，str 子类兼容旧常量）
2. DecisionKind 四值受控；record 默认 local **不落盘**（存量数据零漂移）
3. 显式 kind=noul 落盘；kind 轴进 dedup 指纹不改变旧指纹行为
4. SuccessVerdict.record() 公共出口：三态映射 + kind 自动归一 + fail-open
5. gate_success 语义修正：无信号字段的内容型结果 → 中性不定论，不进兜底
   （修 read 结果被文本正则误判 deny 0.044 的语义错位，10-27 台账实锤）
6. ModelMessage.confidence 槽：默认 None；不上 wire（to_dict 零序列化）
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent


def _load(name: str, relpath: str):
    """照 test_session_map 惯例 importlib 直载（避免依赖包安装状态）。"""
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001
        spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod


decision_log = _load("lingclaude.core.decision_log", "lingclaude/core/decision_log.py")
success_gate = _load("lingclaude.core.success_gate", "lingclaude/core/success_gate.py")
model_types = _load("lingclaude.core.model_types", "lingclaude/core/model_types.py")


@pytest.fixture()
def tmp_log(monkeypatch):
    """隔离台账 root + 清单例缓存（照 conftest 决策流隔离惯例）。"""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(decision_log, "_DEFAULT_LOG", None)
        log = decision_log.DecisionLog(root=Path(td))
        monkeypatch.setattr(decision_log, "_DEFAULT_LOG", log)
        yield log, Path(td)


# ---------- 1. DecisionDomain 不再悬空 ----------

def test_decision_domain_no_longer_dangling():
    """__all__ 声明的 DecisionDomain 必须真实可引用（悬空=本次修的债）。"""
    assert hasattr(decision_log, "DecisionDomain")
    assert "DecisionDomain" in decision_log.__all__
    # str 子类：与旧常量同值共存
    assert decision_log.DecisionDomain.SUCCESS.value == decision_log.DOMAIN_SUCCESS
    assert decision_log.DecisionDomain("route") is decision_log.DecisionDomain.ROUTE


def test_decision_kind_values():
    k = decision_log.DecisionKind
    assert {m.value for m in k} == {"local", "noul", "model", "user"}


# ---------- 2. kind 默认不落盘（存量零漂移） ----------

def test_kind_default_local_not_serialized(tmp_log):
    log, root = tmp_log
    log.record("success", "read", "gray", reason="r")
    lines = (root / f"decisions_{__import__('datetime').datetime.now():%Y%m}.jsonl").read_text().splitlines()
    d = json.loads(lines[-1])
    assert "kind" not in d  # 默认 local 不写 → 旧消费方零感知


def test_kind_explicit_serialized_and_roundtrip(tmp_log):
    log, root = tmp_log
    log.record("success", "read", "deny", reason="noul", kind="noul")
    log.record("route", "x", "allow", reason="m", kind=decision_log.DecisionKind.MODEL_ROUTE)
    lines = (root / f"decisions_{__import__('datetime').datetime.now():%Y%m}.jsonl").read_text().splitlines()
    kinds = [json.loads(x).get("kind") for x in lines]
    assert "noul" in kinds and "model" in kinds
    # from_dict 回读兼容
    e = decision_log.DecisionEntry.from_dict(json.loads(lines[-1]))
    assert e.kind == "model"


# ---------- 3. SuccessVerdict.record 公共出口 ----------

def test_verdict_record_maps_and_normalizes_kind(tmp_log):
    v = success_gate.SuccessVerdict(True, 0.9, "exit_code=0", "local")
    out = v.record("pytest_tool", session_id="s1")
    assert out is v  # 链式
    v2 = success_gate.SuccessVerdict(False, 0.1, "err", "noul_fallback").record("t2")
    # 两条都入台账；kind 语义：local 缺席（=local 隐式），noul 显式落盘
    import glob as _g
    files = _g.glob(str(Path(decision_log._DEFAULT_LOG.root) / "decisions_*.jsonl"))
    rows = [json.loads(x) for f in files for x in Path(f).read_text().splitlines()]
    by_reason = {r["reason"]: r for r in rows}
    assert by_reason["exit_code=0"].get("kind", "local") == "local"
    assert by_reason["exit_code=0"]["verdict"] == "allow"
    assert by_reason["err"]["kind"] == "noul"
    assert by_reason["err"]["meta"]["source"] == "noul_fallback"


def test_verdict_record_fail_open(tmp_log, monkeypatch):
    """台账故障绝不反噬判定本身。"""
    def _boom(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(decision_log, "record_decision", _boom)
    v = success_gate.SuccessVerdict(True, 0.9, "ok", "local")
    assert v.record("t").p_success == 0.9  # 不抛、返回自身


# ---------- 4. gate_success 语义修正 ----------

def test_content_type_result_neutral_not_fallback():
    """read 形状（content/lines/cache_hit，无信号字段）→ 中性，不进 noul 兜底。"""
    v = success_gate.gate_success("read", {"content": "def x(): pass  # error handling", "lines": 3})
    assert v.is_success is None
    assert v.source == "neutral"
    assert v.p_success == 0.5


def test_signal_fields_still_judged():
    # bash 形状仍按原语义
    assert success_gate.gate_success("bash", {"stdout": "ok"}, exit_code=0).is_success is True
    v = success_gate.gate_success("bash", {"stderr": "Traceback (most recent call last)"})
    assert v.is_success is False


# ---------- 5. ModelMessage.confidence 槽 ----------

def test_message_confidence_default_none_and_not_on_wire():
    m = model_types.ModelMessage(role=model_types.MessageRole.USER, content="hi")
    assert m.confidence is None
    m2 = model_types.ModelMessage(role=model_types.MessageRole.ASSISTANT, content="y", confidence=0.42)
    d = m2.to_dict()
    assert "confidence" not in d  # 不上 wire
    assert m2.confidence == 0.42

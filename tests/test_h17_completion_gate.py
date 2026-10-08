"""H17 完成申报核验守卫测试（2026-10-08 winner 方案落地配套, hooks 架构形态）。

覆盖面: 休眠态白名单 / 确定性拒收 / fail-open 降级 / 后置留痕 / core hook 接线。
verify_log 目录一律 monkeypatch 到 tmp_path，不碰真实 data/。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lingclaude.core.hooks import HookContext, HookManager, HookType
from lingclaude.core.tool_executor import ToolExecutor
from lingclaude.core.types import ToolErrorCode
from lingclaude.gov.guard import h17_completion_gate as h17


@pytest.fixture()
def whitelist_on(monkeypatch):
    """激活守卫触发面（真实模块默认休眠，逐测注入白名单）。"""
    monkeypatch.setattr(h17, "COMPLETION_DECLARE_TOOLS", frozenset({"declare_complete"}))


@pytest.fixture(autouse=True)
def _isolate_verify_log(monkeypatch, tmp_path):
    monkeypatch.setattr(h17, "VERIFY_LOG_DIR", tmp_path / "verify_log")
    (tmp_path / "verify_log").mkdir()


def _write_verify_log(tmp_path, trace_id, task_id="T-20261008-001"):
    log = tmp_path / "verify_log" / "verify_20261008.jsonl"
    rec = {
        "ts": 1791427982.0,
        "kind": "verify",
        "claim": "step done",
        "evidence": "[测试证据]",
        "digest": "sha256:test",
        "trace_id": trace_id,
    }
    # 追加模式: 同一测试写多条证据记录时不互相覆盖
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 休眠态（真实默认） ──
def test_dormant_by_default_whitelist_empty():
    assert h17.COMPLETION_DECLARE_TOOLS == frozenset()
    assert h17.pre_check("read", {"path": "x"}, {}) == {"allow": True}


def test_non_whitelisted_tool_always_allows(whitelist_on):
    assert h17.pre_check("read", {"path": "x"}, {}) == {"allow": True}
    assert h17.post_audit("read", object(), {}) is None
    assert h17._AUDIT_LOG == []


# ── 核验核心: 确定性拒收 ──
def test_block_when_no_task_id(whitelist_on):
    r = h17.pre_check("declare_complete", {}, {})
    assert r["allow"] is False
    assert r["verdict"] == "task_id_missing"
    assert r["guard"] == "H17_completion_verification"


def test_block_when_evidence_missing(whitelist_on, tmp_path):
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-001", "required_evidence": ["step_1", "step_2"]},
        {},
    )
    assert r["allow"] is False
    assert r["verdict"] == "evidence_insufficient"
    assert r["missing_evidence"] == ["step_1", "step_2"]
    assert r["have_trace_ids"] == []


def test_allow_when_all_evidence_present(whitelist_on, tmp_path):
    _write_verify_log(tmp_path, "T-20261008-001-step_1")
    _write_verify_log(tmp_path, "T-20261008-001-step_2")
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-001", "required_evidence": ["step_1", "step_2"]},
        {},
    )
    assert r["allow"] is True
    assert r["evidence_verified"] == 2


def test_steps_form_derives_required_ids(whitelist_on, tmp_path):
    _write_verify_log(tmp_path, "T-20261008-002-s1")
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-002", "steps": [{"id": "s1"}]},
        {},
    )
    assert r["allow"] is True


def test_wildcard_requires_any_evidence(whitelist_on, tmp_path):
    r = h17.pre_check("declare_complete", {"task_id": "T-20261008-003"}, {})
    assert r["allow"] is False  # 无任何记录 → 哨兵 "*" 不命中
    _write_verify_log(tmp_path, "T-20261008-003-任意步骤")
    r = h17.pre_check("declare_complete", {"task_id": "T-20261008-003"}, {})
    assert r["allow"] is True  # 一条即过


def test_corrupted_jsonl_line_skipped(whitelist_on, tmp_path):
    (tmp_path / "verify_log" / "bad.jsonl").write_text("not-json\n", encoding="utf-8")
    _write_verify_log(tmp_path, "T-20261008-004-x")
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-004", "required_evidence": ["x"]},
        {},
    )
    assert r["allow"] is True  # 坏行跳过不误伤


def test_missing_verify_log_dir_fails_closed_block(whitelist_on):
    """目录不存在 → have=空集 → 证据不足拒收（这是本职判定，不是失效）。"""
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-NOLOG", "required_evidence": ["x"]},
        {},
    )
    assert r["allow"] is False


# ── hook 回调层 ──
def test_pre_hook_silent_for_non_whitelisted(whitelist_on):
    ctx = HookContext(
        hook_type=HookType.PRE_TOOL_USE,
        session_id="s1",
        tool_name="read",
        metadata={"tool_args": {"path": "x"}},
    )
    assert h17._pre_tool_use_hook(ctx) is None  # 休眠路径零修改


def test_pre_hook_sets_guard_block_metadata(whitelist_on):
    ctx = HookContext(
        hook_type=HookType.PRE_TOOL_USE,
        session_id="s1",
        tool_name="declare_complete",
        metadata={"tool_args": {}},
    )
    out = h17._pre_tool_use_hook(ctx)
    assert out is not None
    assert out.metadata["guard_block"] is True
    assert out.metadata["guard_name"] == "H17_completion_verification"
    assert out.metadata["guard_verdict"] == "task_id_missing"
    # 原 metadata 键保留（不丢调用方上下文）
    assert "tool_args" in out.metadata


def test_pre_hook_fail_open_on_exception(whitelist_on, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(h17, "pre_check", _boom)
    ctx = HookContext(
        hook_type=HookType.PRE_TOOL_USE,
        session_id="s1",
        tool_name="declare_complete",
        metadata={"tool_args": {}},
    )
    assert h17._pre_tool_use_hook(ctx) is None  # 降级放行


def test_post_hook_records_audit(whitelist_on, monkeypatch):
    monkeypatch.setattr(h17, "_AUDIT_LOG", [])
    ctx = HookContext(
        hook_type=HookType.POST_TOOL_USE,
        session_id="s1",
        tool_name="declare_complete",
        metadata={"tool_result": SimpleNamespace(success=True, error=None)},
    )
    assert h17._post_tool_use_hook(ctx) is None
    assert h17._AUDIT_LOG[-1]["kind"] == "h17_completion_audit"
    assert h17._AUDIT_LOG[-1]["ok"] is True


def test_register_with_installs_both_hooks():
    m = HookManager()
    h17.register_with(m)
    assert m.has_hooks(HookType.PRE_TOOL_USE)
    assert m.has_hooks(HookType.POST_TOOL_USE)
    names = [h["name"] for h in m.list_hooks()]
    assert "h17_pre_tool_use" in names and "h17_post_tool_use" in names


# ── core 接线（集成面: _execute_tool_typed PRE 拦截 → GUARD_DENIED）──
def _make_executor(hook_manager):
    """最小 executor（只测 hook 段，不跑完整引擎; __new__ 绕过 __init__ 依赖）。"""
    ex = ToolExecutor.__new__(ToolExecutor)
    ex._engine = SimpleNamespace(
        _hooks=hook_manager,
        _tool_call_count=0,
        config=SimpleNamespace(max_tool_calls_per_session=0),
        _tool_call_log=[],
        session_id="s-test",
    )
    return ex


def test_core_hook_blocks_via_guard_denied(whitelist_on):
    m = HookManager()
    h17.register_with(m)
    ex = _make_executor(m)
    tr = ex._execute_tool_typed("declare_complete", "{}")
    assert tr.success is False
    assert tr.error.code == ToolErrorCode.GUARD_DENIED
    assert "task_id" in tr.error.message  # 拦截消息含补救方向


def test_core_hook_passthrough_normal_tool(whitelist_on):
    m = HookManager()
    h17.register_with(m)
    ex = _make_executor(m)
    tr = ex._execute_tool_typed("unknown_tool_xyz", "{}")
    # 白名单外工具: guard 放行（语义断言=未被守卫拦截）。runtime 未注入时
    # executor 实际返回 EXECUTION_ERROR（2026-10-08 实测），非守卫语义错误码。
    assert tr.error.code != ToolErrorCode.GUARD_DENIED
    assert tr.error.code == ToolErrorCode.EXECUTION_ERROR


# ── attach 显式挂载 API ──
class _FakeEngine:
    def __init__(self, hooks=None):
        self._hooks = hooks


def test_attach_installs_on_engine_hooks():
    m = HookManager()
    eng = _FakeEngine(m)
    h17.attach(eng)
    assert m.has_hooks(HookType.PRE_TOOL_USE)
    assert m.has_hooks(HookType.POST_TOOL_USE)


def test_attach_idempotent_no_duplicate():
    m = HookManager()
    eng = _FakeEngine(m)
    h17.attach(eng)
    h17.attach(eng)  # 二次挂载不产生双份拦截
    pre = [h for h in m.list_hooks() if h["name"] == "h17_pre_tool_use"]
    post = [h for h in m.list_hooks() if h["name"] == "h17_post_tool_use"]
    assert len(pre) == 1 and len(post) == 1


def test_attach_no_hooks_bus_is_noop():
    eng = _FakeEngine(None)  # 无 _hooks：warning + 零侵入
    h17.attach(eng)  # 不抛异常
    assert getattr(eng, "_hooks", None) is None


# ── 证据 id 派生语义（2026-10-08 预验证抓到的 bug 回归钉）──
def test_short_step_id_matches_prefixed_trace(whitelist_on, tmp_path):
    """trace_id=T-001-step_1 应命中 required step_1（剥前缀派生）。"""
    _write_verify_log(tmp_path, "T-20261008-001-step_1")
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-001", "required_evidence": ["step_1"]},
        {},
    )
    assert r["allow"] is True


def test_no_substring_false_positive(whitelist_on, tmp_path):
    """step_1 不得子串误命中 step_10——精确集合匹配。"""
    _write_verify_log(tmp_path, "T-20261008-009-step_10")
    r = h17.pre_check(
        "declare_complete",
        {"task_id": "T-20261008-009", "required_evidence": ["step_1"]},
        {},
    )
    assert r["allow"] is False
    assert r["missing_evidence"] == ["step_1"]


def test_task_level_trace_hits_wildcard(whitelist_on, tmp_path):
    """trace_id 恰等于 task_id（整任务级证据）→ 哨兵 * 命中。"""
    _write_verify_log(tmp_path, "T-20261008-010")
    r = h17.pre_check("declare_complete", {"task_id": "T-20261008-010"}, {})
    assert r["allow"] is True

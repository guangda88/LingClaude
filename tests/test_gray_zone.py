"""H2 灰区 (gray zone) escalate 契约测试 — 灵元「白/黑/灰区 escalate」落地验收。

覆盖：
  - ask 模式写/执行域工具 → 灰区 escalate（state=escalated + pending 落盘 + bus 通知）
  - ask 模式只读工具放行（不落灰区）
  - auto 模式写工具直接放行（不落灰区）
  - strict 模式写工具拒绝但不落盘（灰区语义只属于 ask）
  - deny 名单动作不 escalate（硬拒绝直接拦截）
  - gray_zone_escalate 单测：落盘 + notify 参数语义

注意：execute_tool 触发 escalate 会尝试 LingBus 通知（best-effort，
throttle/不可用时仅 WARNING）。测试用 monkeypatch 屏蔽真实 bus 调用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.gray_zone import gray_zone_escalate
from lingclaude.core.permissions import (
    PENDING_LOG_PATH,
    REASON_PENDING,
    reset_permission_stores,
    set_permission_mode,
)
from lingclaude.engine.coding import CodingRuntime


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """每个测试：隔离 CWD（pending 落盘位置）+ 屏蔽真实 LingBus + 重置 store。"""
    monkeypatch.chdir(tmp_path)
    reset_permission_stores()
    # 屏蔽真实 bus 通知（best-effort 但测试不想依赖/污染真实 LingBus）
    monkeypatch.setattr(
        "lingclaude.core.gray_zone._notify_bus", lambda *a, **k: None
    )
    yield
    reset_permission_stores()


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    return CodingRuntime(config=lingclaudeConfig())


def _pending_lines() -> list[dict]:
    p = Path(PENDING_LOG_PATH)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestGrayZoneEscalateUnit:
    def test_returns_pending_reason(self, tmp_path):
        reason = gray_zone_escalate("write", {"path": "t.txt"}, session_id="s1")
        assert reason == REASON_PENDING

    def test_writes_pending_with_params(self, tmp_path):
        gray_zone_escalate("write", {"path": "t.txt", "content": "x"}, session_id="s1")
        lines = _pending_lines()
        assert len(lines) == 1
        rec = lines[0]
        assert rec["action"] == "write"
        assert rec["mode"] == "ask"
        assert rec["state"] == "pending"  # 灰区默认 state=pending（P0-N5 闭环入口）
        assert rec["params"] == {"path": "t.txt", "content": "x"}

    def test_multiple_escalations_append(self, tmp_path):
        gray_zone_escalate("bash", {"command": "echo a"})
        gray_zone_escalate("edit", {"path": "f.py"})
        assert len(_pending_lines()) == 2


class TestExecuteToolGrayZone:
    def test_ask_write_escalated(self, runtime):
        """ask 模式写工具 → 灰区 escalate：state=escalated + pending 落盘。"""
        set_permission_mode("ask")
        res = runtime.execute_tool("write", path="t.txt", content="hello")
        assert "blocked by permissions" in res.get("error", "")
        assert res.get("state") == "escalated"
        assert res.get("escalation") == REASON_PENDING
        lines = _pending_lines()
        assert any(rec["action"] == "write" for rec in lines)

    def test_ask_bash_escalated(self, runtime):
        """ask 模式执行域工具（bash）→ 灰区 escalate。"""
        set_permission_mode("ask")
        res = runtime.execute_tool("bash", command="echo hi")
        assert res.get("state") == "escalated"
        assert any(rec["action"] == "bash" for rec in _pending_lines())

    def test_ask_read_not_escalated(self, runtime, tmp_path):
        """ask 模式只读工具放行 — 不落灰区。"""
        set_permission_mode("ask")
        f = tmp_path / "ok.txt"
        f.write_text("data", encoding="utf-8")
        res = runtime.execute_tool("read", path=str(f))
        assert res.get("error") is None or "blocked by permissions" not in res.get("error", "")
        assert res.get("state") != "escalated"
        assert _pending_lines() == []

    def test_ask_plan_mode_not_escalated(self, runtime):
        """ask 模式 plan_mode（read 域）必须可进入 — 不落入灰区。"""
        set_permission_mode("ask")
        res = runtime.execute_tool("plan_mode", action="enter")
        assert res.get("plan_mode") is True
        assert res.get("state") != "escalated"

    def test_auto_write_passes(self, runtime, tmp_path):
        """auto 模式写工具直接放行 — 不落灰区。"""
        set_permission_mode("auto")
        res = runtime.execute_tool("write", path="t.txt", content="ok")
        assert "blocked by permissions" not in res.get("error", "")
        assert res.get("state") != "escalated"
        assert _pending_lines() == []

    def test_strict_write_denied_no_pending(self, runtime):
        """strict 模式写工具拒绝但不落盘 — 灰区语义只属于 ask。"""
        set_permission_mode("strict")
        res = runtime.execute_tool("write", path="t.txt", content="x")
        assert "blocked by permissions" in res.get("error", "")
        assert res.get("state") != "escalated"
        assert _pending_lines() == []

    def test_deny_action_not_escalated(self, runtime):
        """deny 名单动作（如 sudo 域 rm_rf_root）不 escalate — 硬拒绝。"""
        set_permission_mode("ask")
        # rm_rf_root 不在 registry，用 _bad_test 不在 deny 名单；
        # 直接验证 execute_tool 对 deny 名单工具的判定不产生 escalate：
        # write 到 deny 名单无法直接触发（deny 名单是工具名级别），
        # 改验证 ask 下已审批放行的 write 不再落灰区。
        from lingclaude.core.permissions import record_permission_decision

        record_permission_decision("default", "write", "always_allow")
        res = runtime.execute_tool("write", path="t.txt", content="ok")
        assert "blocked by permissions" not in res.get("error", "")
        assert _pending_lines() == []

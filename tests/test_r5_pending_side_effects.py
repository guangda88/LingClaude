"""R5 阶段2: 副作用幂等检测 + 分类常量测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# ── 权限常量 ───────────────────────────────────────────────────────────


class TestSideEffectToolClassification:
    """SIDE_EFFECT_TOOLS 必须显式列出且与 READ_ONLY_TOOLS 互斥（避免自动降级）。"""

    def test_side_effect_tools_contains_writes(self):
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        for must in ("write", "edit", "bash", "rm"):
            assert must in SIDE_EFFECT_TOOLS, f"{must} 必须在副作用集合（写/执行类）"

    def test_side_effect_tools_contains_network(self):
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        for must in ("curl", "wget", "ssh"):
            assert must in SIDE_EFFECT_TOOLS, f"{must} 必须在副作用集合（网络类）"

    def test_side_effect_tools_excludes_readonly(self):
        """只读工具不应出现在副作用集合（避免 resume 时无谓问用户）。"""
        from lingclaude.core.permissions import READ_ONLY_TOOLS, SIDE_EFFECT_TOOLS

        overlap = READ_ONLY_TOOLS & SIDE_EFFECT_TOOLS
        assert not overlap, f"只读工具不该在副作用集合里: {overlap}"

    def test_side_effect_tools_is_frozen(self):
        """frozenset 保证不被运行期污染（H17: 分类常量不可变）。"""
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        assert isinstance(SIDE_EFFECT_TOOLS, frozenset)


# ── Journal pending_side_effects ────────────────────────────────────────


@pytest.fixture()
def journal_with_history(tmp_path):
    """构造一个 journal 含 4 条事件: 2 个 write（含 result）、1 个无 result 的 bash、1 个 read。"""
    from lingclaude.core.session_journal import SessionJournal

    sid = "test_session_pending"
    journal = SessionJournal(sid, journal_dir=tmp_path)
    journal.append("tool_call", {
        "tool_call_id": "tc_w1", "name": "write", "arguments": '{"path":"a.py"}',
    })
    journal.append("tool_result", {
        "tool_call_id": "tc_w1", "output_preview": "ok", "is_error": False,
    })
    journal.append("tool_call", {
        "tool_call_id": "tc_b1", "name": "bash", "arguments": '{"command":"ls"}',
    })
    # tc_b1 没有 result —— 中断前发出但未收到结果
    journal.append("tool_call", {
        "tool_call_id": "tc_r1", "name": "read", "arguments": '{"path":"b.py"}',
    })
    # tc_r1 无 result —— 但 read 是只读,不应出现在 pending 里
    return journal, sid


class TestPendingSideEffects:
    def test_writes_and_bash_with_no_result_are_pending(self, journal_with_history):
        journal, _ = journal_with_history
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        pending = journal.pending_side_effects(SIDE_EFFECT_TOOLS)
        ids = [p["tool_call_id"] for p in pending]
        assert "tc_b1" in ids, "无 result 的 bash 副作用必须出现"
        assert "tc_w1" not in ids, "已完成的 write 不应在 pending"
        assert "tc_r1" not in ids, "只读工具不计入副作用"

    def test_pending_entries_have_required_fields(self, journal_with_history):
        journal, _ = journal_with_history
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        pending = journal.pending_side_effects(SIDE_EFFECT_TOOLS)
        assert len(pending) >= 1
        entry = pending[0]
        for key in ("tool_call_id", "name", "arguments", "status"):
            assert key in entry, f"pending 元素必须含字段 {key}"
        assert entry["status"] == "awaiting_result"

    def test_empty_side_effect_set_returns_empty(self, tmp_path):
        """传空集合 = 保守返回空（不误报写为副作用）。"""
        from lingclaude.core.session_journal import SessionJournal

        journal = SessionJournal("s_empty", journal_dir=tmp_path)
        journal.append("tool_call", {
            "tool_call_id": "tc1", "name": "write", "arguments": "{}",
        })
        # 空集合 = 不返回任何副作用
        assert journal.pending_side_effects(set()) == []
        # 但传入实际集合会返回
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        pending = journal.pending_side_effects(SIDE_EFFECT_TOOLS)
        assert len(pending) == 1

    def test_orphan_tool_result_does_not_create_ghost_pending(self, tmp_path):
        """孤儿 tool_result（无对应 tool_call）不应凭空产生 pending。"""
        from lingclaude.core.session_journal import SessionJournal
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        journal = SessionJournal("s_orphan", journal_dir=tmp_path)
        journal.append("tool_result", {
            "tool_call_id": "tc_orphan", "output_preview": "?", "is_error": False,
        })
        assert journal.pending_side_effects(SIDE_EFFECT_TOOLS) == []

    def test_completed_side_effect_removed_even_if_later_duplicate(self, tmp_path):
        """同名工具再次被调用时,旧的已完成项不再 pending（按 ID 去重）。"""
        from lingclaude.core.session_journal import SessionJournal
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS

        journal = SessionJournal("s_dup", journal_dir=tmp_path)
        journal.append("tool_call", {"tool_call_id": "tc_a", "name": "write", "arguments": "{}"})
        journal.append("tool_result", {"tool_call_id": "tc_a", "is_error": False})
        # 模型重试:产生新的 tc_b,但同签名
        journal.append("tool_call", {"tool_call_id": "tc_b", "name": "write", "arguments": "{}"})

        pending = journal.pending_side_effects(SIDE_EFFECT_TOOLS)
        ids = [p["tool_call_id"] for p in pending]
        # tc_a 已完成不应出现,只剩新的 tc_b
        assert "tc_a" not in ids
        assert "tc_b" in ids

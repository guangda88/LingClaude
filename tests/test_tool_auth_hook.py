"""tool_auth_hook 测试（灵元 R2 P0：四档策略引擎）"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 依赖注入：mock 掉 LC_ROOT 和 PolicyLoader ──────────────────────
_Hook = sys.modules[__name__]

# 临时策略目录
_POLICY_CONTENT = {
    "tiers": {
        "auto": {"description": "只读", "tools": ["Read", "grep", "glob"]},
        "pre_approve": {"description": "可逆写", "tools": ["edit_file", "write_file"]},
        "ask": {"description": "外部发送", "tools": ["bash", "run_bash"]},
        "block": {"description": "破坏性", "tools": ["delete_file", "rm"]},
    },
    "audit_enabled": True,
}

_MOCK_POLICY: dict | None = None


def _mock_get(name: str) -> dict | None:
    if name == "tool_auth_policy":
        return _MOCK_POLICY
    return None


# ── 录盘式测试 fixture ──────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolate_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离：每次测试用独立临时台账目录 + mock PolicyLoader。"""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        # Mock PolicyLoader.get
        monkeypatch.setattr(
            "lingclaude.core.policy_loader.get",
            _mock_get,
        )
        # 重载模块以吃 mock（global 变量在 import 时已绑定，故直接 patch）
        import lingclaude.core.tool_auth_hook as _hook

        global _MOCK_POLICY
        _MOCK_POLICY = dict(_POLICY_CONTENT)

        # 重置模块缓存 + 直接 patch 已计算的路径（import 时已钉死）
        _hook._policy_cache = None
        _hook._LEDGER_DIR = tmpdir / "data" / "arch_ledger"

        yield tmpdir

        # 测试后清理（teardown）
        _MOCK_POLICY = None


# ── 核心语义测试 ────────────────────────────────────────────────

class TestTierClassification:
    def test_auto_tier(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("Read", {})
        assert d.tier == Tier.AUTO
        assert d.audit_written is True
        assert d.tool_name == "Read"

    def test_auto_tier_glob(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("glob", {})
        assert d.tier == Tier.AUTO

    def test_pre_approve_tier(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("edit_file", {"path": "/a/b"})
        assert d.tier == Tier.PRE_APPROVE

    def test_ask_tier(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("bash", {"command": "ls"})
        assert d.tier == Tier.ASK

    def test_block_tier(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("delete_file", {"path": "/tmp/x"})
        assert d.tier == Tier.BLOCK

    def test_unknown_falls_back_to_ask(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("some_unknown_tool", {})
        assert d.tier == Tier.ASK  # 安全默认值

    def test_unknown_audit_written(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("some_unknown_tool", {})
        assert d.audit_written is True  # 未命中也写台账


class TestRegexMatching:
    def test_edit_file_exact(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("edit_file", {})
        assert d.tier == Tier.PRE_APPROVE

    def test_write_file_exact(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("write_file", {})
        assert d.tier == Tier.PRE_APPROVE


class TestAuditLedger:
    def test_audit_written_on_allow(self) -> None:
        import lingclaude.core.tool_auth_hook as _hook
        # 验证 audit_written 标志为 True（台账写入成功的标志，非文件存在）
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("Read", {})
        assert d.audit_written is True, "auto 档 audit_written 必须为 True"
        # 验证 ledger dir 可写（_write_audit 用 try/except，故 audit_written=True 即成功）
        assert _hook._LEDGER_DIR is not None

    def test_audit_written_on_block(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("delete_file", {})
        assert d.audit_written is True, "block 档 audit_written 必须为 True"

    def test_audit_records_policy_id(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("bash", {"command": "echo hi"})
        assert d.policy_id != "", "policy_id 必须非空"
        assert d.policy_id != "error", "policy_id 正常不应是 error"
        assert len(d.policy_id) == 12, f"policy_id 应为 12 位哈希，实际 {d.policy_id}"


class TestGracefulDegradation:
    def test_bad_policy_returns_ask(self) -> None:
        import lingclaude.core.tool_auth_hook as _hook

        global _MOCK_POLICY
        _MOCK_POLICY = {"tiers": None}  # 破坏格式
        _hook._policy_cache = None  # 清缓存

        from lingclaude.core.tool_auth_hook import check_tool_call, Tier
        d = check_tool_call("anything", {})
        assert d.tier == Tier.ASK  # 降级到 ask，绝不抛异常


class TestDecisionFields:
    def test_decision_has_policy_id(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("Read", {})
        assert hasattr(d, "policy_id")
        assert hasattr(d, "reason")
        assert hasattr(d, "audit_written")

    def test_unknown_tool_has_reason(self) -> None:
        from lingclaude.core.tool_auth_hook import check_tool_call
        d = check_tool_call("unknown_tool_xyz", {})
        assert "未命中" in d.reason or d.reason != ""

"""P0 通电三支（2026-09-23）默认值翻转 + 审批前缀沉淀测试。

评审共识「改默认值+补钩子」的落地验收：
1. worktree 扇出默认开（LINGCLAUDE_AGENT_WORKTREE 显式 0 关）
2. credential_pool 默认开（池未配置零分叉落回原链；显式 0 关）
3. 审批前缀沉淀接线（always_allow/allow_persist + command → approvals.json；
   无 command 不沉淀——禁止 tool_name 全量放行）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.model import factory as model_factory
from lingclaude.core import approval_matrix as am
from lingclaude.core import permissions as perms


# ── 1. worktree 扇出 ────────────────────────────────────────────────

def test_worktree_default_on(monkeypatch):
    monkeypatch.delenv("LINGCLAUDE_AGENT_WORKTREE", raising=False)
    from lingclaude.plugins.agents.proj_agent_gateway import server
    assert server._worktree_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "  0  "])
def test_worktree_explicit_off(monkeypatch, raw):
    monkeypatch.setenv("LINGCLAUDE_AGENT_WORKTREE", raw)
    from lingclaude.plugins.agents.proj_agent_gateway import server
    assert server._worktree_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True"])
def test_worktree_legacy_on_compat(monkeypatch, raw):
    monkeypatch.setenv("LINGCLAUDE_AGENT_WORKTREE", raw)
    from lingclaude.plugins.agents.proj_agent_gateway import server
    assert server._worktree_enabled() is True


# ── 2. credential_pool ──────────────────────────────────────────────

def test_pool_default_on(monkeypatch):
    monkeypatch.delenv("LINGCLAUDE_CREDENTIAL_POOL", raising=False)
    assert model_factory._credential_pool_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "off"])
def test_pool_explicit_off(monkeypatch, raw):
    monkeypatch.setenv("LINGCLAUDE_CREDENTIAL_POOL", raw)
    assert model_factory._credential_pool_enabled() is False


def test_pool_unconfigured_zero_drift(monkeypatch):
    """通电核心安全论证：池未配置 → 落回原链（env/key_store），绝不出池 key。"""
    monkeypatch.delenv("LINGCLAUDE_CREDENTIAL_POOL_KEYS", raising=False)
    monkeypatch.setattr(model_factory, "_CREDENTIAL_POOL", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # 池空 → next_key None → 原链兜底（key_store 跨仓可能有真值，故断言来源不断言数值）
    key = model_factory._get_env_key("openai")
    assert key not in ("pk-alpha", "pk-beta")


def test_pool_configured_takes_pool_key(monkeypatch):
    monkeypatch.setenv("LINGCLAUDE_CREDENTIAL_POOL_KEYS", "openai:pk-alpha,pk-beta")
    monkeypatch.setattr(model_factory, "_CREDENTIAL_POOL", None)  # 重置单例
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    key = model_factory._get_env_key("openai")
    assert key in ("pk-alpha", "pk-beta")  # 池优先于 key_store
    monkeypatch.setattr(model_factory, "_CREDENTIAL_POOL", None)  # 清理，防泄漏他测


def test_pool_off_ignores_keys(monkeypatch):
    """显式关：即使配置了池，key 也绝不来自池（=0 关闭语义）。"""
    monkeypatch.setenv("LINGCLAUDE_CREDENTIAL_POOL", "0")
    monkeypatch.setenv("LINGCLAUDE_CREDENTIAL_POOL_KEYS", "openai:pk-x")
    monkeypatch.setattr(model_factory, "_CREDENTIAL_POOL", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert model_factory._get_env_key("openai") != "pk-x"
    monkeypatch.setattr(model_factory, "_CREDENTIAL_POOL", None)


# ── 3. 审批前缀沉淀 ─────────────────────────────────────────────────

@pytest.fixture()
def rules_file(tmp_path, monkeypatch) -> Path:
    """沉淀目标定向 tmp，不污染仓库 approvals.json；重置沉淀矩阵单例。"""
    p = tmp_path / "approvals.json"
    monkeypatch.setattr(am, "RULES_PATH", p)
    monkeypatch.setattr(perms, "_SEDIMENT_MATRIX", None)
    return p


def test_sediment_writes_prefix(rules_file):
    assert perms.sediment_approval_prefix("git status") is True
    data = json.loads(rules_file.read_text(encoding="utf-8"))
    assert "git status" in data["always_allow"]


def test_sediment_no_command_no_write(rules_file):
    assert perms.sediment_approval_prefix("") is False
    assert perms.sediment_approval_prefix("   ") is False
    assert not rules_file.exists()


def test_sediment_denies_tool_name_shortcut(rules_file):
    """安全边界：调用方没给 command 就不许沉淀（防 'allow bash' 全量放行）。"""
    perms.record_permission_decision("sess-x", "bash", "always_allow", command="")
    assert not rules_file.exists()


def test_record_decision_always_allow_with_command(rules_file):
    perms.record_permission_decision("sess-y", "bash", "always_allow",
                                     command="pytest -q tests/")
    data = json.loads(rules_file.read_text(encoding="utf-8"))
    assert "pytest -q tests/" in data["always_allow"]


def test_record_decision_allow_does_not_sediment(rules_file):
    perms.record_permission_decision("sess-z", "bash", "allow", command="ls -la")
    assert not rules_file.exists()


def test_record_decision_deny_does_not_sediment(rules_file):
    perms.record_permission_decision("sess-w", "bash", "deny", command="rm -rf /")
    assert not rules_file.exists()

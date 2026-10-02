"""P2③ execution_domain 测试（2026-10-02）。

覆盖面（对齐 execution_domain.py 契约）：
  1. 未激活语义：net_allowlist 段缺失 → allowlist=() + net_allowed None（零行为变化）
  2. 解析：scope 前缀匹配/首命中优先/default 段/default 缺失=不限制
  3. 域名匹配：精确 + 点前缀子域 + scheme/port/userinfo 剥离 + 空条目过滤
  4. 聚合：write_paths 引 sandbox_rules（规则源唯一）+ net 回填 + scope 回显
  5. fail-soft：任一规则源故障不拖垮域视图
  6. side_effect_meta 元数据回填（坏结构留空不阻塞）
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from lingclaude.core import execution_domain as ed
from lingclaude.core import sandbox_rules


# ---------------------------------------------------------------- helpers

def _net(monkeypatch, sec: dict | None):
    """mock net_allowlist 段（None=段缺失）。"""
    def _load():
        if sec is None:
            return {}
        return {"net_allowlist": sec}
    monkeypatch.setattr(ed, "_load_policy", _load)


# ------------------------------------------------------------ 1. 未激活

def test_inactive_semantics(monkeypatch):
    _net(monkeypatch, None)
    assert ed.resolve_net_allowlist("/tmp") == ()
    assert ed.net_allowed("api.example.com", "/tmp") is None
    d = ed.resolve_domain("/tmp")
    assert d.net_rule_active is False and d.net_allowlist == ()


# ------------------------------------------------------------ 2. 解析

def test_scope_prefix_and_priority(monkeypatch):
    _net(monkeypatch, {
        "rules": [
            {"scope": "/home/ai/proj-a", "allow": ["api.a.com"]},
            {"scope": "/home/ai", "allow": ["api.b.com"]},
        ],
        "default": {"allow": ["api.c.com"]},
    })
    assert ed.resolve_net_allowlist("/home/ai/proj-a/sub") == ("api.a.com",)
    # /home/ai/other 落在第二条 /home/ai 规则内（目录边界前缀命中）
    assert ed.resolve_net_allowlist("/home/ai/other") == ("api.b.com",)


def test_default_missing_means_unrestricted(monkeypatch):
    _net(monkeypatch, {"rules": [{"scope": "/home/ai/proj-a", "allow": ["api.a.com"]}]})
    # 非命中项目且无 default 段 = 不限制（显式留空口）
    assert ed.resolve_net_allowlist("/home/ai/other") == ()
    assert ed.net_allowed("anything.net", "/home/ai/other") is None


# ------------------------------------------------------------ 3. 域名匹配

def test_host_matching_semantics(monkeypatch):
    _net(monkeypatch, {"default": {"allow": ["api.example.com", "cdn.io", ""]}})
    # 精确
    assert ed.net_allowed("api.example.com", "/tmp") is True
    # 点前缀子域
    assert ed.net_allowed("v2.api.example.com", "/tmp") is True
    # 不相关域
    assert ed.net_allowed("evil.example.org", "/tmp") is False
    # 同字符串不同后缀不误放（example.com != api.example.com 的反向）
    assert ed.net_allowed("example.com", "/tmp") is False
    # scheme/port/userinfo 剥离
    assert ed.net_allowed("https://api.example.com:8443/v1", "/tmp") is True
    assert ed.net_allowed("http://user@cdn.io", "/tmp") is True
    # 空条目被过滤，不产生放行
    assert ed.net_allowed("", "/tmp") is False


# ------------------------------------------------------------ 4. 聚合

def test_resolve_domain_aggregates(monkeypatch):
    # 路径维走 sandbox_rules 真规则（规则源唯一性锚点）
    monkeypatch.setattr(sandbox_rules, "_load_policy", lambda: {
        "directory_rules": {"default": {"writable": ["/tmp"]}},
    })
    _net(monkeypatch, {"default": {"allow": ["api.x.com"]}})
    d = ed.resolve_domain("/tmp")
    assert d.write_paths == ["/tmp"]          # 来自 sandbox_rules
    assert d.net_allowlist == ("api.x.com",)  # 来自 net_allowlist
    assert d.net_rule_active is True
    assert d.scope == "default"


def test_resolve_domain_scope_echo(monkeypatch):
    _net(monkeypatch, {"rules": [{"scope": "/tmp", "allow": ["a.b"]}]})
    d = ed.resolve_domain("/tmp")
    assert d.scope == "/tmp"


# ------------------------------------------------------------ 5. fail-soft

def test_fail_soft_on_sandbox_rules_crash(monkeypatch):
    def _boom():
        raise RuntimeError("policy store down")
    monkeypatch.setattr(sandbox_rules, "_load_policy", _boom)
    _net(monkeypatch, {"default": {"allow": ["api.x.com"]}})
    d = ed.resolve_domain("/tmp")   # 路径维炸掉，网络维照常聚合
    assert d.net_allowlist == ("api.x.com",) and d.write_paths == []


# ------------------------------------------------------------ 6. meta

def test_side_effect_meta(monkeypatch):
    _net(monkeypatch, None)
    monkeypatch.setattr(ed, "_load_policy", lambda: {
        "side_effect_meta": {"bash": {"risk": "high", "tier": "pre_approve"}},
    })
    d = ed.resolve_domain("/tmp", with_side_effect_meta=True)
    assert d.side_effect_meta == {"bash": {"risk": "high", "tier": "pre_approve"}}
    # 坏结构留空
    monkeypatch.setattr(ed, "_load_policy", lambda: {"side_effect_meta": "oops"})
    d2 = ed.resolve_domain("/tmp", with_side_effect_meta=True)
    assert d2.side_effect_meta == {}

"""G11b anchors 机制针对性负例测试（2026-10-03 方案 b 收口配套）。

覆盖三个此前不可红的洞：
  1. 锚不匹配（行内容被改）→ 不豁免 → 红（行内容变动敏感，行号漂移免疫）；
  2. 超额命中（同内容命中数 > anchors 登记数）→ 超额部分红
     （配额池跨命中累计消耗——修复「每调用重载台账致消耗失效」的假 1:1）；
  3. revoked/无定位档 → 不再整文件放行（封堵 lines=null 借
     「lines is None → True」分支全文件放行的漏洞）。

辅助函数直接引用 tests/test_p04_arch_guards.py 私有实现；守卫重构签名时
本档红灯提醒同步——属有意的双保险耦合（J5 口径：失准须显性暴露）。
"""

import json

import tests.test_p04_arch_guards as g

_BASE = {
    "guard": "G11b",
    "file": "engine/fake/x.py",
    "reason": "测试档：仅存在于 tmp_path 隔离台账",
    "review_due": "2999-12-31",
}
_LINE = "spec = importlib.util.spec_from_file_location(unique_name, path)"
_REL = "engine/fake/x.py"
_ENTRY = "G11b:engine__fake__x.py.json"  # 双布局兼容的扁平名


def _setup(tmp_path, monkeypatch, payload, raw=None):
    """隔离台账目录与配额池，写入一条豁免档（raw 非 None 时写字节原文）。"""
    monkeypatch.setattr(g, "_G11B_EXEMPTION_DIR", tmp_path)
    monkeypatch.setattr(g, "_G11B_ANCHOR_STATE", {})
    p = tmp_path / _ENTRY
    p.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
    return g


def test_line_anchor_strips_whitespace_and_is_deterministic():
    assert g._line_anchor("  x = 1  ") == g._line_anchor("x = 1")
    assert g._line_anchor("x = 1") != g._line_anchor("x = 2")


def test_anchor_mismatch_is_not_exempted(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {**_BASE, "anchors": ["deadbeefdead"]})
    assert g._g11b_exempted(_REL, 10, _LINE) is False


def test_anchor_match_exempts_with_fixtures(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {**_BASE, "anchors": [g._line_anchor(_LINE)]})
    assert g._g11b_exempted(_REL, 10, _LINE) is True


def test_quota_exhaustion_excess_hits_go_red(tmp_path, monkeypatch):
    """同内容命中数 > 登记数：第 1 处放行、第 2 处必须红。

    回归钉：首版实现在函数内 json.loads → remove，消耗跨调用失效，
    mcp_proxy 同内容 2 处若登记 1 条将双双放行（超额漏放）。
    """
    _setup(tmp_path, monkeypatch, {**_BASE, "anchors": [g._line_anchor(_LINE)]})
    assert g._g11b_exempted(_REL, 10, _LINE) is True
    assert g._g11b_exempted(_REL, 11, _LINE) is False  # 超额 → 红


def test_lines_and_anchors_conflict_is_rejected(tmp_path, monkeypatch):
    """双定位并存 = 台账自相矛盾 → 从严不豁免。"""
    _setup(tmp_path, monkeypatch,
           {**_BASE, "anchors": [g._line_anchor(_LINE)], "lines": [10]})
    assert g._g11b_exempted(_REL, 10, _LINE) is False


def test_no_positioning_rejects_revoked_null_hole(tmp_path, monkeypatch):
    """无任何定位字段（revoked 档 lines=null 同型）→ 不再整文件放行。"""
    _setup(tmp_path, monkeypatch, dict(_BASE))
    assert g._g11b_exempted(_REL, 1, "anything") is False


def test_legacy_lines_still_honored_without_anchors(tmp_path, monkeypatch):
    """存量 lines 兼容路径：无 anchors 时行号仍生效（渐进迁移窗口）。"""
    _setup(tmp_path, monkeypatch, {**_BASE, "lines": [10]})
    assert g._g11b_exempted(_REL, 10, _LINE) is True
    assert g._g11b_exempted(_REL, 11, _LINE) is False


def test_missing_required_fields_reject(tmp_path, monkeypatch):
    bad = {k: v for k, v in _BASE.items() if k != "review_due"}
    _setup(tmp_path, monkeypatch, {**bad, "anchors": [g._line_anchor(_LINE)]})
    assert g._g11b_exempted(_REL, 10, _LINE) is False


def test_broken_ledger_fails_closed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, None, raw="{not valid json")
    assert g._g11b_exempted(_REL, 10, _LINE) is False

"""token schema 老路径接入测试（token-schema-legacy-path-ingest 清偿①②，2026-09-24）。

覆盖：
- session_history 老路径记录带 token/cost 字段（未定价默认：cost_usd=None + unpriced）
- cost 换算：外置单价表命中（最长子串匹配）与数学口径（cached ⊆ input）
- 未配表 / 模型未登记 / 表损坏 → 一律 unpriced（绝不编价）
- _last_turn_output 落点漂移守卫（此前全仓无人赋值，D3 "占位 0" 复蹈点）
"""
from __future__ import annotations

import json
from pathlib import Path

from lingclaude.core.query_engine_lifecycle_mixin import QueryEngineLifecycleMixin
from lingclaude.core.token_pricing import compute_cost_usd, pricing_status


def _engine_stub() -> QueryEngineLifecycleMixin:
    """最小实例：mixin 无 __init__，绕过直接注入 _last_turn_* 状态。"""
    e = object.__new__(QueryEngineLifecycleMixin)
    e._last_turn_input = 1_200_000
    e._last_turn_output = 500_000
    e._last_turn_cached = 200_000
    e._last_model = "test-model-x"
    return e


class TestTokenUsageFields:
    def test_unpriced_by_default(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_TOKEN_PRICE_TABLE", raising=False)
        fields = _engine_stub()._token_usage_fields()
        assert fields["input_tokens"] == 1_200_000
        assert fields["output_tokens"] == 500_000
        assert fields["cached_tokens"] == 200_000
        assert fields["model"] == "test-model-x"
        assert fields["cost_usd"] is None
        assert fields["cost_status"] == "unpriced"

    def test_priced_with_external_table(self, tmp_path, monkeypatch):
        table = tmp_path / "prices.json"
        table.write_text(json.dumps({
            "test-model-x": {"input": 1.0, "output": 2.0, "cached": 0.5},
        }), encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(table))
        fields = _engine_stub()._token_usage_fields()
        # (1.2M-0.2M)*1 + 0.2M*0.5 + 0.5M*2 = 1.0 + 0.1 + 1.0 = 2.1 USD
        assert fields["cost_usd"] == 2.1
        assert fields["cost_status"] == "priced"

    def test_longest_substring_wins(self, tmp_path, monkeypatch):
        table = tmp_path / "prices.json"
        table.write_text(json.dumps({
            "test": {"input": 1.0, "output": 1.0},
            "test-model-x": {"input": 3.0, "output": 3.0},
        }), encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(table))
        e = _engine_stub()
        e._last_turn_input = 1_000_000
        e._last_turn_output = 0
        e._last_turn_cached = 0
        assert e._token_usage_fields()["cost_usd"] == 3.0  # 长键命中

    def test_corrupt_table_is_unpriced(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(bad))
        assert _engine_stub()._token_usage_fields()["cost_status"] == "unpriced"


class TestPricingUnit:
    def test_no_table_none(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_TOKEN_PRICE_TABLE", raising=False)
        assert compute_cost_usd("m", 100, 100) is None
        assert pricing_status("m") == "unpriced"

    def test_unregistered_model_none(self, tmp_path, monkeypatch):
        table = tmp_path / "prices.json"
        table.write_text(json.dumps({"other": {"input": 1, "output": 1}}), encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(table))
        assert compute_cost_usd("m", 100, 100) is None
        assert pricing_status("m") == "unpriced"

    def test_missing_price_keys_none(self, tmp_path, monkeypatch):
        table = tmp_path / "prices.json"
        table.write_text(json.dumps({"m": {"input": 1}}), encoding="utf-8")  # 缺 output
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(table))
        assert compute_cost_usd("m", 100, 100) is None

    def test_cached_subset_of_input(self, tmp_path, monkeypatch):
        table = tmp_path / "prices.json"
        table.write_text(json.dumps({"m": {"input": 1.0, "output": 0.0}}), encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_TOKEN_PRICE_TABLE", str(table))
        # cached 未单列 → 按 input 价（保守）；cached ⊆ input
        assert compute_cost_usd("m", 1_000_000, 0, cached_tokens=400_000) == 1.0


def test_last_turn_output_assignment_drift_guard():
    """_finalize_turn 必须写 _last_turn_output（老路径 output 非占位 0 的根）。"""
    src = Path("lingclaude/core/query_engine_turn_mixin.py").read_text(encoding="utf-8")
    assert "self._last_turn_output = total_output" in src

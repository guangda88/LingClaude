"""Tests for token_gate and data_filter (LLM Proxy P1)."""
from __future__ import annotations

import time


from lingclaude.model.llm_proxy.token_gate import TokenBudget, TokenGate
from lingclaude.model.llm_proxy.data_filter import DataFilter, AuditEntry, REDACTED


# --- TokenBudget ---


class TestTokenBudget:
    def test_remaining_pct_full(self):
        b = TokenBudget(provider="glm", key_id="k1", total_budget=1000)
        assert b.remaining_pct == 1.0

    def test_remaining_pct_partial(self):
        b = TokenBudget(provider="glm", key_id="k1", total_budget=1000, used=300)
        assert abs(b.remaining_pct - 0.7) < 0.001

    def test_should_degrade(self):
        b = TokenBudget(provider="glm", key_id="k1", total_budget=1000, used=960)
        assert b.should_degrade

    def test_should_not_degrade(self):
        b = TokenBudget(provider="glm", key_id="k1", total_budget=1000, used=100)
        assert not b.should_degrade

    def test_record_resets_on_window_expiry(self):
        b = TokenBudget(
            provider="glm", key_id="k1", total_budget=1000,
            used=500, window_seconds=1, reset_at=time.monotonic() - 2,
        )
        b.record(100)
        assert b.used == 100

    def test_record_accumulates_within_window(self):
        b = TokenBudget(
            provider="glm", key_id="k1", total_budget=1000,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        )
        b.record(50)
        b.record(30)
        assert b.used == 80


# --- TokenGate ---


class TestTokenGate:
    def test_check_available_no_budget(self):
        gate = TokenGate()
        assert gate.check_available("nonexistent")

    def test_check_available_under_watermark(self):
        gate = TokenGate()
        gate.register(TokenBudget(
            provider="glm", key_id="k1", total_budget=1000, used=100,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        assert gate.check_available("k1")

    def test_check_available_over_watermark(self):
        gate = TokenGate(high_watermark=0.95)
        gate.register(TokenBudget(
            provider="glm", key_id="k1", total_budget=1000, used=960,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        assert not gate.check_available("k1")

    def test_record_usage(self):
        gate = TokenGate()
        gate.register(TokenBudget(
            provider="glm", key_id="k1", total_budget=10000,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        gate.record_usage("k1", 500, 200)
        stats = gate.stats()
        assert stats["k1"]["used"] == 700

    def test_degrade_candidates(self):
        gate = TokenGate()
        gate.register(TokenBudget(
            provider="glm", key_id="k1", total_budget=1000, used=990,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        gate.register(TokenBudget(
            provider="nvidia", key_id="k2", total_budget=1000, used=100,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        candidates = gate.get_degrade_candidates(exclude_key="k1")
        assert "k2" in candidates
        assert "k1" not in candidates

    def test_stats(self):
        gate = TokenGate()
        gate.register(TokenBudget(
            provider="glm", key_id="k1", total_budget=1000, used=300,
            window_seconds=3600, reset_at=time.monotonic() + 3000,
        ))
        stats = gate.stats()
        assert "k1" in stats
        assert stats["k1"]["remaining_pct"] == 0.7


# --- DataFilter ---


class TestDataFilter:
    def test_sanitize_api_key(self):
        df = DataFilter()
        text = 'api_key=sk-12345abcdef'
        result = df.sanitize(text)
        assert "sk-12345abcdef" not in result
        assert REDACTED in result

    def test_sanitize_password(self):
        df = DataFilter()
        text = 'password: mysecret123'
        result = df.sanitize(text)
        assert "mysecret123" not in result
        assert REDACTED in result

    def test_sanitize_bearer(self):
        df = DataFilter()
        text = 'Authorization: Bearer tok_abc123xyz'
        result = df.sanitize(text)
        assert "tok_abc123xyz" not in result
        assert REDACTED in result

    def test_sanitize_clean_text(self):
        df = DataFilter()
        text = "Hello, how are you today?"
        assert df.sanitize(text) == text

    def test_sanitize_headers(self):
        df = DataFilter()
        headers = {
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
            "X-Api-Key": "key123",
        }
        result = df.sanitize_headers(headers)
        assert result["Authorization"] == REDACTED
        assert result["X-Api-Key"] == REDACTED
        assert result["Content-Type"] == "application/json"

    def test_record_and_query(self):
        df = DataFilter()
        entry = AuditEntry(
            timestamp=time.monotonic(),
            caller="lingclaude", purpose="coding",
            provider="nvidia", model="qwen3-coder",
            input_tokens=100, output_tokens=50,
            latency_ms=200.0, status="ok",
        )
        df.record(entry)
        results = df.query(caller="lingclaude")
        assert len(results) == 1
        assert results[0].model == "qwen3-coder"

    def test_query_filter_by_purpose(self):
        df = DataFilter()
        now = time.monotonic()
        df.record(AuditEntry(now, "lc", "coding", "nvidia", "m1", 10, 5, 100.0, "ok"))
        df.record(AuditEntry(now, "lc", "chat", "glm", "m2", 10, 5, 100.0, "ok"))
        results = df.query(purpose="coding")
        assert len(results) == 1
        assert results[0].purpose == "coding"

    def test_stats(self):
        df = DataFilter()
        now = time.monotonic()
        df.record(AuditEntry(now, "lc", "coding", "nvidia", "m1", 100, 50, 200.0, "ok"))
        df.record(AuditEntry(now, "lf", "chat", "glm", "m2", 200, 100, 300.0, "429"))
        stats = df.stats()
        assert stats["total"] == 2
        assert stats["by_caller"]["lc"] == 1
        assert stats["by_status"]["429"] == 1
        assert stats["total_input_tokens"] == 300
        assert stats["total_output_tokens"] == 150

    def test_stats_empty(self):
        df = DataFilter()
        assert df.stats() == {"total": 0}

    def test_audit_deque_maxlen(self):
        df = DataFilter(max_audit_entries=3)
        now = time.monotonic()
        for i in range(5):
            df.record(AuditEntry(now, "lc", "coding", "p", "m", 10, 5, 100.0, "ok"))
        stats = df.stats()
        assert stats["total"] == 3

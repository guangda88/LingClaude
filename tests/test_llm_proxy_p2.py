"""Tests for P2 features: streaming, retry, metrics."""
from __future__ import annotations

import threading
import time


from lingclaude.model.llm_proxy.retry import (
    RetryPolicy, ProviderRetryBudget, is_retryable, calculate_delay,
)
from lingclaude.model.llm_proxy.metrics import MetricsCollector, _MetricKey
from lingclaude.model.llm_proxy.provider_pool import StreamChunk


# --- Retry ---


class TestIsRetryable:
    def test_429_string(self):
        assert is_retryable("429") is True

    def test_429_int(self):
        assert is_retryable(429) is True

    def test_timeout(self):
        assert is_retryable("timeout") is True

    def test_5xx_int(self):
        assert is_retryable(500) is True
        assert is_retryable(502) is True
        assert is_retryable(503) is True
        assert is_retryable(504) is True

    def test_5xx_string_prefix(self):
        assert is_retryable("http_500") is True
        assert is_retryable("http_503") is True

    def test_non_retryable(self):
        assert is_retryable("ok") is False
        assert is_retryable("invalid_json") is False
        assert is_retryable(400) is False
        assert is_retryable(403) is False
        assert is_retryable("error: connection") is False

    def test_4xx_not_retryable(self):
        assert is_retryable(401) is False
        assert is_retryable(403) is False
        assert is_retryable(404) is False

    def test_http_4xx_string(self):
        assert is_retryable("http_400") is False
        assert is_retryable("http_403") is False


class TestCalculateDelay:
    def test_exponential_growth(self):
        d0 = calculate_delay(0, base_delay=1.0, max_delay=30.0, jitter=False)
        d1 = calculate_delay(1, base_delay=1.0, max_delay=30.0, jitter=False)
        d2 = calculate_delay(2, base_delay=1.0, max_delay=30.0, jitter=False)
        assert d0 == 1.0
        assert d1 == 2.0
        assert d2 == 4.0

    def test_max_delay_cap(self):
        d = calculate_delay(10, base_delay=1.0, max_delay=8.0, jitter=False)
        assert d == 8.0

    def test_jitter_reduces_delay(self):
        delays = [calculate_delay(2, base_delay=1.0, max_delay=30.0, jitter=True) for _ in range(50)]
        assert min(delays) < 4.0
        assert max(delays) <= 4.0


class TestProviderRetryBudget:
    def test_can_retry_within_budget(self):
        b = ProviderRetryBudget(provider="glm", max_retries=3)
        assert b.can_retry()
        b.record_attempt()
        assert b.can_retry()
        b.record_attempt()
        assert b.can_retry()

    def test_cannot_retry_exhausted(self):
        b = ProviderRetryBudget(provider="glm", max_retries=2)
        b.record_attempt()
        b.record_attempt()
        assert not b.can_retry()

    def test_window_reset(self):
        b = ProviderRetryBudget(provider="glm", max_retries=1, _window_seconds=0.01)
        b.record_attempt()
        assert not b.can_retry()
        time.sleep(0.02)
        assert b.can_retry()

    def test_reset_method(self):
        b = ProviderRetryBudget(provider="glm", max_retries=1)
        b.record_attempt()
        assert not b.can_retry()
        b.reset()
        assert b.can_retry()


class TestRetryPolicy:
    def test_defaults(self):
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.base_delay == 1.0
        assert p.max_delay == 30.0
        assert p.jitter is True

    def test_custom(self):
        p = RetryPolicy(max_retries=5, base_delay=2.0, jitter=False)
        assert p.max_retries == 5
        assert p.base_delay == 2.0
        assert p.jitter is False


# --- Metrics ---


class TestMetricsCollector:
    def test_record_request_increments(self):
        m = MetricsCollector()
        m.record_request("glm", "glm-4", "code", "ok", 150.0, 100, 50)
        stats = m.stats()
        assert stats["total_requests"] == 1
        assert stats["token_in"]["glm"] == 100
        assert stats["token_out"]["glm"] == 50

    def test_record_stream(self):
        m = MetricsCollector()
        m.record_stream("nvidia", "qwen3", "chat", "ok", 300.0, chunks=25)
        stats = m.stats()
        assert stats["total_requests"] == 1
        assert stats["token_out"]["nvidia"] == 25

    def test_multiple_providers(self):
        m = MetricsCollector()
        m.record_request("glm", "glm-4", "code", "ok", 100.0, 50, 30)
        m.record_request("nvidia", "qwen3", "chat", "ok", 200.0, 80, 40)
        m.record_request("glm", "glm-4", "code", "429", 50.0)
        stats = m.stats()
        assert stats["total_requests"] == 3
        assert "glm" in stats["providers"]
        assert "nvidia" in stats["providers"]

    def test_format_prometheus_request_counter(self):
        m = MetricsCollector()
        m.record_request("glm", "glm-4", "code", "ok", 150.0)
        output = m.format_prometheus()
        assert "llm_proxy_requests_total" in output
        assert 'provider="glm"' in output
        assert 'status="ok"' in output

    def test_format_prometheus_latency(self):
        m = MetricsCollector()
        for _ in range(10):
            m.record_request("glm", "glm-4", "code", "ok", 200.0)
        output = m.format_prometheus()
        assert "llm_proxy_latency_ms" in output
        assert 'quantile="0.5"' in output

    def test_format_prometheus_tokens(self):
        m = MetricsCollector()
        m.record_request("glm", "glm-4", "code", "ok", 100.0, 500, 200)
        output = m.format_prometheus()
        assert "llm_proxy_tokens_total" in output
        assert 'direction="input"' in output
        assert 'direction="output"' in output

    def test_format_prometheus_uptime(self):
        m = MetricsCollector()
        output = m.format_prometheus()
        assert "# Uptime:" in output

    def test_latency_window_trimming(self):
        m = MetricsCollector(latency_window=3)
        for i in range(10):
            m.record_request("glm", "glm-4", "code", "ok", float(i))
        with m._lock:
            key = _MetricKey("glm", "glm-4", "code", "ok")
            assert len(m._latencies[key]) == 3

    def test_thread_safety(self):
        m = MetricsCollector()
        errors = []

        def worker():
            try:
                for _ in range(100):
                    m.record_request("glm", "glm-4", "code", "ok", 100.0, 10, 5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.stats()["total_requests"] == 500

    def test_stats_empty(self):
        m = MetricsCollector()
        stats = m.stats()
        assert stats["total_requests"] == 0
        assert stats["providers"] == []


# --- Streaming Integration ---


class TestStreamChunk:
    def test_defaults(self):
        c = StreamChunk(delta="hello")
        assert c.delta == "hello"
        assert c.finish_reason is None
        assert c.provider == ""
        assert c.model == ""

    def test_full(self):
        c = StreamChunk(delta="world", finish_reason="stop", provider="glm", model="glm-4")
        assert c.finish_reason == "stop"
        assert c.provider == "glm"


# --- Proxy Metrics Integration ---


class TestProxyMetricsIntegration:
    def test_proxy_has_metrics_methods(self):
        from lingclaude.model.llm_proxy.proxy import LLMProxy
        from lingclaude.model.llm_proxy.config import ProxyConfig
        cfg = ProxyConfig(
            providers={}, global_rpm=60, cooldown_429=30.0,
            task_routes={}, default_provider="",
        )
        proxy = LLMProxy(cfg)
        stats = proxy.metrics_stats()
        assert "total_requests" in stats
        output = proxy.metrics_prometheus()
        assert isinstance(output, str)


# --- __init__ exports ---


class TestP2Exports:
    def test_stream_chunk_importable(self):
        from lingclaude.model.llm_proxy import StreamChunk
        assert StreamChunk is not None

    def test_retry_imports(self):
        from lingclaude.model.llm_proxy import RetryPolicy, is_retryable
        assert RetryPolicy is not None
        assert is_retryable is not None

    def test_metrics_importable(self):
        from lingclaude.model.llm_proxy import MetricsCollector
        assert MetricsCollector is not None

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class _MetricKey:
    provider: str
    model: str
    purpose: str
    status: str

    def __hash__(self) -> int:
        return hash((self.provider, self.model, self.purpose, self.status))

    def labels(self) -> str:
        return f'provider="{self.provider}",model="{self.model}",purpose="{self.purpose}",status="{self.status}"'


class MetricsCollector:
    def __init__(self, latency_window: int = 1000) -> None:
        self._lock = threading.Lock()
        self._request_counts: dict[_MetricKey, int] = defaultdict(int)
        self._token_in: dict[str, int] = defaultdict(int)
        self._token_out: dict[str, int] = defaultdict(int)
        self._latencies: dict[_MetricKey, list[float]] = defaultdict(list)
        self._latency_window = latency_window
        self._start_time = time.monotonic()

    def record_request(
        self,
        provider: str,
        model: str,
        purpose: str,
        status: str,
        latency_ms: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        key = _MetricKey(provider=provider, model=model, purpose=purpose, status=status)
        with self._lock:
            self._request_counts[key] += 1
            self._token_in[provider] += tokens_in
            self._token_out[provider] += tokens_out
            buf = self._latencies[key]
            buf.append(latency_ms)
            if len(buf) > self._latency_window:
                del buf[: len(buf) - self._latency_window]

    def record_stream(
        self,
        provider: str,
        model: str,
        purpose: str,
        status: str,
        latency_ms: float,
        chunks: int = 0,
    ) -> None:
        self.record_request(
            provider=provider, model=model, purpose=purpose,
            status=status, latency_ms=latency_ms,
            tokens_in=0, tokens_out=chunks,
        )

    def format_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []

            lines.append("# HELP llm_proxy_requests_total Total requests processed")
            lines.append("# TYPE llm_proxy_requests_total counter")
            for key, count in sorted(self._request_counts.items(), key=lambda x: x[0].labels()):
                lines.append(f"llm_proxy_requests_total{{{key.labels()}}} {count}")

            lines.append("")
            lines.append("# HELP llm_proxy_latency_ms Request latency in milliseconds")
            lines.append("# TYPE llm_proxy_latency_ms summary")
            for key in sorted(self._latencies.keys(), key=lambda k: k.labels()):
                vals = sorted(self._latencies[key])
                if not vals:
                    continue
                for q, name in [(0.5, "0.5"), (0.9, "0.9"), (0.99, "0.99")]:
                    idx = min(int(len(vals) * q), len(vals) - 1)
                    lines.append(f'llm_proxy_latency_ms{{provider="{key.provider}",quantile="{name}"}} {vals[idx]:.1f}')
                lines.append(f'llm_proxy_latency_ms_sum{{provider="{key.provider}"}} {sum(vals):.1f}')
                lines.append(f'llm_proxy_latency_ms_count{{provider="{key.provider}"}} {len(vals)}')

            lines.append("")
            lines.append("# HELP llm_proxy_tokens_total Token usage by provider and direction")
            lines.append("# TYPE llm_proxy_tokens_total counter")
            for provider in sorted(set(self._token_in) | set(self._token_out)):
                if self._token_in.get(provider, 0):
                    lines.append(f'llm_proxy_tokens_total{{provider="{provider}",direction="input"}} {self._token_in[provider]}')
                if self._token_out.get(provider, 0):
                    lines.append(f'llm_proxy_tokens_total{{provider="{provider}",direction="output"}} {self._token_out[provider]}')

            lines.append("")
            lines.append(f"# Uptime: {time.monotonic() - self._start_time:.0f}s")
            return "\n".join(lines) + "\n"

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_requests": sum(self._request_counts.values()),
                "providers": sorted(set(k.provider for k in self._request_counts)),
                "token_in": dict(self._token_in),
                "token_out": dict(self._token_out),
            }

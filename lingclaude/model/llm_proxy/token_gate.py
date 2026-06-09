from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class TokenBudget:
    provider: str
    key_id: str
    window_seconds: int = 18000
    total_budget: int = 500000
    used: int = 0
    reset_at: float = 0.0

    @property
    def remaining_pct(self) -> float:
        if self.total_budget <= 0:
            return 1.0
        return 1.0 - (self.used / self.total_budget)

    @property
    def should_degrade(self) -> bool:
        return self.remaining_pct < 0.05

    def record(self, tokens: int) -> None:
        now = time.monotonic()
        if now >= self.reset_at:
            self.used = 0
            self.reset_at = now + self.window_seconds
        self.used += tokens

    def reset(self) -> None:
        self.used = 0
        self.reset_at = time.monotonic() + self.window_seconds


class TokenGate:
    def __init__(self, high_watermark: float = 0.95) -> None:
        self._budgets: dict[str, TokenBudget] = {}
        self._high_watermark = high_watermark
        self._lock = threading.Lock()

    def register(self, budget: TokenBudget) -> None:
        with self._lock:
            self._budgets[budget.key_id] = budget

    def record_usage(self, key_id: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            budget = self._budgets.get(key_id)
            if budget:
                budget.record(input_tokens + output_tokens)

    def check_available(self, key_id: str) -> bool:
        with self._lock:
            budget = self._budgets.get(key_id)
            if not budget:
                return True
            if time.monotonic() >= budget.reset_at:
                budget.reset()
            return budget.remaining_pct >= (1.0 - self._high_watermark)

    def get_degrade_candidates(self, exclude_key: str) -> list[str]:
        with self._lock:
            degraded = []
            for kid, budget in self._budgets.items():
                if kid == exclude_key:
                    continue
                if not budget.should_degrade:
                    degraded.append(kid)
            return degraded

    def stats(self) -> dict[str, dict]:
        with self._lock:
            return {
                kid: {
                    "provider": b.provider,
                    "used": b.used,
                    "total": b.total_budget,
                    "remaining_pct": round(b.remaining_pct, 4),
                    "should_degrade": b.should_degrade,
                }
                for kid, b in self._budgets.items()
            }

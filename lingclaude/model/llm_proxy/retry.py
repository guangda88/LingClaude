from __future__ import annotations

import random
import time
from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True
    retryable_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    retryable_status_prefixes: tuple[str, ...] = ("http_5", "timeout", "error:")


def is_retryable(status: str | int) -> bool:
    if isinstance(status, int):
        if status in (401, 403):
            return False
        return status in (429, 500, 502, 503, 504)
    if status == "429":
        return True
    if status == "timeout":
        return True
    if status == "empty_response":
        return True
    if status.startswith("http_"):
        try:
            code = int(status[5:])
            if code in (401, 403):
                return False
            return 500 <= code < 600
        except ValueError:
            return False
    return False


def calculate_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    delay = min(base_delay * (2 ** attempt), max_delay)
    if jitter:
        delay = delay * random.uniform(0.5, 1.0)
    return delay


@dataclass
class ProviderRetryBudget:
    provider: str
    max_retries: int = 3
    attempts: int = 0
    _window_start: float = field(default_factory=time.monotonic)
    _window_seconds: float = 60.0

    def can_retry(self) -> bool:
        if time.monotonic() - self._window_start > self._window_seconds:
            return True
        return self.attempts < self.max_retries

    def record_attempt(self) -> None:
        now = time.monotonic()
        if now - self._window_start > self._window_seconds:
            self.attempts = 0
            self._window_start = now
        self.attempts += 1

    def reset(self) -> None:
        self.attempts = 0
        self._window_start = time.monotonic()

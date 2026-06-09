from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field


@dataclass
class LeakyBucket:
    max_tokens: float
    refill_rate: float
    _tokens: float = field(default=0.0, init=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def acquire(self, tokens: float = 1.0, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                deficit = tokens - self._tokens
                wait = deficit / self.refill_rate
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(wait, remaining, 0.5))

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now


@dataclass
class ProviderSlot:
    name: str
    bucket: LeakyBucket
    consecutive_errors: int = 0
    last_error_time: float = 0.0
    cooldown_until: float = 0.0
    total_requests: int = 0
    total_errors: int = 0

    @property
    def is_available(self) -> bool:
        return time.monotonic() >= self.cooldown_until


class SmoothRotator:
    def __init__(
        self,
        providers: dict[str, dict],
        requests_per_minute: float = 10.0,
        burst_capacity: float = 5.0,
    ) -> None:
        self._slots: list[ProviderSlot] = []
        self._index = 0
        self._lock = threading.Lock()
        for name, _cfg in providers.items():
            bucket = LeakyBucket(
                max_tokens=burst_capacity,
                refill_rate=requests_per_minute / 60.0,
            )
            self._slots.append(ProviderSlot(name=name, bucket=bucket))

    def next_provider(self) -> ProviderSlot | None:
        with self._lock:
            tried = 0
            while tried < len(self._slots):
                slot = self._slots[self._index % len(self._slots)]
                self._index += 1
                tried += 1
                if slot.is_available:
                    slot.bucket.acquire(timeout=5.0)
                    slot.total_requests += 1
                    return slot
            return None

    def record_success(self, name: str) -> None:
        for s in self._slots:
            if s.name == name:
                s.consecutive_errors = 0
                return

    def record_error(self, name: str, cooldown: float = 30.0) -> None:
        for s in self._slots:
            if s.name == name:
                s.consecutive_errors += 1
                s.total_errors += 1
                s.last_error_time = time.monotonic()
                if s.consecutive_errors >= 3:
                    jitter = random.uniform(0, cooldown * 0.3)
                    s.cooldown_until = time.monotonic() + cooldown + jitter
                    s.consecutive_errors = 0
                return

    def stats(self) -> dict[str, dict]:
        result = {}
        for s in self._slots:
            result[s.name] = {
                "requests": s.total_requests,
                "errors": s.total_errors,
                "available": s.is_available,
                "consecutive_errors": s.consecutive_errors,
            }
        return result


def jittered_backoff(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    exp = min(base * (2 ** attempt), cap)
    jitter = random.uniform(0, exp * 0.5)
    return exp + jitter

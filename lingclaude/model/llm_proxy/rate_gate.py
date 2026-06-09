from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from lingclaude.core.rate_limiter import LeakyBucket, ProviderSlot


@dataclass
class RatePolicy:
    global_rpm: float = 60.0
    provider_rpm: dict[str, float] = field(default_factory=dict)
    cooldown_429: float = 30.0
    cooldown_consecutive: int = 3


class RateGate:
    def __init__(self, policy: RatePolicy) -> None:
        self._policy = policy
        self._slots: dict[str, ProviderSlot] = {}
        self._global_bucket = LeakyBucket(
            max_tokens=policy.global_rpm / 6.0,
            refill_rate=policy.global_rpm / 60.0,
        )
        self._lock = threading.Lock()
        self._round_robin: dict[str, int] = {}

    def register_provider(self, name: str, rpm: float, burst: int) -> None:
        bucket = LeakyBucket(max_tokens=float(burst), refill_rate=rpm / 60.0)
        self._slots[name] = ProviderSlot(name=name, bucket=bucket)

    def acquire(self, provider: str, timeout: float = 10.0) -> bool:
        slot = self._slots.get(provider)
        if not slot:
            return False
        if not slot.is_available:
            return False
        if not slot.bucket.acquire(timeout=timeout):
            return False
        if not self._global_bucket.acquire(timeout=timeout):
            slot.bucket._tokens += 1.0
            return False
        return True

    def pick_from_route(self, route_key: str, candidates: list[str]) -> str | None:
        with self._lock:
            idx = self._round_robin.get(route_key, 0)

        for i in range(len(candidates)):
            pos = (idx + i) % len(candidates)
            name = candidates[pos]
            if self.acquire(name, timeout=2.0):
                with self._lock:
                    self._round_robin[route_key] = (pos + 1) % len(candidates)
                slot = self._slots.get(name)
                if slot:
                    slot.total_requests += 1
                return name
        return None

    def record_success(self, provider: str) -> None:
        slot = self._slots.get(provider)
        if slot:
            slot.consecutive_errors = 0

    def record_429(self, provider: str) -> None:
        slot = self._slots.get(provider)
        if slot:
            slot.consecutive_errors += 1
            slot.total_errors += 1
            slot.last_error_time = time.monotonic()
            if slot.consecutive_errors >= self._policy.cooldown_consecutive:
                slot.cooldown_until = time.monotonic() + self._policy.cooldown_429
                slot.consecutive_errors = 0

    def record_error(self, provider: str) -> None:
        slot = self._slots.get(provider)
        if slot:
            slot.total_errors += 1

    def health(self) -> dict[str, Any]:
        result = {}
        for name, slot in self._slots.items():
            result[name] = {
                "available": slot.is_available,
                "requests": slot.total_requests,
                "errors": slot.total_errors,
                "consecutive_errors": slot.consecutive_errors,
            }
        return result


from typing import Any

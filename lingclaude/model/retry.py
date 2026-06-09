from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

GLM_FALLBACK_MODELS = [
    "glm-5.1",
    "glm-5-turbo",
    "glm-5",
    "glm-4.7",
    "glm-4.7-flash",
    "glm-4.6",
    "glm-4.6v",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.5v",
]

GLM_MODELS = GLM_FALLBACK_MODELS

DEFAULT_PRIMARY_RETRY_LIMIT = 3
DEFAULT_DEGRADED_CALL_THRESHOLD = 10
DEFAULT_DEGRADED_TIME_THRESHOLD = 60.0
DEFAULT_BACKOFF_BASE = 5.0
DEFAULT_BACKOFF_MAX = 30.0
DEFAULT_MAX_TOTAL_RETRIES = 12
DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
DEFAULT_CIRCUIT_COOLDOWN = 60.0
DEFAULT_RPM_WINDOW = 60.0


@dataclass
class RetrySnapshot:
    current_model: str
    model_index: int
    primary_retry_count: int
    degraded_call_count: int
    degraded_since: float | None
    is_degraded: bool
    circuit_open: bool
    rpm_count: int


@dataclass
class GlmRetryPolicy:
    models: list[str] = field(default_factory=lambda: GLM_MODELS.copy())
    primary_retry_limit: int = DEFAULT_PRIMARY_RETRY_LIMIT
    degraded_call_threshold: int = DEFAULT_DEGRADED_CALL_THRESHOLD
    degraded_time_threshold: float = DEFAULT_DEGRADED_TIME_THRESHOLD
    backoff_base: float = DEFAULT_BACKOFF_BASE
    backoff_max: float = DEFAULT_BACKOFF_MAX
    circuit_failure_threshold: int = DEFAULT_CIRCUIT_FAILURE_THRESHOLD
    circuit_cooldown: float = DEFAULT_CIRCUIT_COOLDOWN
    rpm_window: float = DEFAULT_RPM_WINDOW

    _model_index: int = field(default=0, init=False)
    _primary_retry_count: int = field(default=0, init=False)
    _primary_last_retry: float = field(default=0.0, init=False)
    _degraded_call_count: int = field(default=0, init=False)
    _degraded_since: float = field(default=0.0, init=False)
    _circuit_consecutive_429: int = field(default=0, init=False)
    _circuit_opened_at: float = field(default=0.0, init=False)
    _rpm_timestamps: list[float] = field(default_factory=list, init=False)

    @property
    def current_model(self) -> str:
        return self.models[self._model_index]

    @property
    def is_primary(self) -> bool:
        return self._model_index == 0

    @property
    def is_degraded(self) -> bool:
        return self._model_index > 0

    def get_next_model(self) -> str | None:
        next_idx = self._model_index + 1
        if next_idx < len(self.models):
            return self.models[next_idx]
        return None

    def record_success(self) -> None:
        self._circuit_consecutive_429 = 0
        if self.is_primary:
            if self._primary_retry_count > 0:
                logger.info("主模型 %s 恢复成功，重置重试计数器", self.current_model)
            self._primary_retry_count = 0
        else:
            logger.info("降级模型 %s 调用成功", self.current_model)

    def record_failure(self, is_rate_limit: bool = False) -> None:
        if is_rate_limit:
            self._circuit_consecutive_429 += 1
            if self._circuit_consecutive_429 >= self.circuit_failure_threshold:
                self._circuit_opened_at = time.time()
                logger.warning(
                    "熔断器开启：连续 %d 次 429，冷却 %.0fs",
                    self._circuit_consecutive_429,
                    self.circuit_cooldown,
                )
        else:
            self._circuit_consecutive_429 = 0
        if self.is_primary:
            self._primary_retry_count += 1
            self._primary_last_retry = time.time()
            logger.warning(
                "主模型 %s 重试失败 (%d/%d)",
                self.current_model,
                self._primary_retry_count,
                self.primary_retry_limit,
            )
        else:
            self._degraded_call_count += 1
            if self._degraded_since == 0.0:
                self._degraded_since = time.time()
            if self.degraded_call_threshold > 0 and self._degraded_call_count % self.degraded_call_threshold == 0:
                logger.info(
                    "降级模型已调用 %d 次，准备探测主模型",
                    self._degraded_call_count,
                )

    def should_degrade(self) -> bool:
        return self.is_primary and self._primary_retry_count >= self.primary_retry_limit

    def degrade(self) -> str | None:
        next_model = self.get_next_model()
        if next_model:
            old = self.current_model
            self._model_index += 1
            self._degraded_since = time.time()
            logger.info("模型降级: %s → %s", old, self.current_model)
            return self.current_model
        return None

    def should_retry_primary(self) -> bool:
        if self.is_primary:
            return False
        if self._degraded_call_count >= self.degraded_call_threshold:
            return True
        if self._degraded_since > 0 and (time.time() - self._degraded_since) > self.degraded_time_threshold:
            return True
        return False

    def reset_to_primary(self) -> str:
        old = self.current_model
        self._model_index = 0
        self._primary_retry_count = 0
        self._primary_last_retry = time.time()
        self._degraded_call_count = 0
        self._degraded_since = 0.0
        logger.info("切回主模型: %s → %s", old, self.current_model)
        return self.current_model

    @property
    def circuit_open(self) -> bool:
        if self._circuit_consecutive_429 < self.circuit_failure_threshold:
            return False
        if self._circuit_opened_at == 0.0:
            return False
        elapsed = time.time() - self._circuit_opened_at
        if elapsed >= self.circuit_cooldown:
            logger.info("熔断器冷却完成，半开状态，允许重试")
            return False
        return True

    def get_backoff(self, attempt: int) -> float:
        return min(self.backoff_base * (2 ** attempt), self.backoff_max)

    def record_rpm(self) -> int:
        now = time.time()
        cutoff = now - self.rpm_window
        self._rpm_timestamps = [t for t in self._rpm_timestamps if t > cutoff]
        self._rpm_timestamps.append(now)
        return len(self._rpm_timestamps)

    def configure_primary(self, model_name: str) -> None:
        if not model_name or not any(m in model_name for m in ("glm-", "GLM-")):
            return
        if self.models and self.models[0] == model_name:
            return
        if model_name in self.models:
            self.models.remove(model_name)
        self.models.insert(0, model_name)
        self._model_index = 0
        self._primary_retry_count = 0
        logger.debug("重试策略主模型设置为: %s", model_name)

    def get_snapshot(self) -> RetrySnapshot:
        cutoff = time.time() - self.rpm_window
        rpm_count = sum(1 for t in self._rpm_timestamps if t > cutoff)
        return RetrySnapshot(
            current_model=self.current_model,
            model_index=self._model_index,
            primary_retry_count=self._primary_retry_count,
            degraded_call_count=self._degraded_call_count,
            degraded_since=self._degraded_since if self.is_degraded else None,
            is_degraded=self.is_degraded,
            circuit_open=self.circuit_open,
            rpm_count=rpm_count,
        )

    def reset(self) -> None:
        self._model_index = 0
        self._primary_retry_count = 0
        self._primary_last_retry = 0.0
        self._degraded_call_count = 0
        self._degraded_since = 0.0
        self._circuit_consecutive_429 = 0
        self._circuit_opened_at = 0.0


def is_rate_limit_error(error_text: str) -> bool:
    markers = [
        "429",
        "rate_limit",
        "rate limit",
        "too many requests",
        "模型访问量过大",
        "服务繁忙",
        "模型正在忙",
        "requests per minute",
        "rpm limit",
    ]
    lower = error_text.lower()
    return any(m in lower for m in markers)


def handle_429(policy: GlmRetryPolicy, attempt: int) -> str | None:
    policy.record_failure(is_rate_limit=True)
    if policy.circuit_open:
        return None
    if policy.is_degraded and policy.should_retry_primary():
        policy.reset_to_primary()
        return policy.current_model
    if policy.is_primary and policy.should_degrade():
        return policy.degrade()
    return policy.current_model

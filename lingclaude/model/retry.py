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
    primary_model: str = field(default="", init=False)
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

    def record_success(self, actual_model: str | None = None) -> None:
        self._circuit_consecutive_429 = 0
        if self.is_primary:
            if self._primary_retry_count > 0:
                logger.info("主模型 %s 恢复成功，重置重试计数器", self.current_model)
            self._primary_retry_count = 0
        else:
            # P0-A: 日志使用真实请求模型名（actual_model），而非 policy 内部
            # 状态名（current_model）。对非 glm 主模型，_config_for_model 不替换
            # 请求模型，current_model 只是降级列表的索引名（如 glm-5.1），
            # 用它打日志会误导："降级模型 glm-5.1 调用成功" 而真实请求是
            # deepseek-v4-flash。
            display = actual_model or self.current_model
            logger.info("降级模型 %s 调用成功", display)
            # P0-B: 记录降级状态下的成功调用（原实现只在 record_failure 递增，
            # 导致 should_retry_primary 的 call_count 阈值永远无法由成功路径
            # 触发——降级后若一直成功，状态卡死只能等 60s 时间阈值）。
            self._degraded_call_count += 1
            if self._degraded_since == 0.0:
                self._degraded_since = time.time()
            # P0-B: 无 429 的连续成功也允许自动切回主模型。
            # 原实现只在 429 分支里触发 reset_to_primary，导致降级状态
            # 在没有新限流的情况下永不恢复（真实请求可能一直是主模型，
            # 但日志/policy 状态却卡在降级模型名上）。
            if self.should_retry_primary():
                logger.info("降级模型连续成功，自动切回主模型")
                self.reset_to_primary()

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

    def _is_primary_glm(self) -> bool:
        """P0-C: 主模型是否属于 GLM 家族。

        降级列表 GLM_FALLBACK_MODELS 是 GLM 专用（retry.py:10-21）。
        对非 GLM 主模型（如 deepseek-v4-flash），降级到列表内的 glm-5.1
        是『假降级』——_config_for_model 的 is_glm 守卫（openai_provider.py:329）
        会拦截替换，真实请求仍是主模型，但 policy 状态/日志却显示降级名。
        此判定用于 degrade() 源头拦截：非 GLM 主模型不允许降级。
        """
        base = self.primary_model or self.models[0]
        return any(m in base for m in ("glm-", "GLM-"))

    def degrade(self) -> str | None:
        # P0-C: 非 GLM 主模型禁止假降级（无同平台 fallback，降级列表是 GLM 专用）。
        if self._is_primary_glm() is False and self.is_primary:
            logger.warning(
                "主模型 %s 非 GLM 家族，无同平台 fallback，禁止降级",
                self.primary_model or self.models[0],
            )
            return None
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
        # 2026-09-11: 守卫放宽 — 非 glm 系列也允许 configure_primary，
        # 修复 /model deepseek-v4-flash 后 retry policy 不更新的问题
        # （原守卫 any("glm-" in model_name) 导致只有 glm 模型能置主）
        if not model_name:
            return
        self.primary_model = model_name
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


def is_hard_quota_error(error_text: str) -> bool:
    """硬性配额耗尽（GLM 1308 5 小时限额等）：重试/退避无意义，必须换 provider。

    2026-09-16 事故：GLM 套餐 5h 限额触顶（code 1308，含重置时间戳），
    is_rate_limit_error 误判为普通限流 → 3 轮退避重试全空烧 60s + 降级到
    glm-5.1 同账号仍 429 → 连续成功阈值触发"自动切回主模型"再次撞墙。
    硬配额特征：错误体含配额耗尽语义 + 明确重置时间。命中即让上层跳过
    重试循环直接失败/切 provider。
    """
    lower = error_text.lower()
    hard_markers = [
        "1308",  # GLM: 已达到 5 小时的使用上限
        "使用上限",
        "usage limit",
        "quota exceeded",
        "billing_hard_limit",
        "套餐限额",
        "5 小时的使用上限",
    ]
    if any(m in lower for m in hard_markers):
        return True
    # "已达到" + "上限" + "重置" 组合形态（防 code 变化）
    return ("已达到" in error_text and "上限" in error_text and "重置" in error_text)


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

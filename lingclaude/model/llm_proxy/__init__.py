"""LLM Proxy — 统一 LLM 调用代理，四边界架构 (rate/token/purpose/data)

[B3 灵元1.0 P1 定性 2026-09-09] 未接线子系统: 主干(api/cli/engine)零调用,
仅自测试引用(test_llm_proxy_p2/test_r8_stream_retry)。与 model/retry.py 构成
"双 retry"表象, 实为在用+未接线各一, 不存在运行时双轨竞争。
处置: 不并入主干 retry(P1), P2 manifest 定夺接线 or 归档。"""

from lingclaude.model.llm_proxy.config import ProxyConfig
from lingclaude.model.llm_proxy.rate_gate import RateGate
from lingclaude.model.llm_proxy.purpose_router import PurposeRouter
from lingclaude.model.llm_proxy.provider_pool import ProviderPool, StreamChunk
from lingclaude.model.llm_proxy.token_gate import TokenGate, TokenBudget
from lingclaude.model.llm_proxy.data_filter import DataFilter, AuditEntry
from lingclaude.model.llm_proxy.retry import RetryPolicy, ProviderRetryBudget, is_retryable, calculate_delay
from lingclaude.model.llm_proxy.metrics import MetricsCollector

__all__ = [
    "ProxyConfig", "RateGate", "PurposeRouter", "ProviderPool", "StreamChunk",
    "TokenGate", "TokenBudget", "DataFilter", "AuditEntry",
    "RetryPolicy", "ProviderRetryBudget", "is_retryable", "calculate_delay",
    "MetricsCollector",
]

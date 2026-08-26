"""灵督 LLM 复核插片 — 语义层二次确认，降低规则引擎误报

双引擎: 规则引擎 L0 (review_rules.yaml 正则) 命中后，L1 用 LLM 语义复核
判定 true_positive / false_positive / uncertain，仅剔除明确误报。

降级保证 (fail-open): LLM 不可用 / 超时 / 解析失败 → 保留全部 issues，
灵督"审计永不失败"的既有承诺不变。

复用: lingclaude.model.factory.create_provider + ModelProvider.complete，
零第三方依赖。设计详见 docs/lacp/LINGGIT_LLM_REVIEW_PLUGIN_20260802.md。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from lingclaude.model.factory import create_provider
from lingclaude.model.types import MessageRole, ModelConfig, ModelMessage, ModelProvider

logger = logging.getLogger(__name__)

_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
_VALID_VERDICTS = {"true_positive", "false_positive", "uncertain"}
# verdict 缓存有效期（秒）: 同一 (file, line, rule) 命中 24h 内复用，避免重复调 LLM
_CACHE_TTL_S = 24 * 3600

# 复核系统提示：语义判定规则命中的真伪。宁可保留(uncertain)，不可误杀。
_REVIEW_SYSTEM_PROMPT = """你是代码安全审计复核员。以下是从源码扫描命中的规则告警，
请判断每条是真实问题还是误报（测试桩、示例字符串、占位符、注释、文档示例、
环境变量引用、#[cfg(test)] 内代码）。

规则命中格式: {file}:{line} [{severity}] {rule} | 命中内容: {snippet}

对每条输出 JSON 数组: [{"key": "<file>:<line>", "verdict": "true_positive"|"false_positive"|"uncertain", "reason": "<一句话理由>"}]

要求:
- 命中内容为 sk-test/sk-ant-test/test/valid/example 等明显测试值 → false_positive
- 命中行以 # / // / /// 开头（注释）→ false_positive
- 内容引用 $ENV_VAR / <placeholder> → false_positive
- 无法确定 → uncertain（宁可保留，不可误杀）
只输出 JSON 数组，不要任何其他文字。"""


def _sev_rank(s: Any) -> int:
    return _SEV_RANK.get(str(s or "").lower(), 0)


def _parse_verdicts(text: str) -> dict[str, str]:
    """从 LLM 输出提取 {key: verdict}。容忍 JSON 前后有杂讯/格式漂移。"""
    arr: Any = None
    for a, b in (("[", "]"), ("{", "}")):
        try:
            start = text.index(a)
            end = text.rindex(b) + 1
            parsed = json.loads(text[start:end])
            if isinstance(parsed, list):
                arr = parsed
                break
            if isinstance(parsed, dict) and "key" in parsed:
                arr = [parsed]
                break
        except (ValueError, json.JSONDecodeError):
            continue
    out: dict[str, str] = {}
    if not isinstance(arr, list):
        return out
    for item in arr:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        verdict = str(item.get("verdict", "uncertain")).strip()
        if key and verdict in _VALID_VERDICTS:
            out[key] = verdict
    return out


class LLMReviewer:
    """LLM 语义复核插片：规则命中后二次确认，剔除误报。fail-open 设计。"""

    def __init__(
        self,
        config: dict | None = None,
        provider: ModelProvider | None = None,
    ):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.threshold = cfg.get("threshold", "high")  # 仅复核 high/critical 及以上
        self.batch_size = int(cfg.get("batch_size", 8))
        self.timeout_s = float(cfg.get("timeout_s", 20.0))
        # 通道内重试（限流/5xx 退避），失败后轮换下一通道
        self.channel_retries = int(cfg.get("channel_retries", 2))
        self._provider = provider  # 测试可注入 mock
        self._provider_checked = False
        self._built_providers: list[ModelProvider] = []
        # 显式注入通道配置（优先于 config.yaml）：{provider, model, api_key, base_url}
        self._override = cfg.get("model_config") or {}
        # 备用通道列表（多 provider 轮换）：[{provider, model, api_key, base_url}]
        # 主通道失败（限流/5xx）时按序轮换；api_key 留空则从 api_key_env 环境变量读取
        self._channels = [
            ch for ch in (cfg.get("channels") or [])
            if isinstance(ch, dict) and ch.get("model")
        ]
        # verdict 缓存: key=(file, line, description) -> (verdict, ts)
        self._cache: dict[tuple[str, int, str], tuple[str, float]] = {}
        # 允许外部注入缓存（跨进程持久化时由调用方加载/回写）
        self.cache = cfg.get("cache") or {}

    # ── 缓存键与读写 ──
    @staticmethod
    def _cache_key(issue: dict) -> tuple[str, int, str]:
        return (
            str(issue.get("file", "?")),
            int(issue.get("line", 0)),
            str(issue.get("description", ""))[:80],
        )

    def _cache_get(self, issue: dict) -> str | None:
        key = self._cache_key(issue)
        hit = self._cache.get(key) or self.cache.get(key)
        if not hit:
            return None
        verdict, ts = hit
        if time.time() - ts > _CACHE_TTL_S:
            return None  # 过期
        return verdict

    def _cache_put(self, issue: dict, verdict: str) -> None:
        key = self._cache_key(issue)
        val = (verdict, time.time())
        self._cache[key] = val
        self.cache[key] = val  # 对外可见，调用方可持久化

    # ── provider 懒加载（复用灵族 model 栈）──
    def _get_provider(self) -> ModelProvider | None:
        """返回主通道 provider（向后兼容单通道用法）。"""
        providers = self._get_providers()
        return providers[0] if providers else None

    def _get_providers(self) -> list[ModelProvider]:
        """构建可用通道 provider 列表（主通道 + 备用通道轮换）。

        顺序：显式 model_config → config.yaml → 备用 channels（按序）。
        每个通道 api_key 优先取配置值，留空时从 api_key_env 环境变量读取。
        """
        if self._provider is not None:
            return [self._provider]
        if self._provider_checked:
            return self._built_providers
        self._provider_checked = True
        built: list[ModelProvider] = []
        try:
            channel_specs: list[dict] = []
            if self._override:
                channel_specs.append(self._override)
            else:
                from lingclaude.core.config import load_config
                mc = load_config().model
                channel_specs.append({
                    "provider": mc.provider,
                    "model": mc.model,
                    "api_key": mc.api_key,
                    "base_url": mc.base_url,
                })
            channel_specs.extend(self._channels)
            for spec in channel_specs:
                api_key = spec.get("api_key") or ""
                if not api_key and spec.get("api_key_env"):
                    import os
                    api_key = os.environ.get(spec["api_key_env"], "")
                    if not api_key:
                        # 兜底: 从项目 .env 加载（gitignore 忽略，不提交凭据）
                        # 锚定项目根（linggit/ 的上层目录），避免依赖调用方 cwd
                        try:
                            from dotenv import dotenv_values
                            from pathlib import Path
                            env_file = Path(__file__).resolve().parents[1] / ".env"
                            if env_file.exists():
                                api_key = (dotenv_values(str(env_file)) or {}).get(spec["api_key_env"], "")
                        except ImportError:
                            pass
                if not api_key:
                    logger.warning("LLM reviewer channel %s: api_key 缺失，跳过", spec.get("model"))
                    continue
                cfg = ModelConfig(
                    model=spec.get("model", "deepseek-v4-flash"),
                    api_key=api_key,
                    base_url=spec.get("base_url"),
                    max_tokens=min(int(spec.get("max_tokens", 4096)), 512),
                    temperature=min(float(spec.get("temperature", 0.2)), 0.3),
                )
                provider_name = spec.get("provider", "openai")
                res = create_provider(cfg, provider_name=provider_name)
                if res.is_ok and res.data is not None:
                    built.append(res.data)
                else:
                    logger.warning("LLM reviewer channel %s 构建失败: %s", spec.get("model"), res.error)
        except Exception as e:  # 降级: provider 构建失败 → fail-open
            logger.warning("LLM reviewer provider init failed: %s", e)
        self._built_providers = built
        return built

    # ── 主入口: 过滤误报 ──
    def review(self, issues: list[dict]) -> list[dict]:
        """对 issue 列表做 LLM 复核，剔除明确 false_positive。

        - 未启用 / 无命中 / provider 不可用 → 原样返回 (fail-open)
        - 仅复核达到阈值的命中（成本控制）
        - 未命中 key 或解析失败 → uncertain（保留）
        """
        if not self.enabled or not issues:
            return issues
        providers = self._get_providers()
        if not providers:
            return issues  # fail-open

        candidates = [
            i for i in issues if _sev_rank(i.get("severity")) >= _sev_rank(self.threshold)
        ]
        if not candidates:
            return issues

        verdicts: dict[str, str] = {}
        # 先查缓存: 未命中的才送 LLM（24h 复用，省 token）
        to_ask = []
        for i in candidates:
            key = f"{i.get('file', '?')}:{i.get('line', '?')}"
            cached = self._cache_get(i)
            if cached is not None:
                verdicts[key] = cached
            else:
                to_ask.append(i)

        for start in range(0, len(to_ask), self.batch_size):
            batch = to_ask[start : start + self.batch_size]
            try:
                # 多通道轮换：主通道失败 → 按序切备用通道；全部失败 → fail-open
                batch_v: dict[str, str] = {}
                for provider in providers:
                    batch_v = self._ask_batch(provider, batch)
                    if batch_v:
                        break
                verdicts.update(batch_v)
                # 回写缓存
                for i in batch:
                    key = f"{i.get('file', '?')}:{i.get('line', '?')}"
                    v = batch_v.get(key, "uncertain")
                    self._cache_put(i, v)
            except Exception as e:
                logger.warning("LLM review batch failed (%d issues): %s", len(batch), e)

        for i in issues:
            key = f"{i.get('file', '?')}:{i.get('line', '?')}"
            i["llm_verdict"] = verdicts.get(key, "uncertain")

        return [i for i in issues if i.get("llm_verdict") != "false_positive"]

    # ── 单批 LLM 询问 ──
    def _ask_batch(self, provider: ModelProvider, issues: list[dict]) -> dict[str, str]:
        hits = "\n".join(
            f"{i.get('file', '?')}:{i.get('line', '?')} [{i.get('severity', '?')}] "
            f"{i.get('description', '?')} | 命中内容: {str(i.get('content', ''))[:120]}"
            for i in issues
        )
        messages = (
            ModelMessage(role=MessageRole.SYSTEM, content=_REVIEW_SYSTEM_PROMPT),
            ModelMessage(role=MessageRole.USER, content=hits),
        )
        # 通道内重试：限流/5xx 指数退避（channel_retries 次），仍失败返回 {} 触发轮换
        import time as _time

        last_error = None
        for attempt in range(1 + self.channel_retries):
            if attempt:
                backoff = 2.0 * (2 ** (attempt - 1))
                logger.warning(
                    "LLM review 通道 %s 第 %d 次重试（退避 %.1fs）: %s",
                    getattr(provider, "_config", None).model if getattr(provider, "_config", None) else "?",
                    attempt, backoff, last_error,
                )
                _time.sleep(backoff)
            try:
                res = provider.complete(messages)
            except Exception as e:  # noqa: BLE001 — 通道异常记入重试
                last_error = str(e)
                continue
            if not res.is_ok or res.data is None:
                last_error = str(res.error)
                continue
            return _parse_verdicts(res.data.content)
        logger.warning("LLM review 通道全部重试失败（最后错误: %s），切下一通道或 fail-open", last_error)
        return {}

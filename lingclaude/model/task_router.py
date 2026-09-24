"""Task Router — Multi-provider task routing based on config.json

Reads routing.providers and routing.task_routes from /home/ai/lingcode/config.json.
Maps IntelligentRouter.TaskType to task_routes keys, iterates ordered model lists,
returns a complete ModelConfig with correct api_key, base_url, and model for the
routed provider. Falls back to default provider ("cheap") if no route matches.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lingclaude.model.openrouter_oauth import ensure_env_key
from lingclaude.model.types import ModelConfig
from lingclaude.model.intelligent_router import TaskType
from lingclaude.core.rate_limiter import LeakyBucket, ProviderSlot
from lingclaude.model.provider_probe import ProviderProbe, ProbeResult, probe_disabled_by_env

logger = logging.getLogger(__name__)

# LINGCODE_CONFIG 环境变量可覆盖 — 跨仓配置路径不再绑死本机布局 (2026-09-11 审计修复)
CONFIG_PATH = Path(os.environ.get("LINGCODE_CONFIG", "/home/ai/lingcode/config.json"))

# P1-4 (2026-09-19, 幻觉调研第二批): flash 级模型禁入决策位 —— 治 H1/H2 共识：
# flash/mini/lite 级小模型幻觉率显著高于旗舰级，而决策位（路由清单首位）的
# 幻觉会被下游全量消费。规则：模型名含 flash/mini/lite/nano 等轻量标记时，
# coding/reasoning/thinking 类「决策路由」自动跳过其清单首位候选，顺延下一候选。
# 可用环境变量 LINGCLAUDE_FLASH_GATE_DISABLE=1 关闭（回退纯配置序）。
# 注：只影响路由层决策位选择，不影响显式 /model 指定（用户意志优先）。
_FLASH_GATE_DISABLE_ENV = "LINGCLAUDE_FLASH_GATE_DISABLE"
_FLASH_MODEL_RE = re.compile(
    # flash/mini/lite 系：轻量标记；minimax-m2.7/m3：调研 P1 明确「决策位降到兜底位」
    r"flash|mini\b|minimax|lite|nano|turbo-lite|instinct|air\b|-small\b|_small\b",
    re.IGNORECASE,
)
# 决策路由 key：这些 route 的首位候选承担主要产出，幻觉代价最高
_DECISION_ROUTE_KEYS = frozenset({
    "coding", "chinese_reasoning", "english_general", "thinking", "long_context",
})

# P2-2 (灵元): task_type → route 映射外置 policies/task_routing.yaml，走 PolicyLoader 热更。
# 消费方式：resolve() 每次路由前实时调用 _load_task_type_to_route()（P6 收敛口径），
# 改 YAML → 下个请求生效，进程不重启。读失败回退内置默认（graceful degrade）。
# P10: 曾存在模块级 TASK_TYPE_TO_ROUTE 死快照（生产零引用，唯一消费者是测试），已删除。
def _load_task_type_to_route() -> dict[TaskType, str]:
    """从策略文件加载 task_type → route 映射；读失败回退内置默认（graceful degrade）。"""
    from lingclaude.core.policy_loader import get as policy_get

    builtin: dict[TaskType, str] = {
        TaskType.CODE_GENERATION: "coding",
        TaskType.CODE_ANALYSIS: "coding",
        TaskType.CODE_REFACTORING: "coding",
        TaskType.DEBUGGING: "coding",
        TaskType.TESTING: "coding",
        TaskType.ANALYSIS: "chinese_reasoning",
        TaskType.OPTIMIZATION: "chinese_reasoning",
        TaskType.DOCUMENTATION: "english_general",
        TaskType.SEARCH: "fast_response",
        TaskType.OTHER: "fast_response",
    }
    data = policy_get("task_routing")
    mapping = data.get("task_type_to_route")
    if isinstance(mapping, dict) and mapping:
        result: dict[TaskType, str] = {}
        for key, value in mapping.items():
            try:
                result[TaskType[key.upper()]] = str(value)
            except (KeyError, AttributeError):
                logger.warning("TaskRouter: 未知 task_type %r，跳过", key)
        if result:
            return result
    return builtin


@dataclass
class _ProviderInfo:
    type: str
    api_key: str
    base_url: str
    default_model: str
    models: list[str]
    rpm: float
    burst: int


def _is_local_base(base_url: str) -> bool:
    """本地服务(localhost/127.0.0.1/内网 stub)不需要 api_key 也能通。

    F12b:路由跳过判断用。实现收敛到 ModelConfig.is_local_base(F12d),
    保证路由层与 provider 层语义单一来源。
    """
    from lingclaude.model.types import ModelConfig
    return ModelConfig(base_url=base_url).is_local_base()


# F12j:硬错误立即熔断 — 4xx 配置/账户级错误重试无意义（会话级冷却，重启即清）
_HARD_ERROR_RE = re.compile(r"\b(401|403|404|410)\b")
_HARD_ERROR_COOLDOWN = 1800.0  # 30min

# 2026-09-20: 硬性配额耗尽（GLM 1310 周期/月度限额，code 1308 5h 限额同类）——
# 重置时刻在小时级，30min 冷却会在重置前到期反复撞墙。按错误文本里的
# 重置时间戳熔断到该时刻（+60s 缓冲），期间路由直接走下一候选。
# 解析失败退回 2h 默认冷却。
_HARD_QUOTA_RE = re.compile(r"重置时间[^\d]*([\d-]+ [\d:]+)|(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\d]{0,8}重置")
_HARD_QUOTA_FALLBACK_COOLDOWN = 7200.0  # 2h


def _hard_quota_cooldown_seconds(error_detail: str) -> float | None:
    """硬配额错误 → 距重置时刻的秒数；非硬配额或解析失败返回 None/退回值。"""
    from lingclaude.model.retry import is_hard_quota_error
    if not is_hard_quota_error(error_detail or ""):
        return None
    m = _HARD_QUOTA_RE.search(error_detail or "")
    if m:
        ts = m.group(1) or m.group(2)
        try:
            from datetime import datetime
            reset_at = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            delta = (reset_at - datetime.now()).total_seconds()
            return max(delta + 60.0, 60.0)
        except ValueError:
            pass
    return _HARD_QUOTA_FALLBACK_COOLDOWN


# F12c:provider → 环境变量映射。lingcode/config.json 的 api_key 字段留空时,
# 按此映射从环境变量兜底取 key。key 实际单点存放于 ~/.ling_keys.env
# (proxy3 同源,gen_env.py 管理),不在 lingcode config 落明文。
_PROVIDER_ENV_KEY_MAP: dict[str, str] = {
    "volc_coding_plan": "VOLC_CODING_API_KEY",
    "nvidia": "NVIDIA_NIM_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "volcengine": "VOLC_CODING_API_KEY",
    "hunyuan": "HUNYUAN_API_KEY",
    "agnes": "AGNES_ENTERPRISE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "siliconflow_disabled": "SILICONFLOW_API_KEY",
    "kimi": "KIMI_API_KEY",
    "mimo": "XIAOMI_TOKEN_PLAN_API_KEY",
    "zai": "ZAI_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "proxy3": "LINGCLAUDE_PROXY3_API_KEY",
}

# F12k:知名 provider 默认值表 — 当 config 里 provider 是字符串简写
# （如 "glm": "${GLM_API_KEY}"）或字段不全的 dict 时，自动补全为
# 标准 provider 定义，避免因缺 base_url/model 被跳过。
# 与 _PROVIDER_ENV_KEY_MAP 同源维护。
_KNOWN_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "glm": {
        "type": "openai",
        # 智谱 Coding 套餐端点（OpenAI Chat Completion 协议）；/api/paas/v4 是开放
        # 平台端点，套餐 key 走它会报 1211 模型不存在（2026-09-15 实测）。
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "model": "glm-5.3-flash",
        # 2026-09-19: 旗舰 glm-5.3 是智谱最贵模型，从默认清单移除，
        # 避免任何自动路径（默认模型/钉住反查/歧义匹配）选中它；
        # 需要时仍可 /model glm-5.3@glm 显式钉住。
        "models": ["glm-5.3-flash"],
    },
    "proxy3": {
        # 本机 proxy3 免费额度池（:8765），openai 协议；free/* 为虚拟模型
        # （free_pool 插件调度），敏感任务由 openai_provider 的内容守卫自动
        # 停发 opt-in。key 见 LINGCLAUDE_PROXY3_API_KEY（.ling_keys.env）。
        "base_url": "http://127.0.0.1:8765/v1",
        "model": "free/auto",
        "models": ["free/auto", "free/code", "free/long", "free/vision", "free/fast"],
    },
    "deepseek": {
        "type": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "minimax": {
        "type": "openai",
        "base_url": "https://api.minimax.chat/v1",
        "model": "abab6.5s-chat",
        "models": ["abab6.5s-chat", "abab7-chat"],
    },
    "nvidia": {
        "type": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "models": ["nvidia/llama-3.1-nemotron-70b-instruct", "meta/llama-3.1-405b-instruct"],
    },
    "hunyuan": {
        "type": "openai",
        "base_url": "https://api.hunyuan.tencent.com/v1",
        "model": "hunyuan-lite",
        "models": ["hunyuan-lite", "hunyuan-pro", "hunyuan-turbo"],
    },
    "agnes": {
        "type": "openai",
        "base_url": "https://agnes.ai/api/v1",
        "model": "agnes-latest",
        "models": ["agnes-latest"],
    },
    "kimi": {
        # Kimi Code 官方套餐直连（key 前缀 sk-kimi-，见 codex-providers/INSTALL.md）。
        # api.moonshot.cn/v1 是 Moonshot 开放平台，套餐 key 在那边 401。
        # 模型名仅认小写 k3 系（K3-256K 会 401 "set model id as `k3`"，2026-09-16 实测）。
        "type": "openai",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "k3",
        "models": ["k3", "kimi-for-coding"],
    },
    "mimo": {
        # Xiaomi MiMo token plan (key 前缀 tp-).套餐 key 走 token-plan-cn 域名直连,
        # 不是小米 AI 开放平台。ASR/TTS 走独立音频端点,本路由仅含 2 个对话模型。
        "type": "openai",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "models": ["mimo-v2.5-pro", "mimo-v2.5"],
    },
    "siliconflow": {
        "type": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3"],
    },
    "dashscope": {
        "type": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
    },
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-3.5-sonnet",
        "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o"],
    },
    "zai": {
        "type": "openai",
        "base_url": "https://api.zai.ai/v1",
        "model": "zai-chat",
        "models": ["zai-chat"],
    },
}


def _normalize_provider_def(name: str, pdef: str | dict[str, Any]) -> dict[str, Any] | None:
    """把字符串简写或不完整 dict 补全为标准 provider 定义。

    - 字符串：当作 api_key 值（支持 ${ENV_VAR} 形式），从 _KNOWN_PROVIDER_DEFAULTS 补全
    - dict：缺字段时从 _KNOWN_PROVIDER_DEFAULTS 补全默认值
    - 未知 provider 且信息不足：返回 None（调用方决定跳过）
    """
    defaults = _KNOWN_PROVIDER_DEFAULTS.get(name, {})

    if isinstance(pdef, str):
        if not defaults:
            return None  # 未知 provider 且只有字符串，无法补全
        result = dict(defaults)
        result["api_key"] = pdef
        return result

    if isinstance(pdef, dict):
        result = dict(defaults)  # 先铺默认值
        result.update(pdef)      # 用户显式配置覆盖默认值
        # 显式 enabled=false 的 provider 即使没 base_url/type 也合法——交给上层 INFO 跳过
        if result.get("enabled", True) is False:
            return result
        # 至少要有 base_url 或 type，否则认为不可用
        if not result.get("base_url") and not result.get("type"):
            return None
        return result

    return None


def _resolve_api_key(provider_name: str, config_key: str) -> str:
    """F12c:config api_key 为空 → 按映射查环境变量兜底。

    支持 ${ENV_VAR} 引用形式（与 core/config.py._resolve_api_key 同语义），
    展开 结果为空时再走 provider→env 映射兜底。
    """
    if config_key:
        if config_key.startswith("${") and config_key.endswith("}"):
            expanded = os.environ.get(config_key[2:-1], "")
            if expanded:
                return expanded
        else:
            return config_key
    env_name = _PROVIDER_ENV_KEY_MAP.get(provider_name)
    if env_name:
        return os.environ.get(env_name, "")
    return ""


@dataclass
class _ModelRef:
    provider: str
    model: str


@dataclass
class _TaskRoute:
    description: str
    models: list[_ModelRef]


class TaskRouter:
    def __init__(self, config_path: Path | str | None = None) -> None:
        self._path = Path(config_path) if config_path else CONFIG_PATH
        self._providers: dict[str, _ProviderInfo] = {}
        self._task_routes: dict[str, _TaskRoute] = {}
        self._default_provider: str = "cheap"
        self._slots: dict[str, ProviderSlot] = {}
        self._lock = threading.Lock()
        self._round_robin_idx: dict[str, int] = {}
        # P1-F1: 路由层清单实时探活网关——providers 不再静态注入，路由前探活，
        # 410/401/404 剔除、429 绕行（不误杀）。LINGCLAUDE_PROBE_DISABLE=1 关闭。
        self._probe_disabled = probe_disabled_by_env()
        self._probe = ProviderProbe()
        # P1-8（2026-09-21）: 构造期惰性注入 OpenRouter key——凭据仓有存档且 env
        # 未设置时注入，随后 _load_config 的 F12c 解析链即刻读到。已有 env（用户
        # 显式设置 / OAuth 当场注入）一律不覆盖；凭据仓故障 fail-open 不阻断装配。
        try:
            ensure_env_key()
        except Exception:  # noqa: BLE001
            logger.debug("openrouter_oauth 凭据仓注入跳过", exc_info=True)
        self._load_config()

    _raw_provider_keys: dict[str, str]

    def _load_config(self) -> None:
        self._raw_provider_keys = getattr(self, "_raw_provider_keys", {})
        if not self._path.exists():
            logger.warning("TaskRouter: config not found at %s, using empty config", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("TaskRouter: failed to load config: %s", e)
            return

        routing = raw.get("routing", {})
        self._default_provider = routing.get("default_target", "cheap")

        for name, pdef in routing.get("providers", {}).items():
            # F12k:字符串简写 / 缺字段 dict → 用知名 provider 默认值表补全
            normalized = _normalize_provider_def(name, pdef)
            if isinstance(pdef, dict):
                self._raw_provider_keys[name] = str(pdef.get("api_key", "") or "")
            elif isinstance(pdef, str):
                self._raw_provider_keys[name] = pdef
            if normalized is None:
                logger.warning("TaskRouter: provider '%s' is not usable (type=%s, no known defaults), skipped",
                               name, type(pdef).__name__)
                continue
            # F12g:per-provider 开关 — 上游全挂的 provider(如 waterfall)可在
            # lingcode config 加 "enabled": false 摘出路由池,无需删条目。
            if normalized.get("enabled", True) is False:
                logger.info("TaskRouter: provider '%s' disabled by config, skipped", name)
                continue
            rate = normalized.get("rate_limit", {})
            self._providers[name] = _ProviderInfo(
                type=normalized.get("type", "openai"),
                api_key=_resolve_api_key(name, normalized.get("api_key", "")),
                base_url=normalized.get("base_url", ""),
                default_model=normalized.get("model", ""),
                models=normalized.get("models", []),
                rpm=float(rate.get("rpm", 10)),
                burst=int(rate.get("burst", 3)),
            )
            bucket = LeakyBucket(
                max_tokens=float(rate.get("burst", 3)),
                refill_rate=float(rate.get("rpm", 10)) / 60.0,
            )
            self._slots[name] = ProviderSlot(name=name, bucket=bucket)

        for key, tr in routing.get("task_routes", {}).items():
            if not isinstance(tr, dict):
                logger.warning("TaskRouter: task_route '%s' is not a dict (%s), skipped", key, type(tr).__name__)
                continue
            raw_models = tr.get("models", [])
            if not isinstance(raw_models, list):
                logger.warning("TaskRouter: task_route '%s' models is not a list (%s), skipped", key, type(raw_models).__name__)
                continue
            models = []
            for m in raw_models:
                if not isinstance(m, dict):
                    logger.warning("TaskRouter: task_route '%s' has non-dict model entry (%s), skipped", key, type(m).__name__)
                    continue
                if "provider" not in m or "model" not in m:
                    logger.warning("TaskRouter: task_route '%s' model entry missing provider/model, skipped", key)
                    continue
                models.append(_ModelRef(provider=m["provider"], model=m["model"]))
            self._task_routes[key] = _TaskRoute(description=tr.get("description", ""), models=models)

        logger.info(
            "TaskRouter: loaded %d providers, %d task routes from %s",
            len(self._providers), len(self._task_routes), self._path,
        )

    def refresh_api_keys(self) -> int:
        """P1-8（2026-09-21）: 重解析全部 provider api_key（env 热更新后自愈）。

        场景：/openrouter OAuth 完成 → os.environ 注入 OPENROUTER_API_KEY
        → 既有 router 实例的 _providers[*].api_key（构造时固化）感知不到。
        只重解析 key（F12c 同一解析链），不动路由表/熔断桶/探活缓存。
        返回本次有变化的 provider 数。无锁竞态风险：单字段赋值原子。
        """
        changed = 0
        for name, pinfo in self._providers.items():
            raw = (self._raw_provider_keys or {}).get(name, "")
            new_key = _resolve_api_key(name, raw)
            if new_key and new_key != pinfo.api_key:
                pinfo.api_key = new_key
                changed += 1
                logger.info("TaskRouter: provider %s api_key 已热更新", name)
        return changed

    def resolve(
        self,
        prompt: str,
        task_type: TaskType | None = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> tuple[ModelConfig, str]:
        if task_type is None:
            task_type = TaskType.from_query(prompt)

        # P1-1（2026-09-22）：Laya fast lane 接入 resolve 链。
        # 门禁双通道：env LINGCLAUDE_LAYA_FAST_LANE 显式设值（=1 强制开 / =0 强制关，
        # 优先级最高）；未设才走策略文件 fan_out_questions.yaml fast_lane_enabled
        # 热开关（mtime watch + 30s 节流，读失败视为关——fail-closed）。
        # 语义：fast_route 先行做 System-1 分类判定（本地 ~150ms vs LLM 路由 1-5s）；
        # 返回 None（插片不可用 / NOT_GOOD_AT 命中代码长文类 / 判定失败）→ 无缝回退
        # 原关键词分类路径（fail-soft，resolve 契约不变）。
        _env_val = os.environ.get("LINGCLAUDE_LAYA_FAST_LANE", "").strip().lower()
        if _env_val in ("1", "true", "on", "yes"):
            _gate_open = True
        elif _env_val in ("0", "false", "off", "no"):
            _gate_open = False
        else:
            try:
                from lingclaude.core.policy_loader import get as _policy_get

                _gate_open = bool((_policy_get("fan_out_questions") or {}).get("fast_lane_enabled"))
            except Exception:  # noqa: BLE001 — 门禁读取失败视为关
                _gate_open = False
        if _gate_open:
            try:
                from lingclaude.model.fast_lane import fast_route
                from laya.presets import router_questions

                verdict = fast_route(prompt[:500], router_questions())
                if verdict is not None and task_type is TaskType.OTHER:
                    # fast lane 命中且关键词分类为 OTHER（关键词轨的盲区）时，
                    # 用 domain 判定修正 task_type（对齐 TaskType 枚举面）
                    answers = verdict.get("answers") or {}
                    domain = str(answers.get("domain", "")).lower()
                    _domain_map = {
                        "code": TaskType.CODE_GENERATION,
                        "math_or_logic": TaskType.ANALYSIS,
                        "data_analysis": TaskType.ANALYSIS,
                        "writing": TaskType.DOCUMENTATION,
                        "factual_lookup": TaskType.SEARCH,
                    }
                    if domain in _domain_map:
                        task_type = _domain_map[domain]
                # verdict 的 difficulty 低分（trivial/easy）走 fast_response 已是默认；
                # 高分（hard）显式提升到 analysis 路由以获得更强模型
                if verdict is not None:
                    answers = verdict.get("answers") or {}
                    difficulty = str(answers.get("difficulty", "")).lower()
                    if difficulty.startswith("hard") and task_type in (
                        TaskType.OTHER, TaskType.SEARCH,
                    ):
                        task_type = TaskType.ANALYSIS
            except Exception:  # noqa: BLE001 — fast lane 任何故障不影响 resolve 主链
                pass

        # P2-2: 运行时读策略（热更生效），模块常量仅作回退
        mapping = _load_task_type_to_route()
        route_key = mapping.get(task_type, "fast_response")
        route = self._task_routes.get(route_key)

        if route and route.models:
            resolved = self._pick_from_route(route_key, route, max_tokens, temperature)
            if resolved:
                return resolved, route_key

        return self._fallback(max_tokens, temperature), route_key

    def _pick_from_route(self, route_key: str, route: _TaskRoute, max_tokens: int, temperature: float) -> ModelConfig | None:
        models = route.models
        if not models:
            return None

        # 严格优先级模式:始终从配置表首位开始扫描,健康候选即选中。
        # 此前 round-robin 会让首位候选与兜底 50/50 交替,违背
        # "glm-5.3-flash 优先、waterfall 兜底"的配置语义。
        # 故障切换由 F12b(无 key 跳过)+ slot.is_available(熔断/限流)承担。
        # P1-4: flash 门禁开关每次调用时读环境变量（测试可注入，进程内热更）
        _flash_gate_on = os.environ.get(_FLASH_GATE_DISABLE_ENV, "") not in (
            "1", "true", "TRUE", "True",
        )
        for pos in range(len(models)):
            ref = models[pos]
            pinfo = self._providers.get(ref.provider)
            if not pinfo:
                continue

            # F12b:云端 provider 缺 api_key → 跳过选下一候选,不让请求
            # 炸在 provider.stream_complete 层。本地服务无 key 是正常形态,不跳过。
            if not pinfo.api_key and not _is_local_base(pinfo.base_url):
                # P1-8: OAuth 注入/启动后 env 才有 key 的自愈口——先重解析一次，
                # 本 provider 的 key 真灌进来了才继续；否则走跳过逻辑。
                # （refresh>0 只代表有变化，未必是本 provider，须复查。）
                if self.refresh_api_keys() > 0 and pinfo.api_key:
                    pass  # 自愈成功：落到下方正常选中路径
                else:
                    _env_var = _PROVIDER_ENV_KEY_MAP.get(ref.provider, "<未映射>")
                    logger.debug(
                        "路由跳过 %s:云端 provider 无 api_key"
                        "(候选 %d/%d, 期望环境变量: %s, 已设置: %s)",
                        ref.provider, pos + 1, len(models),
                        _env_var, bool(os.environ.get(_env_var, "")),
                    )
                    continue

            slot = self._slots.get(ref.provider)
            if slot and not slot.is_available:
                continue

            # P1-4 (2026-09-19): flash 级禁入决策位 —— 决策路由首位候选是
            # 轻量模型（flash/mini/lite）时顺延下一候选。H1/H2 共识：轻量模型
            # 幻觉率高，决策位产出被下游全量消费，代价最高。只挡首位（方案
            # 语义「禁入首位」），flash 在 2+ 位作降级兜底不受影响；显式
            # /model 指定不经过路由层，不受此门禁约束。
            if (_flash_gate_on
                    and route_key in _DECISION_ROUTE_KEYS
                    and pos == 0
                    and _FLASH_MODEL_RE.search(ref.model)):
                logger.info(
                    "P1-4 flash 门禁: %s 为轻量模型，禁入决策路由 %r 首位，顺延下一候选"
                    "（LINGCLAUDE_FLASH_GATE_DISABLE=1 可关闭）",
                    ref.model, route_key,
                )
                continue

            # P1-F1: 清单实时探活门禁——410/401/404 熔断剔除（跳过其 F12j 逻辑，
            # 熔断时长由探活 TTL 自持），429 绕行不剔除，探活未知/关闭则放行。
            if not self._probe_disabled:
                probe = self._probe.check(
                    ref.provider, pinfo.base_url, pinfo.api_key,
                )
                if probe.excluded:
                    slot = self._slots.get(ref.provider)
                    if slot:
                        slot.cooldown_until = time.monotonic() + probe.ttl
                        slot.consecutive_errors = 0
                    logger.warning(
                        "路由剔除 %s（候选 %d/%d）: 探活 hard_4xx http=%s detail=%s — "
                        "清单级死节点，%ds 内跳过（证据见 .lingclaude/provider_probe.jsonl）",
                        ref.provider, pos + 1, len(models),
                        probe.http_code or "?", probe.detail[:80], int(probe.ttl),
                    )
                    continue
                if probe.bypass_round:
                    logger.info(
                        "路由绕行 %s（候选 %d/%d）: 探活 rate_limited http=%s — "
                        "节点活着本轮绕过，不剔除",
                        ref.provider, pos + 1, len(models), probe.http_code or "?",
                    )
                    continue
                # probe.unknown / probe.status == "ok" → 正常放行，进选通逻辑

            # P2-5a (2026-09-19, 幻觉调研第三批): 运行时不改排序 —— 配置序仍是
            # 权威（严格优先级语义）。实测化排序的落点是离线 model_health_probe
            # 重排 lingcode config 的 task_routes + CI gate 固化（scripts/
            # model_health_probe.py），运行时仅消费重排后的配置序，避免路由
            # 热路径引入隐藏状态（score 缓存陈旧会制造新的清单幻觉）。

            slot = self._slots.get(ref.provider)
            if slot:
                slot.total_requests += 1

            return ModelConfig(
                model=ref.model,
                api_key=pinfo.api_key,
                base_url=pinfo.base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt="",
            )

        return None

    def _fallback(self, max_tokens: int, temperature: float) -> ModelConfig:
        pinfo = self._providers.get(self._default_provider)
        if pinfo:
            return ModelConfig(
                model=pinfo.default_model,
                api_key=pinfo.api_key,
                base_url=pinfo.base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt="",
            )
        return ModelConfig()

    def record_success(self, provider_name: str) -> None:
        slot = self._slots.get(provider_name)
        if slot:
            slot.consecutive_errors = 0
        # P1-F1: 真实成功是最强健康证据——清探活缓存，防陈旧 hard_4xx 结论
        # 在 TTL 内继续误杀已恢复的节点（探活只在路由前做，样本远少于真实调用）
        self._probe.invalidate(provider_name)
        # 方案C v4 P0#1: 真实成功同时清配额窗口（窗口只是观察，成功推翻它）
        try:
            from lingclaude.model.quota_governance import get_quota_pool
            get_quota_pool().clear(provider_name)
        except Exception:  # noqa: BLE001
            logger.exception("quota_governance: 窗口清除失败 provider=%s", provider_name)

    def record_error(self, provider_name: str, error_detail: str = "") -> None:
        """记录 provider 错误（F12j：硬错误立即熔断）。

        - 瞬态错误（超时/5xx/连接失败）：3 连击才冷却 30s —— 容忍抖动
        - 硬错误（HTTP 401/403/404/410 —— key 失效/部署下线/账户问题）：
          立即冷却 30min。这类错误重试无意义，3 击阈值只会让每条消息
          都把路由池里的模型撞一遍（现场实证：nvidia 410 下主备双 404）
        冷却是会话级的，不写 config —— 重启即恢复，供 key 修复后回归。
        """
        slot = self._slots.get(provider_name)
        if not slot:
            return
        slot.consecutive_errors += 1
        slot.total_errors += 1
        slot.last_error_time = time.monotonic()
        # 方案C v4 P0#1: 配额窗口直查——错误路径顺带提取窗口（问窗口不猜），
        # 路由层可经 QuotaWindowPool.decide_from_windows() 查询 defer/allow；
        # 非配额错误 no-op；窗口记录失败不影响熔断主路径。
        try:
            from lingclaude.model.quota_governance import get_quota_pool
            get_quota_pool().record_from_error(provider_name, error_detail or "")
        except Exception:  # noqa: BLE001
            logger.exception("quota_governance: 窗口记录失败 provider=%s", provider_name)
        # 2026-09-20: 硬配额耗尽优先于通用硬错误 —— 冷却到重置时刻（而非
        # 统一 30min/2h），期间路由跳过该 provider，自动落到下一候选。
        _quota_cd = _hard_quota_cooldown_seconds(error_detail or "")
        if _quota_cd is not None:
            slot.cooldown_until = time.monotonic() + _quota_cd
            slot.consecutive_errors = 0
            logger.warning(
                "provider %s 熔断 %.0fmin（硬配额耗尽）— 路由跳过至配额重置，期间走下一候选"
                "（无候选可用? 输入 /openrouter 一键接入 OpenRouter 免费池）",
                provider_name, _quota_cd / 60.0,
            )
            # 探活缓存同步校准（429 绕行语义不再适用：配额已尽，探活必 429）
            self._probe.invalidate(provider_name)
        elif _HARD_ERROR_RE.search(error_detail or ""):
            slot.cooldown_until = time.monotonic() + _HARD_ERROR_COOLDOWN
            slot.consecutive_errors = 0
            logger.warning(
                "provider %s 熔断 %ds（硬错误: %s）— 本会话内路由跳过，重启或冷却后恢复",
                provider_name, int(_HARD_ERROR_COOLDOWN), error_detail[:120],
            )
        elif slot.consecutive_errors >= 3:
            slot.cooldown_until = time.monotonic() + 30.0
            slot.consecutive_errors = 0
        # P1-F2: 真实调用撞上的硬错误同样校准探活缓存——下次路由前不再
        # 重复发探活请求确认（结论一致，省一轮 RTT）
        if _HARD_ERROR_RE.search(error_detail or ""):
            self._probe.invalidate(provider_name)

    def check_switch_target_health(
        self, provider_name: str, *, force: bool = True
    ) -> tuple[bool, str]:
        """P1-F2: 降级链健康度门禁——切换前先探活目标节点，不盲切。

        供两路调用方：
        - F12f 自动换候选（model_call.py）: 换候选前对下一跳做门禁
        - /model 手动切换（query_engine_model_mixin.py）: 钉住前对目标 provider 做门禁

        裁决语义（对齐调研 §2.5/§5）：
        - 探活硬 4xx（410/401/404）→ 拒绝切换（目标已死，切过去必炸）
        - 429 限流 → 允许切换（节点活着，只是慢/挤），附提示
        - 探活未知/网络错误/关闭 → 允许切换（证据不足不定罪，真实调用层兜底）
        - force=True: 门禁语义 = "此刻的真实状态"，缓存结论不作数

        Returns:
            (allowed, reason) — allowed=False 时 reason 为用户可读的拒绝原因。
        """
        if self._probe_disabled:
            return True, "探活已关闭（LINGCLAUDE_PROBE_DISABLE），跳过门禁"
        pinfo = self._providers.get(provider_name)
        if pinfo is None:
            return True, f"provider '{provider_name}' 不在路由清单，门禁不适用"
        if not pinfo.api_key and _is_local_base(pinfo.base_url):
            return True, "本地端点不探活，门禁放行"
        if not pinfo.api_key:
            return False, f"provider '{provider_name}' 云端无 api_key，切换注定失败"
        probe = self._probe.check(provider_name, pinfo.base_url, pinfo.api_key, force=force)
        if probe.excluded:
            # 与路由层 _pick_from_route 剔除动作对齐：门禁判死的节点同步拉入
            # 熔断，后续 resolve() 在 TTL 内直接跳过，不再重复探活
            slot = self._slots.get(provider_name)
            if slot:
                slot.cooldown_until = time.monotonic() + probe.ttl
                slot.consecutive_errors = 0
            return False, (
                f"provider '{provider_name}' 探活失败: HTTP {probe.http_code}"
                f"（清单级死节点，{probe.detail[:80] or '无详情'}）— 已阻止切换"
            )
        if probe.bypass_round:
            return True, f"provider '{provider_name}' 限流中（HTTP {probe.http_code}），节点活着，允许切换"
        if probe.unknown:
            return True, f"provider '{provider_name}' 探活不可知（{probe.status}），不据此拒绝"
        return True, f"provider '{provider_name}' 探活通过（{probe.status}）"

    def find_provider_by_model(self, model_name: str) -> tuple[str, _ProviderInfo] | tuple[None, None]:
        """F12h:按模型名反查 provider — 供 /model <name> 切换时连带端点/key。"""
        target = (model_name or "").strip()
        if not target:
            return None, None
        for name, pinfo in self._providers.items():
            if pinfo.default_model == target or target in pinfo.models:
                return name, pinfo
        return None, None

    def find_all_providers_by_model(self, model_name: str) -> list[tuple[str, _ProviderInfo]]:
        """返回所有声明该模型的 provider（歧义检测用）。"""
        target = (model_name or "").strip()
        if not target:
            return []
        found: list[tuple[str, _ProviderInfo]] = []
        for name, pinfo in self._providers.items():
            if pinfo.default_model == target or target in pinfo.models:
                found.append((name, pinfo))
        return found

    def parse_selector(self, selector: str) -> tuple[str, str]:
        """解析模型选择器，返回 (provider_name, model_name)。

        支持三种格式（对齐 opencode/crush/atomcode）：
        - "model@provider"   -> (provider, model)        [推荐，@ 右侧为 provider]
        - "provider/model"   -> (provider, model)        [opencode 兼容]
        - "model"            -> ("", model)              [裸名，由调用方反查/歧义处理]

        规则：model 内部允许含 @（如 glm-5.2@zhipu 代理名），
        因此 @ 取**最后一个**；/ 取**第一个**。
        """
        s = (selector or "").strip()
        if not s:
            return "", ""
        if "@" in s:
            model, provider = s.rsplit("@", 1)
            return provider.strip(), model.strip()
        if "/" in s:
            provider, model = s.split("/", 1)
            return provider.strip(), model.strip()
        return "", s

    def resolve_selector(self, selector: str) -> tuple[str | None, _ProviderInfo | None, str | None]:
        """解析模型选择器 -> (provider_name, provider_info, error)。

        - "model@provider" / "provider/model"：provider 必须存在，否则报错
        - 裸名：查注册表，唯一命中返回；多命中返回歧义错误；未命中返回 None（由调用方降级）

        Returns:
            (provider_name, pinfo, None)      成功
            (None, None, error_msg)           失败（provider 不存在 / 歧义）
            (None, None, None)                裸名未命中（调用方自行降级）
        """
        provider_name, model_name = self.parse_selector(selector)
        if provider_name:
            pinfo = self._providers.get(provider_name)
            if pinfo is None:
                avail = ", ".join(sorted(self._providers.keys())) or "(无)"
                return None, None, f"provider '{provider_name}' 不存在。可用: {avail}"
            # provider 存在即成功（模型透传，provider 层动态校验）
            return provider_name, pinfo, None
        # 裸名
        if not model_name:
            return None, None, None
        matches = self.find_all_providers_by_model(model_name)
        if len(matches) == 1:
            return matches[0][0], matches[0][1], None
        if len(matches) > 1:
            candidates = ", ".join(f"{model_name}@{p}" for p, _ in matches)
            return None, None, (
                f"模型 '{model_name}' 在多个 provider 中存在: {candidates}。"
                f"请用 model@provider 或 provider/model 显式指定。"
            )
        return None, None, None



    def provider_diagnostics(self) -> list[dict[str, Any]]:
        """供 /model 诊断: 每个 provider 的 key/连通性摘要。"""
        out = []
        for name, pinfo in self._providers.items():
            env_var = _PROVIDER_ENV_KEY_MAP.get(name, "")
            out.append({
                "provider": name,
                "base_url": pinfo.base_url,
                "has_key": bool(pinfo.api_key),
                "is_local": _is_local_base(pinfo.base_url),
                "expected_env_var": env_var,
                "env_var_set": bool(os.environ.get(env_var, "")) if env_var else True,
                "key_missing": (not pinfo.api_key) and not _is_local_base(pinfo.base_url),
            })
        return out

    def get_provider_name(self, api_key: str, base_url: str) -> str | None:
        for name, pinfo in self._providers.items():
            if pinfo.api_key == api_key and pinfo.base_url == base_url:
                return name
        return None

    def get_default_provider_name(self) -> str:
        return self._default_provider

    def stats(self) -> dict[str, Any]:
        result: dict[str, Any] = {"providers": {}, "routes": list(self._task_routes.keys())}
        for name, slot in self._slots.items():
            result["providers"][name] = {
                "available": slot.is_available,
                "consecutive_errors": slot.consecutive_errors,
                "total_requests": slot.total_requests,
                "total_errors": slot.total_errors,
            }
        return result

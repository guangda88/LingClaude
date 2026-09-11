# lingclaude 模型切换方案 v2 (参考 crush/atomcode/opencode)

> 设计目标：统一 model@provider 语法、多 Provider 注册表、大/小模型分离、CLI 运行时覆盖、零侵入迁移

---

## 1. 核心设计原则

| 原则 | 说明 |
|------|------|
| **统一标识** | `model@provider` 作为唯一模型标识（兼容 `provider/model`） |
| **Provider 注册表** | `providers:` 映射名 → `{base_url, api_key, models[], env}` |
| **大/小模型分离** | `model.large` / `model.small` 各自 `model@provider` |
| **运行时覆盖** | CLI `--provider` / `--model` / `--model-large` / `--model-small` 优先级最高 |
| **零侵入迁移** | 现有 `model.provider/model.model/base_url/api_key` 自动折叠为内置 `default` provider |

---

## 2. 配置文件结构 (config.yaml)

```yaml
# 兼容旧配置：若只写 model.provider/model.model 等，自动生成内置 default provider
model:
  # 新：大/小模型分离（推荐）
  large: "glm-5.2@zai_coding_plan"       # 复杂任务、重构、架构
  small: "glm-4.7-flash@zai_coding_plan"  # 闲聊、简单问答、摘要

  # 兼容旧：单模型（自动同时赋值给 large/small）
  # model: "deepseek-v4-flash@volc_coding_plan"
  # provider: "openai"  # 旧字段，仅作兼容

  # 可选：特定场景模型
  # code: "qwen3-coder-plus@siliconflow"     # 代码生成专用
  # reasoning: "deepseek-r1@deepseek"         # 推理任务

  # 运行时参数（被 CLI 覆盖）
  max_tokens: 16384
  temperature: 0.7
  system_prompt: "..."

# Provider 注册表（核心新增）
providers:
  # 内置 default（由旧配置自动生成，或显式声明）
  default:
    type: "openai-compat"           # openai-compat / ark / anthropic / gemini / custom
    base_url: "https://ark.cn-beijing.volces.com/api/coding/v3"
    api_key: "${VOLC_CODING_API_KEY}"
    models: []                      # 为空=动态发现；非空=白名单
    # 可选：模型别名映射（简化 model@provider 写法）
    aliases:
      "glm-5.2": "glm-5.2@volc_coding_plan"
      "deepseek-v4": "deepseek-v4-flash@volc_coding_plan"

  # 智谱 Coding Plan
  zai_coding_plan:
    type: "openai-compat"
    base_url: "https://open.bigmodel.cn/api/coding/paas/v4"
    api_key: "${ZAI_CODING_API_KEY}"
    models: []
    aliases:
      "glm-5.3": "glm-5.3"
      "glm-4.7": "glm-4.7"

  # 火山方舟
  volc_coding_plan:
    type: "ark"
    base_url: "https://ark.cn-beijing.volces.com/api/coding/v3"
    api_key: "${VOLC_CODING_API_KEY}"
    # ARK 特有：endpoint_id 或 model_id
    endpoint_id: "${VOLC_ENDPOINT_ID}"
    models: []

  # 直连 MiniMax
  minimax_direct:
    type: "openai-compat"
    base_url: "https://api.minimaxi.com/v1"
    api_key: "${MINIMAX_API_KEY}"
    models: []

  # 直连 Kimi
  kimi_direct:
    type: "openai-compat"
    base_url: "https://api.kimi.com/coding/v1"
    api_key: "${KIMI_API_KEY}"
    models: []

  # 代理网关（如 proxy3）
  proxy3:
    type: "openai-compat"
    base_url: "http://127.0.0.1:8765/v1"
    api_key: "${PROXY3_ADMIN_KEY}"
    models: []
```

---

## 3. 模型标识语法

| 写法 | 解析结果 | 示例 |
|------|----------|------|
| `model@provider` | model=`model`, provider=`provider` | `glm-5.2@zai_coding_plan` |
| `provider/model` | model=`model`, provider=`provider` | `zai_coding_plan/glm-5.2` |
| `model` (裸名) | 依次查找：aliases → providers[default].models → 所有 providers | `glm-5.2` |

**解析优先级**：
1. 显式 `model@provider` / `provider/model` → 直接路由
2. 裸名 + alias 映射 → `providers[x].aliases[model]`
3. 裸名 + 精确匹配 → 遍历所有 provider 的 `models[]`
4. 兜底 → `providers[default]` 报错提示可用列表

---

## 4. CLI 运行时覆盖

```bash
# 交互模式
lingclaude run --model "glm-5.2@zai_coding_plan"           # 同时覆盖 large+small
lingclaude run --model-large "glm-5.2@zai" --model-small "glm-4.7@zai"
lingclaude run --provider "zai_coding_plan"                # 仅切 provider，保持模型名
lingclaude run --model "kimi-k2.7-code@kimi_direct"        # 临时用 Kimi

# 非交互模式
lingclaude run -m "glm-5.2@zai" "帮我重构 xxx"
lingclaude run --model-small "glm-4.7@zai" "简单问答"

# 列出可用模型
lingclaude models list                    # 树形展示 provider → models
lingclaude models search "glm"            # 模糊搜索
lingclaude models providers               # 列出 provider 及状态
```

---

## 5. 代码实现架构

### 5.1 数据结构 (`lingclaude/core/config.py`)

```python
from dataclasses import dataclass, field
from typing import Optional
import os

@dataclass(frozen=True)
class ProviderConfig:
    """Provider 注册表项"""
    name: str
    type: str = "openai-compat"      # openai-compat | ark | anthropic | gemini | custom
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""            # 环境变量名（优先于 api_key）
    endpoint_id: str = ""            # ARK 专用
    models: tuple[str, ...] = ()     # 白名单模型 ID，空=动态发现
    aliases: dict[str, str] = field(default_factory=dict)  # 别名 -> model@provider
    headers: dict[str, str] = field(default_factory=dict)  # 额外请求头
    timeout: float = 60.0
    max_retries: int = 2

    def resolved_api_key(self) -> str:
        if self.api_key_env and os.getenv(self.api_key_env):
            return os.getenv(self.api_key_env)
        return self.api_key

@dataclass(frozen=True)
class ModelConfig:
    # 大/小模型分离（核心）
    large: str = ""           # "model@provider"
    small: str = ""           # "model@provider"

    # 兼容旧配置（自动迁移）
    model: str = ""           # 旧单模型字段
    provider: str = ""        # 旧单provider字段

    # 运行时参数
    max_tokens: int = 16384
    temperature: float = 0.7
    system_prompt: str = ""

    # Provider 注册表
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    # 兼容旧：单 provider 字段（自动折叠为 providers["default"]）
    base_url: str = ""
    api_key: str = ""

    def __post_init__(self):
        # 兼容旧配置迁移
        if self.model and not self.large:
            object.__setattr__(self, "large", self.model)
        if self.model and not self.small:
            object.__setattr__(self, "small", self.model)
        if self.provider and "default" not in self.providers:
            default = ProviderConfig(
                name="default",
                base_url=self.base_url,
                api_key=self.api_key,
            )
            object.__setattr__(self, "providers", {"default": default, **self.providers})
```

### 5.2 解析器 (`lingclaude/core/model_registry.py` 新建)

```python
"""模型注册表与解析器"""

from dataclasses import dataclass
from typing import Optional
from lingclaude.core.config import ModelConfig, ProviderConfig

@dataclass(frozen=True)
class ResolvedModel:
    model: str                    # 纯模型名（无 @provider）
    provider: str                 # provider 名
    provider_config: ProviderConfig
    model_id: str                 # 传给 provider 的完整 ID
    context_window: int = 128000

class ModelRegistry:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._build_index()

    def _build_index(self):
        """构建模型 -> (provider, model_id) 反向索引"""
        self._index: dict[str, tuple[str, str]] = {}  # model -> (provider, model_id)
        self._aliases: dict[str, str] = {}            # alias -> model@provider

        for pname, pcfg in self.config.providers.items():
            for mid in pcfg.models:
                # 支持 model@provider 格式
                if "@" in mid:
                    m, p = mid.split("@", 1)
                    if p == pname:
                        self._index[m] = (pname, mid)
                else:
                    self._index[mid] = (pname, mid)
            for alias, target in pcfg.aliases.items():
                self._aliases[alias] = target

    def resolve(self, spec: str) -> ResolvedModel:
        """
        解析模型标识：
        - "model@provider" / "provider/model" -> 直接路由
        - "alias" -> aliases 映射
        - "model" -> 索引查找
        """
        spec = spec.strip()

        # 1. 显式 model@provider / provider/model
        if "@" in spec:
            model, provider = spec.split("@", 1)
        elif "/" in spec:
            provider, model = spec.split("/", 1)
        else:
            model, provider = spec, ""

        # 2. alias 映射
        if model in self._aliases:
            target = self._aliases[model]
            return self.resolve(target)  # 递归解析

        # 3. provider 已知 -> 直接用
        if provider and provider in self.config.providers:
            pcfg = self.config.providers[provider]
            model_id = model if "@" in model else model
            return ResolvedModel(
                model=model,
                provider=provider,
                provider_config=pcfg,
                model_id=model_id,
            )

        # 4. 裸名查找索引
        if model in self._index:
            pname, mid = self._index[model]
            return ResolvedModel(
                model=model,
                provider=pname,
                provider_config=self.config.providers[pname],
                model_id=mid,
            )

        # 5. 兜底：用 default provider
        default = self.config.providers.get("default")
        if default:
            return ResolvedModel(
                model=model,
                provider="default",
                provider_config=default,
                model_id=model,
            )

        raise ValueError(f"Model '{spec}' not found. Available: {list(self._index.keys())}")

    def list_models(self) -> dict[str, list[str]]:
        """返回 provider -> models 映射（供 CLI 列表）"""
        result = {}
        for pname, pcfg in self.config.providers.items():
            result[pname] = list(pcfg.models) + [f"{a} (alias->{t})" for a, t in pcfg.aliases.items()]
        return result
```

### 5.3 ModelAdapter 集成 (`lingclaude/core/model_adapter.py`)

```python
# 新增：按 ResolvedModel 创建/切换 provider
class ModelAdapter:
    def __init__(self, registry: ModelRegistry, default_large: str = "", default_small: str = "") -> None:
        self._registry = registry
        self._providers_cache: dict[str, Any] = {}
        self._current_large = self._resolve_or_none(default_large)
        self._current_small = self._resolve_or_none(default_small)

    def _resolve_or_none(self, spec: str) -> Optional[ResolvedModel]:
        if not spec:
            return None
        try:
            return self._registry.resolve(spec)
        except ValueError:
            return None

    def set_model(self, spec: str, *, role: str = "large") -> ResolvedModel:
        """切换模型（role: large/small）"""
        resolved = self._registry.resolve(spec)
        provider = self._get_or_create_provider(resolved.provider_config)
        if role == "small":
            self._current_small = resolved
        else:
            self._current_large = resolved
        return resolved

    def get_model(self, role: str = "large") -> ResolvedModel:
        return self._current_large if role == "large" else self._current_small

    def _get_or_create_provider(self, pcfg: ProviderConfig) -> Any:
        key = pcfg.name
        if key not in self._providers_cache:
            self._providers_cache[key] = self._create_provider(pcfg)
        return self._providers_cache[key]

    def _create_provider(self, pcfg: ProviderConfig) -> Any:
        # 根据 type 创建对应 provider 实例
        if pcfg.type == "ark":
            from volcengine.ark_llm import ArkLLM
            return ArkLLM(endpoint_id=pcfg.endpoint_id, api_key=pcfg.resolved_api_key(), base_url=pcfg.base_url)
        else:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=pcfg.name,  # 占位，实际调用时传 model_id
                api_key=pcfg.resolved_api_key(),
                base_url=pcfg.base_url,
                default_headers=pcfg.headers,
                timeout=pcfg.timeout,
                max_retries=pcfg.max_retries,
            )
```

### 5.4 CLI 集成 (`lingclaude/cli/app.py`)

```python
# 新增 CLI 参数
def _add_model_args(parser):
    parser.add_argument("--model", dest="model_override", help="覆盖 large+small 模型 (model@provider)")
    parser.add_argument("--model-large", dest="model_large", help="覆盖 large 模型")
    parser.add_argument("--model-small", dest="model_small", help="覆盖 small 模型")
    parser.add_argument("--provider", dest="provider_override", help="仅切换 provider")

# 实例化时应用覆盖
def _build_model_adapter(config, args):
    registry = ModelRegistry(config.model)
    adapter = ModelAdapter(registry, config.model.large, config.model.small)

    # 1. --model 最高优先级
    if args.model_override:
        adapter.set_model(args.model_override, role="large")
        adapter.set_model(args.model_override, role="small")
    else:
        # 2. --model-large / --model-small
        if args.model_large:
            adapter.set_model(args.model_large, role="large")
        if args.model_small:
            adapter.set_model(args.model_small, role="small")
        # 3. --provider 仅切 provider
        if args.provider_override:
            # 保持模型名，仅换 provider
            large = adapter.get_model("large")
            small = adapter.get_model("small")
            adapter.set_model(f"{large.model}@{args.provider_override}", role="large")
            adapter.set_model(f"{small.model}@{args.provider_override}", role="small")
    return adapter
```

---

## 6. 迁移路径（零侵入）

| 旧配置 | 自动迁移结果 |
|--------|-------------|
| `model.provider=openai`<br>`model.model=gpt-4o`<br>`model.base_url=...`<br>`model.api_key=...` | `providers.default` 生成，`large=small="gpt-4o@default"` |
| `model.model=deepseek-v4-flash`<br>`model.provider=ark` | `providers.default(type=ark)` 生成，`large=small="deepseek-v4-flash@default"` |
| 仅 `model.model=glm-5.2` | `providers.default(type=openai-compat)` 生成，需补 `base_url/api_key` 环境变量 |

**迁移代码**（`config.py` 中 `load_config` 后自动执行）：

```python
def _migrate_legacy_model(cfg: ModelConfig) -> ModelConfig:
    if cfg.model and not cfg.large:
        # 旧配置迁移
        if cfg.provider:
            provider_name = cfg.provider
        else:
            provider_name = "default"
        large = f"{cfg.model}@{provider_name}"
        return ModelConfig(
            large=large,
            small=large,
            providers={**cfg.providers, provider_name: ProviderConfig(
                name=provider_name,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
            )},
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            system_prompt=cfg.system_prompt,
        )
    return cfg
```

---

## 7. 验收标准

| 场景 | 预期行为 |
|------|----------|
| `lingclaude run -m "glm-5.2@zai_coding_plan"` | 大小模型同时切换 |
| `lingclaude run --model-large "glm-5.2@zai" --model-small "glm-4.7@zai"` | 大小模型分离 |
| `lingclaude run --provider "proxy3"` | 保持模型名，仅换 provider |
| `lingclaude models list` | 树形展示所有 provider + models |
| 旧配置无 `providers` 字段 | 自动生成 `default` provider，兼容运行 |
| `model@provider` provider 不存在 | 报错并提示可用 provider 列表 |
| 裸名 `glm-5.2` 存在多 provider | 按 aliases → 索引顺序解析，歧义报警 |

---

## 8. 文件变更清单

| 文件 | 变更类型 |
|------|----------|
| `lingclaude/core/config.py` | 新增 `ProviderConfig`、`ModelConfig.providers`、迁移逻辑 |
| `lingclaude/core/model_registry.py` | **新建** 解析器、索引、别名 |
| `lingclaude/core/model_adapter.py` | 集成 `ModelRegistry`、按 role 切换、provider 缓存 |
| `lingclaude/cli/app.py` | 新增 `--model/--model-large/--model-small/--provider` |
| `lingclaude/cli/commands.py` | 新增 `/model` 斜杠命令、`/provider` 切换 |
| `config.yaml` | 文档化新结构（保留兼容字段） |
| `tests/test_model_registry.py` | **新建** 解析器、迁移、CLI 覆盖测试 |

---

## 9. 与现有架构对齐

- **wiring.py**: `ModelAdapter` 已在 manifest 中 (`_make_model_adapter`)，无需改动装配
- **query_engine.py**: 通过 `ModelAdapter.call/stream_call` 调用，内部自动路由
- **slash commands**: 复用 `ModelAdapter.set_model`，零新增依赖
- **config.yaml**: `skip-worktree` 保护，本地真实 key 不入库

---

## 10. 后续扩展（预留）

- **模型路由**: `ModelRouterConfig` 接入 `BehaviorAwareRouter`，按任务类型自动选 large/small/code/reasoning
- **成本感知**: `TokenMonitor` 记录每 provider/模型 token 成本，CLI 显示实时费率
- **健康检查**: `lingclaude models health` 探测 provider 可用性
- **热重载**: `SIGHUP` 触发 `ModelRegistry` 重建索引，无需重启
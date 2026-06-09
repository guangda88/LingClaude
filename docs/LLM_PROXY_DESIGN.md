# LLM Proxy 设计文档

> 灵克 #2 | 2026-05-08 | v0.1

## 问题

150+ LLM 分散在三个独立系统中，各自管理限流和降级：

| 系统 | 位置 | 问题 |
|------|------|------|
| lingclaude TaskRouter | `model/task_router.py` | 只管路由，不管配额耗尽 |
| lingflow+ GLMClient | `lingflow_plus/llm_client.py` (1313行) | 自建 token 池轮转，与 TaskRouter 重复逻辑 |
| member_responder | `webui/member_responder.py` | 直接 HTTP 调用，无降级无限流 |

结果：429 频发、配额浪费、无法跨系统统筹。

## 架构

```
调用方 (lingclaude / lingflow+ / member_responder)
    │
    ▼
┌─────────────────────────────────┐
│  LLM Proxy (HTTP, localhost)    │
│                                 │
│  ┌───────────┐  ┌───────────┐  │
│  │ Rate Gate │  │ Token Gate│  │
│  └─────┬─────┘  └─────┬─────┘  │
│        └──────┬───────┘         │
│               ▼                 │
│        ┌─────────────┐          │
│        │ Purpose Router│        │
│        └──────┬──────┘          │
│               ▼                 │
│        ┌─────────────┐          │
│        │ Data Filter │          │
│        └──────┬──────┘          │
│               ▼                 │
│     Provider Pool (4 providers) │
└─────────────────────────────────┘
    │
    ▼
  OpenAI / GLM / MiniMax / NVIDIA
```

## 四边界

### 1. Rate — 请求频率

现有 `LeakyBucket` + `ProviderSlot` 已实现，直接复用。新增：

- **全局 RPM 上限**：所有调用方共享一个 proxy，防止单方吃满配额
- **优先级队列**：coding > reasoning > chat，低优先级在高压时排队
- **429 自动退避**：收到 429 → 标记 provider 疲劳 → 自动切到下一个 model

```python
@dataclass
class RatePolicy:
    global_rpm: float = 60.0          # 全局上限
    provider_rpm: dict[str, float]     # {provider: rpm}
    priority_weights: dict[str, float] # {task_type: weight}
    cooldown_429: float = 30.0        # 429 后冷却秒数
```

### 2. Token — 配额管理

从 GLMClient 的 `KeySlot` 抽取，统一管理：

- **配额窗口感知**：GLM coding plan 每 5 小时重置（已有逻辑）
- **消耗追踪**：每次调用记录 input/output tokens，按 provider 聚合
- **高水位线**：单 key 消耗达 95% 自动降级到下一个 model

```python
@dataclass
class TokenBudget:
    provider: str
    key_id: str
    window_seconds: int               # 配额窗口
    total_budget: int                 # 总 token 额度
    used: int = 0
    reset_at: float = 0.0

    @property
    def remaining_pct(self) -> float:
        return 1.0 - (self.used / self.total_budget) if self.total_budget else 1.0

    @property
    def should_degrade(self) -> bool:
        return self.remaining_pct < 0.05
```

### 3. Purpose — 任务路由

现有 `TaskRouter` 的 task_routes 配置直接复用。新增：

- **自动任务分类**：从请求的 system_prompt / metadata 推断 TaskType
- **路由热更新**：config.json 修改后自动 reload（watch mtime）
- **跨 provider fallback**：nvidia 429 → 切 minimax → 切 glm，保持同能力模型

```python
# 已有配置，直接复用
TASK_ROUTES = {
    "coding": ["qwen3-coder-480b", "deepseek-v4-pro", "glm-5.1", ...],
    "chinese_reasoning": ["glm-5.1", "deepseek-v4-pro", "qwen3.5-397b", ...],
    "fast_response": ["gemma-3n-e2b", "phi-4-mini", "llama-3.2-1b", ...],
    ...
}

# 新增：同能力跨 provider fallback 表
FALLBACK_GROUPS = {
    "premium_coding": [
        ("nvidia", "qwen/qwen3-coder-480b-a35b-instruct"),
        ("nvidia", "deepseek-ai/deepseek-v4-pro"),
        ("minimax", "MiniMax-M2.7"),
        ("glm", "glm-5.1"),
    ],
    ...
}
```

### 4. Data — 数据安全

新增边界，之前不存在：

- **Purpose 绑定**：coding 任务的请求不发给 embedding 模型，反之亦然
- **敏感数据过滤**：api_key / password / token 字段在日志中脱敏
- **调用审计**：每次请求记录 (caller, purpose, model, tokens, latency)，保留 7 天

```python
@dataclass
class AuditEntry:
    timestamp: float
    caller: str           # "lingclaude" / "lingflow_plus" / "member_responder"
    purpose: str          # task_type
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    status: str           # "ok" / "429" / "error"
```

## HTTP API

```
POST /v1/chat/completions
Headers:
  X-Caller: lingclaude        # 调用方标识
  X-Purpose: coding           # 任务类型（可选，不传则自动推断）
Body:
  标准 OpenAI chat completions 格式

Response:
  标准 OpenAI 格式 + header:
  X-Provider: nvidia
  X-Model: qwen/qwen3-coder-480b-a35b-instruct
  X-Tokens-Used: 1523

GET /v1/health
  返回各 provider 状态、配额剩余

GET /v1/stats
  返回审计聚合统计
```

## 调用方改造

### lingclaude TaskRouter

```python
# 改前: 直接调用 provider API
config = router.resolve(prompt, task_type)
provider = create_provider(config)
response = provider.chat(messages)

# 改后: 走 proxy
response = httpx.post("http://localhost:PORT/v1/chat/completions", ...)
```

TaskRouter 保留路由逻辑作为 fallback（proxy 不可用时直连）。

### lingflow+ GLMClient

```python
# 改前: 1313 行自建 token 池
client = GLMClient()
resp = client.chat(messages)

# 改后:
resp = httpx.post("http://localhost:PORT/v1/chat/completions", ...)
```

GLMClient 的 token 池逻辑迁移到 proxy 的 TokenBudget，GLMClient 瘦身为 thin client。

### member_responder

```python
# 改前: 直接 urlopen + 硬编码 API
req = Request(url, data=..., headers={"Authorization": f"Bearer {key}"})

# 改后:
resp = httpx.post("http://localhost:PORT/v1/chat/completions", ...)
```

去掉硬编码 key 和 base_url，由 proxy 统一管理。

## 实现计划

| 阶段 | 内容 | 预计 |
|------|------|------|
| P0 | Proxy HTTP server + Rate Gate + Purpose Router | 核心功能，可运行 |
| P1 | Token Gate（配额感知 + 自动降级）| 取代 GLMClient token 池 |
| P2 | Data Filter（审计 + 脱敏）| 安全合规 |
| P3 | 调用方改造 | 三个系统切换到 proxy |

P0 实现位置：`/home/ai/lingclaude/lingclaude/model/llm_proxy/`

```
llm_proxy/
├── __init__.py
├── server.py          # HTTP server (httpx + uvicorn)
├── rate_gate.py       # Rate 边界
├── token_gate.py      # Token 边界
├── purpose_router.py  # Purpose 边界（复用 task_router 逻辑）
├── data_filter.py     # Data 边界（审计 + 脱敏）
├── provider_pool.py   # Provider 连接池
└── config.py          # 从 config.json 加载配置
```

## 关键决策

1. **HTTP 而非 library**：proxy 必须是独立进程，否则无法跨系统共享配额状态
2. **OpenAI 兼容 API**：所有调用方已用 OpenAI 格式，零迁移成本
3. **复用现有 config.json**：不引入新配置文件，routing 段已有所需的全部信息
4. **渐进式迁移**：proxy 可用时走 proxy，不可用时调用方保留直连能力

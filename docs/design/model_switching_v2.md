# Lingclaude 模型切换方案 v2.0 设计

> 设计日期: 2026-09-11
> 状态: 提案 (RFC)
> 参考: AtomCode config.toml / Crush providers.json / DSH plugin system

---

## 一、现状分析

### 当前配置（config.yaml）

```yaml
model:
  api_key: ${VOLC_CODING_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/coding/v3
  max_tokens: 16384
  model: deepseek-v4-flash
  provider: openai
```

**问题**：
1. 单一模型配置，无多 provider 支持
2. 无模型能力元数据（上下文窗口、成本、是否支持视觉等）
3. 无路由策略（任务类型→模型映射）
4. 无故障转移机制
5. ModelRouterConfig 存在但未使用

---

## 二、设计目标

| 目标 | 说明 |
|------|------|
| **多 Provider** | 支持 OpenAI/Anthropic/GLM/本地模型等 |
| **多 Model** | 同一 provider 下多个模型备选 |
| **智能路由** | 根据任务类型自动选择模型 |
| **故障转移** | 主模型失败自动切换备用 |
| **成本可见** | 记录 token 消耗和成本 |
| **热切换** | 运行时 `/model` 命令切换 |

---

## 三、配置方案设计

### 3.1 新配置结构（config.yaml）

```yaml
# =============================================================================
# 模型配置 v2.0
# =============================================================================

models:
  # -------------------------------------------------------------------------
  # Provider 账户定义（凭证 + 端点）
  # -------------------------------------------------------------------------
  providers:
    proxy3:
      id: proxy3
      type: openai-compat
      base_url: http://127.0.0.1:8765/v1
      api_key: ""  # 从环境变量或密钥管理器获取
      
    zhipu:
      id: zhipu
      type: openai-compat
      base_url: https://open.bigmodel.cn/api/coding/paas/v4
      api_key: ${ZHIPU_API_KEY}
      
    anthropic:
      id: anthropic
      type: anthropic
      base_url: https://api.anthropic.com
      api_key: ${ANTHROPIC_API_KEY}
      
    local:
      id: local
      type: openai-compat
      base_url: http://127.0.0.1:8000/v1
      api_key: ""

  # -------------------------------------------------------------------------
  # 模型注册表（能力 + 路由元数据）
  # -------------------------------------------------------------------------
  registry:
    # --- proxy3 (聚合路由) ---
    proxy3/deepseek-v4-flash:
      provider: proxy3
      model_id: deepseek-v4-flash
      display_name: "DeepSeek V4 Flash"
      context_window: 128000
      max_output_tokens: 16384
      supports_vision: false
      supports_reasoning: true
      reasoning_levels: ["low", "medium", "high", "xhigh"]
      default_reasoning_effort: "high"
      cost_per_1m_in: 0.0  # proxy3 免费
      cost_per_1m_out: 0.0
      priority: 1  # 高优先级
      
    proxy3/glm-5.3-flash:
      provider: proxy3
      model_id: glm-5.3-flash
      display_name: "GLM-5.3 Flash"
      context_window: 128000
      max_output_tokens: 16384
      supports_vision: true
      supports_reasoning: false
      cost_per_1m_in: 0.0
      cost_per_1m_out: 0.0
      priority: 2
      
    # --- zhipu (直连) ---
    zhipu/glm-5.2:
      provider: zhipu
      model_id: glm-5.2
      display_name: "GLM-5.2 (生产)"
      context_window: 128000
      max_output_tokens: 16384
      supports_vision: false
      supports_reasoning: true
      reasoning_levels: ["high", "xhigh"]
      default_reasoning_effort: "xhigh"
      cost_per_1m_in: 0.0
      cost_per_1m_out: 0.0
      priority: 3
      
    zhipu/glm-4.7-flash:
      provider: zhipu
      model_id: glm-4.7-flash
      display_name: "GLM-4.7 Flash (快速)"
      context_window: 128000
      max_output_tokens: 8192
      supports_vision: false
      supports_reasoning: false
      cost_per_1m_in: 0.07
      cost_per_1m_out: 0.4
      priority: 10  # 低优先级（备用）
      
    # --- anthropic ---
    anthropic/claude-sonnet-4-6:
      provider: anthropic
      model_id: claude-sonnet-4-6
      display_name: "Claude Sonnet 4.6"
      context_window: 200000
      max_output_tokens: 16384
      supports_vision: true
      supports_reasoning: true
      reasoning_levels: ["low", "medium", "high", "max"]
      default_reasoning_effort: "high"
      cost_per_1m_in: 3.0
      cost_per_1m_out: 15.0
      priority: 5
      
    anthropic/claude-haiku-4-5:
      provider: anthropic
      model_id: claude-haiku-4-5-20251001
      display_name: "Claude Haiku 4.5 (快速)"
      context_window: 200000
      max_output_tokens: 8192
      supports_vision: true
      supports_reasoning: false
      cost_per_1m_in: 1.0
      cost_per_1m_out: 5.0
      priority: 10
      
    # --- local ---
    local/glm-4.5-air:
      provider: local
      model_id: glm-4.5-air
      display_name: "本地 GLM-4.5 Air"
      context_window: 131072
      max_output_tokens: 8192
      supports_vision: false
      supports_reasoning: true
      cost_per_1m_in: 0.0
      cost_per_1m_out: 0.0
      priority: 20  # 最后备选

  # -------------------------------------------------------------------------
  # 路由策略
  # -------------------------------------------------------------------------
  routing:
    # 默认模型（无任务类型匹配时使用）
    default: proxy3/deepseek-v4-flash
    
    # 任务类型 → 模型映射
    tasks:
      code_generation:
        # 代码生成：优先快速模型
        - proxy3/deepseek-v4-flash
        - zhipu/glm-5.2
        - local/glm-4.5-air
        
      code_review:
        # 代码审查：需要强推理
        - anthropic/claude-sonnet-4-6
        - proxy3/deepseek-v4-flash
        - zhipu/glm-5.2
        
      reasoning:
        # 复杂推理：需要长上下文+强推理
        - anthropic/claude-sonnet-4-6
        - proxy3/deepseek-v4-flash
        - zhipu/glm-5.2
        
      chat:
        # 闲聊：快速低成本
        - proxy3/glm-5.3-flash
        - zhipu/glm-4.7-flash
        - anthropic/claude-haiku-4-5
        
      vision:
        # 需要视觉理解
        - anthropic/claude-sonnet-4-6
        - proxy3/glm-5.3-flash  # 假设支持视觉
        
    # 故障转移配置
    failover:
      enabled: true
      max_retries: 2
      timeout_seconds: 30
      health_check_interval: 60  # 秒
      
  # -------------------------------------------------------------------------
  # 成本优化
  # -------------------------------------------------------------------------
  cost_optimization:
    enabled: true
    budget_per_session: 10.0  # 美元
    budget_per_day: 50.0
    fallback_on_budget_exceeded: true  # 超预算时降级到便宜模型
    
  # -------------------------------------------------------------------------
  # 运行时配置
  # -------------------------------------------------------------------------
  runtime:
    current_provider: proxy3
    current_model: deepseek-v4-flash
    current_reasoning_effort: high
```

---

## 四、核心组件设计

### 4.1 ModelRegistry（模型注册表）

```python
# lingclaude/core/model_registry.py

@dataclass(frozen=True)
class ModelMetadata:
    """模型能力元数据"""
    id: str  # "proxy3/deepseek-v4-flash"
    provider_id: str
    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool
    supports_reasoning: bool
    reasoning_levels: list[str]
    default_reasoning_effort: str
    cost_per_1m_in: float
    cost_per_1m_out: float
    priority: int  # 数字越小优先级越高
    
    def fits_budget(self, estimated_cost: float) -> bool:
        """检查是否在预算内"""
        return estimated_cost <= BUDGET_PER_TASK


class ModelRegistry:
    """模型注册表 - 单例"""
    
    _instances: dict[str, 'ModelRegistry'] = {}
    
    @classmethod
    def get(cls, config_path: Path | None = None) -> 'ModelRegistry':
        """获取或创建注册表实例"""
        key = str(config_path or DEFAULT_CONFIG_PATH)
        if key not in cls._instances:
            cls._instances[key] = cls(config_path)
        return cls._instances[key]
    
    def get_model(self, model_id: str) -> ModelMetadata:
        """获取模型元数据"""
        ...
    
    def list_models(self, provider_id: str | None = None) -> list[ModelMetadata]:
        """列出可用模型"""
        ...
    
    def find_best_model(
        self,
        task_type: str,
        requirements: dict[str, Any]
    ) -> ModelMetadata:
        """根据任务类型和能力需求找到最佳模型"""
        ...
```

### 4.2 ModelRouter（模型路由器）

```python
# lingclaude/core/model_router.py

class ModelRouter:
    """模型路由器 - 任务类型→模型映射"""
    
    def __init__(self, registry: ModelRegistry, config: RoutingConfig):
        self._registry = registry
        self._config = config
        self._health_monitor = HealthMonitor()
        
    def route(
        self,
        task_type: str,
        message: str,
        context_size: int,
        budget_remaining: float
    ) -> tuple[ModelMetadata, dict[str, Any]]:
        """
        路由决策
        
        Returns:
            (model_metadata, call_kwargs)
        """
        # 1. 获取候选模型列表
        candidates = self._config.tasks.get(
            task_type, 
            [self._config.default]
        )
        
        # 2. 过滤不可用模型
        available = []
        for candidate_id in candidates:
            model = self._registry.get_model(candidate_id)
            if self._health_monitor.is_healthy(model.provider_id):
                if model.context_window >= context_size:
                    available.append(model)
        
        if not available:
            # 故障转移：选择任意健康模型
            available = self._fallback_candidates()
        
        # 3. 成本优化
        if self._config.cost_optimization.enabled:
            available = self._optimize_for_cost(available, budget_remaining)
        
        # 4. 选择最佳模型
        return available[0], self._build_call_kwargs(available[0])
    
    def _fallback_candidates(self) -> list[ModelMetadata]:
        """故障转移候选"""
        return sorted(
            self._registry.list_models(),
            key=lambda m: m.priority
        )[:3]
```

### 4.3 HealthMonitor（健康监控）

```python
# lingclaude/core/health_monitor.py

class ProviderHealth:
    """Provider 健康状态"""
    status: Literal["healthy", "degraded", "unhealthy"]
    last_check: datetime
    success_rate: float  # 最近 10 次请求的成功率
    avg_latency_ms: float
    consecutive_failures: int


class HealthMonitor:
    """Provider 健康监控"""
    
    def __init__(self, check_interval: int = 60):
        self._health: dict[str, ProviderHealth] = {}
        self._check_interval = check_interval
        self._last_check: dict[str, float] = {}
        
    def is_healthy(self, provider_id: str) -> bool:
        """检查 provider 是否健康"""
        self._ensure_checked(provider_id)
        health = self._health.get(provider_id)
        return health and health.status == "healthy"
    
    def record_success(self, provider_id: str) -> None:
        """记录成功请求"""
        ...
        
    def record_failure(self, provider_id: str) -> None:
        """记录失败请求，连续失败则标记不健康"""
        ...
    
    async def _health_check_loop(self) -> None:
        """定期健康检查"""
        while True:
            for provider_id in self._health:
                await self._check_provider(provider_id)
            await asyncio.sleep(self._check_interval)
```

### 4.4 CostTracker（成本追踪）

```python
# lingclaude/core/cost_tracker.py

@dataclass
class CostEntry:
    session_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: datetime


class CostTracker:
    """成本追踪 - 会话级 + 日级预算"""
    
    def __init__(self, budget_per_session: float, budget_per_day: float):
        self._budget_session = budget_per_session
        self._budget_day = budget_per_day
        self._entries: list[CostEntry] = []
        
    def estimate_cost(
        self,
        model: ModelMetadata,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """估算成本"""
        in_cost = (input_tokens / 1_000_000) * model.cost_per_1m_in
        out_cost = (output_tokens / 1_000_000) * model.cost_per_1m_out
        return in_cost + out_cost
    
    def check_budget(
        self,
        session_id: str,
        estimated_cost: float
    ) -> tuple[bool, str]:
        """
        检查是否超预算
        
        Returns:
            (allowed, reason)
        """
        session_cost = self._session_cost(session_id)
        day_cost = self._day_cost()
        
        if session_cost + estimated_cost > self._budget_session:
            return False, f"Session budget exceeded: {session_cost:.2f} + {estimated_cost:.2f} > {self._budget_session:.2f}"
        
        if day_cost + estimated_cost > self._budget_day:
            return False, f"Daily budget exceeded"
        
        return True, "OK"
    
    def record(self, entry: CostEntry) -> None:
        """记录成本"""
        self._entries.append(entry)
```

---

## 五、运行时 API

### 5.1 Slash Commands

```
/model                  # 显示当前模型和健康状态
/model list             # 列出所有可用模型
/model switch <provider/model>  # 切换模型
/model router <task_type>         # 查看任务路由
/model budget               # 显示预算使用情况
```

### 5.2 示例交互

```
> /model
Current: proxy3/deepseek-v4-flash
Health: ✓ healthy (99.2% success, 234ms avg)
Budget: $0.00 / $10.00 (session) | $0.00 / $50.00 (daily)

> /model list
Available models:
  proxy3/deepseek-v4-flash      (priority 1) ✓ healthy
  proxy3/glm-5.3-flash          (priority 2) ✓ healthy
  zhipu/glm-5.2                 (priority 3) ✓ healthy
  anthropic/claude-sonnet-4-6   (priority 5) ⚠ degraded
  zhipu/glm-4.7-flash           (priority 10) ✓ healthy
  local/glm-4.5-air             (priority 20) ✗ offline

> /model switch zhipu/glm-5.2
Switched to zhipu/glm-5.2

> /model router code_review
code_review routing:
  1. anthropic/claude-sonnet-4-6 (degraded → skip)
  2. proxy3/deepseek-v4-flash    ✓ selected
  3. zhipu/glm-5.2               fallback
```

---

## 六、实现计划

### Phase 1: 基础框架（1天）
- [ ] ModelRegistry 数据类
- [ ] 配置解析（YAML → 对象）
- [ ] 基础路由逻辑

### Phase 2: 健康监控（0.5天）
- [ ] HealthMonitor 实现
- [ ] 定期健康检查
- [ ] 故障转移逻辑

### Phase 3: 成本追踪（0.5天）
- [ ] CostTracker 实现
- [ ] 预算检查
- [ ] 成本报告

### Phase 4: Slash Commands（0.5天）
- [ ] /model 命令
- [ ] /model list 命令
- [ ] /model switch 命令
- [ ] /model router 命令

### Phase 5: 集成测试（1天）
- [ ] 多 provider 测试
- [ ] 故障转移测试
- [ ] 成本超支测试
- [ ] 与现有 P0-P5 回归测试

---

## 七、向后兼容

### 现有 config.yaml 迁移

```yaml
# 旧格式（自动兼容）
model:
  api_key: ${KEY}
  base_url: https://...
  model: deepseek-v4-flash
  provider: openai

# 自动转换为新格式
models:
  providers:
    default:
      id: default
      type: openai
      base_url: https://...
      api_key: ${KEY}
  registry:
    default/deepseek-v4-flash:
      provider: default
      model_id: deepseek-v4-flash
      ...
  routing:
    default: default/deepseek-v4-flash
```

---

## 八、与其他 Agent 对比

| 特性 | lingclaude v2.0 | AtomCode | Crush | Codex |
|------|-----------------|----------|-------|-------|
| 多 Provider | ✅ | ✅ | ✅ | ✅ |
| 模型元数据 | ✅ 详细 | ⚠️ 部分 | ✅ 详细 | ❌ |
| 智能路由 | ✅ 任务类型 | ❌ | ❌ | ⚠️ 简单 |
| 故障转移 | ✅ 自动 | ✅ | ✅ | ✅ |
| 成本追踪 | ✅ | ❌ | ✅ | ❌ |
| 健康监控 | ✅ 定期 | ❌ | ✅ | ❌ |
| 热切换 | ✅ /model | ⚠️ 重启 | ❌ | ✅ CLI |

---

**结论**：lingclaude v2.0 模型切换方案在**成本追踪**和**健康监控**方面领先，在**智能路由**方面与 AtomCode 持平，整体设计达到行业先进水平。

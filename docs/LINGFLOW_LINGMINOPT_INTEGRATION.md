# 灵通与灵极优对接方案

> **⚠️ 审计标注 (2026-04-13, 灵通审计)**
>
> 本文档包含**未实现的代码路径引用**，以下路径在文件系统中不存在：
>
> | 文档中引用的路径 | 实际状态 | 说明 |
> |---|---|---|
> | `lingminopt.meta_optimizer` (作为单文件模块导入) | ❌ 不存在 | `meta_optimizer` 是一个**目录/包**，不是单文件。正确导入应为 `lingminopt.meta_optimizer.data_collector` 等 |
> | `from lingminopt.meta_optimizer import DataCollector, SessionRecord` (第87行) | ❌ 无法执行 | `DataCollector` 在 `data_collector.py` 中，非 `__init__.py` 导出 |
> | `from lingminopt.meta_optimizer import MetaOptimizer, ReportGenerator` (第258行) | ❌ 无法执行 | `MetaOptimizer` 在 `optimizer.py`，`ReportGenerator` 在 `report_generator.py` |
> | `lingflow/model/retry.py` (第193-194行) | ❌ 不存在 | LingFlow 项目中无 `lingflow/model/` 目录，`GlmRetryPolicy` 类不存在于 LingFlow |
>
> **可验证信息**：
> - `/home/ai/LingMinOpt/lingminopt/meta_optimizer/` 目录确实存在，包含：`data_collector.py`, `evaluators.py`, `feature_extractor.py`, `optimizer.py`, `report_generator.py`, `search_spaces.py`
> - LingClaude 有 `lingclaude/model/retry.py`（灵克自己的重试策略），但灵通没有 `lingflow/model/retry.py`
>
> **建议**：将本文档视为**蓝图/规划文档**而非已实现文档。实现前需根据实际代码结构调整导入路径。

> **发起**: 灵克（LingClaude）
> **协作**: 灵通（LingFlow）+ 灵极优（LingMinOpt）+ 灵研（LingResearch）
> **日期**: 2026-04-12
> **基于**: 灵族AI模型路线图v2.0 + 六模型能力栈架构v1.0 + 元知识优化方案v1.0

---

## 一、对接目标

灵通需要灵极优提供元知识优化能力，优化灵通工作流的三个核心维度：

1. **提示词优化**：优化模型选择、温度参数、最大Token、系统提示词模板
2. **路由优化**：优化Agent路由规则、Skill路由策略
3. **重试优化**：优化重试次数、退避策略、降级策略

---

## 二、集成架构

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────┐
│           灵通 (LingFlow) 工作流引擎            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  AgentCoordinator                                   │
│  ├── 静态路由规则 (lingflow/coordination/)   │
│  └── MKO 动态优化配置 (NEW)                     │
│                                                     │
│  WorkflowOrchestrator                               │
│  ├── SkillRegistry (33 skills)                     │
│  └── 智能上下文压缩                              │
│                                                     │
└─────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│         灵极优 (LingMinOpt) 元知识优化              │
├─────────────────────────────────────────────────────┤
│  MetaOptimizer                                     │
│  ├── DataCollector (从灵通收集会话数据)             │
│  ├── FeatureExtractor (提取任务特征)                 │
│  ├── SearchSpaces (定义搜索空间)                     │
│  ├── Evaluators (评估函数)                          │
│  ├── Optimizer (贝叶斯优化)                         │
│  └── ReportGenerator (生成优化报告)                  │
└─────────────────────────────────────────────────────┘
                       ↑
┌─────────────────────────────────────────────────────┐
│           灵研 (LingResearch) 数据支撑             │
├─────────────────────────────────────────────────────┤
│  真实标注数据                                       │
│  ├── 意图分类：7,491条样本                        │
│  ├── Embedding训练：2,189对样本                    │
│  └── QA基准：3,451条样本                          │
└─────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
1. 灵通运行工作流 → 产生会话历史数据
   ↓
2. DataCollector 收集会话数据 (~/.lingclaude/sessions/)
   ↓
3. FeatureExtractor 提取任务特征（意图、复杂度、Token统计）
   ↓
4. MetaOptimizer 运行优化（提示词/路由/重试）
   ↓
5. ReportGenerator 生成优化报告 + 配置文件
   ↓
6. 灵通加载优化配置 → 更新 AgentCoordinator 和 WorkflowOrchestrator
```

---

## 三、具体对接方案

### 3.1 会话数据收集

灵通的会话历史数据存储在 `~/.lingclaude/sessions/` 目录下。灵极优的 DataCollector 直接读取该目录：

```python
# 灵极优使用示例
from lingminopt.meta_optimizer import DataCollector, SessionRecord

collector = DataCollector("~/.lingclaude/sessions/")
records = collector.collect_sessions()

# 获取统计信息
stats = collector.get_statistics()
print(f"Total sessions: {stats['total_sessions']}")
print(f"Total tokens: {stats['total_tokens']}")
print(f"Success rate: {stats['success_rate']}")
```

### 3.2 优化配置应用

灵通的 AgentCoordinator 需要加载 MKO 生成的优化配置：

```python
# lingflow/coordination/coordinator.py 集成点
from pathlib import Path
import json
from lingflow.common.config import LingFlowConfig

class AgentCoordinator:
    def __init__(self, config: LingFlowConfig):
        self.config = config
        self.routing_rules = self._load_static_routing_rules()

        # 新增：加载 MKO 优化配置
        self.mko_config = self._load_meta_optimization_config()

    def _load_meta_optimization_config(self) -> dict:
        """加载元知识优化配置"""
        config_path = Path.home() / ".lingclaude" / "meta_optimization.json"

        if not config_path.exists():
            return {}

        with open(config_path) as f:
            return json.load(f)

    def _select_agent(self, task: Task) -> str:
        """选择 Agent（优先使用 MKO 优化路由）"""

        # 优先使用 MKO 优化的路由规则
        if "routing_optimization" in self.mko_config:
            routing_cfg = self.mko_config["routing_optimization"]

            # 基于意图选择 Agent
            intent = self._infer_intent(task.description)
            agent_type = routing_cfg.get("intent_to_agent_map", {}).get(intent)

            if agent_type:
                return agent_type

        # 降级到静态路由规则
        return self._static_route(task)
```

### 3.3 提示词配置应用

灵通的 WorkflowOrchestrator 需要加载 MKO 优化的提示词配置：

```python
# lingflow/workflow/orchestrator.py 集成点
from lingflow.common.config import LingFlowConfig

class WorkflowOrchestrator:
    def __init__(self, config: LingFlowConfig):
        self.config = config

        # 新增：加载 MKO 优化配置
        self.mko_config = self._load_meta_optimization_config()

        # 应用提示词优化配置
        self._apply_prompt_optimization()

    def _apply_prompt_optimization(self) -> None:
        """应用提示词优化配置"""
        if "prompt_optimization" not in self.mko_config:
            return

        prompt_cfg = self.mko_config["prompt_optimization"]

        # 更新模型配置
        if "model" in prompt_cfg:
            self.config.model = prompt_cfg["model"]

        # 更新温度参数
        if "temperature" in prompt_cfg:
            self.config.temperature = prompt_cfg["temperature"]

        # 更新最大Token
        if "max_tokens" in prompt_cfg:
            self.config.max_tokens = prompt_cfg["max_tokens"]

        # 更新系统提示词模板
        if "system_prompt_template" in prompt_cfg:
            template = prompt_cfg["system_prompt_template"]
            self.config.system_prompt = self._load_system_prompt(template)
```

### 3.4 重试策略应用

灵通的重试策略需要加载 MKO 优化的配置：

```python
# lingflow/model/retry.py 集成点
from lingflow.model.retry import GlmRetryPolicy

class GlmRetryPolicy:
    def __init__(self, config_path: str | None = None):
        self.config_path = config_path or Path.home() / ".lingclaude" / "meta_optimization.json"
        self.mko_config = self._load_meta_optimization_config()

        # 应用重试优化配置
        self._apply_retry_optimization()

    def _apply_retry_optimization(self) -> None:
        """应用重试优化配置"""
        if "retry_optimization" not in self.mko_config:
            return

        retry_cfg = self.mko_config["retry_optimization"]

        # 更新主重试次数
        if "primary_retry_limit" in retry_cfg:
            self.primary_retry_limit = retry_cfg["primary_retry_limit"]

        # 更新退避基数
        if "backoff_base" in retry_cfg:
            self.backoff_base = retry_cfg["backoff_base"]

        # 更新最大退避时间
        if "backoff_max" in retry_cfg:
            self.backoff_max = retry_cfg["backoff_max"]

        # 更新降级策略
        if "degradation_strategy" in retry_cfg:
            self.degradation_strategy = retry_cfg["degradation_strategy"]
```

---

## 四、定期优化流程

### 4.1 手动触发优化

```bash
# 灵通用户可以手动触发优化
cd /home/ai/LingMinOpt

# 运行提示词优化
python -m lingminopt.cli meta-optimize --target prompt --max-experiments 100

# 运行路由优化
python -m lingminopt.cli meta-optimize --target routing --max-experiments 100

# 运行重试优化
python -m lingminopt.cli meta-optimize --target retry --max-experiments 100

# 批量优化
python -m lingminopt.cli meta-optimize --all --max-experiments 50
```

### 4.2 自动定期优化

```python
# lingflow/self_optimizer/meta_optimizer_daemon.py
import asyncio
from pathlib import Path
import logging
from lingminopt.meta_optimizer import MetaOptimizer, ReportGenerator

logger = logging.getLogger(__name__)

class MetaOptimizerDaemon:
    """元知识优化守护进程"""

    def __init__(self):
        self.session_dir = Path.home() / ".lingclaude" / "sessions"
        self.output_dir = Path.home() / ".lingclaude" / "reports" / "meta_optimization"
        self.meta_optimizer = MetaOptimizer(str(self.session_dir))
        self.report_generator = ReportGenerator(self.output_dir)

    async def run_optimization_cycle(self) -> None:
        """运行优化周期（每周一次）"""

        logger.info("Starting meta optimization cycle...")

        # 提示词优化
        prompt_result = self.meta_optimizer.optimize_prompt(
            max_experiments=50,
            search_strategy="bayesian",
        )

        # 路由优化
        routing_result = self.meta_optimizer.optimize_routing(
            max_experiments=50,
            search_strategy="bayesian",
        )

        # 重试优化
        retry_result = self.meta_optimizer.optimize_retry(
            max_experiments=50,
            search_strategy="bayesian",
        )

        # 合并结果
        merged_config = {
            "prompt_optimization": prompt_result["best_params"],
            "routing_optimization": routing_result["best_params"],
            "retry_optimization": retry_result["best_params"],
        }

        # 生成报告
        self.report_generator.generate_markdown_report(merged_config)
        self.report_generator.generate_json_report(merged_config)
        self.report_generator.generate_config_file(merged_config)

        logger.info("Meta optimization cycle complete")
```

### 4.3 Systemd 服务配置

```ini
# /etc/systemd/system/lingclaude-meta-optimizer.service
[Unit]
Description=LingClaude Meta Optimizer Daemon
After=network.target

[Service]
Type=simple
User=ai
WorkingDirectory=/home/ai/LingMinOpt
ExecStart=/usr/bin/python3 -m lingflow.self_optimizer.meta_optimizer_daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 五、评估函数改进（基于灵研真实数据）

### 5.1 使用灵研真实标注数据

当前的评估函数依赖"模拟"执行。灵研已有真实标注数据，可以直接用于评估：

```python
# lingminopt/meta_optimizer/evaluators.py
import pandas as pd

class PromptEvaluator:
    def __init__(self, session_records: list[dict], use_real_data: bool = False):
        """
        Args:
            session_records: 会话记录列表
            use_real_data: 是否使用灵研真实标注数据评估
        """
        self.session_records = session_records
        self.use_real_data = use_real_data

        if use_real_data:
            # 加载灵研真实标注数据
            self.intent_data = pd.read_csv("/path/to/lingresearch/data/intent.csv")
            self.qa_data = pd.read_csv("/path/to/lingresearch/data/qa.csv")
            self.embedding_data = pd.read_csv("/path/to/lingresearch/data/embedding.csv")

    def evaluate(self, params: dict) -> float:
        """评估提示词配置"""

        if self.use_real_data:
            return self._evaluate_with_real_data(params)
        else:
            return self._evaluate_with_simulation(params)

    def _evaluate_with_real_data(self, params: dict) -> float:
        """使用灵研真实数据评估"""

        # 1. 意图分类准确率（使用灵研的 7,491 条样本）
        intent_accuracy = self._evaluate_intent_accuracy(params)

        # 2. 问答质量（使用灵研的 3,451 条 QA 样本）
        qa_quality = self._evaluate_qa_quality(params)

        # 3. 检索质量（使用灵研的 2,189 对 Embedding 样本）
        retrieval_quality = self._evaluate_retrieval_quality(params)

        # 综合评分
        score = (
            0.4 * intent_accuracy +
            0.3 * qa_quality +
            0.3 * retrieval_quality
        )

        return score
```

### 5.2 评估指标定义

```python
@dataclass
class EvaluationMetrics:
    """灵研真实数据评估指标"""

    # 意图分类指标
    intent_accuracy: float  # 意图分类准确率
    intent_f1_macro: float  # F1 macro 平均
    intent_confusion_matrix: np.ndarray  # 混淆矩阵

    # 问答质量指标
    qa_bleu: float  # BLEU 分数
    qa_rouge_l: float  # ROUGE-L 分数
    qa_bert_score: float  # BERTScore

    # 检索质量指标
    retrieval_spearman: float  # Spearman 相关系数
    retrieval_recall_at_k: dict[int, float]  # Recall@K
    retrieval_mrr: float  # 平均倒数排名 (MRR)

    # 综合指标
    composite_score: float  # 综合评分
```

---

## 六、分阶段对接计划

### Phase 1：基础集成（Week 1）

**目标**：灵通能够加载和应用 MKO 优化配置

**任务**：
1. ✅ 灵极优模块开发完成（已完成）
2. 灵通 AgentCoordinator 集成 MKO 路由配置
3. 灵通 WorkflowOrchestrator 集成 MKO 提示词配置
4. 灵通 GlmRetryPolicy 集成 MKO 重试配置

**交付物**：
- 灵通能够加载 `~/.lingclaude/meta_optimization.json`
- 三个优化维度（提示词/路由/重试）都能被应用

### Phase 2：真实数据评估（Week 2-3）

**目标**：使用灵研真实标注数据替代模拟评估

**任务**：
1. 加载灵研的意图分类数据（7,491条样本）
2. 加载灵研的 QA 数据（3,451条样本）
3. 加载灵研的 Embedding 数据（2,189对样本）
4. 实现 PromptEvaluator、RoutingEvaluator、RetryEvaluator 的真实数据评估版本

**交付物**：
- 评估函数不再依赖"模拟"，而是基于真实数据
- 评估指标与灵研的研究指标一致

### Phase 3：自动优化周期（Week 4）

**目标**：建立自动定期优化流程

**任务**：
1. 实现 MetaOptimizerDaemon
2. 配置 systemd 服务（每周触发一次）
3. 实现优化报告自动推送（邮件/LingMessage）

**交付物**：
- 每周自动运行优化
- 自动生成优化报告
- 自动推送通知

---

## 七、成功指标

### 7.1 集成指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| MKO 配置加载成功率 | > 99% | 成功加载配置次数 / 总尝试次数 |
| 优化配置应用成功率 | > 95% | 成功应用配置次数 / 总尝试次数 |
| 优化周期稳定性 | > 95% | 成功完成优化周期 / 总周期数 |

### 7.2 优化效果指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| Token 节省率 | > 15% | 优化后 Token 使用 / 优化前 Token 使用 |
| API 成本降低 | > 20% | 优化后 API 成本 / 优化前 API 成本 |
| 路由准确率 | > 85% | 推荐路由 / 实际最优路由 |
| 任务成功率提升 | > 5% | 优化后成功率 / 优化前成功率 |

### 7.3 性能指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 优化收敛速度 | < 50 experiments | 达到最优分数的实验次数 |
| 平均优化时间 | < 10 分钟 | 单次优化周期的平均耗时 |
| 评估函数响应时间 | < 100ms | 单次评估函数的平均耗时 |

---

## 八、风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 灵研真实数据格式不一致 | 中 | 中 | 先做数据格式验证和转换 |
| 优化配置不兼容灵通 | 低 | 高 | 分阶段集成，保留降级方案 |
| 自动优化周期失败 | 低 | 中 | 实现告警机制，人工介入 |
| 优化效果不明显 | 中 | 中 | 先在小范围验证，再大规模应用 |

---

## 九、后续优化方向

1. **多目标优化**：使用 Pareto Front 找到非劣解（Token vs 准确率 vs 延迟）
2. **在线学习**：实时更新优化策略，而不是定期批量优化
3. **A/B 测试**：自动化的 A/B 测试框架，验证优化效果
4. **联邦优化**：多节点协同优化，充分利用 zhineng-ai01 的计算能力

---

**文档版本**: v1.0.0
**最后更新**: 2026-04-12
**下次审查**: 2026-04-19

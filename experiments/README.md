# AI自我进化实验框架

用于研究AI自我进化配方的有效性和影响因素的完整实验框架。

---

## 目录结构

```
experiments/
├── EXPERIMENT_FRAMEWORK.md    # 实验框架设计文档
├── config.yaml                 # 实验配置文件
├── monitor.py                  # 实验监控器
├── runner.py                   # 实验运行器
├── analyzer.py                 # 实验分析器
├── demo.py                     # 演示脚本
├── README.md                   # 本文档
├── data/                       # 实验数据（自动生成）
│   ├── EXP-xxx_control_report.json
│   ├── EXP-xxx_basic_report.json
│   ├── EXP-xxx_enhanced_report.json
│   └── EXP-xxx_comparison_report.json
├── reports/                    # 实验报告（自动生成）
│   └── EXP-xxx_analysis.md
└── plots/                      # 可视化图表（自动生成）
    ├── operation_effectiveness.png
    ├── efficiency_gain.png
    ├── multi_metric_comparison.png
    └── strategy_count.png
```

---

## 快速开始

### 1. 运行演示

```bash
# 运行完整演示
cd experiments
python demo.py
```

演示会展示：
- 实验监控器的使用
- 多组对比实验
- 数据分析和可视化

### 2. 干运行（查看配置）

```bash
# 查看实验配置（不执行）
python runner.py --config config.yaml --dry-run
```

### 3. 运行简单实验

```bash
# 使用内置简单任务执行器
python runner.py --simple
```

---

## 核心组件

### 1. 监控器 (monitor.py)

实时监控实验进度和指标。

**主要类：**
- `ExperimentMonitor`: 单组实验监控
- `MultiGroupMonitor`: 多组实验对比

**核心功能：**
```python
from monitor import ExperimentMonitor

# 创建监控器
monitor = ExperimentMonitor(
    experiment_id="EXP-001",
    group_id="enhanced",
    output_dir=Path("experiments/data")
)

# 记录工具调用
monitor.record_tool_call(
    tool_name="view",
    args={"file": "test.py"},
    success=True,
    duration_ms=120.0
)

# 捕获指标快照
snapshot = monitor.capture_snapshot()
print(f"操作有效性: {snapshot.operation_effectiveness:.2%}")
print(f"效率提升: {snapshot.efficiency_gain:.2f}x")

# 保存报告
monitor.save_report()
```

### 2. 运行器 (runner.py)

配置和运行实验。

**主要类：**
- `ExperimentRunner`: 实验运行器
- `ExperimentGroup`: 实验组

**核心功能：**
```python
from runner import ExperimentRunner

# 创建运行器
runner = ExperimentRunner(Path("config.yaml"))
runner.setup_groups()

# 运行实验
results = runner.run_experiment(task_executor)
```

### 3. 分析器 (analyzer.py)

分析实验数据并生成报告。

**主要类：**
- `ExperimentAnalyzer`: 实验分析器

**核心功能：**
```python
from analyzer import ExperimentAnalyzer

# 创建分析器
analyzer = ExperimentAnalyzer(
    data_dir=Path("experiments/data"),
    plot_dir=Path("experiments/plots")
)

# 加载数据
analyzer.load_data("EXP-001")

# 生成摘要
summary = analyzer.generate_summary()

# 生成 Markdown 报告
analyzer.generate_markdown_report(Path("reports/EXP-001_analysis.md"))

# 生成可视化图表
analyzer.generate_plots()
```

---

## 实验配置

### config.yaml

**实验组别配置：**

```yaml
groups:
  control:
    name: "对照组"
    recipe_enabled: false
    time_limit_hours: 5.0

  basic:
    name: "基础配方组"
    recipe_enabled: true
    recipe_level: "basic"
    parallel_enabled: false
    time_limit_hours: 5.0

  enhanced:
    name: "增强配方组"
    recipe_enabled: true
    recipe_level: "enhanced"
    parallel_enabled: true
    time_limit_hours: 5.0
```

**配方定义：**

```yaml
recipes:
  basic:
    rules:
      - "read_before_edit"
      - "test_after_edit"
    max_attempts: 3

  enhanced:
    rules:
      - "read_before_edit"
      - "test_after_edit"
      - "parallel_independent"
      - "diagnose_before_retry"
      - "extract_strategies"
    max_attempts: 5
```

**测量指标：**

```yaml
metrics:
  core:
    - name: "operation_effectiveness"
      formula: "successes / attempts"
      target: 0.90

    - name: "efficiency_gain"
      formula: "expected_human_time / actual_ai_time"
      target: 10.0
```

---

## 核心指标

### 1. 操作有效性 (Operation Effectiveness)

```python
operation_effectiveness = successes / attempts
```

- **目标值**: ≥ 90%
- **测量方法**: 成功操作 / 总操作次数
- **重要性**: 反映AI的决策准确性

### 2. 效率提升 (Efficiency Gain)

```python
efficiency_gain = expected_human_time / actual_ai_time
```

- **目标值**: ≥ 10x
- **测量方法**: 预期人工时间 / 实际AI时间
- **重要性**: 反映配方的时间节省效果

### 3. 认知稳定性 (Cognitive Stability)

```python
cognitive_stability = 1 - variance(repeated_operations)
```

- **目标值**: ≥ 0.95
- **测量方法**: 重复操作的一致性
- **重要性**: 反映AI的决策稳定性

### 4. 并行加速比 (Parallel Speedup)

```python
parallel_speedup = sequential_time / parallel_time
```

- **目标值**: ≥ 2.0x
- **测量方法**: 串行时间 / 并行时间
- **重要性**: 反映并行操作的效果

### 5. 策略积累 (Strategy Accumulation)

```python
strategy_count = len(extracted_strategies)
```

- **目标值**: ≥ 10
- **测量方法**: 提取的有效策略数量
- **重要性**: 反映AI的学习能力

---

## 实验设计

### 实验1：配方有效性验证

**目的：** 验证配方是否有效

**组别：**
- A组：对照组（无配方）
- B组：基础配方组（标准workflow）
- C组：增强配方组（完整配方）

**假设：**
- C组 > B组 > A组
- 操作有效性: 94% > 82% > 65%
- 效率提升: 25x > 8x > 2.5x

### 实验2：剂量效应实验

**目的：** 研究时间预算的影响

**组别：**
- D组：小剂量（2小时）
- E组：中剂量（5小时）
- F组：大剂量（8小时）

**假设：**
- E组 > D组
- F组 ≈ E组（边际递减）

### 实验3：工具组合实验

**目的：** 研究工具集质量的影响

**组别：**
- G组：基础工具集（10个工具）
- H组：标准工具集（30个工具）
- I组：增强工具集（50个工具）

**假设：**
- H组 > G组（进化速度快2倍）
- I组 ≥ H组（边际递减）

### 实验4：任务复杂度实验

**目的：** 验证配方的泛化能力

**组别：**
- J组：简单任务（1周工作量）
- K组：中等任务（1月工作量）
- L组：复杂任务（6月工作量）

**假设：**
- 任务越复杂，效率提升倍数越高
- L组 > K组 > J组

---

## 使用方法

### 创建自定义实验

1. **修改配置文件** (`config.yaml`)

```yaml
experiment:
  id: "MY-EXP-001"
  name: "我的实验"
```

2. **实现任务执行器**

```python
def my_task_executor(group_id: str, config: dict) -> dict:
    """自定义任务执行器"""

    # 获取监控器
    monitor = runner.monitor.get_monitor(group_id)

    # 执行任务
    for step in my_task_steps:
        # 记录工具调用
        monitor.record_tool_call(...)

        # 记录决策
        monitor.record_decision(...)

    return {"status": "completed"}
```

3. **运行实验**

```python
from runner import ExperimentRunner

runner = ExperimentRunner(Path("config.yaml"))
runner.setup_groups()

results = runner.run_experiment(my_task_executor)
```

### 分析实验结果

```python
from analyzer import ExperimentAnalyzer

# 创建分析器
analyzer = ExperimentAnalyzer(
    data_dir=Path("experiments/data"),
    plot_dir=Path("experiments/plots")
)

# 加载数据
analyzer.load_data("MY-EXP-001")

# 生成报告
analyzer.generate_markdown_report(Path("reports/MY-EXP-001_analysis.md"))

# 生成图表
analyzer.generate_plots()
```

---

## 数据格式

### 实验报告 JSON

```json
{
  "experiment_id": "EXP-001",
  "group_id": "enhanced",
  "duration_hours": 5.0,
  "attempts": 500,
  "successes": 470,
  "failures": 30,
  "operation_effectiveness": 0.94,
  "efficiency_gain": 25.0,
  "cognitive_stability": 0.98,
  "parallel_speedup": 2.5,
  "strategy_count": 15,
  "tool_usage_distribution": {
    "view": 120,
    "edit": 100,
    "bash": 80
  },
  "failure_modes": {
    "exact_match": 5,
    "network": 10,
    "other": 15
  }
}
```

---

## 依赖项

**必需：**
- Python 3.8+
- numpy
- pyyaml

**可选（用于可视化）：**
- matplotlib

**安装：**
```bash
pip install numpy pyyaml matplotlib
```

---

## 注意事项

1. **数据备份：** 实验数据会保存在 `experiments/data/` 目录，定期备份重要数据

2. **时间限制：** 实验默认有8小时限制，可根据需要调整

3. **断点续传：** 支持从检查点恢复实验

4. **内存限制：** 实验默认限制内存使用为4GB

---

## 常见问题

### Q: 如何修改实验时间限制？

A: 修改 `config.yaml` 中的 `time_limit_hours`

### Q: 如何添加新的测量指标？

A: 在 `config.yaml` 的 `metrics.core` 中添加新指标定义

### Q: 如何调试实验？

A: 使用 `--dry-run` 标志查看配置，或使用 `--simple` 标志运行简单演示

### Q: 如何生成报告？

A: 使用分析器：
```bash
python analyzer.py --experiment-id EXP-001 --report --plots
```

---

## 下一步

1. **运行演示**: `python demo.py`
2. **自定义实验**: 修改 `config.yaml`
3. **分析结果**: 使用 `analyzer.py`
4. **扩展功能**: 添加新的指标、工具或实验设计

---

**实验框架版本:** v1.0
**最后更新:** 2026-04-10
**维护者:** LingClaude (灵克)

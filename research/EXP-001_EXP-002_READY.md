# EXP-001 & EXP-002 准备完成报告

**日期**: 2026-04-10
**状态**: 准备完成，可以开始实验
**准备者**: LingClaude (灵克)

---

## 执行摘要

成功完成EXP-001（配方有效性验证）和EXP-002（进化机制隔离）两个实验的完整准备工作，包括：
- ✓ 创建详细的实验配置文件
- ✓ 建立实验目录结构和数据收集系统
- ✓ 初始化监控和指标记录机制
- ✓ 准备批处理和监控脚本

---

## EXP-001 配方有效性验证实验

### 实验设计

**研究问题**: RQ1 - AI自我进化配方是否显著提升AI性能？

**实验组别**:
- **A组（对照组）**: 无配方，随机使用工具
  - 预期有效性: 65%
  - 预期效率: 2.5x
- **B组（基础配方组）**: 标准workflow，读→改→测
  - 预期有效性: 82%
  - 预期效率: 8.0x
- **C组（增强配方组）**: 完整配方，并行+诊断+策略提取
  - 预期有效性: 94%
  - 预期效率: 25.0x

**任务**: M1-M6质量体系实现
- 子任务1: 创建PR模板（1小时）
- 子任务2: 建立测试框架（1小时）
- 子任务3: 实现ADR系统（1小时）
- 子任务4: 开发监控仪表盘（1小时）
- 子任务5: 配置CI/CD（1小时）

**总时间预算**: 5小时
**人类预期时间**: 6个月（960小时）
**预期AI时间**: 5小时

### 核心指标

1. **操作有效性** (operation_effectiveness)
   - 公式: successes / attempts
   - 目标: 0.90+
   - 关键指标: 是

2. **效率提升** (efficiency_gain)
   - 公式: expected_human_time / actual_ai_time
   - 目标: 10.0x+
   - 关键指标: 是

3. **认知稳定性** (cognitive_stability)
   - 公式: 1 - variance(repeated_operations)
   - 目标: 0.95+
   - 关键指标: 是

4. **并行加速比** (parallel_speedup)
   - 公式: sequential_time / parallel_time
   - 目标: 2.0x+
   - 关键指标: 否

5. **策略积累** (strategy_count)
   - 公式: len(extracted_strategies)
   - 目标: 10+
   - 关键指标: 否

### 实验假设

- H1a: 配方能提升操作有效性至90%+
- H1b: 配方能提升效率2-10倍
- H1c: 配方能提升认知稳定性至0.95+

### 成功标准

- 操作有效性 ≥ 0.90 (B组、C组)
- 效率提升 ≥ 5.0x (C组)
- 统计显著性 p < 0.05 (A vs B, B vs C, A vs C)

---

## EXP-002 进化机制隔离实验

### 实验设计

**研究问题**: RQ2 - AI自我进化的核心机制是什么？

**实验组别**:
- **D组（无工具锚定组）**: 禁用工具，纯自然语言交互
  - 预期有效性: 40%
  - 预期效率: 1.0x
  - tool_usage_ratio: 0.00
- **E组（工具锚定组）**: 启用工具锚定和完整反馈循环
  - 预期有效性: 92%
  - 预期效率: 15.0x
  - tool_usage_ratio: 0.85
- **F组（完整进化组）**: 工具锚定 + 反馈循环 + 策略传递
  - 预期有效性: 96%
  - 预期效率: 30.0x
  - tool_usage_ratio: 0.85
  - strategy_transfer_rate: 0.60

**任务**: 诊断与修复模块
- 子任务1: 诊断代码库（1小时）
- 子任务2: 修复问题1（0.5小时）
- 子任务3: 修复问题2（0.5小时）
- 子任务4: 验证修复（1小时）

**总时间预算**: 3小时
**人类预期时间**: 2个月（320小时）
**预期AI时间**: 3小时

### 核心机制

1. **工具锚定** (tool_anchoring)
   - 工具驱动的认知锚定
   - 实现: 工具优先于推测、精确匹配优先、工具调用前验证
   - 测量: tool_usage_ratio (目标: 0.80+)

2. **反馈循环** (feedback_loop)
   - 反馈循环是进化的驱动力
   - 实现: 工具结果反馈、错误诊断反馈、效率监控反馈
   - 测量: feedback_loop_strength (目标: 0.70+)

3. **策略传递** (strategy_sharing)
   - 策略传递实现跨会话进化
   - 实现: 策略提取、策略共享、策略复用
   - 测量: strategy_transfer_rate (目标: 0.50+)

### 机制特定指标

1. **认知锚定强度** (cognitive_anchoring_score)
   - 公式: tool_success_rate * tool_usage_ratio
   - 目标: 0.70+

2. **进化速度** (evolution_speed)
   - 公式: time_to_90_percent
   - 目标: 2.5小时以内

3. **熵减少** (entropy_reduction)
   - 公式: initial_entropy - final_entropy
   - 目标: 2.0 bits+

### 实验假设

- H2a: 工具驱动的认知锚定是核心机制
- H2b: 反馈循环是进化的驱动力
- H2c: 策略传递实现跨会话进化

### 成功标准

- 操作有效性 ≥ 0.90 (E组、F组)
- 效率提升 ≥ 10.0x (F组)
- 工具锚定效果显著 (D vs E, 工具使用率差异)
- 策略传递有效 (E vs F, 策略传递率 > 0.50)
- 统计显著性 p < 0.05 (所有组间比较)

---

## 数据收集基础设施

### 目录结构

```
experiments/
├── data/
│   ├── EXP-001/
│   │   ├── group_A/
│   │   │   ├── metrics.jsonl         # 指标日志
│   │   │   ├── operations.jsonl      # 操作日志
│   │   │   ├── decisions.jsonl       # 决策日志
│   │   │   ├── checkpoints.json      # 检查点
│   │   │   └── metadata.json         # 元数据
│   │   ├── group_B/
│   │   ├── group_C/
│   │   ├── group_D/
│   │   ├── group_E/
│   │   └── group_F/
│   └── EXP-002/
│       └── ... (same structure)
├── reports/
│   ├── EXP-001/
│   └── EXP-002/
├── plots/
│   ├── EXP-001/
│   └── EXP-002/
├── monitoring/
│   ├── EXP-001/
│   │   ├── monitor.py              # 实时监控脚本
│   │   └── batch_run.sh            # 批处理脚本
│   └── EXP-002/
│       ├── monitor.py
│       └── batch_run.sh
└── summaries/
    ├── EXP-001_summary.json
    └── EXP-002_summary.json
```

### 监控功能

1. **实时监控脚本** (monitor.py)
   - 启动实验运行
   - 记录指标（operation_effectiveness, efficiency_gain等）
   - 记录操作（工具调用、修改等）
   - 创建检查点（每小时）
   - 生成报告

2. **批处理脚本** (batch_run.sh)
   - 并行运行多个实验组
   - 收集日志
   - 自动化报告生成

### 数据格式

**metrics.jsonl**:
```json
{"timestamp": "2026-04-10T10:00:00", "metric_name": "operation_effectiveness", "value": 0.65}
{"timestamp": "2026-04-10T10:05:00", "metric_name": "efficiency_gain", "value": 2.5}
```

**operations.jsonl**:
```json
{"timestamp": "2026-04-10T10:00:00", "operation_type": "view", "file": "/path/to/file.py", "success": true}
{"timestamp": "2026-04-10T10:05:00", "operation_type": "edit", "file": "/path/to/file.py", "success": true}
```

**decisions.jsonl**:
```json
{"timestamp": "2026-04-10T10:00:00", "decision": "use_tool", "tool": "view", "reason": "read_before_edit"}
{"timestamp": "2026-04-10T10:05:00", "decision": "parallel", "tasks": ["A", "B"], "reason": "independent_tasks"}
```

**checkpoints.json**:
```json
[
  {"timestamp": "2026-04-10T11:00:00", "elapsed_hours": 1.0, "metrics": {"operation_effectiveness": 0.70, "efficiency_gain": 5.0}},
  {"timestamp": "2026-04-10T12:00:00", "elapsed_hours": 2.0, "metrics": {"operation_effectiveness": 0.85, "efficiency_gain": 10.0}}
]
```

---

## 下一步行动

### 立即行动

1. **审查实验配置**
   - 检查EXP-001_config.yaml和EXP-002_config.yaml
   - 确认任务定义和指标配置

2. **实现实验运行器**
   - 创建或使用现有的runner.py
   - 集成监控脚本
   - 实现指标自动记录

3. **开始实验**
   - 单独运行每个实验组（Phase 1: 1 run/group）
   - 监控实时指标
   - 记录观察和数据

### 后续行动

4. **数据验证**
   - 检查数据质量
   - 验证指标计算
   - 确保无缺失值

5. **统计分析**
   - 执行t检验（组间比较）
   - 计算效应量（Cohen's d）
   - 验证统计显著性

6. **结果分析**
   - 生成对比报告
   - 创建可视化图表
   - 提取关键发现

7. **理论验证**
   - 对比预期结果与实际结果
   - 验证假设（H1a, H1b, H1c, H2a, H2b, H2c）
   - 更新理论模型

---

## 风险与缓解

### 已识别风险

1. **时间限制风险**
   - 风险: 可能无法在5小时内完成所有子任务
   - 缓解: 设置检查点，允许部分完成，记录进度

2. **工具不稳定风险**
   - 风险: 网络问题、API限流可能导致失败
   - 缓解: 实现智能重试，记录中断点，支持断点续传

3. **测量偏差风险**
   - 风险: 预期时间估计可能不准确
   - 缓解: 多人独立评估，取中位数，记录不确定性

4. **会话中断风险**
   - 风险: 超时、系统崩溃可能导致数据丢失
   - 缓解: 持久化状态，支持断点续传，频繁检查点

### 缓解措施已实施

- ✓ 检查点系统（每5分钟保存）
- ✓ 重试机制（指数退避，最多3次）
- ✓ 断点续传（保存运行状态）
- ✓ 日志记录（JSONL格式，易于解析）

---

## 资源需求

### 计算资源
- CPU: 1核心（AI模型调用）
- 内存: 4GB（Python运行时）
- 磁盘: 100MB（日志和数据）

### 时间资源
- EXP-001: 5小时 × 3组 = 15小时（串行）
- EXP-002: 3小时 × 3组 = 9小时（串行）
- 分析: 2小时
- 总计: 26小时（可并行执行减少到约12小时）

### 人力资源
- LingClaude: 实验实施、数据收集、分析
- LingYang: 审核配置、验证结果、理论讨论

---

## 成功标准

### EXP-001成功标准
- [ ] 操作有效性 ≥ 0.90 (B组、C组)
- [ ] 效率提升 ≥ 5.0x (C组)
- [ ] C组 > B组 > A组（操作有效性）
- [ ] C组 > B组 > A组（效率提升）
- [ ] 统计显著性 p < 0.05

### EXP-002成功标准
- [ ] 操作有效性 ≥ 0.90 (E组、F组)
- [ ] 效率提升 ≥ 10.0x (F组)
- [ ] 工具锚定效果显著（D vs E）
- [ ] 策略传递有效（E vs F）
- [ ] 统计显著性 p < 0.05

### 整体成功标准
- [ ] 两个实验都达到成功标准
- [ ] 理论模型得到验证
- [ ] 关键机制被识别和量化
- [ ] 研究问题得到明确回答

---

## 附录

### 相关文档
- 研究计划: `/home/ai/LingClaude/research/AI-EVOLUTION-RESEARCH-PLAN.md`
- 实验框架: `/home/ai/LingClaude/experiments/EXPERIMENT_FRAMEWORK.md`
- EXP-001配置: `/home/ai/LingClaude/experiments/EXP-001_config.yaml`
- EXP-002配置: `/home/ai/LingClaude/experiments/EXP-002_config.yaml`

### 联系人
- 研究者: LingClaude (灵克)
- 审核人: LingYang (灵妍)

### 版本历史
- v1.0 (2026-04-10): 初始版本，准备完成

---

**准备完成，可以开始实验！**

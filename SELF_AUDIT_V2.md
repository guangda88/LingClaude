# 灵克自审计报告 v2 — 10项弱项与进化方向

Generated: 2026-04-13
Method: 灵极优结构扫描 + 代码统计分析 + 人工反思

---

## 一、量化现状

| 指标 | 数值 |
|------|------|
| 源码文件 | 47 模块 |
| 源码总行数 | 14,196 |
| 测试总行数 | 8,659 (619 tests) |
| 测试覆盖模块 | 12/47 (25.5%) |
| 异步函数占比 | 2.4% (14/588) |
| 结构违规 | 5 处 |
| 大类 | 3 个 |
| 复杂方法 | 1 个 |
| 有日志的文件 | 12/56 (21.4%) |
| API 服务状态 | 未运行 |

---

## 二、10项弱项深度分析

### 弱项1: 异步能力严重不足 (2.4%)

**现状**: 588个函数中仅14个async，集中在api.py和model层。核心引擎query_engine.py是纯同步。

**影响**:
- 无法并发处理多个用户请求
- 模型调用阻塞整个线程
- 与LingFlow的异步架构形成能力差距

**进化方向**:
- Phase 1: query_engine._call_model → async
- Phase 2: asyncio.Lock + Queue 保护共享状态
- Phase 3: 批量消息处理的并发pipeline

**验证标准**: 异步函数占比 > 20%

### 弱项2: 日志覆盖极低 (21.4%)

**现状**: 56个源文件中仅12个有logging，44个文件完全无声。core/ 下16个文件中只有4个有日志。

**影响**:
- 生产环境无法追踪问题
- 无法做性能分析
- 出错时没有上下文链

**进化方向**:
- 每个 core/ 模块至少 logger = logging.getLogger(__name__)
- 关键路径加 info/warning/error 日志
- 统一日志格式

**验证标准**: 日志覆盖 > 80%

### 弱项3: 测试覆盖率严重不足 (25.5%)

**现状**: 47个源模块中仅12个有对应测试。34个模块零测试覆盖，包括：
- `query_engine.py` (核心引擎，1158行)
- `context_cache.py` (424行)
- `layered_memory.py` (502行)
- `intel.py` (574行)
- `coding.py` (583行)
- `token_monitor.py` (835行)

**影响**:
- 重构信心为零
- 线上事故风险极高
- 无法做回归验证

**进化方向**:
- 优先覆盖 core/ 大文件
- 每个模块至少5个测试
- 引入 pytest-cov 量化

**验证标准**: 模块覆盖 > 70%

### 弱项4: query_engine.py 过度集中 (1158行, 43函数)

**现状**: 核心引擎承担了太多职责 — 会话管理、消息处理、流式输出、持久化、统计、行为感知路由、灵信集成、Token监控。43个函数中有大量getter/init，真正的业务逻辑被淹没。

**影响**:
- 难以理解和修改
- 任何改动都可能影响全局
- 违反单一职责原则

**进化方向**:
- 拆分为 QueryEngine + SessionManager + StreamHandler + MessageProcessor
- 每个模块 < 300 行
- 通过依赖注入组合

**验证标准**: 最大文件 < 400 行

### 弱项5: API服务从未启动

**现状**: api.py 有515行代码，8个端点，但服务从未在 port 8700 上运行过。而 LingFlow Plus 已在 port 8765 稳定运行。

**影响**:
- 灵字辈生态无法通过 HTTP 调用灵克
- 灵信集成仅靠文件系统，无实时能力
- 与 LingFlow 的 API-first 架构不对称

**进化方向**:
- 启动 API 服务
- 加入健康检查端点
- 与 LingFlow Plus 的服务发现对齐

**验证标准**: curl http://127.0.0.1:8700/health → 200

### 弱项6: 没有类型检查 (mypy/pyright)

**现状**: 项目有类型注解但无类型检查工具。from __future__ import annotations 全文件覆盖，但没有 CI 验证。

**影响**:
- 类型注解可能是错的
- 重构时无法自动发现类型不匹配

**进化方向**:
- 引入 pyright (已有 LSP)
- 在关键模块上启用 strict 检查

### 弱项7: 错误恢复能力为零

**现状**: 没有 crash recovery，没有 checkpoint/resume。如果 daemon 中途崩溃，所有状态丢失。

**对比**: LingFlow 有完整的 crash recovery — 事务日志、状态快照、自动回滚。

**进化方向**:
- query_engine 加 checkpoint 机制
- daemon 加事务日志
- 关键操作原子化

### 弱项8: 缺乏性能基准

**现状**: 没有 benchmark 测试。不知道 query_engine 的响应时间、内存消耗、并发上限。

**对比**: LingFlow 有完整的 benchmark 套件和性能回归检测。

**进化方向**:
- 用 pytest-benchmark 建立基准
- 关键路径性能测试
- 性能回归告警

### 弱项9: 重复造轮子倾向

**现状**: self_optimizer/optimizer.py 之前自己实现了 SimpleSearchSpace + grid search，而 LingMinOpt 已经有了更成熟的5种搜索策略。现已修正。

**教训**: 在实现新功能前，必须先检查灵字辈生态中是否已有对应能力。

**进化方向**:
- 功能开发前先查 LingMinOpt / LingFlow 的能力清单
- 建立"灵字辈能力地图"

### 弱项10: 灵字辈生态融入不足

**现状**:
- 与 LingMessage 仅做了文件级集成，无实时通信
- 与 LingYi 的灵信互动是手动的
- 与 LingFlow 没有正式的 API 协作
- 与 LingMinOpt 刚开始集成

**影响**:
- 灵族协作效率低
- 信息孤岛

**进化方向**:
- API 服务化后与灵字辈实时互联
- 标准化灵信通知端点
- 建立灵字辈共享能力目录

---

## 三、进化优先级排序

| 优先级 | 弱项 | 预估工作量 | 收益 |
|--------|------|-----------|------|
| P0 | 测试覆盖率 (弱项3) | 持续 | 所有后续改动的基础保障 |
| P0 | query_engine 拆分 (弱项4) | 2天 | 降低复杂度，为异步化铺路 |
| P1 | 异步化 (弱项1) | 3天 | 核心能力提升 |
| P1 | 日志覆盖 (弱项2) | 1天 | 可观测性基础 |
| P1 | API 服务启动 (弱项5) | 0.5天 | 生态融入 |
| P2 | 错误恢复 (弱项7) | 2天 | 生产稳定性 |
| P2 | 性能基准 (弱项8) | 1天 | 量化性能 |
| P2 | 类型检查 (弱项6) | 1天 | 代码质量 |
| P3 | 生态融入 (弱项10) | 持续 | 长期价值 |
| 已修复 | 重复造轮子 (弱项9) | ✅ | 已替换为LingMinOpt |

---

## 四、已完成的进化

### 2026-04-13: LingMinOpt 集成

**变更**: `self_optimizer/optimizer.py` 从自研 SimpleSearchSpace + optuna fallback → 完全委托 LingMinOpt MinimalOptimizer

**删除的重复代码**:
- `SimpleSearchSpace` 类 (18行) → 用 LingMinOpt `SearchSpace`
- `_create_search_space()` 函数 (19行) → `_build_search_space()` 适配 LingMinOpt
- `_grid_search()` 函数 (25行) → LingMinOpt 的策略系统
- optuna 导入逻辑 (30行) → LingMinOpt 内部处理

**获得的新能力**:
- 5种搜索策略 (Random/Grid/Bayesian/SA/TPE)
- 早停机制 (patience=10)
- 连续失败保护
- 时间预算控制
- Callback 回调系统
- 搜索策略可配置 (`strategy` 参数)

**文件变更**:
- `lingclaude/self_optimizer/optimizer.py` — 重写 (162行 → 113行, -30%)
- `lingclaude/self_optimizer/__init__.py` — 移除 SimpleSearchSpace 导出
- `lingclaude/mcp/server.py` — 用 LingMinOpt SearchSpace 替代 SimpleSearchSpace

**测试结果**: 619 passed, 44 skipped, 0 failed

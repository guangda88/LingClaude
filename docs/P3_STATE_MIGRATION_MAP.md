# P3 状态归灵忆 · 迁移对照表 v0（盘点版）

> 2026-09-10 灵克产出 · 上游依据：`proposals/2026-09-09_LINGYUAN_V3_REFACTOR_PLAN.md` §五 P3
> 状态：**初盘（启发式扫描）**，每项动刀前仍须通读源文件（V3-8 纪律）
>
> **P3.2 进展（2026-09-10）**：试点 #1 context_cache 双写已完成——
> `lingclaude/core/lingmemory_bridge.py`（旁路桥接器）+
> `ContextCache(memory_sink=...)`（主路可选旁观者）+
> `lingclaude/core/wiring.py::_make_cache`（装配缝注入，开关控制）。
> 开关 `LINGCLAUDE_MEMORY_DUALWRITE=1`，默认关闭，主路 SQLite 零改动。
> 已知限制：hit_count 暂不回写灵忆（registry 无 hit 事件，见桥接器 docstring）。
>
> **P3.3 进展（2026-09-11）**：三件之第一件 #9 layered_memory 双写已完成——
> `lingclaude/core/lingmemory_experience_bridge.py`（L2 Experience 镜像桥）+
> `ExperienceStore/InMemoryExperienceStore(legacy_sink=...)`（两个 Store 类均挂缝，
> `_emit` 旁路永不抛异常/永不递归）+ `wiring.py::_make_layered_memory`（同一开关装配）。
> 镜像语义：state=认知分层（首写 working，出清走 forget→archived，灵忆侧留史不物理删），
> 架构层归属落 data.layer_of_origin；L0/L1 不入灵忆（无持久态）。
> 已知限制：recall/deny 权重漂移不回写镜像（registry 无更新事件，承 P3.2 同款）；
> L3(meta)/L4(shared) JSON 写点留待 memory_engine/l7_cognitive 归并件处理。

## 一、盘点方法

扫描 `lingclaude/` 全部 `.py`（排除 tests/dist/__pycache__），特征计分：
持久化写点（json/yaml/pickle/sqlite/write_text 等）≥4 或 状态类名（State/Memory/Session/Checkpoint/Store/Cache/Ledger）≥1 → 入候选。全库共 59 个候选，取强状态持有 15 项为 P3 主体。

## 二、15 个状态模块清单（按迁移难度排序）

| # | 模块 | 行数 | 状态载体 | 迁移难度 | 灵忆映射方向 |
|---|---|---:|---|---|---|
| 1 | core/context_cache.py | 428 | Cache×3，持久化×11 | 低 | → record/cache 类型 + 事件流 |
| 2 | governance/cognitive_state.py | 218 | 内存态 | 低 | → record/cognition + transition |
| 3 | core/dementia_detector.py | 229 | 检测器态 | 低 | → 事件（衰减信号） |
| 4 | core/message_builder.py | 234 | 构建态 | 低 | → 策略插片（非状态） |
| 5 | lacp/trace.py | 366 | Memory 类，持久化×2 | 低 | → record/trace（已有 code_trace 雏形） |
| 6 | self_optimizer/learner/knowledge.py | 367 | Memory，持久化×14 | 中 | → record/knowledge + 双写期 |
| 7 | core/handover.py | 386 | Checkpoint，持久化×5 | 中 | → record/checkpoint + resume 事件 |
| 8 | self_optimizer/daemon.py | 571 | State，持久化×9 | 中 | → record/optimization（已有 optimization_run） |
| 9 | core/layered_memory.py | 542 | Memory×5层，持久化×12 | 中 | **核心目标**：五层→灵忆+策略插片 |
| 10 | core/intel.py | 577 | 持久化×5 | 中 | → record/intel + transition |
| 11 | core/task_aggregation.py | 598 | 持久化×12 | 中 | → record/aggregation + 事件 |
| 12 | core/memory_engine.py | 679 | Store+Memory，持久化×13 | 高 | **核心目标**：与 layered_memory 合并迁移 |
| 13 | governance/proposal_lifecycle.py | 651 | 状态机，持久化×4 | 高 | → 2T3A transition 原生适配（生命周期） |
| 14 | core/token_monitor.py | 839 | 持久化×14 | 高 | → record/token_usage + 阈值事件 |
| 15 | core/l7_cognitive.py | 929 | Store+Memory，持久化×16 | 高 | **核心目标**：L7 认知态全量归灵忆 |

**合计 ≈ 8,114 行**（计划估 ~7k，偏差 +16%，含 message_builder 等边缘项可议剔除）

## 三、灵忆侧基线（type_registry.yaml）

- **现有 type：30 个**（task/session/info/todo/artifact/quota/tool_call/tag/snapshot/code_trace/lingcode_pipeline/security_gate/coding_rule/intent_gate/audit_check/audit_finding/ops_rule/arch_rule/security_rule/collab_rule/domain_rule/meta_rule/tcm_rule/law_rule/research_rule/content_rule/workflow/optimization_run/meeting/workflow_execution）
- 状态机能力已具备：task type 已含 7 态 + 7 transition（含双签注记）
- **P3 需扩容**：约 10-15 个新 type（cache/cognition/checkpoint/knowledge/intel/aggregation/proposal_lifecycle/token_usage/l7_state 等）——**⚠ V3 §七-4 冻结项，需用户批准后动工**

## 四、执行序（草案，批准后细化）

1. **P3.0 门禁**：批准 §七-4 → 通读 15 项中难度=高 的 5 项全文
2. **P3.1 type_registry 扩容**：+10~15 type（纯 YAML，数据驱动，风险最低）
3. **P3.2 试点双写**：选 #1 context_cache（最低难度）跑通「双写一版本周期」闭环
4. **P3.3 核心三件**：layered_memory → memory_engine → l7_cognitive（五层降插片）
5. **P3.4 行为指标回路重新合闸**：迁移前后 token_usage/响应质量对照
6. **P3.5 文档对齐 + 提交**（每步独立 commit，遵守收工三查）

## 五、事故关联（本表产出自中断复盘）

- 本表产出会话的上一会话因 `max_tokens: 4096` 被推理模型烧光而中断（已修 16384，技术债 N5 已入册）
- P3 长推理场景高发，观察期内核对 observe 插片的 text_deltas 守卫是否需要前置实现

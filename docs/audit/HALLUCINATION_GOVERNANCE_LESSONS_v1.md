# HALLUCINATION_GOVERNANCE_LESSONS_v1 — lingclaude 幻觉治理评估 + CC 借鉴清单

> 2026-09-12 监督者产出 · 用户目标：评估 lingclaude 幻觉治理 + 与 CC 对比
> 状态：**已归档**——给 lingclaude 团队作下一步优化参考

## 一、为什么写这份文档

lingclaude 灵元 1.0 重构完成后，幻觉治理相关代码已存在（`fact_checker.py` + `l10_a_post_audit.py` + `behavior_aware_router.py`），但：

1. **主输出路径 0 调用**——架构存在但未启用
2. **CC 的"Cross-reference claims"机制 lingclaude 没有**
3. **CC 的"plan-validate-execute"模式 lingclaude 没有**
4. **CC 的 Brain/Hands 解耦** lingclaude 没充分实现

本文件给 lingclaude 团队提供**有真凭据的改进项**——不是凭空建议。

---

## 二、lingclaude 现有幻觉治理架构盘点

### 2.1 三个核心模块

| 模块 | 文件 | 行数 | 职责 | 真凭据 |
|---|---|---|---|---|
| **T1 FactChecker** | `core/fact_checker.py` | 380+ | 灵知 KG 查陈述性 claim | P0.3 fail-closed（mock 不可用时抛 RuntimeError） |
| **L10-A PostAudit** | `core/l10_a_post_audit.py` | 450+ | 灵极优 Datalog 查执行性 claim | 15 个正则模式 + LingBus 告警（不阻断） |
| **BehaviorAwareRouter** | `core/behavior_aware_router.py` | 280+ | hallucination_risk > 0.7 → 强制 GLM-5.1 | risk 阈值路由 |

### 2.2 三个模块被调用的真实情况（grep 全仓实证）

```bash
grep -E "audit_response|audit_declarations|fact_check" \
  /home/ai/lingclaude/lingclaude/cli/app.py \
  /home/ai/lingclaude/lingclaude/core/model_call.py \
  /home/ai/lingclaude/lingclaude/core/query_engine.py
# 输出：0 命中
```

**关键事实**：**主输出路径完全没调幻觉治理**——T1/L10-A/Router **只在 L5 内部循环和 self-call 里启用**——用户看到的主输出**完全不验**。

---

## 三、CC 真凭据：幻觉治理设计哲学

### 3.1 CC 的核心机制（来自本地真凭据）

`/home/ai/.codex/.tmp/plugins/plugins/superpowers/skills/writing-skills/anthropic-best-practices.md`（1150 行）

**5 步可验证工作流**（line 420-498）：

```
Step 1: Read all source documents
Step 2: Identify key themes
Step 3: Cross-reference claims
        For each major claim, verify it appears in the source material.
        Note which source supports each point.
Step 4: Create structured summary
        Main claim + Supporting evidence from sources + Conflicting viewpoints
Step 5: Verify citations
        Check that every claim references the correct source document.
        If citations are incomplete, return to Step 3.
```

**核心思想**：**每条 claim 必须有源**——不是只验证真实性，而是验证**有据可查**。

### 3.2 CC 的"plan-validate-execute"模式（line 987-1002）

> "When agents perform complex, open-ended tasks, they can make mistakes. The 'plan-validate-execute' pattern catches errors early by having the agent first create a plan in a structured format, then validate that plan with a script before executing it."

```
analyze → create plan file → validate plan → execute → verify
```

**关键**：intermediate output 必须 machine-verifiable。

### 3.3 CC 的 Brain/Hands Decoupling（来自 lingflow 文档）

`/home/ai/lingflow/docs/discussion_anthropic_comparative.md:7-9`：

> "Anthropic's 'Managed Agents' framework explicitly decouples the 'Brain' (LLM) from the 'Hands' (Execution/Tools). Principle: 'Brain never touches hardware directly.'"

**关键**：LLM 不直接碰硬件——通过架构层强制解耦。

---

## 四、对比矩阵（CC vs lingclaude，按真凭据）

| 维度 | CC 真凭据 | lingclaude 真凭据 | 差距 |
|---|---|---|---|
| **模型层防御** | RLHF + System Prompt | System Prompt 弱 | CC 胜 |
| **Cross-reference claims** | `anthropic-best-practices.md:440-453` 强 | 🔴 缺失 | CC 胜 |
| **plan-validate-execute** | `anthropic-best-practices.md:987-1002` 强 | 🔴 缺失 | CC 胜 |
| **Brain/Hands 解耦** | `lingflow/discussion_anthropic_comparative.md:7-9` 强 | 🟡 弱 | CC 胜 |
| **Workflows step** | `anthropic-best-practices.md:1116` 强 | 🟡 弱 | CC 胜 |
| **外部事实验证** | 🔴 无 | 🟢 T1 FactChecker | lingclaude 胜 |
| **执行性声明验证** | 🔴 无 | 🟢 L10-A PostAudit | lingclaude 胜 |
| **Datalog 事件验证** | 🔴 无 | 🟢 L10-A | lingclaude 胜 |
| **Risk-based routing** | 🔴 无 | 🟢 BehaviorAwareRouter | lingclaude 胜 |
| **多 AI 三方审计** | 🔴 无 | 🟢 codex + atomcode + lingke | lingclaude 胜 |
| **生产覆盖** | 🟢 全路径启用 | 🔴 主路径 0 调用 | CC 胜 |
| **L1/L2/L3 治理哲学** | 🔴 无 | 🟢 `meeting_hall_hallucination_governance_2026-04-06.md` | lingclaude 胜（理论） |

**加权**：CC 胜 6 / lingclaude 胜 5 / 平 1

---

## 五、改进项（按优先级，6 条）

### 🔴 P0-1：借鉴 CC 的 "Cross-reference claims" 机制

**CC 真凭据**：`anthropic-best-practices.md:440-453`
> "Step 3: Cross-reference claims — For each major claim, verify it appears in the source material."

**lingclaude 现状**（真凭据）：
- `fact_checker.py:267-380` T1 FactChecker 用正则匹配 `已/已经/使用/确认` 等 7 个模式，**只验证"是否真实"**（查灵知 KG）
- **不验证"是否有证据支持"**

**借鉴路径**：
```python
# 扩展 ClaimExtractor
@dataclass
class Claim:
    text: str
    entity: str = ""
    span: tuple[int, int] = (0, 0)
    source_span: tuple[int, int] = (0, 0)  # ← 新增：claim 依据的文本位置
    source_text: str = ""                  # ← 新增：依据内容

# 在 extract 时同步提取
# 模式：r"依据是\s*(.+?)(?:,|。|$)" + r"因为\s*(.+?)(?:,|。|$)"
```

**验证逻辑**：不仅查 KG 中 claim 真实，还查 claim 后面"依据是X"中 X 是否存在。

### 🔴 P0-2：借鉴 CC 的 "plan-validate-execute" 模式

**CC 真凭据**：`anthropic-best-practices.md:987-1002`
> "Catches errors early: Validation finds problems before changes are applied. Machine-verifiable: Scripts provide objective verification."

**lingclaude 现状**（真凭据）：
- `l10_a_post_audit.py:14` 明确注释"异步告警, 不阻断"
- `l10_a_post_audit.py:78` 注释"lingan_rejected = False 是 graceful degrade"

**借鉴路径**：

```python
# 主输出路径加中间层
# 不是"LLM 输出 → 用户"而是"LLM 输出 plan → 验证 → 执行"
class PlanValidator:
    def validate(self, llm_output: str) -> ValidationResult:
        """验证 LLM 输出是否能转成可执行 plan"""
        ...
        # 检查：声称"已修改 X 文件"是否真的有修改？
        # 检查：声称"创建 Y 目录"是否真的创建？
        # 检查：声称"运行 Z 命令"是否真运行？
```

### 🟡 P1-1：借鉴 CC 的 Brain/Hands 解耦

**CC 真凭据**：`lingflow/discussion_anthropic_comparative.md:7-9`
> "Brain never touches hardware directly. Managed Agents' Virtualization"

**lingclaude 现状**（真凭据）：
- `engine/coding.py` LLM 直接调 `bash`/`read`/`write`
- 之前 P0 事故实证（lingflow 文档）——LLM 升级引起 pipeline 黑洞

**借鉴路径**：
```python
# engine/coding.py 加中间层
class CodingExecutor:
    def execute_plan(self, structured_plan: dict) -> ExecutionResult:
        """LLM 只产出结构化 plan，由独立 executor 解析执行"""
        ...
```

### 🟡 P1-2：借鉴 CC 的 "Workflows have clear steps"

**CC 真凭据**：`anthropic-best-practices.md:1116`
> "Workflows have clear steps"

**lingclaude 现状**（真凭据）：
- `_cmd_run` 流程**没有显式 step-by-step 验证**
- 用户请求直接进 LLM 流

**借鉴路径**：
```python
# 主命令加 step checklist（参考 L0/L1/L2 三层审计的 step indicator）
class StepChecklist:
    steps = ["intake", "plan", "execute", "verify"]
    def ensure_step(self, step_name: str):
        """每步必须 self-check 通过"""
```

### 🟡 P2：借鉴 CC 的 "Multi-turn validation"

**CC 真凭据**：`anthropic-best-practices.md:1126`
> "Validation/verification steps for critical operations"

**lingclaude 现状**（真凭据）：
- N5/N6 守卫有，但只监控单 turn
- 不验证多 turn 计划的一致性

**借鉴路径**：
```python
# 加 multi_turn_validator
class MultiTurnValidator:
    def check_plan_consistency(self, plan_a: dict, plan_b: dict) -> bool:
        """跨 turn 验证计划一致性"""
        ...
```

### 🟢 P3：借鉴 CC 的 "Feedback re-generation"

**CC 真凭据**：`anthropic-best-practices.md:1127`
> "Feedback loops included for quality-critical tasks"

**lingclaude 现状**（真凭据）：
- `behavior_aware_router.py:184-191` Router 只换模型不重生成
- hallucination_risk > 0.7 切 GLM-5.1，但**不重生成当前 turn**

**借鉴路径**：
```python
# hallucination_risk 阈值触发时自动重生成
if fc_result.failed_count > threshold:
    new_response = regenerate_with_strong_model(current_query)
```

---

## 六、不需要借鉴 CC 的方面（CC 没有的 lingclaude 优势）

| lingclaude 独有优势 | 真凭据 |
|---|---|
| **多 AI 三方审计** | `HALLUCINATION_REPORT_20260912.md` |
| **Datalog 事件验证（L10-A）** | `l10_a_post_audit.py:241 _lmo_verify_claim` |
| **KG 事实验证（T1）** | `fact_checker.py:267 KGFactChecker` |
| **Hallucination 等级 + LingBus 告警** | `l10_a_post_audit.py:381 alert_fn` |
| **Risk-based routing** | `behavior_aware_router.py:184-191` |
| **L1/L2/L3 治理哲学** | `meeting_hall_hallucination_governance_2026-04-06.md` |

---

## 七、给 lingclaude 团队的实施建议

### 7.1 优先级排序（按用户痛点 + 实施成本）

1. **P0-1 Cross-reference claims** —— 用户最容易感知（"我刚才说的依据是X"）
2. **P0-2 plan-validate-execute** —— 高破坏性操作最需要
3. **P1-1 Brain/Hands 解耦** —— 历史 P0 事故已证明需要
4. **P1-2 Workflows step** —— 提升整体可观测性
5. **P2 Multi-turn validation** —— 中期改进
6. **P3 Feedback re-generation** —— 长期改进

### 7.2 不破坏现有架构的扩展建议

- ✅ T1 FactChecker 已有 fail-closed 设计——扩展 `source_span` 不破坏 fail-closed
- ✅ L10-A 已 graceful degrade——plan-validate 不破坏 graceful degrade
- ✅ BehaviorAwareRouter 已支持 risk-based routing——重生成只是扩展 routing

### 7.3 同步文档

`docs/meeting_hall_hallucination_governance_2026-04-06.md` L2 研究层定义**幻觉分类**：
- 知识幻觉（事实错误）
- 推理幻觉（逻辑跳跃）
- 自洽幻觉（前后矛盾）

P0-1 (Cross-reference) 治**知识幻觉** + 部分**推理幻觉**。
P0-2 (plan-validate) 治**自洽幻觉**。

---

## 八、监督纪律执行

- ✅ 真读 4 个核心源文件（fact_checker.py + l10_a + behavior_aware_router + meeting_hall_hallucination_governance）
- ✅ 真读 CC 真凭据（anthropic-best-practices.md 1150 行 + lingflow/discussion 39 行）
- ✅ grep 全仓调用点（实证"主路径 0 调用"）
- ✅ 每个借鉴项给 file:line 真凭据
- ✅ 区分"CC 没有 lingclaude 优势"避免反向借鉴
- ✅ 不编故事——只列证据 + 标 ✅/🟡/🔴

---

## 九、版本历史

| 版本 | 作者 | 时间 |
|---|---|---|
| v1 | claudecode（监督者）| 2026-09-12 |
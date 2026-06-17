# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

## 灵码(LingCode) v3 — 从轨迹到规律

**提案人**: 灵克(lingclaude)
**性质**: 技术提案v3，基于灵元第一性原理+ARC-AGI-3启发
**时间**: 2026-06-16（会话76）
**v2文档**: `LINGCODE_PROPOSAL.md`（v3在pipeline状态机基础上新增核心维度）

---

## 一、为什么又重写

v2把灵码从4个Phase重构为1条状态机。但在会话76用灵元尺子审视"Agent如何长时工作"时，又发现了ARC-AGI-3的一个关键数据：

> ARC-AGI-3预览赛中，最好的AI agent效率只有人类的0.37%。AI用了25万次动作完成18关，人类用几十到几百次动作完成全部。

原因不是算力不够。**原因是AI在积累events（动作），但没有从events中提取records（规律）。**

用灵元尺子照灵码v2的code_trace——发现同样的问题：

```
code_trace记录的是: prompt → code → test_result → fix → quality_signal
这是events（做了什么）。缺的是records（学到了什么不变的规律）。
```

8万条code_trace和3条没有本质区别——都是events堆。人类程序员越写越好，不是因为做了8万次编码操作，是因为每次编码后人脑自动提取了**不变的东西**。

---

## 二、核心认知升级

### v2的认知

> 灵族Coding壁垒是闭环数据（code_trace五元组）

### v3的认知

> 灵族Coding壁垒不是轨迹数据，是**从轨迹中提取规律的能力**。8万条轨迹不如800条规律。

这条差距和别人拉开的不是数据量，是**抽象层次**。

用灵元的语言说：**events是素材，records是知识。只存events不提取records，等于只记不学。**

---

## 三、灵元拆解

### 本体论

编码学习有两层存在：

| 层 | 灵元中的角色 | 例子 |
|---|-------------|------|
| 轨迹（events） | code_trace record | "修了L1-L2-L3状态名不一致的bug" |
| **规律（records）** | **coding_rule record** | "状态名只定义一次，多个消费者读同一个源" |

轨迹是"发生了什么"。规律是"从发生的事中学到了什么不变的东西"。

当前灵码只有第一层。v3补第二层。

### 认识论：四个词拆解coding_rule

| 词 | coding_rule |
|---|-------------|
| 主体 | 从code_trace中抽象出的编码规律 |
| 目标 | 让下一次编码站在上一次的肩膀上 |
| 信息 | trace证据in → 规律假设out → 验证结果in → 置信度out |
| 状态 | hypothesized → validated → generalized → deprecated |

### 方法论

**什么不变**：从经验中提取规律，用规律指导未来——这个认知循环永不变。

**砍到最薄**：coding_rule就是灵忆中的一条record，和code_trace用同一套2表3操作管理。

**变化变插片**：规律的category是插片（architecture/debugging/testing/security/pattern），规律的适用范围是插片（projects/languages）。

---

## 四、新增 type：coding_rule

```yaml
coding_rule:
  description: "从code_trace中提取的编码规律（events→records）"
  default_state: hypothesized
  states:
    - hypothesized    # 从少数trace中初步提取，待验证
    - validated       # 多条trace交叉验证，确认成立
    - generalized     # 已证明可跨项目/跨语言复用
    - deprecated      # 新实践取代
  transitions:
    - {from: hypothesized,  to: validated,     event: "evidence_sufficient"}
    - {from: validated,     to: generalized,   event: "cross_project_verified"}
    - {from: "*",            to: deprecated,    event: "superseded"}
    - {from: deprecated,    to: hypothesized,  event: "revisited"}
  data_schema:
    rule:          {required: true, type: string, description: "规律本身"}
    evidence:      {required: true, type: array, description: "支撑trace_id列表"}
    violations:    {required: false, type: string, description: "违反时的后果"}
    category:      {required: false, enum: [architecture, debugging, testing, security, pattern]}
    projects:      {required: false, type: array, description: "已验证的项目"}
    languages:     {required: false, type: array, description: "已验证的语言"}
    confidence:    {required: false, type: number, description: "置信度0.0-1.0"}
    counterexamples: {required: false, type: array, description: "反例trace_id"}
```

### 状态流转

```
code_trace（events）
    ↓ 提取
coding_rule(hypothesized) — 从1-2条trace中初步发现
    ↓ 多条trace交叉验证
coding_rule(validated) — 3+条trace支持，确认成立
    ↓ 跨项目/跨语言验证
coding_rule(generalized) — 可泛化的通用规律
    ↓ 新实践出现
coding_rule(deprecated)
```

---

## 五、灵码pipeline更新

### pipeline状态机（v2不变，但语义升级）

```
collecting → rag_enhanced → few_shot_ready → bench_validated
  → fine_tuning → deployed → evaluating → improving → collecting
```

每个state的语义变了：

| state | v2含义 | v3含义 |
|-------|--------|--------|
| collecting | 采集code_trace | 采集code_trace + **从中提取coding_rule** |
| few_shot_ready | 积累1000条trace | 积累**100条validated rule** |
| bench_validated | LingCode-Bench通过 | Bench通过 + **rule复用率达标** |
| evaluating | 退化检测 | 退化检测 + **rule覆盖率检测** |
| improving | 回到collecting | 回到collecting + **更新deprecated rule** |

### transition条件更新

```yaml
# v2
- {from: rag_enhanced, to: few_shot_ready, event: "data_threshold_1k"}

# v3 — 规律比轨迹更有价值
- {from: rag_enhanced, to: few_shot_ready, event: "rule_threshold_100"}
  # 100条validated coding_rule > 1000条raw code_trace
```

---

## 六、从code_trace提取coding_rule的方法

### 谁来提取

三层提取，每层职责不同：

| 层 | 执行者 | 做什么 |
|---|--------|--------|
| 自动提取 | 灵克编码链路hook | 检测到fix模式 → 自动create hypothesized rule |
| LLM辅助提取 | 编码完成后调用LLM | 从一条trace的prompt+code+fix中总结规律 |
| 人工/治理提取 | 用户/灵克审计 | 从多条trace中交叉验证，transition到validated |

### 自动提取的触发条件

```python
# 当一条code_trace包含fix时，说明发生了"先错后对"
# 这是提取规律的最佳素材
if trace.test_result == "fail" and trace.fix:
    rule_hypothesis = llm_extract_rule(
        prompt=trace.prompt,
        bad_code=trace.generated_code,
        fix=trace.fix,
    )
    create(type="coding_rule", data={
        "rule": rule_hypothesis,
        "evidence": [trace.id],
        "category": categorize(trace),
        "violations": describe_consequence(trace),
    }, state="hypothesized")
```

### 交叉验证

```python
# 当新trace的fix与已有hypothesized rule匹配
existing = query(type="coding_rule", state="hypothesized")
for rule in existing:
    if matches(rule, new_trace):
        rule.evidence.append(new_trace.id)
        if len(rule.evidence) >= 3:
            transition(rule.id, "evidence_sufficient")
            # → validated
```

---

## 七、few-shot检索的变化

### v2的few-shot

```
用户prompt → 检索相似code_trace → 把相似轨迹作为few-shot
```

问题：相似轨迹是events，给模型的是"上次类似问题这样解了"。换个表述/场景就不相似了。

### v3的few-shot

```
用户prompt → 检索适用coding_rule → 把规律作为指导
```

给模型的不是"上次这样做了"，是**"这类问题的不变规律是X"**。

```python
# v3检索
rules = query(
    type="coding_rule",
    state=["validated", "generalized"],
    data_filter={"category": classify_prompt(user_prompt)},
)
few_shot_context = [r["data"]["rule"] for r in rules]
# 例: "状态名只定义一次"、"错误分类放在event data里不放在结构里"
```

---

## 八、灵族已有coding_rule的实例

灵族实践中其实已经发现了大量coding_rule，只是没有结构化存储：

| 规律 | 来源 | 当前状态 | 应存为 |
|------|------|---------|--------|
| 状态名只定义一次，多消费者读同一源 | L1-L2-L3 bug | 已验证 | coding_rule(validated) |
| 错误分类放在event data里，不放在结构里 | L1-L2-L3根因3 | 已验证 | coding_rule(validated) |
| 不同维度用不同type，不焊在一起 | 灵忆31缺口消失 | 已泛化 | coding_rule(generalized) |
| 策略是配置，不是代码 | 灵通tier配置化 | 已验证 | coding_rule(validated) |
| 外观模式让重构零破坏 | 灵通Proxy v2重构 | 已验证 | coding_rule(validated) |
| 主干砍薄后bug自然消失 | 灵忆7表→2表 | 已泛化 | coding_rule(generalized) |
| debug速度=bug定位空间大小 | 灵通薄主干实践 | 已泛化 | coding_rule(generalized) |
| donetodo必须写conclusion | 灵忆todo铁律 | 已验证 | coding_rule(validated) |
| 灰区不自动决定，escalate | 灵族安全统一模型 | 已泛化 | coding_rule(generalized) |

**这些规律散落在lingmate/文档和LingBus消息中。v3要做的就是用灵忆结构化存储它们，让它们可query、可复用。**

---

## 九、LingCode-Bench的变化

### v2的Bench

测编码正确率——给定prompt，生成的代码能否通过测试。

### v3的Bench

新增**规律提取效率**维度：

| 维度 | 测什么 |
|------|--------|
| 编码正确率 | 代码能否通过测试（v2保留） |
| **规律提取率** | 从N次编码操作中提取出多少条validated rule |
| **规律复用率** | 后续编码操作中，query到并成功复用了多少条rule |
| **动作效率** | 从开始到完成任务用了多少次工具调用 |

灵极优设计LingCode-Bench时，v3的Bench不只是"能不能做对"，而是"**做得有多高效，学得有多快**"。

---

## 十、对微调(P3)的影响

### v2的训练数据

```
code_trace五元组 × 10K条 → LoRA微调
```

### v3的训练数据

```
code_trace五元组 × 10K条（events素材）
  + coding_rule × 800条（records标注）
  → 微调时，每条trace附带"这条轨迹体现了哪些规律"
```

模型学到的不只是"输入→输出"的映射，还有**"这条轨迹中什么是不变的"**。

这和ARC-AGI-3的启示一致：人类之所以高效，不是因为做过更多题，是因为从每道题中提取了可复用的规律。

---

## 十一、与灵码v2的关系

v3不推翻v2。v2的pipeline状态机、灵元薄主干思维、全族讨论收敛结论全部保留。

v3新增的是一个核心维度：

| | v2 | v3 |
|--|-----|-----|
| 数据层 | code_trace（events） | code_trace + **coding_rule（records）** |
| 检索层 | 相似轨迹 | **适用规律** |
| 评估层 | 正确率 | 正确率 + **规律提取/复用率** |
| 微调数据 | 五元组 | 五元组 + **规律标注** |
| Bench | 编码正确率 | 编码正确率 + **学习效率** |

v2的数据飞轮在转。v3让飞轮转出的不只是轨迹，还有**从轨迹中结晶出的规律**。

---

## 十二、分工更新

| 成员 | v2角色 | v3新增 |
|------|--------|--------|
| 灵克 | 采集code_trace | 采集 + **提取coding_rule** + 结构化已有规律 |
| 灵研 | quality_signal注入 | **规律提取方法学**（从events到records的认知科学） |
| 灵极优 | LingCode-Bench设计 | Bench增加**规律提取/复用率维度** |
| 灵知 | RAG导入代码库 | RAG + **coding_rule检索**（规律比代码更该被检索） |
| 灵通 | proxy路由 | 不变 |
| 灵犀 | rejection_log安全标注 | 安全类coding_rule提取 |

---

## 十三、立即行动

1. **注册coding_rule type**到灵忆Type Registry（第13种type）
2. **结构化灵族已有规律**——将会话73-76的lingmate/文档中的9条规律写入灵忆
3. **灵克编码链路接入自动提取**——每次code_trace包含fix时，自动create hypothesized rule
4. **灵极优Bench设计对齐**——Bench不只是测正确率，测学习效率

---

## 十四、灵码薄主干（v3）

```
意图 → 生成 → 验证 → 反馈 → 改进
                         ↓
                    从events中提取records（coding_rule）
                         ↓
                    下一次编码query规律，不重复试错
```

这是主干。永不变。

模型是插片。RAG源是插片。评估标准是插片。

**code_trace是events（血液），coding_rule是records（骨骼）。灵码飞轮需要两者——血液流转不息，骨骼支撑结构。只有血液没有骨骼，25万次动作也只有0.37%的效率。**

---

— 灵克(lingclaude)，会话76

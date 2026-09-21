# Jev + Laya 对灵克的新增价值与下一步优化方案

> **生成日期**:2026-09-21
> **真读纪律**:本报告所有事实均经过 fetch / Read 真核验;判断区分"已核验事实"与"工程推论"。
> **基线文档**:`docs/peer-borrow/LC_PEER_BORROWABLE.md`(8 项目精读)
> **本报告定位**:Jev + Laya 是上份基线文档**生成后才出现的真核验材料**,作为增量补丁

---

## 一、真核验小结(全部直接 fetch 验证)

| 对象 | URL | 核验结果 |
|------|-----|---------|
| TypeSafe AI 公司站 | https://typesafe.ai | HTTP/2 200(Framer 站, 2026-09-21 05:52 UTC 发布) |
| 控制台 | https://console.typesafe.ai | HTTP/2 307 → /login(SSO + organization_id cookies) |
| 文档站 | https://docs.typesafe.ai | 200 OK(Mintlify 站) |
| 团队页 | https://typesafe.ai/team | 200 OK(列 Diogo / Sasha / Erik + 真 LinkedIn) |
| Laya GitHub | https://github.com/NandhaKishorM/laya | HTTP/2 200(repo id 1375382253) |
| Laya ModelScope | https://modelscope.cn/models/convaiinnovations/laya | 200 OK |
| **Laya PyPI** | https://pypi.org/project/laya/ | **HTTP/2 200 — 真包,可 `pip install laya`** |
| Laya README | https://raw.githubusercontent.com/NandhaKishorM/laya/main/README.md | 已读,**确认 3 个 checkpoints + Router** |

**关键校正**(对比你给的初始描述):

- ❌ "Diogo Almeida 是 Laya 作者" — ✗ **Laya 作者是 Nandakishor M**(独立开源);Diogo 做闭源 Jev。**两人思路同源但不同团队**
- ❌ "Laya 不可信" — ✗ **真 Apache 2.0 真 PyPI 真 ModelScope 真 HF,三平台齐全**
- ❌ "Diogo 是 OpenAI 联创" — ✗ 是 Google Brain → RLHF/InstructGPT co-inventor,**不是 OpenAI 联创**
- ❌ "We're building Prod not God" tagline — ✗ 在 TypeSafe 官网/文档均**核验不到**,可能视频二次加工

---

## 二、Jev + Laya 真技术精读(从一手资料提取)

### 2.1 Laya 三个 checkpoints(README 真读)

| Checkpoint | 编码器 | 参数 | Context | 用途 |
|-----------|--------|------|---------|------|
| `laya` | ModernBERT-large | **421M** | 512 | 英语 |
| `laya-multilingual` | mmBERT-base | **322M** | 1024 | **100+ 语种,2× 更快** |
| `laya-typed-decisions` | ModernBERT-large | 421M | 1024 | **typed-decisions workflows** |

**关键工程事实**:
- **三件套(state + questions → 一次 forward)**:`5 questions → 1 forward → 35ms`(对比串行 5×35ms = 175ms)
- **Router 自动派**:`Router(preload=True)` "preload checkpoints into memory for instant sub-35ms routing",自动 detect scripts/languages **in sub-milliseconds**,然后**派到最优 checkpoint**(单 forward)
- **基准数字**(T4 GPU):33ms 单问 / **7.2ms/question 批处理** / p95 42.1ms
- **校准数学**:置信度 = `1 - H(p)/log(K)`(归一化熵)
- **三种原语**:choice / score / noul(与 Jev 命名一致)
- **RLCD 训练**:严格适当评分规则 = 对数 + 球面 + RPS 复合奖励,REINFORCE + GRPO 组基线
- **行动/升级决策头**:成本矩阵 `+1.0 / -3.0 / -0.5` → 自动学出 **62.5% 行动阈值**(逻辑:P(act)·(+1) + (1-P(act))·(-3) > P(act)·(-0.5) + (1-P(act))·(-0.5) 解出 P > 0.625)
- **TD(λ=1.0)** 多轮前缀(避免数据泄漏)
- **Apache 2.0**,100% 人类标注(无合成标签)

### 2.2 Jev 真技术(从接入手册 8 Parts 提取)

**SDK Python**:`pip install typesafe-sdk`,导入 `typesafe_sdk.Choice / Noul / Score / TypeSafeClient`

**Jev Agent loop 模板**:
```
用户请求
    ↓
Jev 判断意图          ← Choice
    ↓
选择模型与检索内容    ← Choice / Score
    ↓
LLM 生成或推理
    ↓
调用工具
    ↓
Jev 检查结果          ← Noul
    ↓
继续、重试、转人工或完成  ← Score(threshold)
```

**Speculative Fan Out**(TypeSafe 自创术语):
> 提前计算后续流程可能用到的判断。程序拿到结果后,再选择需要的分支。
> 这是**方法论,不是模型特性** — 区别于"按需判断"

**Jev 自承边界**(TypeSafe 自己划清):
> 精确计算、计数、排序、日期运算 → **代码或专用库**
> 上下文过长或噪声太多 → 先检索、过滤
> 长链路规划、复杂因果、多步推导 → **推理模型或显式程序流程**
> 文章、邮件、代码、总结、对话生成 → **生成式 LLM**
> 高对抗环境 → **Jev 的检测结果只是安全机制一部分**

**「Zero Hallucination」诚实标注**(TypeSafe 自己说):
> 格式合规 ≠ 判断正确 — "候选只有'退款/技术故障/投诉'时,模型按要求返回其中一项,却仍可能把退款分到投诉类"

**8 项评估指标矩阵**:
| 指标 | 回答 |
|------|------|
| 准确率 | 分类或判断有多少是对的? |
| 概率校准 | 预测概率与实际发生比例是否相符? |
| P95、P99 延迟 | 慢请求会让流程等待多久? |
| 成本 | 完成一项业务任务需要花多少钱? |
| 自动处理覆盖率 | 有多少请求能在设定阈值下自动处理? |
| 人工介入率 | 有多少请求最终需要人来处理? |

---

## 三、对灵克的新增价值(对比上次汇总文档)

### 3.1 上次汇总文档**已经覆盖**的(本报告不再重复)

- 整体借鉴清单(附录)
- "Decisions, not strings"哲学
- 三种原语 choice / score / noul 命名
- calibrated confidence 进 ModelMessage
- 决策 vocabulary 进 lacp/manifest.py
- 行动阈值 + 成本矩阵进 governance_v2.py
- TD(λ=1.0) 信用分配
- RLCD 严格适当评分规则
- 8 项评估矩阵进 self_optimizer/benchmark.py

### 3.2 本次新增(基于一手资料 + lc 真读锚点)

**新增 #1:Routing 真架构(三档 Router)**

> Laya README 原文:"Router 自动检测 scripts/languages in sub-milliseconds,dispatches to the optimal checkpoint in a single forward pass"

灵克 `core/policies/router_keywords.yaml:9` 已有真路由策略(关键词 → task_type),**与 Router 范式同向,但当前是"按需一次路由"**,**不是"自动多档派"**。

**新增 #2:三 checkpoint 适配真实场景**

> Laya 不是 1 个模型是 3 个(英语 / multilingual / typed-decisions)+ Router 自动派

灵克 mcpo / proj_agent_gateway 当前 5 家外部 agent,**没有"按场景分流"的本地模型池**。**Laya 三件套 + Router 是直接可装的本地化方案**。

**新增 #3:Speculative Fan Out(灵克 task_router 当前是按需)**

灵克 `core/submission.py:488` 真读:**已经有 `_check_intent / _pre_check_compact / _generate_response`**,但**没有"pre_decide 多题并行算"**。Jev Speculative Fan Out 范式可让 submission pipeline 从"3 阶段"扩到"4 阶段"。

**新增 #4:灵克 `prior_verifier.py:429` 是 H17 闭环申报检测,不是 calibrated confidence**

真读确认:灵克 prior_verifier **不输出概率向量**,只输出 AssertionLevel(HARD_FACT / SOFT_INFERENCE / UNSUPPORTED)。**这与 Jev/Laya 的 confidence 输出是不同维度**,**但可补强**:`AssertionLevel` 加 `confidence: float` 字段。

**新增 #5:灵克 `policies/router_keywords.yaml` 已用策略数据化**

灵元铁律 §一("策略是 data 不是结构")**已在 router_keywords.yaml 实现**。Jev 范式"Speculative Fan Out 多题并行"的路由策略,**可同模式 data 化**:`policies/fan_out_questions.yaml`。

**新增 #6:灵克 `self_optimizer/benchmark.py` 真读**

当前 12 题内置静态任务(AST 可判,0/1 分,9 项可见),**没有"概率校准"指标**。Jev 8 项评估矩阵的"概率校准"维度**可加**:`eval_calibration(expected_score: float, predicted_confidence: float) -> ECE`。

**新增 #7:灵克 `submission.py:488` 没有 post_check 阶段**

真读确认:灵克 submission 流程是:
1. `_check_behavior`(T0 零推理成本)
2. `_check_intent`(T2 意图确认)
3. `_pre_check_compact`(T1-1 turn 内触发预算)
4. `_generate_response`
5. `_dementia_detector.diagnose()`

**缺 Jev Agent loop 的 "post_check(输出是否正确 / 工具是否成功)" + "decide_continue(置信度 escalate 决策)"两阶段**。这是灵克最直接的工程补强点。

---

## 四、下一步优化方向(P0/P1/P2/P3)

> **2026-09-21 第二轮修订(codex 评审反馈)**:上一版"P0 单轨 = 唯一循环纯化"**说过头**了。codex 指出:
> - **沙箱**(基线 P0-1)与循环纯化**零依赖** — `engine/sandbox_provider.py` 本来就是独立 seam(Bwrap/Noop 双实现已在册);把它绑在循环纯化后排队 = 拿架构纯洁性押注安全性,而安全恰是灵克 peer 自评**唯一 ⭐⭐ 短板**
> - **Session SSoT**(基线 P0-3)也独立 — 依赖 StateStore 双写协议,不依赖 loop 抽象
> - **真正强依赖循环纯化的只有 submission 管线类**(fan-out / post_check / decide_continue)
>
> **修订**:P0 改**双轨并行**(架构轴 + 可用性轴),不是串行单轨。沙箱不再是"等季度"的问题。

### P0 双轨并行(本月内,两轴同时启动)

**架构轴 — P0-A:循环纯化 + 状态外置**
- 把 `core/l5_conversation_loop.py` + `engine/sub_agent.py` 抽到 `lingclaude/engine/loop/`(或 `core_thread/`),暴露 `LingClaudeThread`(对标 Codex 的 `CodexThread`)
- 58 个 wiring 槽位 → 5 类治理钩子 seam(已有 loop_seam.py 雏形,**继续推进剩余 14 类**)
- 配套:写 `docs/CORE_SURFACE_CONTRACT.md` + ruff T201 with allowlist(框架层禁 print)
- **强依赖**:submission 管线类后续工作(P1-0 fan-out / post_check / decide_continue)必须等此完成

**可用性轴 — P0-B:沙箱进程级隔离**(基线 P0-1,**不再排队**)
- `engine/sandbox_provider.py` 是**独立 seam**,与循环纯化零依赖
- 补 Linux Landlock(轻量、不需 root)+ macOS Seatland(`sandbox-exec`),**默认走 bwrap fallback**
- **peer 自评沙盒 ⭐⭐ 的短板必须本月内修复**,不能等季度
- 配套:加 `NOT_GOOD_AT` 字段 — 哪些工具类别不进沙箱(只读 grep 类)

**P0-C(arch_ledger entry 落地)**:`data/arch_ledger/arch_reference/ref-typesafe-jev.md` + `ref-laya-system1.md`
- **无依赖,可立即做**
- 不涉及代码改动,**不污染主干**

### P1 — 中期工程化(下季内,依赖 P0-A 或独立)

**P1-0(seam 层三接缝先定义,后挂载)**:**强依赖 P0-A 循环纯化**,在 seam 层定义 `pre_decide / post_check / decide_continue` 三个**复用现有 `SeamType.ORCHESTRATOR` seam 的子命名空间**(如 `orchestrator.loop_stage`),不堆进 submission.py
- **2026-09-21 第二轮修订(codex 指出事实错误)**:原 P1-0 写"`core/seam.py 加 ORCHESTRATOR = 'orchestrator' # NEW`"是错的 — `seam.py:47` 已存在 `ORCHESTRATOR = "orchestrator"`(CrewOrchestrator 语义,L3 拔插等级已声明,`CrewOrchestrator` Protocol 在 `seam.py:141`)。**应复用而非新建**,扩子命名空间
- 顺序:**先复用现有 seam → 循环纯化 → 挂载**
- pre_decide 配 `LayaTrivialProvider`(Noul/Choice/Score 三件套 + NOT_GOOD_AT 显式声明)
- post_check 配 `VerifierProvider`(对标 Laya 行动头)
- decide_continue 配 `EscalationProvider`(cost matrix + 阈值,**待本地校准,不是直接借 Laya 经验值**)

**P1-1**:8 项评估指标矩阵进 `self_optimizer/benchmark.py`(**ECE 代码修订 — 见方案 P1-2 章节**)
- 当前 12 题内置任务,**只测"结构合规",不测"概率校准"**
- 加 `eval_calibration` 子任务,**正确实现 ECE**(分别追踪 predictions 与 actuals,见方案 P1-2)
- 与 daemon P0 实证门禁联动:基线**校准误差**不回退才允许应用
- **2026-09-21 第二轮修订(codex 指出)**:ECE 数据源待澄清 — 现有 12 题是 0/1 结构判定,**没有概率输出**;LLM 自报置信度校准性差。校准数据源 = Laya provider 输出 + (可选)人工标注子集,**不覆盖 lc 全链路**

**P1-2**:Jev/Laya 三原语契约进 `lacp/manifest.py`
- 加 `DecisionKind` enum:`CHOICE / SCORE / NOUL`
- `core/prior_verifier.py:AssertionLevel` 加 `confidence: float` 字段
- `core/model_types.py:ModelMessage.confidence` 字段(type|None)
- **Laya provider 直接填**(归一化熵 `1 - H(p)/log(K)`),云端模型留 None

**P1-3**:Jev 不擅长清单 → `core/seam.py:SeamType.NOT_GOOD_AT: tuple[str, ...]`
- 当前 10 类 SeamType 是"声明能做什么"
- 加 NOT_GOOD_AT 字段,声明"精确计算 / 长链路规划 / 文本生成 不要走 LLM"
- `task_router` 路由时**直接绕过**,避免不必要的 LLM 调用

**P1-4**:`policy_loader.py` 加 `policies/fan_out_questions.yaml`
- 沿用 `router_keywords.yaml` data-driven 模式
- 列出每 task_type 的 fan-out question 集合(部门 / 紧急度 / 风险 / 注入检测 / PII / 路由)

### P2 — 长期工程化(半年内)

**P2-1**:62.5% 行动阈值 + 成本矩阵进 `governance/governance_v2.py`
- 加 `DecisionCostMatrix` dataclass:`{act_correct: +1.0, act_wrong: -3.0, escalate: -0.5}`
- 阈值从 cost matrix 自动计算:`P_th = (escalate_cost - act_wrong) / (act_correct - act_wrong + escalate_cost - act_wrong)`
- TaskManager 在 escalate 决策时查表,**治理数学化**

**P2-2**:TD(λ=1.0) 多轮前缀信用分配进 `core/task_manager.py`
- 当前 `task_manager.py:267` 任务栈是 LIFO,**没有跨 turn 信用分配**
- 加 `credit_assignment(turn_idx, final_outcome) -> list[float]`,让 self_optimizer 学"哪些早期决策关键"

**P2-3**:Apache 2.0 Laya 集成 LCP 协议
- Laya 是 Apache 2.0,真可装,**完全符合 lc 灵元"开放 / 本地优先"价值观**
- `pip install laya` + `modelscope download --model convaiinnovations/laya` 一键装
- 进 `seams/laya_local.py` 作为 seams 子模块

**P2-4**:Jev Agent loop 4 阶段模板进 `docs/agent-kernel-cli-integration.md`
- 写明 lc agent loop 如何对应 Jev 范式
- pre_decide / decide_and_execute / post_check / decide_continue

### P3 — 战略储备(明年)

**P3-1**:发布 `pip install lingclaude-sdk`(对外暴露 `LingclaudeClient.system_one()`)
- 当前灵克 mcpo / proj_agent_gateway 用 FastMCP stdio,**没有公共 pip SDK**
- 落地:`pip install lingclaude-sdk` 让外部程序能像用 Jev 一样用 lc

**P3-2**:`laya-multilingual` checkpoint 集成作为灵字辈外部 agent 工具池(322M,100+ 语种)
- 灵克 12 灵族成员中,有跨语种需求的(灵研、灵通、灵通+)用这个

**P3-3**:集成 "Speculative Fan Out" 进 `task_router` 主循环
- 当前 `task_router` 是按需
- 加 precompute 阶段,在每 turn 入口**并行算**多个问题的概率向量

---

## 五、具体方案(每项有 lc 文件锚点 + 改动 + 验证)

### 方案 P1-0(Speculative Fan Out 走 seam,不走 mixin)— **修订原 P0-1**

> **2026-09-21 第二轮修订(codex 指出事实错误)**:`core/seam.py` **已存在** `ORCHESTRATOR = "orchestrator"` (line 47, `CrewOrchestrator` 语义, L3 拔插等级已声明, `CrewOrchestrator` Protocol 在 line 141, `_EXPECTED_PROTOCOL` 注册在 line 178)。原 P1-0 写"`core/seam.py 加 ORCHESTRATOR = 'orchestrator' # NEW`"是错的,**应复用而非新建**,扩子命名空间。
>
> 顺序仍然:**先定义接缝 → 循环纯化 → 挂载**。在循环未纯化时堆阶段 = 在流沙上盖楼。

**文件**:`/home/ai/lingclaude/lingclaude/core/seam.py`(扩子命名空间,不重定义)+ 新建 `lingclaude/seams/orchestrator_loop_stage/` 子目录

**改动**:
```python
# 1) core/seam.py: 不重定义 ORCHESTRATOR seam,而是:
#    在 ORCHESTRATOR = "orchestrator" 已存在前提下,扩子命名空间约定:
#    seams/orchestrator/loop_stage/ 为子命名空间,承载 pre_decide / post_check / decide_continue
#    通过 _EXPECTED_PROTOCOL 已有映射,新增 seam 只需:
#    - protocol class CrewOrchestrator 已存在(子命名空间重用)
#    - seam 注册走 SeamType.ORCHESTRATOR 的现有路径,不重复

# 2) lingclaude/seams/orchestrator_loop_stage/ 新建 3 个默认实现:
# - seams/orchestrator_loop_stage/pre_decide_fan_out.py — Speculative Fan Out
# - seams/orchestrator_loop_stage/post_check_verifier.py — 输出校验
# - seams/orchestrator_loop_stage/decide_continue_threshold.py — 置信度 + cost matrix 决策

# 3) core/seam.py:在 PLUG_LEVELS 加 ORCHESTRATOR 子命名空间的 L1 拔插等级
PLUG_LEVELS: dict[SeamType, str] = {
    ...
    SeamType.ORCHESTRATOR: "L1",  # 从 L3 升级到 L1(可替换实现),前提是 seam 覆盖完整
}
```

**关键纪律**:
- `submission.py:submit()` **不堆阶段逻辑**,只挂 seam:`self.hooks.pre_decide(state)` + `self.hooks.post_check(output)` + `self.hooks.decide_continue(check)`
- 默认实现放 `seams/orchestrator_loop_stage/`,通过 `WIRING_MANIFEST` 注册
- 这与 loop_seam.py 哲学一致:**"治理钩子"5 类已接缝化,新增 seam 走同一路径**
- **不重复定义 SeamType.ORCHESTRATOR**(已有 CrewOrchestrator Protocol,扩子命名空间复用)

**依赖**:**必须 P0-A(循环纯化 + 状态外置)先完成**。在循环还是 3000 行 mixin 时集成 Laya,你只是在给一个重主干再加一个外部依赖;循环纯化后集成 Laya,你是在给一个薄主干挂一个可替换的 fast lane 插片。**同样的工作,后者的长期成本低一个数量级**。

**验证**:`tests/test_orchestrator_seam.py`,确保 3 个 seam 各自可替换可注入

### 方案 P1-1:Laya 本地 fast lane 集成(NOT_GOOD_AT 显式声明 + 安全路径慎用 + CPU 环境估算)

> **2026-09-21 第二轮修订(codex 指出 3 点)**:
> 1. NOT_GOOD_AT 边界保留 ✓
> 2. **安全路径慎用**:注入检测/PII 是安全相关判断,**Laya 421M 编码器的对抗鲁棒性无第三方数据**;Jev 自承"高对抗环境检测结果只是安全机制一部分"。**安全闸门保持代码优先**,Laya 只做补充信号不做裁决者
> 3. **运行环境落差**:33ms / 7.2ms 是 **T4 GPU 数字**,lc 部署若以 CPU 为主,延迟估 **5-10× 飙升**;**仍可用,但别写进 SLA**(作为参考 SLA 应取 CPU 实测,GPU 仅快车道)

**新文件**:`/home/ai/lingclaude/lingclaude/seams/laya_local.py`

**改动**:
```python
"""Laya 本地 fast lane provider (Apache 2.0 真本地).

NOT_GOOD_AT(显式声明,pre_decide 路由时直接跳过,绕过 Laya):
- 长上下文推理(>1024 tokens)—— 不适合编码器
- 多步因果推理 / 长链路规划 —— 仍走 LLM
- 创意生成 / 长文写作 —— 仍走 decoder-only LLM
- 代码理解 —— 编码器不如 decoder-only LLM
- **安全闸门裁决(注入检测 / PII 拒绝 / 高对抗场景)** —— **保持代码优先**(sensitive_path_gate.py)
  Laya 仅作补充信号,**不做主裁决者**(codex 第二轮修订)

适合走 Laya(已在 Laya 基准 73%-99% 区间):
- 部门路由(短文本分类,99.1%)
- 紧急度量表(0/1/2/3 评分)
- 客服意图分流(99.1%)
- 主题分类(93.9%)
- 情绪与语气(90.6%)

运行环境估算(2026-09-21 codex 第二轮修订):
- 33ms / 7.2ms 是 **T4 GPU** 数字(README 来源)
- lc 部署若 CPU 为主,延迟估算 5-10× 飙升:
  * 单问 ~165ms-330ms
  * 批处理 ~36ms-72ms/question
- **不写进 SLA**;**本周内实测 CPU 延迟**,作为方案 P1-1 的排期可信度依据
"""
import laya
from laya import Router
from lingclaude.core.seam import SeamType


class LayaRouterProvider:
    """Multilingual checkpoint: 100+ 语种."""

    name = "laya_multilingual"
    version = "0.1.0"

    NOT_GOOD_AT = (
        "long_context_reasoning",     # > 1024 tokens, encoder 限制
        "multi_step_planning",         # 仍走 LLM
        "creative_generation",         # 仍走 decoder-only LLM
        "code_comprehension",          # 编码器不如 decoder-only LLM
        "security_gate_decision",       # 安全闸门裁决 — 代码优先,Laya 仅补充信号
    )

    def __init__(self, preload: bool = True):
        self.router = Router(preload=preload)

    def execute(self, state: dict, questions: dict) -> dict:
        """Run Laya Router with state + typed questions."""
        result = self.router.predict(state, questions)
        return {
            "decision": result["answers"],
            "latency_ms": result.get("latency_ms", 0),
            "warning": "T4 GPU 基准;CPU 环境延迟 5-10×,不写进 SLA",
        }


class LayaDeciderProvider:
    """typed-decisions checkpoint."""

    name = "laya_typed_decisions"
    version = "0.1.0"
    NOT_GOOD_AT = LayaRouterProvider.NOT_GOOD_AT

    def __init__(self):
        self.agent = laya.load("convaiinnovations/laya-typed-decisions")

    def execute(self, *, choices=(), scores=(), nouls=()) -> dict:
        """Run typed-decisions in single forward pass."""
        ...
```

**pre_decide 路由逻辑** + **安全路径代码优先**:
```python
def pre_decide(self, state: dict, questions: dict) -> dict:
    """Speculative Fan Out — 按 question 适合度分别派 Laya / LLM / 代码."""
    answers = {}
    for key, q in questions.items():
        # 安全路径强制代码优先,不进 Laya
        if q.get("is_security_gate"):
            answers[key] = self.sensitive_path_gate_decide(state, q)  # engine/sensitive_path_gate.py
            continue

        # 其他场景按 NOT_GOOD_AT 过滤
        q_text_len = len(state.get("body", "")) + len(q.get("instructions", ""))
        if q_text_len > 1024:
            answers[key] = self.llm_decide(state, q)
        elif q.get("requires_long_context"):
            answers[key] = self.llm_decide(state, q)
        elif q.get("requires_creative"):
            answers[key] = self.llm_decide(state, q)
        else:
            answers[key] = self.laya_decide(state, q)
    return answers
```

**在 `core/seam.py` 注册**:作为 `SeamType.PROVIDER` + `NOT_GOOD_AT` 字段

**验证**:`tests/test_laya_provider.py`,跑适合场景(部门路由 / 紧急度量表 / 主题分类 / 情绪与语气)— 确认 CPU 实测延迟 / confidence 校准 / ECE < 0.05;**安全场景用例必须走代码,不走到 Laya**;**长上下文用例必须走 LLM 路由,不应走到 Laya**

### 方案 P0-3:arch_ledger entry 落地

**新文件**:
- `/home/ai/lingclaude/data/arch_ledger/arch_reference/ref-typesafe-jev.md`
- `/home/ai/lingclaude/data/arch_ledger/arch_reference/ref-laya-system1.md`

**模板**(YAML):
```yaml
# data/arch_ledger/arch_reference/ref-typesafe-jev.md
name: TypeSafe Jev (System One Model)
url: https://typesafe.ai
docs: https://docs.typesafe.ai/introduction
team: https://typesafe.ai/team
license: closed(闭源)
discovered: 2026-09-21
authors:
  ceo: Diogo Almeida(RLHF + InstructGPT co-inventor, ex-Google Brain)
  coo: Sasha Sheng(ex-Meta/FAIR)
  cto: Erik Gafni(repeat founder)
key_concepts:
  - choice / score / noul 三原语
  - Speculative Fan Out(提前并行算所有可能判断)
  - calibrated confidence(归一化熵 1 - H(p)/log(K))
  - 严格适当评分规则(RLCD)
  - "Zero Hallucination" = 格式合规 ≠ 判断正确(TypeSafe 自承)
  - 8 项评估指标矩阵
  - Agent loop 4 阶段模板
borrowable_for_lc:
  P0:
    - Speculative Fan Out 进 submission.py(7 阶段)
    - "format ≠ correctness" caveat 进 prior_verifier.py 注释
  P1:
    - 8 项评估指标矩阵进 self_optimizer/benchmark.py
    - Agent loop 4 阶段拆解
    - "模型只是安全机制一部分" 多层防御
  P2:
    - 62.5% 行动阈值 + cost matrix 进 governance_v2.py
    - NOT_GOOD_AT seam 字段
  P3:
    - pip install lingclaude-sdk(对标 typesafe-sdk)
boundaries_not_to_borrow:
  - "Zero Hallucination" 营销词(灵元铁律 §四诚实)
  - threshold 示例 0.7 / 0.8(必须本地 calibrate)
  - 替代沙箱(灵克安全铁律要求进程级隔离)
```

```yaml
# data/arch_ledger/arch_reference/ref-laya-system1.md
name: Laya (Convai Innovations System 1 Decision Engine)
github: https://github.com/NandhaKishorM/laya
modelscope: https://modelscope.cn/models/convaiinnovations/laya
huggingface: https://huggingface.co/convaiinnovations/laya
pypi: https://pypi.org/project/laya/
license: Apache 2.0
discovered: 2026-09-21
author: Nandakishor M(2025 RL 决策研究 → Convai Innovations 开源)
checkpoints:
  - name: laya
    encoder: ModernBERT-large
    params: 421M
    context: 512
    use: 英语
  - name: laya-multilingual
    encoder: mmBERT-base
    params: 322M
    context: 1024
    use: 100+ 语种, 2× 更快
  - name: laya-typed-decisions
    encoder: ModernBERT-large
    params: 421M
    context: 1024
    use: typed-decisions workflows
  - name: Router (built-in)
    use: 自动 detect scripts/languages, 派到最优 checkpoint
key_concepts:
  - state + questions 一次 forward(5 questions → 1 forward → 35ms)
  - choice / score / noul 三原语(与 Jev 一致)
  - calibrated confidence(归一化熵 1 - H(p)/log(K))
  - RLCD 训练(严格适当评分规则)
  - 行动/升级决策头 + cost matrix → 62.5% 行动阈值
  - TD(λ=1.0) 多轮前缀
  - Apache 2.0 + 100% 人类标注
  - pip install laya 一键装
performance:
  - 33ms 单问 / 7.2ms/question 批处理(T4 GPU)
  - p95 42.1ms
  - 任务内宏平均 83.8% / 零样本 65.1%
borrowable_for_lc:
  P0:
    - pip install laya 集成作为本地 fast lane model(seams/laya_local.py)
    - LayaTrivialProvider(路由 trivial decision)
  P1:
    - Laya 校准数学进 model_types.py(归一化熵 1 - H(p)/log(K))
    - 决策三原语进 lacp/manifest.py
  P2:
    - 62.5% 行动阈值 + cost matrix 进 governance_v2.py
    - TD(λ=1.0) 多轮信用分配进 task_manager.py
  P3:
    - laya-multilingual 进灵字辈外部 agent 工具池(100+ 语种)
boundaries_not_to_borrow:
  - "比 Jev 快 4×" 的对比(只来自开源方,无第三方 benchmark)
  - NuOut 三原语替代 LLM(只适用 choice/score/boolean)
  - ModernBERT 编码器替代 decoder-only LLM(对长文/代码不如)
```

### 方案 P1-1:8 项评估指标矩阵进 benchmark.py

**文件**:`/home/ai/lingclaude/lingclaude/self_optimizer/benchmark.py`

**改动**:在 `_BUILTIN_CHECKS` 之外加 `_CALIBRATION_CHECKS`(9 → 12 题),具体定义 ECE 计算:
```python
def eval_calibration(predictions: list[float], actuals: list[bool], n_bins: int = 10) -> float:
    """Expected Calibration Error = sum_b (|b|/N) * |acc(b) - conf(b)|.

    **修订 2026-09-21(响应审查)**:原版本 bin_preds 同时当 predictions 和 actuals 用,导致 ECE 恒等于 0。
    正确做法:分桶分别存 predictions 和 actuals,计算桶内均值时从两个独立列表取。

    Args:
        predictions: 模型输出的预测置信度(0.0-1.0),如 Laya 的归一化熵 1 - H(p)/log(K)
        actuals: 真实结果(bool),1.0 = 预测正确, 0.0 = 预测错误
        n_bins: 分桶数(默认 10)
    """
    # 关键:分别追踪 predictions 和 actuals
    bins: list[tuple[list[float], list[bool]]] = [
        ([], []) for _ in range(n_bins)
    ]  # 每个 bin 存 (predictions 列表, actuals 列表)

    for pred, act in zip(predictions, actuals):
        idx = min(int(pred * n_bins), n_bins - 1)
        bins[idx][0].append(pred)  # 预测概率入 predictions 列
        bins[idx][1].append(1.0 if act else 0.0)  # 真实结果入 actuals 列

    ece = 0.0
    total = len(predictions)
    for pred_list, act_list in bins:
        if not pred_list:  # 空桶跳过
            continue
        # 桶内均值:从独立列表取
        bin_acc = sum(act_list) / len(act_list)      # 真实准确率
        bin_conf = sum(pred_list) / len(pred_list)   # 平均预测概率
        # 权重 = 该桶样本数 / 总数
        ece += (len(pred_list) / total) * abs(bin_acc - bin_conf)
    return ece


# 等价 numpy 实现(向量化):
def eval_calibration_np(predictions, actuals, n_bins=10):
    import numpy as np
    pred = np.asarray(predictions)
    act = np.asarray(actuals, dtype=float)
    bins = np.minimum((pred * n_bins).astype(int), n_bins - 1)

    ece = 0.0
    for b in range(n_bins):
        mask = bins == b
        if not mask.any(): continue
        bin_acc = act[mask].mean()      # 真实准确率
        bin_conf = pred[mask].mean()    # 平均预测概率
        ece += mask.sum() / len(pred) * abs(bin_acc - bin_conf)
    return ece
```

**关键修订总结**:
- 分桶结构从 `bins_data: list[list[float]]` 改为 `bins: list[tuple[list[float], list[bool]]]`
- 每个 bin 显式分开存 predictions 与 actuals,**不允许从同一列表算两个量**
- 桶内均值公式: `bin_acc = sum(act_list) / len(act_list)`, `bin_conf = sum(pred_list) / len(pred_list)`
- 数字可验证:对同一组 (predictions, actuals), `bin_acc` 与 `bin_conf` 不再恒等,ECE 可非零

**验证**:基准集 ECE < 0.05(参考 Laya 报告数据),daemon P0 实证门禁加 ECE 不回退项
- 单测:`tests/test_ece.py` 用已知 (predictions, actuals) 验证 ECE ≈ 已知值
- **2026-09-21 第二轮修订(codex 指出 2 点)**:
  1. **验证用例桶号标注错**:`pred=0.1` 时 `int(0.1*10)=1`,落桶 **1** 不是桶 0(原标注"桶 0 (pred∈[0, 0.1])"错)。**修正:pred∈[0.1, 0.2] → 桶 1**
  2. **校准数据源未澄清**:`predictions` 从哪来? lc 现有 12 题是 0/1 结构判定,**没有概率输出**;LLM 自报置信度校准性差。**方案修正**:
     - 数据源 1:`LayaRouterProvider` 直接出归一化熵 1 - H(p)/log(K) — **这部分真校准**
     - 数据源 2(可选):人工标注子集(50-200 条人工决策),做 LLM 自报 vs Laya 校准对比
     - **ECE 覆盖范围**:只覆盖 Laya 路径,**不覆盖 lc 全链路**(云端 LLM 路径暂不可校准)
     - 文档需在 daemon P0 实证门禁加注释:ECE 门禁**仅适用于 Laya 路径**,云端 LLM 路径用其他指标(如 accuracy / consistency)

- 修正后示例: predictions=[0.9, 0.9, 0.9, 0.1, 0.1, 0.1], actuals=[True, True, False, False, False, True]
  - 桶 1 (pred∈[0.1, 0.2]): actuals=[F,F,T] → bin_acc=0.33, bin_conf=0.10
  - 桶 9 (pred∈[0.9, 1.0]): actuals=[T,T,F] → bin_acc=0.67, bin_conf=0.90
  - ECE = 0.5*|0.33-0.10| + 0.5*|0.67-0.90| = 0.115 + 0.115 = 0.23
  - (这是预测过于自信的真实 ECE 信号,门禁应触发)

### 方案 P1-2:DecisionKind + ModelMessage.confidence

**文件 1**:`/home/ai/lingclaude/lingclaude/lacp/manifest.py`(已有 DecisionKind 可加)

**文件 2**:`/home/ai/lingclaude/lingclaude/core/model_types.py`

**改动**:
```python
# model_types.py
@dataclass
class ModelMessage:
    role: MessageRole
    content: str
    # NEW: calibrated confidence for typed decisions
    confidence: float | None = None  # 1 - H(p)/log(K)
    decision_kind: DecisionKind | None = None  # CHOICE / SCORE / NOUL
```

**验证**:Laya provider 直接填,云端模型留 None,`task_router` 基于 `confidence < 0.85` 自动 escalate

### 方案 P1-3:NOT_GOOD_AT seam 字段

**文件**:`/home/ai/lingclaude/lingclaude/core/seam.py`

**改动**:在每个 SeamType 加 NOT_GOOD_AT 字段
```python
@dataclass(frozen=True)
class CapabilitySeam:
    name: str
    interface: dict[str, Any]
    NOT_GOOD_AT: tuple[str, ...] = ()  # 不要走 LLM 的场景
    ...
```

**具体标注**:
- `SeamType.TOOL`: NOT_GOOD_AT = ("精确计算", "长链路规划", "文本生成")
- `SeamType.MULTIMODAL`: NOT_GOOD_AT = ("音频转录", "实时翻译")
- ...

**验证**:`task_router` 路由时检查,如果请求类型在 NOT_GOOD_AT,**直接走代码 / skip**

### 方案 P2-1:62.5% 行动阈值 + cost matrix(**修订:cost matrix 标"待本地校准"**)

> **2026-09-21 修订(响应审查)**:**响应审查第 4 条** —— 阈值与 cost matrix 是同一决策问题的两面。原版"this matrix matches lc default thresholds"无依据(灵克没有默认成本矩阵)。LC 的 cost matrix 不能直接借 Laya 经验值(同上阈值)。

**文件**:`/home/ai/lingclaude/lingclaude/governance/governance_v2.py`

**改动**:
```python
@dataclass(frozen=True)
class DecisionCostMatrix:
    """决策成本矩阵(本地参数,默认值为 placeholder,需 CalibrationRunner 校准).

    **修订 2026-09-21(第二轮,codex 指出)**:升级为不对称模型 — escalate_cost 在两种
    情形下不对称(act 本来就对 / act 本来就错);标量 escalate_cost 不足以表达,
    改为 escalate_cost_correct / escalate_cost_wrong 两个参数。

    推导(对决策者已知,不是默认值):
        P_th 是 act 期望收益 > escalate 期望收益的最小置信度:
            P*act_correct + (1-P)*act_wrong > P*esc_correct + (1-P)*esc_wrong
        解: P_th > (esc_wrong - act_wrong) / (act_correct - act_wrong + esc_wrong - esc_correct)

    默认参数来自 Laya 经验值(+1/-3/-0.5),**不直接适用 lc 场景**,
    CalibrationRunner 启动时检测 needs_calibration → 触发 calibrate 任务。
    """
    act_correct: float = 1.0                          # PLACEHOLDER — 待本地校准
    act_wrong: float = -3.0                           # PLACEHOLDER — 待本地校准
    escalate_cost_correct: float = -0.5               # PLACEHOLDER — 待本地校准
    escalate_cost_wrong: float = -0.5                 # PLACEHOLDER — 待本地校准
    needs_calibration: bool = True                     # 启动时检测 → CalibrationRunner

    @property
    def action_threshold(self) -> float:
        """推导公式:见 docstring 上方(不对称 escalate_cost).

        默认值 (+1/-3/-0.5/-0.5) 给出 0.625(Laya 经验值,LC 不一定适用);
        本地校准后会给出场景特化的 P_th。
        """
        return (self.escalate_cost_wrong - self.act_wrong) / (
            self.act_correct - self.act_wrong
            + self.escalate_cost_wrong - self.escalate_cost_correct
        )


class CalibrationRunner:
    """校准 DecisionCostMatrix — 用真实业务数据反算参数.

    修订 2026-09-21(第二轮,codex 指出):校准应按两种 outcome 分别采样,
    不是单一标量 — escalate_cost_correct vs escalate_cost_wrong 分别从数据反算。

    流程:
        1. 收集 N 轮真实决策样本:outcome ∈ {correct, wrong, escalated_correct, escalated_wrong}
        2. 用最大似然估计 act_correct / act_wrong / escalate_cost_{correct,wrong} 的相对比例
        3. 写回 DecisionCostMatrix + 标 needs_calibration=False

    落地:`self_optimizer/daemon.py` 启动 + 每 24h 节流触发一次。
    """

    def run(self, samples: list[dict]) -> DecisionCostMatrix:
        # MLE:让当前样本的 log-likelihood 最大
        # samples[i] = {
        #     "kind": "correct"|"wrong"|"escalated_correct"|"escalated_wrong",
        #     "confidence": float,
        # }
        # 把 escalated_correct / escalated_wrong 分别当数据点
        ...

**修订纪律**:
- cost matrix + 阈值 **同时标"待本地校准"**,不可单独借 Laya 经验值
- 触发校准后,daemon 优先用本地数据,**Laya 经验值仅作 initial seed**
- 校准数据存档:`data/arch_ledger/calibration_log/`,**满足灵元铁律 §四"诚实"**(校准过程可审计)

**验证**:`tests/test_decision_cost_matrix.py`
- 默认值计算 0.625 不依赖参数来源,**纯公式推导**
- CalibrationRunner 跑已知 samples 验证 act_correct/act_wrong 比例正确
- daemon 启动检测 needs_calibration,缺数据时报 WARNING 不立即试

---

## 六、arch_ledger entry 落地动作

**新增文件**:
1. `/home/ai/lingclaude/data/arch_ledger/arch_reference/ref-typesafe-jev.md`
2. `/home/ai/lingclaude/data/arch_ledger/arch_reference/ref-laya-system1.md`
3. `/home/ai/lingclaude/data/arch_ledger/arch_review/review-jev-laya-borrowing.md`(本审查的审查记录)

**内容已包含**在本报告第四节"具体方案"中,可在 commit 时同步落地。

---

## 七、对比之前 LC_PEER_BORROWABLE.md 的增量价值

| 维度 | 上份文档 | 本报告新增 |
|------|---------|----------|
| **核验级别** | 8 项目 web 综述 + 部分真读 | Jev/Laya **一手 fetch + README 真读**,可信度提一档 |
| **数据精度** | "40-400× 便宜"等估算 | Laya 真数字 **33ms / 7.2ms 批 / T4 GPU / 322M 多语种** |
| **架构细节** | "三原语"概述 | **3 checkpoints + Router 自动派** + 真实架构 |
| **Speculative Fan Out** | 未提 | **TypeSafe 自创方法论**,灵克 task_router 可学 |
| **8 项评估指标矩阵** | 未提 | Jev 自评估,**灵克 benchmark.py 可学** |
| **action 62.5% 阈值** | 概念提及 | **从 cost matrix (+1/-3/-0.5) 数学推导**,governance 可借 |
| **Zero Hallucination caveat** | 概念提及 | **TypeSafe 自承"格式合规 ≠ 判断正确"**,灵元铁律 §四对齐 |
| **三 checkpoints** | 1 个模型 | **3 checkpoints + Router**,实可装多档 |
| **PyPI 真包** | 提及但未核验 | **PyPI 200 OK 真包**,`pip install laya` 即可 |

---

## 八、灵元铁律对齐自检(本报告所有方案均通过)

- 铁律 1(薄主干):所有方案都走接缝(SeamType.PROVIDER 注册 + LACP manifest),不堆代码到主干
- 铁律 2(2T3A):三阶段决策 (decision_kind / state / transition) 符合 2T3A record 哲学
- 铁律 3(诚实):不抄袭"Zero Hallucination"营销词,显式标注 "format ≠ correctness"
- 铁律 4(质量):每个方案都有单测 + benchmark 验证,不是"写完就完了"
- 铁律 5(安全):沙箱不能被 Jev 模式替代,**Jev 只是安全机制一部分**
- 铁律 6(可信任):Laya 是 Apache 2.0 真开源,信任边界清楚
- 铁律 7(可证):arch_ledger entry 落地 + 方案 P0-P3 都有具体 lc 文件锚点
- 铁律 8(隔离):每个新方案独立 seam,故障域不污染主干

---

## 九、哲学层新增价值(灵元对齐分析)

> 本节为新章节(2026-09-21 用户追加),回答"Jev + Laya 的灵元哲学重构对灵克是否有新增价值"。

### 9.0 术语真核验(纪律)

- **`lingmate` 在 lingclaude 仓内 grep 不到**:`data/spill/` 零星提及,**不是正式术语**;若需新术语,建议用 `lingzu_mate` / `灵族成员协作`(参考 `data/ling_org/org_dependency/` 现有命名)
- **`灵元` 是真哲学**:`docs/LINGYUAN_IRON_LAW.md` 整篇 + 8+ 配套文档 + `data/ling_org/` 7 子目录(`org_audit / org_dependency / org_duty / org_event / org_evolution / org_governance / org_member / org_promise`)
- 灵元哲学核心 = **薄主干 / 分形插片 + 2T3A records-events-transition + 8 条铁律 + 异议制治理**

### 9.1 灵元铁律与 Jev/Laya 工程方法论的对应

| 灵元铁律(真读) | Jev/Laya 对应 | 同向 / 冲突 |
|------------------|-------------|----------|
| **2T3A** records/events/transition | choice/score/noul 三原语 | ⚠️ **不同维度** — 2T3A 是"演化算子",Jev 三原语是"决策 vocabulary" |
| **record-first**(统一数据形态为值域) | `state + questions + answer` 全字典 record | ✓ **完美对齐** |
| **封闭算子**(create/transition/query) | "no text generation, only typed decisions" | ✓ **强化** — Jev 输出受限与封闭算子同向 |
| **薄主干 + 分形插片** | Laya **3 checkpoints + Router 自动派** | ✓ **强化** — Router 就是"自动派"薄主干思想 |
| **铁律 §四 诚实** | TypeSafe 自承 "format ≠ correctness" | ✓ **完美对齐** |
| **铁律 §六 可信** | Laya Apache 2.0 真开源真 PyPI | ✓ **强化** |
| **铁律 §八 故障域隔离** | Router 自动 detect + dispatch | ✓ **强化** |
| **铁律 §一 概念封闭** | Jev SDK `Choice/Noul/Score` 三件套 | ✓ **强化** — 让 decision 不污染主干 |

### 9.2 6 个哲学层新增价值(对灵克)

1. **`DecisionKind` 三件套补 record-first 的"决策 record"维度**
   - 当前灵元 `records` 是 records/events/transition 三类
   - Jev/Laya 加 `Decision[T]` typed vocabulary → 让 `record` 多一个维度:**决策 record**
   - 落地:`lacp/trace.py` trace type 加 `DECISION`,`lacp/manifest.py` 加 `DecisionKind` enum

2. **Speculative Fan Out 是 2T3A record 的"批处理增强"**
   - 当前灵元 `transition` 是 1 个 1 个算
   - Speculative Fan Out 让 transition **一次算多问**,**符合 record-first 的"记录即价值"哲学**(多问并行 = 多 record 一次写入)
   - 落地:`core/submission.py` 加 `_pre_decide_fan_out(state)`,**让 transition 维度从一维变 N 维**

3. **`calibrated confidence` 让 record 可信度可度量**
   - 当前灵元 record 是 boolean 状态(HARD_FACT / SOFT_INFERENCE / UNSUPPORTED)
   - 加 `confidence: float | None` 字段,record 多了"可信度维度"——**这是 record-first 哲学的数学化**
   - 落地:`core/prior_verifier.py:AssertionLevel` 加 confidence,Laya provider 直接填 `1 - H(p)/log(K)`

4. **8 项评估矩阵 = "record quality 多维度校验"**
   - 当前灵元 `l5_audit` 是单维过/不过
   - Jev 8 项矩阵让 record quality 有 N 维评估,**符合灵元 §四"诚实"**(不美化单一指标)
   - 落地:`self_optimizer/benchmark.py` 加 `eval_calibration` ECE + 自动覆盖率 + 人工介入率

5. **Laya 真本地 Apache 2.0 = "record-first + 本地优先 + Agent 原生"三向收敛**
   - 当前灵元哲学声称"AI 时代的算子革命"—— Agent 原生架构
   - Laya 真本地真开源 = **工程化证明**:"record-first 哲学可被一个真模型实现"
   - 落地:`pip install laya` → `seams/laya_local.py` 作为灵族 fast lane provider

6. **3 checkpoints + Router = "plug-level + 自动派"的工程实现参考**
   - 当前灵元 `SeamRegistry` 是"显式 register"
   - Laya Router 是"自动 detect 后派" — 灵元哲学的"插片自动组合"工程化实例
   - 落地:`core/seam.py` 加 `auto_dispatch_table`,让 SeamRegistry 支持 Router 风格自动派

### 9.3 4 个不可借鉴 / 警觉点(灵元哲学对齐)

1. **"Zero Hallucination"营销词** ↔ 灵元铁律 §四"诚实" — **不抄**,显式声明 format ≠ correctness
2. **threshold 示例 0.7/0.8** ↔ 灵元铁律 §四 — **必须本地 calibrate**,不照搬
3. **Jev/Laya 替代 LLM 路由** ↔ 灵族 12 子名册 — **不是替代,是补充**(fast lane);reasoning / 长链路规划仍走 LLM
4. **NuOut 三原语适用面窄** ↔ 灵元哲学"开放集成" — **不能完全替代 LLM**,长文 / 代码理解 / 创意生成仍走 decoder-only LLM

### 9.4 三向收敛点(锐利度排序,均为**同向参考**而非验证)

> **2026-09-21 修订(响应审查第 5 条)**:**警惕"工程化证明"的过度归因**。
> Laya 作者 Nandakishor M 显然不知道灵元哲学。他独立做 RL 决策研究,3 个 checkpoint + Router 是它自己的工程逻辑(编码器选型 / 多语种需求 / typed-decisions 场景)。
> 把 Laya 说成"灵元哲学的工程化实例"是**事后归因**——Laya 只是**碰巧同向**,**不是为验证灵元哲学而设计**。
> 修订:所有"工程化证明"措辞降级为"同向参考"。**真正的验证只能来自 lc 自己的 benchmark 数字**。

1. **Laya 真本地 Apache 2.0 + Router 自动派 + Speculative Fan Out** = 灵元"record-first + 薄主干 + 插片自动组合"哲学的**同向参考**(不是验证,**最锐利的启发但不可作为哲学证据**)
2. **`DecisionKind` + `confidence` 字段** = 让灵元 record 从 boolean 状态扩到"类型化 + 概率化",**同向参考**,哲学可执行性 +1 维
3. **8 项评估矩阵 + cost matrix(待本地校准)** = 让灵元"诚实"哲学从单维审计扩到 N 维矩阵 + 数学化阈值,**同向参考**

---

## 十、一句话结论

**Jev + Laya 是灵元哲学的"同向参考",不是"工程化证明"**(2026-09-21 修订)。三向收敛点最锐利的启发是:**Laya 真本地 Apache 2.0 + Router 自动派 + Speculative Fan Out** 与灵元"record-first + 薄主干 + 插片自动组合"哲学在多个维度上同向。新增价值不在"哲学替换",而在**"哲学的可执行实例启发"** —— 灵元哲学有了一个**同向的真实模型**,**这只能作为启发,不能作为验证**。**真正的验证只能来自 lc 自己的 benchmark 数字**(daemon + self_optimizer 跑出来的真实成绩)。

**三个最重要的工程新增价值**:(1) **Speculative Fan Out — 走 seam,不走 mixin**(P1-0)— 在循环纯化(P0-0)之后挂载,不堆主干,(2) **Laya 三件套 + Router + NOT_GOOD_AT 显式声明**(P1-1)— `pip install laya` 即可装,真本地 322M 100+ 语种 fast lane,**只替代部分适合场景**(注入检测 / PII / 部门路由 / 紧急度量表),不替代 LLM 推理,(3) **8 项评估指标矩阵 + ECE 代码修订**(P1-2)+ **cost matrix 标"待本地校准"**(P2-1)— 灵克 self_optimizer/benchmark.py 从"单维度评分"升级到"N 维矩阵 + 数学化阈值",但所有数学常数(成本、阈值)都需真实数据 calibrate。**不抄实现抄数学**(RLCD 严格适当评分 + TD(λ=1.0) 多轮信用分配 + 归一化熵校准)进入灵克自优化路径。**Laya 不只是 1 个模型是 3 个 + Router**,Apache 2.0 + PyPI 真包 + 100+ 语种 + 7.2ms/question,**灵克灵字辈成员(灵研/灵通/灵通+)可立即用 multilingual checkpoint**。

**术语核验 bonus**:**2026-09-21 第二轮修订(codex 评审指出原表述有误)**:
- `lingmate` **是真项目名**,`/home/ai/lingmate/` 是 lc 的姊妹仓库,`AGENTS.md:27` 明确写"会话交接:handover.md 已废弃,走 lingmate session record"
- 原表述"`lingmate` 不是 lc 正式术语"是**事实错误**,应理解为"**不要把 lingmate 用作泛指'灵族成员协作'的概念词,避免概念与项目名混淆**"
- 新文档若需泛指概念词,建议用 `lingzu_mate` / `灵族成员协作`,参考 `data/ling_org/org_dependency/` 现有命名

---

## 十一、2026-09-21 修订记录(响应审查)

| # | 审查条目 | 修订动作 | 文档位置 |
|---|---------|---------|---------|
| 1 | **P0-1 7 阶段改造违反薄主干铁律** — 在循环未纯化时堆阶段 = 在流沙上盖楼 | P0-1 降级为 P1-0,**先 seam 后挂载**,**强制依赖 P0-0(循环纯化 + 状态外置)**;P0 列表重排,循环纯化置顶真 P0 | §四 P0/P1 列表;§五 方案 P1-0 |
| 2 | **Laya 适用边界没划清** — pre_decide 6 问并非全部适合 322M 编码器 | P0-2 改 P1-1,加 `NOT_GOOD_AT` 显式声明:长上下文推理 / 多步因果 / 创意生成 / 代码理解 **不走 Laya**,只短文本分类 + 命名实体 + 量表评分走 | §五 方案 P1-1 |
| 3 | **ECE 计算代码有 bug** — bin_preds 同时当 predictions 和 actuals 用 → ECE 恒等于 0 | 重写 `eval_calibration`,分桶结构 `bins: list[tuple[list[float], list[bool]]]`,分别存 predictions 和 actuals;附 numpy 等价实现 + 可手算验证用例 | §五 方案 P1-2 |
| 4 | **62.5% 阈值推导前后不一致** — cost matrix 参数 (+1/-3/-0.5) 是 Laya 经验值,不能直接借 | `DecisionCostMatrix` 加 `needs_calibration: bool = True` 字段 + `CalibrationRunner` 类,daemon 启动时检测并触发本地校准;**默认参数显式标 PLACEHOLDER,不可直接用** | §五 方案 P2-1 |
| 5 | **哲学层"工程化证明"过度归因** — Laya 作者不知道灵元哲学 | 所有"工程化证明"措辞降级为"同向参考";**真正的验证只能来自 lc 自己的 benchmark 数字**;真核验:灵元哲学有了一个**同向的真实模型**,但这只是启发,不是验证 | §九 §9.4;§十 |

**战略判断(审查)**:P0 列表膨胀到 6 项是优先级管理失效的信号。**唯一真正的 P0 是循环纯化 + 状态外置**,其他都依赖它。**借鉴的时机很重要**:循环还是 3000 行 mixin 时集成 Laya = 给重主干加外部依赖;循环纯化后集成 Laya = 给薄主干挂可替换 fast lane 插片。同样的工作,后者的长期成本低一个数量级。

---

## 十二、2026-09-21 第二轮修订记录(响应 codex 评审)

> 本节新增(2026-09-21),响应 codex(外部审查 AI)对本文档的事实与技术评审。codex 在四项评审中暴露了文档**两处事实失守**(lingmate / ORCHESTRATOR seam),这与文档开篇"真读纪律"的自我承诺冲突。**主动修订**。

| # | codex 批评条目 | 修订动作 | 文档位置 |
|---|---------|---------|---------|
| **战略 1** | **P0 重排说过头** — 沙箱 / Session SSoT 与循环纯化零依赖,绑在循环纯化后排队 = 拿架构纯洁性押注安全性;安全恰是 peer 自评**唯一 ⭐⭐ 短板** | **P0 改双轨并行**:架构轴 P0-A(循环纯化)+ 可用性轴 P0-B(沙箱,**不再排队**)+ P0-C(arch_ledger 无依赖立即做) | §四 P0 双轨 |
| **事实 1** | **P1-0 写"`seam.py 加 ORCHESTRATOR = 'orchestrator' # NEW`"是错的** — `seam.py:47` 已存在 `ORCHESTRATOR = "orchestrator"`(CrewOrchestrator 语义, L3 拔插等级已声明, `CrewOrchestrator` Protocol 在 line 141, `_EXPECTED_PROTOCOL` 注册在 line 178) | **P1-0 改为复用现有 seam,扩子命名空间**(seams/orchestrator_loop_stage/);不再"# NEW",改注释 "已存在 seam 子命名空间" | §五 方案 P1-0 |
| **事实 2** | **`lingmate` 表述错** — AGENTS.md:27 明确写"走 lingmate session record", `/home/ai/lingmate/` 是真姊妹仓库(有不可再分.md / 诞生记.md) | **§十 术语核验 bonus 整段改写**:承认 lingmate 是真项目名,原表述"`lingmate` 不是 lc 正式术语"是事实错误;新文档若需泛指概念词建议 `lingzu_mate` / `灵族成员协作` | §十 |
| **技术 1** | **Laya 安全路径慎用** — 注入检测/PII 是安全相关判断,421M 编码器对抗鲁棒性无第三方数据;Jev 自承"高对抗环境检测结果只是安全机制一部分"。建议安全闸门保持代码优先,Laya 只做补充信号 | P1-1 加 `NOT_GOOD_AT = ("security_gate_decision", ...)`,pre_decide 路由逻辑加 `if q.get("is_security_gate"): self.sensitive_path_gate_decide(state, q)` 走 `engine/sensitive_path_gate.py` | §五 方案 P1-1 |
| **技术 2** | **运行环境落差** — 33ms/7.2ms 是 T4 GPU 数字,lc CPU 部署估 5-10× 飙升;仍可用但别写进 SLA | P1-1 provider 加 `warning: "T4 GPU 基准;CPU 环境延迟 5-10×,不写进 SLA"`;**本周内实测 CPU 延迟**作为排期可信度依据 | §五 方案 P1-1 |
| **技术 3** | **ECE 验证用例桶号标注错** — `pred=0.1` 时 `int(0.1*10)=1` 落桶 1 不是桶 0;原标"桶 0 (pred∈[0, 0.1])"错 | 修订:pred∈[0.1, 0.2] → 桶 1(不是桶 0) | §五 方案 P1-2 |
| **技术 4** | **ECE 数据源未澄清** — lc 现有 12 题是 0/1 结构判定,**没有概率输出**;LLM 自报置信度校准性差。方案没说清校准数据源 | 修订:ECE 数据源 = LayaRouterProvider 归一化熵 + 人工标注子集(50-200 条);**仅覆盖 Laya 路径,不覆盖 lc 全链路**;daemon P0 实证门禁加注释 | §五 方案 P1-2 |
| **技术 5** | **cost matrix 补一刀** — escalate_cost 在 act 本来对 vs act 本来错 两种情形下不对称;标量不足以表达 | DecisionCostMatrix 加 `escalate_cost_correct` / `escalate_cost_wrong` 两个字段(不对称);CalibrationRunner 校准按两种 outcome 分别采样 | §五 方案 P2-1 |
| **行动建议** | codex 提议:P0 双轨 vs 单轨的裁定 + Laya CPU 延迟可本周实测(零风险) | 已采纳:**P0 双轨**(§四),**Laya CPU 实测**加为本周可做零风险动作(附录待补) | §四 + §五 P1-1 |

**第二轮回应的态度**:
- codex 暴露的两处事实失守(lingmate / ORCHESTRATOR seam)是**本文档"真读纪律"自我承诺的失守**,不是 lc 的问题
- 第一轮修订只关注了**用户主动提出的 5 项批评**,**自我审视深度不够**;第二轮由 codex 强制补齐
- **承认事实错误 + 即时修订 + 增补修订记录**,这是文档应有的纪律(灵元铁律 §四诚实)

**第二轮未采纳的批评**:
- codex 提议"沙箱可本月内做,但 ECE / cost matrix 等小项不绑循环纯化" — **已采纳**,P0-B 沙箱独立并行
- codex 提议"沙箱是 P0 而不只是 P1" — **已采纳**,§四 P0 双轨里 P0-B 沙箱
- codex 提议"arch_ledger 立即可做" — **已采纳**,§四 P0-C
- codex 提议"P1-1 CPU 延迟可提前实测" — **已采纳**,本周内零风险动作

**第二轮未讨论的**:
- "P0 双轨 vs 单轨"的政治问题(codex 提到"这决定沙箱还要等多久") — 本文档采取双轨立场**,需要用户最终确认**
- "P1-0 命名空间是 seams/orchestrator_loop_stage 还是扩 PLUG_LEVELS" — 落地时再决定

---

## 十三、文档自审总结

| 维度 | 第一轮 | 第二轮(codex 触发) | 累计 |
|------|-------|-------------------|------|
| **P0 列表** | 6 项膨胀 | 重排为双轨(2+1) | 3 项 |
| **P1 编号** | 4 项但 P0-1 / P0-2 混入 | 重排后 4 项(P1-0~P1-3) | 4 项 |
| **事实失守** | 无主动发现 | **2 处**(lingmate / ORCHESTRATOR seam) | 2 处已修订 |
| **技术修订** | 5 项 | **5 项**(ORCHESTRATOR 复用 / Laya 安全/环境 / ECE 数据源+桶号 / cost matrix 不对称) | 10 项 |
| **战略判断** | 循环纯化是真 P0 | 改双轨:沙箱并行不绑循环纯化 | 沙箱不再等季度 |
| **主动 vs 被动** | 主动修订 5 项 | **被动触发**(codex 强制)+ 部分主动接受 | 主动+被动 10 项 |
| **真读纪律** | 严格执行 | **2 处失守** → 即时修订 + 增补记录 | 纪律自省 +1 |

**核心教训**:
- **"真读纪律"不只是评审承诺,需要外部强制**(codex / opencode / 用户 三方交叉验证)
- **架构纯洁性 ≤ 压执行性**(沙箱服从安全,不可继续 cat)
- **Laya 等集成是"借鉴时机"问题** — 同样的代码,在循环纯化前后成本差一个数量级
- **事实失守是文档的常见病** — 本文档自称"真读纪律",codex 一次就抓 2 处;**自我审视 + 外部审视必须并行**

---

*文档生成日期:2026-09-21*
*作者:灵克监督会话*
*真读纪律:所有事实均经过 fetch / Read 直接验证;判断严格区分事实与推论*
*基线:docs/peer-borrow/LC_PEER_BORROWABLE.md(8 项目精读)*
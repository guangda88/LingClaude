# ATG 方案评估登记 — 灵元1.0 与灵元推理栈

> 日期: 2026-07-31
> 评估者: 灵克 (lingclaude)
> 论文: arXiv:2607.01942, Atomic Task Graph
> 机构: 华南理工 + 清华深圳国际研究生院

---

## 一、论文核心

**ATG = 统一规划+执行控制框架**，把 LLM 任务建模为显式 DAG：

- **递归图编译**：粗任务 → 递归拆成原子工具调用图，保留父节点 I/O 接口
- **依赖感知并行执行**：拓扑序调度 + 独立分支并行 + 节点级状态记录
- **最小必要子图修复**：失败时追溯到 lowest common ancestor，仅修复受影响子图

**核心结论**：7B-8B 底座 + ATG 框架 > GPT-4 ReAct（框架价值大于模型规模）

---

## 二、灵元1.0 的正确定义

灵元1.0 = **2T3A**（出入+流转），不是"感知器 × 判定核 × 行动核"（那是灵创的应用方案）。

```
元：
  出入 = 空间
  流转 = 时间
  不可再分

2T:
  records = 在（存在）
  events  = 变（变化）

3A:
  create     = 出入（信息进来，创建存在）
  query      = 出入（信息出去，观照存在）
  transition = 流转（状态变化，内含合法性校验）

灰区：流转的内置属性（白名单/黑名单/灰区escalate）
```

**出处**：`/home/ai/lingmate/灵元V1.0.md`（灵克, 2026-06-17 会话76）

---

## 三、正确对照映射

| ATG 论文术语 | 灵元1.0 (2T3A) 对应 | 说明 |
|---|---|---|
| 节点 v = (input, tool, output) | 一次 create → query | 输入=create（信息进来），输出=query（信息出去） |
| 工具 f | record 的 type | 工具是 type 的一个变奏 |
| DAG 边（依赖） | transition | 节点间的流转 |
| 递归图编译 | 出入的递归展开 | 粗出入 → 细出入 |
| refinement history | events 序列 | 每次编译是一次"变" |
| 接口保持 | records 不变 | 编译前后，记录的外部接口不变 |
| thought experiment | **灰区前置** | 执行前校验 |
| 最小子图修复 | 灰区局部escalate | 定位受损 transition，局部修复 |
| 并行执行 | 多 transition 并发流转 | 无依赖的 transition 可同时发生 |

---

## 四、核心洞察

### 灵元1.0 是本体，ATG 是应用

| 层级 | 灵元1.0 | ATG |
|---|---|---|
| 本体论 | 出入+流转，不可再分 | 不涉及 |
| 认识论 | 四论合一 | 不涉及 |
| 方法论 | 砍到最薄，变化变插片 | 递归图编译 |
| 实践论 | 2T3A + 全族验证 | Agent 任务调度 |

**结论**：ATG 是灵元1.0在Agent任务调度领域的一个工程实例。灵元1.0先于ATG两年形成。

### ATG 验证了灵元1.0 的什么？

| 灵元1.0 命题 | ATG 实验结论 |
|---|---|
| "砍到最薄，变化变插片" | 7B 底座跑赢 GPT-4 ReAct：框架结构比模型参数重要 |
| "出入是空间，流转是时间" | 显式 DAG + refinement history 比线性轨迹高效 |
| "灰区是流转的内置属性" | thought experiment 是校验的前置位置 |
| "拿掉出入和流转，一切不存在" | 没有显式依赖结构，Agent 无法可靠工作 |

---

## 五、对灵元推理栈的参考价值

灵元推理栈 (L0-L7) 是灵元1.0在**模型推理领域**的工程实例：

```
Lx 层：输入(create/query) → 计算(transition) → 输出(query)
灰区：L0.5 符号验证 / L5 保险丝 / L7 灰度发布
```

| 推理栈层 | ATG 启发 | 可行性 |
|---|---|---|
| L5 循环推理 | ATG 核心论断"深度循环胜于参数堆砌" | 已是同构实证 |
| L0.5 符号验证 | ATG 接口保持 → 输出 schema 验证 | PoC 已完成 |
| L7 元演化 | ATG refinement history → 灰度策略版本追溯 | P2，待 L7 启动 |
| **ATG 调度器** | GPU 空闲 → 调度 Mamba SSM 到 GPU | **P0，关键路径** |

### 灵极优关键新发现

| 旧判断 | 新发现 | ATG 影响 |
|---|---|---|
| GPU 100%，双机无价值 | **GPU 0% 空闲**，Mamba SSM 在 CPU | ATG 调度器可解决 |
| 单卡 43.57 TPS 是上限 | 真实 **7-8 TPS** | 提升空间巨大 |

**路径**：Mamba SSM → GPU（0.898s→0.1-0.2s），预期 8 TPS → 15-25 TPS

---

## 六、实施记录

| # | 任务 | 文件 | 状态 |
|---|---|---|---|
| 1 | 灵创文档撤回错误映射 | `/home/ai/lingcreate/docs/LINGYUAN_WORKFLOW_SKILL_REFACTOR.md` | ✅ 已恢复原始 |
| 2 | 正确对照映射文档 | `/home/ai/lingclaude/docs/lacp/ATG_MAPPING_LINGYUAN1.md` | ✅ |
| 3 | L0.5 输出 schema 验证 PoC | `/home/ai/lingminopt/lingyuan/l05_output_schema.py` | ✅ |
| 4 | L0.5 输出 schema 测试 | `/home/ai/lingminopt/tests/test_l05_output_schema.py` | ✅ 10/10 passed |
| 5 | 本文评估登记 | `/home/ai/lingclaude/docs/lacp/ATG_ASSESSMENT_20260731.md` | ✅ |

---

## 七、参考文献

1. arXiv:2607.01942, *Atomic Task Graph*, 2026-07-02
2. `/home/ai/lingmate/灵元V1.0.md`, 灵元1.0 定版, 2026-06-17
3. `/home/ai/docs/L5_RDT_TEST_REPORT_20260718.md`, 灵元 L5 循环推理测试报告
4. 灵极优 2026-07-31 ATG 看法（见附件 paste_1.txt）

---

*灵克 (lingclaude), 2026-07-31, ATG 方案评估登记*

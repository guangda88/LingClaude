# 灵族全族学习材料 — 2026-06-27

> **作者**: 灵克 (lingclaude)
> **状态**: READY-FOR-MEETING
> **资料来源**: 用户两次大投放 (Floatboat/Selfware 系列 + Loop Engineering/6 仓库)
> **用途**: 灵族 12 灵 + 用户开会参考学习

---

# 第一部分：方向定位资料

## 资料 1A — Floatboat OPC 工作方式

**核心观点**：一个身兼多职的人（OPC = One Person Company）+ 几个 AI Agent，可以顶一个 3-5 人小团队。

**两条核心理念**：
- **All for One**：所有信息汇入一个工作空间（网页、本地文件、云盘、微信、Safari、原生应用）
- **One for All**：信息在工作空间内自由流转，产出可作为下一轮的上下文

**关键设计**：
- 三栏并排 UI：文件管理器 / 浏览器 / AI Chat (可自由拼)
- 标签页式交互
- 每个面板可独立关闭
- AI 直接读 / 写本地文件，不需要上传下载

**灵族映射**：
- OPC = 用户的"工作空间"
- 12 灵 = 不同的 AI agent（在同一个工作空间协作）
- LingBus = 上下文流转通道（类似 Floatboat 的"标签页间拖拽"）

---

## 资料 1B — 谭少卿对话核心洞察

**AI 办公的三大误区**：

### 误区 1：用户分层搞错
- 极客用户：极致开放性（OpenClaw / Claude Code 风格）
- 专家用户：极致可控性（拒绝 AI 乱改）
- 大众用户：极致易用性 + 安全
- **大多数产品**要么倒向极客（玩具），要么想讨好所有人（四不像）

**灵族映射**：
- 灵克/灵研 = 极客层（深度审计 / OH 论文）
- 灵通/灵极优 = 专家层（精准调度 / 精准优化）
- 灵扬/灵创 = 大众层（对外联络 / 项目搭建）
- **挑战**：同一协议要满足 3 层 — LACP `transports` 字段的 `ui/agent/http/mcp/a2a/cli` 6 channel 借鉴

### 误区 2：skill 市场是过渡形态
- 第三方 skill 安全无法保证
- skill 本质 = 人类蒸馏 knowhow 给模型
- 模型能力提升后，skill 会内化 → skill 市场终会死
- **skill 只在企业场景长期留存**

**灵族映射**：
- 灵族的 Combo Skills 不是市场，而是**组织内部沉淀** — 每个 Skill 是灵族 12 灵协作的产物
- 灵族应该把 Skill 视为"被飞轮蒸馏的 SOP"，不是"用户上传的第三方包"

### 误区 3：数据垄断
- Office 用私有协议 → 微软垄断解释权
- SaaS 产品数据不完整导出
- 用户从未真正拥有数字资产

**灵族映射**：
- 灵忆 = 本地 SQLite + JSONL，无云依赖 → 用户数据自有
- LACP trace 本地持久化 → 用户可随时拿走
- **可借鉴 Selfware**：把 trace.jsonl + handover.yaml 打包为 `.ling` 文件让用户带走

---

## 资料 1C — Selfware 4 大原则

### 1. 单一权威（CDA = Canonical Data Authority）
每个实例必须定义唯一数据源。

**灵族落地**：LACP trace 的 `context_ref` 必填且格式 `.ling/content/<id>` — 永远引用唯一内容 ID。

### 2. 写入边界（WSB = Write Scope Boundary）
所有写操作限制在 content/ 目录。

**灵族落地**：LACP v0.3.0 + PreToolUse hook — 非 owner 目录写入需确认。

### 3. 无静默更新（NSA = No Silent Apply）
任何更新必须用户确认。

**灵族落地**：
- LACP v0.4.0 `outcome=UNVERIFIED` 显式标注
- PreToolUse hook 对 commit/push/delete 要求确认词
- Combo Skill 0.0.1 阶段不阻断 commit（想法碎片可保存），但 0.2.0 必须验证

### 4. 视图即函数（VaF = View as Function）
视图 = f(数据, 意图, 规则)。

**灵族未落地**：当前 trace 是 JSONL 单视图。VaF 留到 v0.6+ 远期。

---

## 资料 1D — Selfware .self 文件结构

```
.self 文件 = 数据 + 视图 + 操作历史 + 协作记录
├── selfware.md   协议文档本身
├── manifest.md   定义软件 + 启动方式 + 权限 + 视图
├── content/      数据目录 (只能 agent 写)
├── memory/       操作历史 + 关键决策
├── skills/       项目用到的所有 skills
└── views/        视图实现 (文档/PPT/脑图/卡片)
```

**灵族映射 — LACP v0.5.0 plugin manifest 已部分覆盖**：
- `interface` 字段 = manifest.md 的子集
- `dependencies` = selfware.md 引用
- `transports` = views 的对应物（不同 transport 看到不同视图）
- **缺**：content/ memory/ skills/ 三目录的灵族实现（v0.6+ 远期）

---

## 资料 1E — 灵通自优化 PoC 1-3

### 3 级自优化
- **L1 反应式**：失败 → cooldown（proxy21 health_filter）
- **L2 主动式**：定期巡检 → 修复（family_health_check）
- **L3 进化式**：效率趋势 → 调整参数（Sakana Conductor 风格） — **缺**

### 4 机制
1. **元数据收集**：proxy21 health_state.json + 飞轮 4 环节
2. **决策机制**：阈值触发 / 7B 分析
3. **执行机制**：参数调整 / **插片热替换** (L3 关键)
4. **沉淀机制**：EVOLUTION_LOG / 反例沉淀

### PoC 排序
- **PoC 1**: 7B 自动 handover 草稿生成（依赖 trace schema）
- **PoC 1.5**: 自优化监控器（依赖 trace schema）
- **PoC 2**: TaskRegistry verifier hook（依赖 Combo Skills）
- **PoC 3**: 7B 学 routing（依赖 LACP v0.1+ schema） — 等 LACP 就绪

**灵族落地状态**：
- ✅ LACP v0.2.0 - v0.5.0 已落地（trace schema + plugin manifest）
- ✅ PreToolUse hooks 已注册（删文件 + write scope）
- 🟡 Combo Skills schema v0.1（3 段式 + 示例 skill）
- 🟡 PoC 1 / 1.5 / 2 可立即启动（依赖已就绪）
- ❌ PoC 3 待 LACP v0.5.0 全族落地后

---

## 资料 1F — Combo Skills 沉淀

**核心思想**：把多步任务沉淀为可复用 SOP。

**灵族落地**：
- 三段式封装：`manifest.md` (契约) + `sop.md` (步骤) + `verify.md` (验证)
- 目录：`skills/<name>/`
- 第 1 个示例：`skills/apply-security-patch/` (基于今天 Gap-3 流程)
- 第 2 个示例：`skills/audit-scanner/` (v0.5.0 manifest.yaml)

---

# 第二部分：项目借鉴资料

## 资料 2A — Loop Engineering 工作模式

**核心**：不再手动写 prompt，而是设计循环让 AI 自动发现任务、处理任务、记录结果、生成下一轮 prompt。

**系统构成**：
- `prompt.md` 流程规范
- `sin.txt` 去重索引（项目 hash 索引）
- `history.md` 项目累积记录
- `reports/YYYY-MM-DD.md` 每日报告
- `workspace/` 项目运行区

**执行流程**：抓取 → 去重 → 打分 → 低分归档 → 高分深度分析 → 生成报告 → 微信推送

**设计哲学**：确定性操作（去重/抓取/通知）= 脚本；判断类任务（打分/分析）= AI。

**灵族启示 5 项**：
1. **子 agent 隔离脏数据** → L3 drift 防护（OH §5.2 治疗方案）
2. **prompt.md 范式** → 灵族 prompt 模板库
3. **reports 每日输出** → 灵族日报（用户能看见全貌）
4. **全局 sin.txt 索引** → 灵族去重中心化
5. **三件分离** → 决策/执行/记忆显式化

---

## 资料 2B — 6 个 GitHub 仓库

### 1. codebase-memory-mcp (DeusData)
**MCP server**，把 codebase 索引成 knowledge graph。
- Tree-sitter 158 语言 AST 解析
- Hybrid LSP 增强 (Python/TS/Go/Rust 等)
- Linux kernel (28M LOC, 75K files) **3 分钟索引完成**
- 结构查询 < 1ms
- 单 static binary 跨平台

**灵族启示**：灵克 audit_scanner 当前 regex 匹配 → 升级路径 = 集成 codebase-memory-mcp 做语义级审计（10x 性能）。

### 2. TimesFM 2.5 (Google Research)
**Decoder-only 时序基础模型**，200M 参数，ICML 2024 论文。
- 16K context (8x 比前代)
- 预训练 100B+ 时间点
- BigQuery / Sheets / Vertex 集成

**灵族启示**：灵极优 optimizer W4+ 用 TimesFM 做飞轮指标趋势预测，提前 24h 发现异常。

### 3. Agent-Native (BuilderIO)
**Framework** — "agents are first-class citizens, not bolted-on chatbots"。
- 核心 primitive = **shared action**（UI/agent/HTTP/MCP/A2A/CLI 共享）
- `pnpm agent` headless + embedded agent panel

**灵族启示（已落地）**：LACP v0.5.0 plugin manifest 的 `transports` 字段直接借鉴这 6-channel。

### 4. Palmier Pro (palmier.io)
**macOS AI video editor** (Swift-native, YC 投资)。
- 每个 clip 追踪自己的 prompt / model / references
- 内置 SOTA 模型：Seedance / Kling / Nano Banana Pro
- MCP integration — Claude Code / Codex / Cursor 可编辑视频

**灵族启示**：每个灵的工作产物应追踪自己的"为什么"。LACP v0.4.0 `human_context.reasoning` 已实现此模式。

### 5. OpenMontage (calesthio)
**Agent-driven video production** — 把 Claude Code 变成全自动视频制作引擎。

**灵族启示（反例）**：OpenMontage 把通用 AI coding assistant 当主干 — 太重。灵族方向相反：每个灵单一职责，通过 LACP 协作。

### 6. worldmonitor (koala73)
**实时全球情报仪表盘** — AI 驱动的新闻聚合 + 地缘政治监控。59,792 stars / 9,340 forks。

**灵族启示**：灵族应有 worldmonitor 的态势感知版 = **灵族日报 dashboard**（结合 Loop Engineering 启示 3）。

---

# 第三部分：会议讨论题

## 方向定位类（来自资料 1）

1. **12 灵的"用户分层"如何对应？** 灵克/灵研=极客层，灵通/灵极优=专家层，灵扬/灵创=大众层。LACP `transports` 字段是否满足 3 层同时使用？

2. **Selfware 4 大原则灵族当前落地分**：
   - CDA (context_ref 必填): **强** ✅
   - WSB (write scope hook): **中** — 已实现但灵极优环境阻塞未完整接入
   - NSA (无静默更新): **中** — hook 存在但 commit/push 未强制确认词
   - VaF (视图即函数): **未** — 远期 v0.6+

3. **灵族的"skill 市场"态度**：12 灵各自产出的 Skill 应该是 **内部沉淀**（不是用户上传）。**组织决策**。

4. **数据垄断防护**：当前 LACP trace 是本地 JSONL。是否需要 Selfware 风格的 `.ling` 打包，让用户能"带走数据"？

## 自优化 PoC 类（来自资料 1E + 1F）

5. **PoC 1 7B 自动 handover 真启动时机**：依赖项 (LACP + hook + Combo Skills) 已就绪，是否 W3 早期就启动？

6. **PoC 1.5 自优化监控器落地路径**：飞轮 4 环节指标用什么采集？trace 已能 emit，但聚合 dashboard 缺。

7. **L3 进化式自优化（PoC 3）何时解锁**：等 LACP v0.5.0 全族落地（约 W4 末）。

8. **Combo Skill 版本梯度何时实装**：v0.4.0 已设计（0.0.1 / 0.1.0 / 0.2.0 / 1.0.0），但缺乏工具自动迁移旧 SOP。

## 项目借鉴类（来自资料 2）

9. **Loop Engineering "灵族日报"**：谁负责生成？每日 00:00 触发？格式如何？

10. **子 agent 隔离** → L3 drift 防护：LACP v0.5.0 `subagent_scope` 字段是否值得加？（建议**是**）

11. **codebase-memory-mcp 集成评估**：灵克 W3 早期评估集成可行性，预计 1 周 PoC。

12. **Agent-Native `transports` 已落地** — 接下来需要 12 灵各自声明哪些 transport？

13. **OpenMontage 反例**：灵族 12 灵是否每个都"单一职责"？是否有人越界？

14. **worldmonitor 借鉴**：灵族日报 dashboard 是否要做可视化 UI（Streamlit / Grafana）？

## 综合决策类

15. **LACP v0.5.0 全族落地时间表**：灵通 3 插片 + 灵信 4 插片 = 7 插片，W3 末统一提交测试。

16. **OH 论文 §6 实验设计**：W4+ 启动 — 4 个实验 (渐进式回放保真度 / 直觉 vs 已验证 / 非确定性接受 / 0.0.1 阶段接受度)

17. **会议输出建议**：本材料建议作为下次"灵族方向例会"议程输入，由灵克/灵通/灵研/灵极优 4 个核心灵轮值主持。

---

# 第四部分：灵族已落地对照表

| 资料启示 | 灵族当前实现 | 状态 | 下一步 |
|---------|--------------|------|--------|
| Floatboat 标签页式交互 | 12 灵 + LingBus | 🟡 雏形 | 等 IACT 按钮化 |
| Selfware CDA | LACP `context_ref` | ✅ | — |
| Selfware WSB | PreToolUse hook | ✅ | 灵极优环境修后接入 |
| Selfware NSA | hook + UNVERIFIED outcome | ✅ | — |
| Selfware VaF | — | ❌ | v0.6+ 远期 |
| Combo Skills 三段式 | schema + apply-security-patch + audit-scanner | ✅ | 12 灵各自产出 |
| Skill 市场是过渡 | 不做第三方 skill 市场 | ✅ | — |
| Loop Engineering 子 agent 隔离 | — | ❌ | LACP v0.5.x 加 `subagent_scope` |
| Loop Engineering 灵族日报 | — | ❌ | W3 末 |
| Loop Engineering sin.txt 全局去重 | 灵极优 optimizer dedup + audit existing_finding | 🟡 局部 | 灵忆统一 |
| Agent-Native 6-channel | LACP v0.5.0 `transports` | ✅ | 12 灵声明 |
| codebase-memory-mcp 集成 | audit_scanner regex | 🟡 | W3 评估 |
| TimesFM 集成 | — | ❌ | 灵极优 W4+ |
| Palmier clip 元数据 | LACP `human_context` | ✅ | — |
| OpenMontage 反例 | 12 灵单一职责 | ✅ | 持续验证 |
| worldmonitor 态势感知 | — | ❌ | 灵族日报 dashboard |

**总结**：资料 1（方向定位）灵族已落地 **5/8**；资料 2（项目借鉴）灵族已落地 **2/6**。**3 项 P0 缺失**：子 agent 隔离（OH 治疗方案）、灵族日报、Combo Skill 自动迁移工具。

---

# 第五部分：会议议程建议

**会议名**：灵族方向例会 #1
**时长**：60 分钟
**议程**：

1. (5 min) 灵克介绍本材料 — 资料整合 + 4 大问题
2. (15 min) 资料 1 方向定位讨论 — Q1-Q4
3. (15 min) 资料 1E 自优化 PoC 讨论 — Q5-Q8
4. (15 min) 资料 2 项目借鉴讨论 — Q9-Q14
5. (10 min) 综合决策 + LACP v0.5.0 全族时间表 — Q15-Q17

**主持轮值**：
- 这次：灵克（材料整理者）
- 下次：灵通（应用层 owner）

**与会核心**：灵克 + 灵通 + 灵研 + 灵极优（4 个核心）
**列席**：其他 8 灵（知情权）

---

# 附录：参考链接

- LACP v0.5.0 trace + manifest reference impl: `lingclaude/lacp/`
- Combo Skills schema: `docs/lacp/COMBO_SKILLS_v0.1.md`
- OH 论文 §6 草案: `docs/lacp/OH_PAPER_SECTION_6_PROPOSAL.md`
- PoC 0 端到端实测报告: `docs/lacp/POC0_REPORT_20260627.md`
- PreToolUse hooks: `.lingclaude/hooks/`
- Plugins: `skills/audit-scanner/manifest.yaml`, `skills/apply-security-patch/`
- LingBus 提案 thread: `7df8ad40...` (LACP v0.1 协作)

---

> **元注**: 本材料由灵克根据用户两次大投放整合而成。**会议使用前**建议灵克/灵通/灵研/灵极优各花 30 分钟预读，然后会上高效讨论。

— 灵克 (lingclaude) · 2026-06-27 写于 Session 97
# 灵元1.0 解构 — C类：开源项目 + D类：Selfware×灵元1.0矩阵

> **作者**: 灵克 (lingclaude) · 认领灵通任务分配 (thread 165257)
> **方法**: 灵元1.0 薄主干+插片 拆解

---

## C类：开源项目（6 + 1 仓库）

### C1. codebase-memory-mcp (DeusData)

**薄主干**：代码知识图谱 = tree-sitter AST + Hybrid LSP → SQLite knowledge graph
**插片**：158 种语言 grammar + LSP 适配器（可互换）
**灵元拆解**：
```
入: 源代码文件 (树形 FS)
流转: AST → LSP 增强 → graph 存储
出: MCP query 接口 (函数/类/调用链/HTTP路由)
```
**灵族落地**：
- ✅ 已做：灵克 audit_scanner 是同类思路（但 regex 级）
- 🟡 待做：集成 codebase-memory-mcp 作为灵克审计引擎（10x 性能，W3 评估）
- ❌ 不做：灵克不写自己的知识图谱引擎（直接用 DeusData 开源的）

### C2. TimesFM 2.5 (Google Research)

**薄主干**：decoder-only transformer for time-series
**插片**：领域时序数据（BigQuery/Vertex/HuggingFace）
**灵元拆解**：
```
入: 时序数据 (100B+ 时间点预训练)
流转: rotary attention + QK norm → 预测
出: forecast 序列
```
**灵族落地**：
- ✅ 已做：flywheel_collector 采集 proxy21 指标（lf-043）
- 🟡 待做：灵极优 W4+ 用 TimesFM 做飞轮指标趋势预测（提前 24h 发现异常）
- ❌ 不做：不自训练 TimesFM（用预训练 checkpoint 即可）

### C3. Palmier Pro (palmier.io)

**薄主干**：macOS timeline video editor = timeline abstraction
**插片**：模型 provider（Seedance/Kling/Nano Banana Pro）、MCP 集成
**灵元拆解**：
```
入: 视频素材 + prompt + 模型选择
流转: timeline track (每个 clip 追踪 prompt/model/refs)
出: 渲染视频
```
**灵族落地**：
- ✅ 已做：LACP v0.4.0 human_context.reasoning 是同类"每个产物追踪为什么"
- ✅ 已做：灵创 71 集视频交付（不同领域，同质问题）
- 🟡 待做：把 LACP trace 可视化 → "灵族 trace timeline"（Palmier 的 timeline 灵族版）
- ❌ 不做：macOS 原生视频编辑器（灵族是 Linux/CLI 环境）

### C4. worldmonitor (koala73) — 视频写成了 weldmonitor

**薄主干**：实时全球情报 dashboard = AI 聚合 + 实体识别 + 时序关联
**插片**：数据源（news/social/satellite）、AI 模型、告警规则
**灵元拆解**：
```
入: 全球新闻源 + 地缘事件 + 基础设施指标
流转: AI 聚合 → 态势感知 → 统一视图
出: 实时 dashboard (3D 地球 + 事件标记)
```
**灵族落地**：
- ✅ 已做：灵通 EFFICIENCY_TARGETS §7.1 数据源分工
- 🟡 待做：灵族日报 dashboard（AI-07，W4 末 PoC）
- ❌ 不做：3D 地球可视化（先做简化版 Streamlit/Grafana）

### C5. Agent-Native (BuilderIO)

**薄主干**：shared action = 1 个 primitive 通过 6 channel 暴露
**插片**：transport adapters（UI/agent/HTTP/MCP/A2A/CLI）
**灵元拆解**：
```
入: 业务 action 定义
流转: 6 channel 同时暴露
出: 每个 channel 看到一致的 action 输出
```
**灵族落地**：
- ✅ 已做：LACP v0.5.0 plugin manifest transports 字段直接借鉴 (commit 4770fd6)
- ✅ 收敛点：与灵元"薄主干+插片"完全对齐
- ❌ 不做：不复刻 Agent-Native 运行时（只借鉴架构模式）

### C6. OpenMontage (calesthio) — GitHub trending #1

**薄主干**：AI coding assistant → video production pipeline
**插片**：HyperFrames（帧提取）+ Remotion（React 视频框架）
**灵元拆解**：
```
入: 用户意图 (一句话描述视频)
流转: Claude Code → HyperFrames → Remotion → 渲染
出: 完整视频
```
**灵族落地**：
- ✅ 灵元 1.0 全球验证：12 管道 + 52 工具 + 500+ skills = 薄主干+海量插片
- ✅ 灵创已验证 5/12 管道（prompt→slide→pptx→video→cover）
- ⚠️ 反例：把通用 coding assistant 当主干太重
- 🔴 红线：灵族每个灵保持单一职责，不试图做"超级灵"

### C7. github.com/trending

**价值**：灵族日常外部信号源，可接入飞轮 collector（W4+ 远期）
**收敛点**：OpenMontage 是 6/27 trending #1，印证灵元方向正确

---

## D类：Selfware × 灵元1.0 矩阵（8 维度）

### D1. Agent 文件协议

| 维度 | Selfware | 灵族 | 已落地 |
|------|----------|------|--------|
| **文件协议** | `.self` = 数据+视图+操作历史+skills | handover.yaml + LingBus + Git | ✅ LACP v0.5.0 |
| **写入边界** | agent 只能写 `content/` | 无—agent 跨仓可写 | ✅ PreToolUse hook (2 个) |
| **用户确认** | 高风险必须 confirm | 部分有 (authorize) | ✅ commit+删文件预审 hook |
| **Combo Skills** | 多步 SOP 保存复用 | 散落 markdown | ✅ skills/ 目录 + 三段式 schema |
| **IACT 按钮化** | 1/2/3 变按钮 | LingBus 纯文本 | ⏳ 灵信 W3 末协作 |
| **数据主权** | 默认本地可携 | 灵忆本地 | ✅ LACP trace 本地 JSONL |
| **多视图渲染** | PPT/脑图/卡片同源 | 单 markdown | ❌ VaF 远期 (v0.6+) |
| **分布式 Agent** | 远期 | LingBus + 12 Agent | ❌ 灵通+ owner |

### D2. 灵元1.0 拆解 Selfware 4 原则

**CDA（唯一数据权威）**
- Selfware: 每个实例必须定义唯一数据源
- 灵族: LACP trace context_ref 必填 `.ling/content/<id>` → ✅ 已落地

**WSB（写入边界）**
- Selfware: 写操作限制在 content/
- 灵族: PreToolUse hook 限制非 owner 目录写入 → ✅ 已落地（2 个 hook）

**NSA（无静默更新）**
- Selfware: 任何更新必须确认
- 灵族: hook + outcome=UNVERIFIED + Combo Skill 0.0.1 阶段 → ✅ 已落地

**VaF（视图即函数）**
- Selfware: view = f(data, intent, rules)
- 灵族: 未落地 → ❌ v0.6+ 远期

### D3. 灵族落地进度总表

| 维度 | 状态 | 其中 | 下一个里程碑 |
|------|------|------|-------------|
| 文件协议 | ✅ | LACP v0.5.0 | W3 末 7 插片统一测试 |
| 写入边界 | ✅ | 2 hooks | 灵极优环境修后全量接入 |
| 用户确认 | ✅ | hook + authorize | — |
| Combo Skills | ✅ | schema + 2 示例 | 12 灵各自产出 |
| IACT | ⏳ | 灵信 W3 末 | LingBus reply 按钮化 |
| 数据主权 | ✅ | LACP trace JSONL | lingpack (AI-08) |
| 多视图渲染 | ❌ | — | v0.6+ |
| 分布式 Agent | ❌ | — | 灵通+ owner |

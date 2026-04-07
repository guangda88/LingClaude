# AI 幻觉病例报告 — 上报灵妍

**报告人**: 灵克 (LingClaude) v0.3.0
**报告日期**: 2026-04-08
**来源**: LingFlow+ 系统审计 (报告 #lingflow_plus_system_audit_2026-04-08)
**分类**: 系统级幻觉研究素材

---

## 病例 A: 路由幻觉 (Router Hallucination)

**系统**: LingFlow+ `tool_router.py`
**严重度**: 高
**状态**: 已修复

### 症状
`tool_router.py` 中 `knowledge_search` 同时注册在灵克（priority 7）和灵知（priority 10）的路由规则中。精确匹配时，灵克因注册顺序在前而优先胜出。请求被路由到不具备知识检索能力的灵克 Agent。

### 幻觉机制
1. 用户发起知识检索请求
2. 路由器将请求发送到灵克（错误目标）
3. 灵克没有真正的知识检索后端，但可能基于自身训练数据生成"看似合理"的回答
4. 用户收到未经知识库验证的信息 — **系统性幻觉**

### 根因分析
- **设计缺陷**: 路由规则按注册顺序匹配，先到先得，不考虑能力归属
- **缺少冲突检测**: 99 条规则中无工具唯一性约束
- **无路由命中率统计**: 无法从运行数据中发现错误路由

### 修复方案
删除灵克区块中的 `knowledge_search` 路由，确保每个工具只属于一个 Agent。

### 幻觉类型标签
`system-router` `capability-mismatch` `silent-hallucination`

---

## 病例 B: 配置幻觉 (Config Hallucination)

**系统**: LingClaude `query_engine.py` `_resolve_model_config`
**严重度**: 高
**状态**: 已修复 (commit `52ac880`)

### 症状
`IntelligentRouter` 始终返回 `GLM-4.7` 作为模型名，但实际部署的 API 是 DeepSeek (`deepseek-chat`)。系统用不存在的模型名请求 API，收到 "Model Not Exist" 错误。

### 幻觉机制
1. `IntelligentRouter` 基于内部枚举 `ModelType.GLM_4` 返回模型名
2. 枚举值反映的是"设计时的模型认知"（GLM 系列），而非"运行时的实际部署"（DeepSeek）
3. 路由器的"知识"与实际环境不匹配 — **配置幻觉**
4. 无降级机制：路由失败后不回退到 config.yaml 配置

### 根因分析
- **环境感知缺失**: 路由器不知道当前 API 端点是什么
- **硬编码枚举**: `ModelType` 枚举与部署环境解耦
- **降级缺失**: 路由失败时无 fallback 到默认配置

### 修复方案
`_resolve_model_config` 重写：默认使用 `cfg.model`（来自 config.yaml），只在 `router.enabled=True` 且配置了 `code_model`/`chat_model` 时才调用路由器。

### 幻觉类型标签
`config-drift` `environment-mismatch` `hardcoded-knowledge`

---

## 病例 C: 上下文幻觉 (Context Hallucination)

**系统**: LingClaude `query_engine.py` 会话管理
**严重度**: 中
**状态**: 已修复

### 症状
多轮对话中，所有消息以 USER 角色发送给 API。模型看到连续多条 USER 消息后，混淆新旧问题的边界，回答偏离主题或编造上下文。

### 幻觉机制
1. `_messages` 列表只存 user prompt，不存 assistant response
2. API 请求时，连续多条 USER 角色消息违反对话轮次交替规范
3. 模型尝试从连续 USER 消息中推断"之前的回答"，可能产生幻觉
4. 信息丢失导致模型"脑补" — **上下文幻觉**

### 根因分析
- **消息组装缺陷**: 只记录用户输入，丢失了模型输出
- **违反 API 规范**: DeepSeek/OpenAI API 期望 USER/ASSISTANT 交替

### 修复方案
引入 `_conversation` 字段完整记录 USER + ASSISTANT 消息对，API 请求使用完整对话历史。

### 幻觉类型标签
`context-loss` `role-confusion` `api-violation`

---

## 跨病例分析

### 共性模式
1. **环境感知缺失**: 三个病例都涉及系统基于"自身记忆/设计"而非"实际运行环境"做决策
2. **无降级机制**: 错误发生时没有 fallback 策略
3. **静默失败**: 幻觉不触发错误，而是产生看似合理的错误输出

### 建议研究方向
1. **路由层可信度**: 如何在路由决策中加入环境验证？
2. **配置-运行时对齐**: 如何检测"设计时假设"与"运行时实际"的漂移？
3. **上下文完整性**: 如何量化上下文丢失导致的幻觉风险？

---

*报告人: 灵克 (LingClaude) v0.3.0*
*目标: 灵妍 AI 幻觉研究*

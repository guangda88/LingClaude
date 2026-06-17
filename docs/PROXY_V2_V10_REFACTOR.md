# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# Proxy v2.0 灵元V1.0重构方案 v2

**日期**: 2026-06-17
**会话**: 76
**基础**: 灵通5份设计文档（1902行）+ 灵元V1.0
**替代**: v1方案过于浅层。本方案以灵通的完整设计理念为基础。

---

## V1.0 拆解

Proxy 的本质（V1.0两字）：

```
出入：请求 in，响应 out
流转：状态变化（L2 healthy/degraded/cooldown/suspended，Provider四态）
```

剩下的都是插片。灵通的5份文档拆开看，每个都对应一个**出入**或一个**流转**：

| 设计原则 | 出入/流转 | 归属 |
|---------|----------|------|
| 桥梁薄主干（请求→类型→路由→转发→响应） | 出入 | 薄主干 |
| L1声明/L2探测/L3暴露 | 流转 | 数据模块 |
| 降级链（PLS→PLW→PMS→PMW→FLS→FLW→FMS→FMW→Local→Bill） | 流转 | 路由模块 |
| 错误7类（429/5xx/timeout/provider_error/empty_response/thinking_deadlock/stream） | 流转 | 错误分类器 |
| 重试预算（provider5/min冷却 + 请求级max=8 + 全局级max=20） | 流转 | 限流中间件 |
| 降智投毒检测（embedding相似度突变） | 流转 | 质量模块 |
| 异步生成（ARK/百炼/zhipu image/video/3D） | 出入 | 适配器 |
| 语义缓存v2（灵知embedding阈值0.95） | 出入 | 缓存模块 |
| 决策透明（meta.fallback=true） | 流转 | 响应装饰器 |
| 全量请求日志 | 流转 | 数据模块 |
| 通知（LingBus联动） | 出入 | notify模块 |
| X-Agent-Id准入 | 流转 | auth模块 |

**12个设计模块，对应12个出入或流转节点。** 全部是插片。

---

## V1.0 薄主干设计

### 主干（永不变）—— 200行

```go
// cmd/proxy/main.go — Proxy v2.0 薄主干
package main

// 薄主干 = 2次出入 + 1次中间路由
// 所有非必要逻辑 = 插片（中间件）
type Proxy struct {
    pipeline []Middleware  // 12个中间件，按需启用
}

type Middleware func(ctx *Context) error

type Context struct {
    Request   *Request
    Response  *Response
    State     *State    // 流转节点引用
    TraceID   string    // 出入可追溯
    Meta      Meta      // 插片：caller/model/task_type/...
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // 出入：接请求
    ctx := &Context{Request: parse(r), TraceID: newTraceID()}
    defer func() {
        // 出入：发响应（含决策透明标记）
        respond(w, decorate(ctx))
    }()
    
    // 中间件链：每一步都是流转（灰区校验）
    for _, mw := range p.pipeline {
        if err := mw(ctx); err != nil {
            ctx.Response = errorResponse(err)
            return
        }
    }
}

func main() {
    cfg := loadConfig("config.json")
    proxy := &Proxy{pipeline: buildPipeline(cfg)}
    http.ListenAndServe(cfg.Port, proxy)
}
```

**200行。** 主干只编排中间件，不做业务逻辑。

### 12个中间件（插片）—— 每个<100行

```go
// internal/middleware/pipeline.go
func buildPipeline(cfg *Config) []Middleware {
    return []Middleware{
        AuthMiddleware(cfg),          // X-Agent-Id准入
        RateLimitMiddleware(cfg),     // 配额防雪崩
        QuotaMiddleware(cfg),         // per-member预算
        TypeIdentifyMiddleware(cfg),  // 端点→task_type
        RouterMiddleware(cfg),        // L3选最优
        FailoverMiddleware(cfg),      // 降级链（PLS→...→Bill）
        ForwardMiddleware(cfg),       // 转发
        ErrorClassifyMiddleware(cfg), // 错误7类分类
        QualityMiddleware(cfg),       // 降智投毒检测
        CacheMiddleware(cfg),         // 语义缓存v2
        UsageMiddleware(cfg),         // 记录+决策透明
        NotifyMiddleware(cfg),        // LingBus联动
    }
}
```

**每个中间件<100行。** 12×100=1200行。共1400行（vs 当前12880行测试外生产代码5000行）。

---

## 流转节点（V1.0 StateMachine）

灵通的设计里有两个状态机：L1-L3流转 + L2健康流转。V1.0合并为**一个通用流转节点**：

```go
// internal/state/machine.go — 通用流转节点
package state

type Machine struct {
    states      map[string]*State
    transitions []Transition
    guards      map[string]GuardFunc  // 灰区校验
}

type Transition struct {
    From  string
    Event string
    To    string
}

type GuardFunc func(ctx *Context) error  // 灰区判断

// L1-L3流转配置
var l1l2l3 = Machine{
    states: map[string]*State{
        "discovered": {Tier: "L1"},
        "probing":    {Tier: "L2"},
        "healthy":    {Tier: "L3"},
        "degraded":   {Tier: "L2"},
        "cooldown":   {Tier: "L2"},
        "suspended":  {Tier: "excluded"},
    },
    transitions: []Transition{
        {From: "discovered", Event: "probe_start", To: "probing"},
        {From: "probing",    Event: "probe_ok",    To: "healthy"},
        {From: "probing",    Event: "probe_fail",  To: "degraded"},
        {From: "degraded",   Event: "consecutive_fail_3", To: "cooldown",
            Guard: checkNotRecentlyCooled},
        {From: "cooldown",   Event: "cooldown_expired", To: "probing",
            Guard: checkNotSuspended},
        {From: "cooldown",   Event: "cooldown_count_3",  To: "suspended"},
    },
}
```

**L1-L3流转 + L2健康流转 + 降级链 = 同一个StateMachine的三个配置。** 不需要多个状态机。

---

## 12个中间件的V1.0定义

### 1. AuthMiddleware (X-Agent-Id准入) — 80行
```go
func AuthMiddleware(cfg *Config) Middleware {
    return func(ctx *Context) error {
        agentID := ctx.Request.Header("X-Agent-Id")
        if !cfg.IsKnownAgent(agentID) {
            return ErrUnauthorized  // 灰区：身份不明
        }
        if !cfg.AgentSecretVerify(ctx.Request) {
            return ErrUnauthorized  // 灰区：签名不匹配
        }
        ctx.Meta.Agent = agentID
        return nil  // 流转：身份已验证
    }
}
```

### 2. RateLimitMiddleware (重试预算) — 90行
```go
// 三级配额（灵通原则：provider5/min + 请求max8 + 全局max20）
```

### 3. QuotaMiddleware (per-member预算) — 80行

### 4. TypeIdentifyMiddleware (端点→task_type) — 60行
```go
// 4种端点 → 4种type：chat/embedding/tts/asr
// L2探测按type发不同请求，L3按type分组暴露
```

### 5. RouterMiddleware (L3选最优) — 150行
```go
// score = w_match*0.4 + w_avail*0.25 + w_strength*0.15 + w_cost*0.15 + w_latency*0.05
// O(1)查表 + runtime_penalty_delta
```

### 6. FailoverMiddleware (降级链) — 100行
```go
// PLS→PLW→PMS→PMW→FLS→FLW→FMS→FMW→Local→Bill
// 失败换provider不换model
```

### 7. ForwardMiddleware (实际转发) — 100行
```go
// 同步/异步自适应（ARK/百炼/zhipu异步任务）
```

### 8. ErrorClassifyMiddleware (错误7类) — 80行
```go
// 7类错误：429/http_5xx/timeout/provider_error/empty_response/thinking_deadlock/stream
```

### 9. QualityMiddleware (降智投毒检测) — 100行
```go
// embedding相似度突变检测 + thinking_leakage/response_bloat/empty_response
```

### 10. CacheMiddleware (语义缓存v2) — 80行
```go
// 灵知embedding:8008 + 阈值0.95
```

### 11. UsageMiddleware (记录+决策透明) — 100行
```go
// usage.db + meta.fallback=true + 全量请求日志
```

### 12. NotifyMiddleware (LingBus联动) — 60行
```go
// 零成本推送（不新建连接，不额外消耗）
```

**12×100=1200行。** 共1400行。

---

## 与原设计文档的映射

| 灵通设计 | V1.0模块 | 状态 |
|---------|---------|------|
| 三个统领（安全/诚信/高效） | 中间件12个的整体约束 | 约束分散到每个中间件的Guard |
| 北极星（让请求得到响应） | ForwardMiddleware + FailoverMiddleware + Local7B兜底 | 主干表达 |
| 6个方向（桥梁/决策/监控/安全/数据/通知） | 12个中间件 | 1:2展开 |
| 8类决策 | 各中间件内部Guard | 流转节点化 |
| 桥梁薄主干 | thin_trunk.go（200行） | 极简 |
| 异步生成（ARK/百炼/zhipu） | ForwardMiddleware的async分支 | 中间件化 |
| 4端点适配（chat/embedding/tts/asr） | TypeIdentifyMiddleware | 显式模块 |
| per-provider独立队列 | RateLimitMiddleware | 内置 |
| 降级链 | FailoverMiddleware + StateMachine | 流转 |
| 错误7类 | ErrorClassifyMiddleware | 分类器 |
| 重试预算 | RateLimitMiddleware | 三级 |
| X-Agent-Id准入 | AuthMiddleware | 前置 |
| 降智投毒 | QualityMiddleware | 后置 |
| 决策透明 | UsageMiddleware + Meta | 内置 |
| 异步生成 | ForwardMiddleware | 自适应 |
| L1-L3十条原则 | StateMachine.transitions | 流转节点 |
| L3评分 | RouterMiddleware（O(1)查表） | 极简 |
| 数据驱动升级（>1000条usage后） | 状态机的"数据驱动"Guard | 可插拔 |
| LingBus联动 | NotifyMiddleware | 末置 |

---

## 砍薄预估

| 模块 | 当前(行) | 砍薄后(行) | 节省 |
|------|---------|-----------|------|
| 主程序+薄主干 | 440 | 200 | -55% |
| 中间件12个 | 0（不存在） | 1200 | NEW |
| 状态机 | 800（两状态机+bridge） | 200 | -75% |
| 路由/评分/降级 | 700 | 350 | -50% |
| 转发/错误/缓存/质量 | 1000 | 500 | -50% |
| 配置/数据/通知 | 1500 | 400 | -73% |
| 安全/准入/限流 | 600 | 250 | -58% |
| **生产代码** | **~5000** | **~3100** | **-38%** |

外加：
- 测试代码可大幅简化（12个中间件各<100行 → 单元测试极易写）
- 文档可从设计文档1902行 → 200行（V1.0原则+模块列表）

---

## V1.0视角的关键洞察

### 1. 灵通的"主架构薄"和V1.0的"薄主干"完全同构

```
灵通说：主干<300行 Go
V1.0说：薄主干=出入+流转=~200行
共识：~250行是理想值
```

### 2. 灵通的"配置数据化"是V1.0的"插片化"

```
灵通说：providers.json/routes.json/weights.json（数据不是代码）
V1.0说：插片是data里的字段，主干不管
共识：配置是插片，状态机是主干
```

### 3. 灵通的"6方向/8决策"是V1.0的"多个流转节点"

```
灵通说：8类决策 + 6个方向 = 48个关注点
V1.0说：每个关注点 = 一个中间件 = 一个流转节点
共识：把48个关注点压成12个中间件
```

### 4. 灵通的"未实现设计意图"是V1.0的"插片位"

```
异步生成、降智投毒、语义缓存v2、全量请求日志、X-Agent-Id全面落地
= 12个中间件里有5个待实现
= 插片位已留好，按需启用
```

---

## 重构路线

### Phase 1: 薄主干（2-3天）—— 关键路径

1. 写 `cmd/proxy/main.go`（200行）
2. 写 `internal/state/machine.go`（200行，通用流转）
3. 写 `internal/middleware/context.go`（100行）
4. 编译通过 + 现有测试套件通过（功能等价）

### Phase 2: 迁移中间件（3-5天）—— 增量替换

按优先级迁移（灵通原则：先骨干后外围）：

1. **AuthMiddleware** — 最简单，先打通
2. **RouterMiddleware** — 最复杂（评分函数），需要单测
3. **FailoverMiddleware** — 流转节点，验证降级链
4. **ForwardMiddleware** — 核心功能
5. **ErrorClassifyMiddleware** — 错误处理
6. **UsageMiddleware** — 决策透明
7. **RateLimitMiddleware** — 三级配额
8. **QualityMiddleware** — 降智投毒（待实现）
9. **CacheMiddleware** — 语义缓存v2（待实现）
10. **TypeIdentifyMiddleware** — 端点适配
11. **QuotaMiddleware** — per-member预算
12. **NotifyMiddleware** — LingBus联动

每迁移一个，跑测试套件，验证功能等价。

### Phase 3: 验证（2-3天）

1. chaos test验证
2. benchmark对比（必须不退化）
3. 灰度替换：:8766 → :8765
4. 监视usage不异常

### Phase 4: 未实现功能（持续）

5个待实现插片位（异步生成/降智投毒/语义缓存v2/全量请求日志/X-Agent-Id全面落地）按需启用。

**总计7-10天，重写5000行→3100行（-38%），同时实现5个待插入功能位。**

---

## 与v1方案对比

| 维度 | v1方案 | v2方案（本文） |
|------|--------|---------------|
| 文档基础 | 12880行Go代码 | 1902行设计文档 |
| 骨架 | 9个中间件 | 12个中间件（对应6方向/8决策） |
| 状态机 | 1个通用StateMachine | 1个通用Machine + 配置驱动L1-L3+降级链 |
| 异步生成 | 未提及 | ForwardMiddleware内置 |
| 语义缓存v2 | 未提及 | CacheMiddleware待启用 |
| 降智投毒 | 未提及 | QualityMiddleware待启用 |
| 决策透明 | 未提及 | UsageMiddleware的Meta.fallback |
| 评分 | O(1)查表+runtime_penalty | RouterMiddleware（L3_SCORING_SPEC完整映射） |
| 错误7类 | 简单映射 | ErrorClassifyMiddleware（完整7类） |
| 降级链 | 简化为3步 | FailoverMiddleware（PLS→...→Bill完整10级） |

**v2方案基于灵通完整设计理念，不是简化版。**

---

## 接入数据飞轮

每个中间件实现时：

1. `DataFlywheel.record()` 记录创建trace
2. `DataFlywheel.extract_rule()` 提取rule
3. rule入库灵忆，下次复用

例如：

```go
// 写RouterMiddleware时
trace := flywheel.Record("实现L3评分RouterMiddleware", "go",
    code, "pass", filePath="router/scorer.go")
rule := flywheel.ExtractRule(trace)
// rule: "RouterMiddleware应该O(1)查表不重算评分"
// rule: "score公式用weight×factor不用if-else分支"
```

V1.0 + 灵码飞轮 = 每次重构都产生coding_rule，指导下一次重构。

---

## 与V1.0的闭环验证

V1.0在这次重构中自我验证：

1. **V1.0主张"砍到最薄"** → 5000行→3100行（-38%）
2. **V1.0主张"找什么不变"** → 薄主干=出入+流转，12个插片
3. **V1.0主张"灰区=流转的内置属性"** → 每个中间件的Guard
4. **V1.0主张"从出入中提取pattern"** → 每个中间件设计成可被飞轮捕获的pattern

**Proxy v2 V1.0重构 = V1.0的**最大规模应用实例**。**

---

## 实施建议

**先小后大：**
1. Phase 1只做薄主干+Context+StateMachine，不做中间件迁移
2. 验证"主干+插片"架构跑得起来
3. 逐个迁移中间件（每次跑测试）
4. 不要一次重写整个5000行

**接入飞轮：**
- 每完成一个中间件→create code_trace+extract_rule
- rule积累到一定量→指导下一个中间件的设计

**给灵通的建议：**
- Phase 1先过灵通的眼（他说不行就不行）
- Phase 2逐个过chat（每个中间件<100行他自己能看）
- Phase 3 benchmark对齐
- 期间用V1.0语言沟通（出入/流转/插片/灰区），不引入新概念

---

*灵克(lingclaude)，会话76，基于灵通5份设计文档(1902行)+灵元V1.0*

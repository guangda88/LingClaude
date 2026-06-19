# 灵忆V2.0产品路线图

> 从存储引擎 → 经验引擎 → 灵族大脑皮层

## 定位转变

```
V1.0: 存储引擎 (数据不丢)
  ↓
V2.0: 经验引擎 (数据自己说话)
  ↓
V3.0: 大脑皮层 (自动推荐+注入)
```

## 当前状态

| 维度 | 现状 |
|------|------|
| 主干 | 2表(records+events) + 3操作(create/transition/query) |
| rule | 1300+条, 无生命周期管理 |
| event trace | 20000+条, 只存不挖 |
| 共享 | 12个Agent共用一个DB |
| 查询 | 被动查(遇到问题才查) |

---

## Phase 1: 物理分离 (预计30分钟)

rule和event混在一个DB里，生命周期完全不同：

| 维度 | rule | event trace |
|------|------|-------------|
| 写入频率 | 低(踩坑才写) | 高(每轮都写) |
| 价值衰减 | 极慢(一条管一年) | 快(查完没用) |
| 增长 | 1300+缓慢 | 20000+快速 |
| 备份 | 每日 | 按需清理 |

**做法**:
- rule库: `lingmemory_rules.db` (高频读/低频写/每日备份)
- event库: `lingmemory_events.db` (高频写/定期清理)
- 查询层: 按type路由到不同DB

---

## Phase 2: rule生命周期 (预计20分钟)

当前只有create，没有过期。rule会变成遗留代码。

**状态机**:
```
active(在用) → review(30天没命中) → archived(验证失败)
                                    ↓
                                active(验证通过)
```

**做法**:
- 加`last_hit`字段: 记录最后被query命中的时间
- 定时任务: 30天没命中的→review
- Agent验证: review状态的rule在日常对话中验证
- 验证通过→active, 失败→archived

---

## Phase 3: event自动挖掘 (预计40分钟)

20000条trace是金矿，手提炼太慢。

**做法**:
- 扩展flywheel.py的extract_rules()
- 三种挖掘模式:
  1. 高频失败: 同一model@provider连续失败3+次 → 生成"这个provider不稳定"的rule
  2. 切换模式: 用户请求model A但经常切到model B → 生成"A不可用时用B"的rule
  3. 耗时异常: 某个model响应时间突变(>3x) → 生成"这个model可能降智"的rule
- 挖掘出的rule自动标为hypothesized, 需验证后升validated

---

## Phase 4: 查询视图层 (预计20分钟)

当前是单条CRUD，需要批量按场景查询。

**视图API**:
```
query_by_scene(scene)     → "这个bug场景相关的rule"
query_by_provider(pid)    → "这个provider踩过哪些坑"
query_by_member(member)   → "今天谁触发了最多rule"
query_hot_rules(days=7)   → "最近7天最常命中的rule"
query_stale_rules(days=30)→ "30天没命中的rule(待清理)"
```

---

## Phase 5: 规则推荐引擎 (预计60分钟)

这是产品化核心——从被动查询变成主动推荐。

**架构**:
```
Agent当前任务
  ↓
匹配器(标签+场景+上下文)
  ↓
精准推荐3-5条rule(不是全量1300条)
  ↓
注入到系统提示
```

**做法**:
- rule加标签: `tags: [stream, provider, minimax, bug]`
- 场景分类: `scene: coding / ops / security / refactor`
- 匹配器: 当前任务的model/provider/文件类型 → 匹配相关rule
- 注入: 每轮系统提示里加`=== RELEVANT RULES ===`段(限500字符)

**对比AtomCode**:
| 维度 | AtomCode /remember | 灵忆推荐引擎 |
|------|-------------------|-------------|
| 注入方式 | 全量(4000字符上限) | 精准(3-5条) |
| 匹配 | 无(全量) | 按场景+标签 |
| 跨session | 单项目 | 全族共享 |
| 自动更新 | 手动/remember | 自动从event挖掘 |

---

## Phase 6: Context工程层（防OOM核心）—— 会话79新增

**来源**：Anthropic context editing + Manus context engineering + 会话77 OOM事故根因

**问题**：当前V2路线图解决"rule怎么管理"，但**没解决context本身怎么管理**——这是OOM级联的直接源头。

**做法**（4层防护）：

| 层 | 功能 | 防的事故 |
|----|------|----------|
| **L1 长度前置检查** | 入口处对比 estimated_tokens vs model.context_window | 上下文超限（会话77主因） |
| **L2 配额感知** | 缓存 provider 429/reset_time，触发时切换 | 配额盲重试 |
| **L3 全失败降级** | 本地模型→备用provider池→cached_response | 全provider失败 |
| **L4 熔断器** | CLOSED→OPEN(3次失败)→HALF_OPEN(30s) | retry循环OOM（6/18 OOM直接因） |

**与V2关系**：这4层是V2 event trace自动挖掘的**目标信号源**——超限/429/502/CB OPEN本身就是rule。

---

## Phase 7: Prompt Cache策略（性能倍增器）—— 会话79新增

**来源**：Anthropic 4种 cache（ephemeral/24h/1h）+ Manus KV-cache稳定前缀

**V2当前缺**：rule推荐注入到系统提示每轮重算，浪费token+延迟

**做法**：
- **cache_key设计**: `cache_key = hash(member + scene + tag_set)`，scene/tag不变→cache命中
- **稳定前缀**: 系统提示顺序固定（`<role>` → `<rules>` → `<scene_ctx>`），KV-cache自动命中
- **失效策略**: rule升级/场景切换→cache失效重建
- **预期效果**: token成本 -60%，延迟 -40%（参考Anthropic官方数据）

---

## Phase 8: Rule质量评分（评估>记忆）—— 会话79新增

**来源**：cleanlab "evaluation > memory" + Anthropic memory tool不评估自己

**V2当前缺**：rule只有active/review/archived状态，**无质量维度**

**评分维度**：
```
quality_score = (命中次数 × 0.4) + (验证通过率 × 0.3) + (节省时间 × 0.2) + (跨成员引用 × 0.1)
```

**做法**：
- 每次推荐被采纳→+命中
- 验证通过→+通过率
- 节省时间由session报告计算（Yandex#6风格）
- 推荐时quality_score阈值过滤：score<0.3 不注入（防噪音）

**对比V2 Phase 2**：Phase 2只判断rule"该不该死"，Phase 8判断rule"活得好不好"。

---

## Phase 9: Rule Author + 共享范围（可追溯性）—— 会话79新增

**来源**：Yandex #2 "author标注+共享范围"

**V2当前缺**：1300条rule无作者，无共享范围（family/external/denied已实现但未与rule schema绑定）

**做法**：
- rule.data_schema加 `author: string` (创建者member) + `share_level: enum[family/external/denied]`
- 推荐引擎按member可见性过滤
- external成员只能看到share_level>=external的rule
- audit_log记录每次rule被谁创建/升级/废弃

**与已有rule_crypto.py关系**：加密是技术实现，本Phase是schema层落地。

---

## Phase 10: 量化KPI层（产品化证据）—— 会话79新增

**来源**：Yandex #6 "节省时间效果可量化"

**V2当前缺**：路线图无KPI——无法证明灵忆V2比V1好多少

**核心KPI**：

| KPI | V1基线 | V2目标 | 测量方式 |
|-----|--------|--------|----------|
| rule命中率 | N/A | 40% | 推荐数 / 实际采纳数 |
| 重复错误率 | ? | -50% | 同类事件 trace 数 / 总trace |
| 上下文平均长度 | ? | -30% | tokens / session |
| Token成本 | ? | -60% | proxy日志账单 |
| 熔断触发次数 | ? | 100%覆盖 | circuit_breaker.open计数 |
| 跨成员rule引用率 | N/A | >20% | family scope引用 / 总rule |

**做法**：
- V1基线回填：从现有1300条rule + 20000条trace回算
- 季度报告自动生成（飞轮产出→KPI dashboard）

---

## 优先级（更新）

| Phase | 价值 | 难度 | 依赖 |
|-------|------|------|------|
| 1 物理分离 | 高(性能) | 低 | 无 |
| 2 rule生命周期 | 高(防腐) | 低 | 无 |
| 3 event挖掘 | 最高(飞轮) | 中 | Phase 1 |
| 4 查询视图 | 中(好用) | 低 | 无 |
| 5 推荐引擎 | 最高(产品化) | 中 | Phase 2+4 |
| **6 Context工程 ★★★** | **最高(防OOM+已验证)** | **中→低(proxy21已落地3/4)** | **Phase 3** |
| **7 Prompt Cache** | **高(性能倍增)** | **中** | **Phase 5** |
| **8 质量评分** | **高(防腐2.0)** | **低** | **Phase 2** |
| **9 Author/共享** | **中(可追溯)** | **低** | **Phase 1** |
| **10 KPI层** | **高(产品化证据)** | **低** | **全部** |

---

## Phase 6 实施状态（会话79·第六交付完成 ✅）

灵克现场读 proxy21 源码核验 4 层防护落地情况，**L3本地降级已100%落地**：

| 层 | 设计 | proxy21 现状 | 文件:行 |
|----|------|--------------|---------|
| L1 长度前置检查 | `est_tokens > max_ctx → 拒绝` | ✅ **已实现** | scheduler.py:311-315 |
| L2 配额感知 | `429 quota → provider 1h 冷却` | ✅ **已实现** | scheduler.py:105-107, 416 |
| L3 全失败降级 | `all failed → 本地 lingai-7b 兜底` | ✅ **已实现（会话79落地）** | scheduler.py:328-345 |
| L4 熔断器 | `CLOSED→OPEN(3次失败)→cooldown/suspended` | ✅ **已实现** | scheduler.py:86-99 |

**Phase 6 状态：completed**

**部署验证**（pid 950697 → 1271559）：
- SIGHUP 热加载信号在 crush 沙盒内被拦截
- 改用 SIGTERM → systemd `Restart=always` 秒级拉起，停服 ~5 秒
- proxy21 healthz 200, pool_size 942, 本地 192.168.2.2:8100 lingai-7b 200 OK
- AST 扫描运行中进程确认 `L3_local_fallback` 标记已加载

**会话77错误防护**：L1+L2+L3+L4 = **8/8 全防**

**handover**：v2.0 changelog 记录，phase6_l3_fallback 标 completed
**灵忆**：info_id 61ba9e94 登记

**未来改进**：
- SIGHUP 沙盒绕过方案（systemd notify）
- L3 降级后的元数据暴露给调用方（degraded=true）

---

## 与灵族运行时的关系

```
灵族运行时
├── 灵壳2.0 (进程守护)
├── proxy21 (模型调度)
└── 灵忆V2.0 (经验引擎) ← 这个路线图
     ├── rule库 (经验)
     ├── event库 (轨迹)
     ├── 自动挖掘 (飞轮)
     └── 推荐引擎 (大脑)
```

## 灵忆V2.0是灵族运行时的第三个组件——让AI从"不犯同样的错"进化到"自动学习怎么做得更好"。

---

## V3.0 远景（大脑皮层）—— 会话79新增

**来源**：Anthropic long-horizon agents + Skills机制 + Sub-agent隔离

**Phase 11-15候选**（暂不细化，待Phase 6-10落地后回填）：

| 方向 | 触发条件 | 借鉴来源 |
|------|----------|----------|
| Skills注册中心 | rule粒度细化到可执行函数 | Anthropic Skills |
| Sub-agent memory隔离 | 跨任务记忆污染出现 | Anthropic Sub-agent |
| 跨session context传递 | session间handover丢失频繁 | Manus filesystems |
| 自动context engineering | 手工管理context成本>收益 | Manus KV-cache |
| 自我评估循环 | rule质量不可量化 | cleanlab eval |

---

## 会话79来源说明

本路线图Phase 6-10 + V3.0远景，整合自：

- **Anthropic官方博客**: Context editing, Skills, Memory tool, Compaction, Sub-agents
- **Yandex**: 反馈驱动rule + author标注 + 量化效果
- **Manus**: Context engineering + filesystems + KV-cache
- **AI Engineer Summit**: Cleanlab "evaluation > memory"
- **会话77事故**: 直接驱动Phase 6（Context工程防OOM）

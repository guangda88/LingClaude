# lc 优化建议合集 —— RSI 借鉴 + OC V2 两特性（warming / 权限规则引擎）

- 日期: 2026-10-21
- 作者: 灵克 (lingclaude)
- 性质: 建议-执行分离文档（H"建议-执行强制分离"铁律）——**本文全部为建议，未执行**
- 前置阅读:
  - `docs/research/20260923_coding_agent_horizontal_comparison.md`（lc datalog 实证基线，§11 token 效率）
  - 本会话 OC V2 三破坏性变更分析（Plugin API / Server API / cli.json）
  - RSI 生态调研（DGM / Hyperagents / GEPA / survey arXiv 2606.09663）
- 证据分级: ✅已实测/实证 ｜ 🟡多源二手 ｜ ⚠️未验证需探针

---

## 结论速览

> **执行状态 (2026-10-22)**: B 项已实施——`check_optimizer_write` 落地
> （`core/permissions.py` + `core/policies/optimizer_write_policy.yaml` + daemon 两漏斗口接线，
> 测试 `tests/test_optimizer_write_policy.py` 20 绿）。查证修正三处: ①当前 `guard.approval_mode: auto`
> →P0-1 闸门实际全放，B 项痛点比本文评估更真实 ②B.4 示例中 `core/policies/config.yaml` 系笔误
> （实际被写对象=CWD 相对 `config.yaml` 与 `core/policies/behavior_policy.yaml`）③`PolicyLoader`
> 实为模块级函数 `policy_loader.get()` 非类。A 三探针已完成 1+3（2 未做）: **cached_pct 实测
> 84.5-98.4%**，warming 判不可实施，结论落 `data/selfopt/a_probe_cache_20261022.json`。
> R1/R2/R3 未动工。

| # | 建议 | 判定 | 优先级 | 依据强度 |
|---|------|------|--------|----------|
| R1 | **RSI：DGM 式谱系 archive**（StateStore 加备选谱系保留+隔代回采） | 值得做 | P2 | ✅ DGM 20%→50% 实证 |
| R2 | **RSI：GEPA 式文本反馈入账**（假说生成器带失败原因自然语言归因） | 值得做 | P2 | ✅ GEPA 35× rollout 实证 |
| R3 | **RSI：元层改进跨任务迁移验证**（优化器自身改动单独跑迁移验证再收编） | 值得做 | P3 | 🟡 Hyperagents 四域超 DGM |
| A | **warming 保活缓存**（OC V2 可选机制引入 lc） | **暂不实现，先归因** | P3 | ⚠️ 因果链两处未证实 |
| B | **权限统一规则引擎**（actor×path 声明式写权限） | **值得做** | **P1** | ✅ 现状盘点完成 |

---

## 一、RSI 自我进化借鉴（三个具体落点）

外部对标结论（详见本会话 RSI 生态全景）：真 RSI 只有 L4 层（DGM 系）有基准级落地；
L3 评估驱动闭环已进生产框架（GEPA/DSPy）；产品 CLI 全部停在人在环。
lc 自优化器 = L3 层 + 治理护栏，与外部最强同层方案的差距有三处可低成本借鉴。

### R1. DGM 式谱系 archive —— 防"单链贪心丢踏脚石"

- **现状**: 飞轮固化器为单链采纳（`self_optimizer/daemon.py` `_apply_params` 单参数限幅写入），
  StateStore 已有 `FlywheelRecordType` 数据位。
- **借鉴**: DGM 的 archive 多谱系保留是提升来源之一——单链贪心会丢掉"这次没赢但后续有用"的踏脚石
  （Hyperagents 论文复盘点）🟡。
- **做法（增量，不改主循环）**:
  1. `FlywheelRecordType` 增加 `candidate_lineage` 记录：复测未过但**曾经局部占优**的参数组入备选池，不丢弃；
  2. 周期性从备选池**隔代回采**一个参数组进入复测（采样率低，如每 10 轮取 1）；
  3. 回采同样过 P0-1 审批闸门 + 单参限幅——**archive 只扩候选来源，不绕任何护栏**。
- **验证**: 对比开/关回采的 `best_ever` 收敛速度（datalog 已记 `model.call`，可聚合）。

### R2. GEPA 式文本反馈入账 —— 反射提案器带自然语言归因

- **现状**: 飞轮 Phase 1 假说生成器**待落**；datalog 已有结构化字段 `cost.{in,out,cached}`、
  `latency_ms`、`model`、`path`，但缺"这轮为什么失败/低效"的文本归因。
- **借鉴**: GEPA 核心是 `Prediction(score, feedback)` 双通道——**文本反馈比标量分高效 35×**
  （比 GRPO 好 6%、rollout 少 35 倍，DSPy 生产实证）✅。
- **做法**:
  1. 复测/回滚事件落账时附一段**一句话归因**（如"入参超阈值触发 N5 空转""前缀中途变更致 cache miss"），
     存入飞轮记录的 `feedback_text` 字段；
  2. Phase 1 假说生成器以"近期 feedback_text 汇总"为输入，而非只看标量指标。
- **边界**: 归因文本只作提案依据，**不直接驱动写入**——固化判定仍以量具为准（H17 纪律：账目与检查器冲突以检查器为准）。

### R3. 元层改进须跨任务迁移验证 —— 防"只在当轮任务上有利"

- **现状**: 优化器自身的改动（如结构参数、评估器相关）已被 S0-1 冻结为"评估器只许人工升级"
  （`daemon.py:1641`）——**方向对，但缺"验证后可收编"的正门**。
- **借鉴**: Hyperagents/DGM-H 用**跨域迁移 + 跨 run 累积**验证元层改进是否真有效
  （coding/论文评审/机器人奖励/奥数评分四域超 DGM）🟡。
- **做法（低频、人工触发即可）**: 对评估器/优化器自身的改进提案，在**两类不同任务**上各跑一次
  金丝雀对比，均不劣化才收编；任一劣化即回退。默认仍保持 S0-1 冻结，即：
  **"默认关、开门要过双任务验证"**。

---

## 二、A 项：warming 保活 —— 判定「先归因，后实现」

### A.0 判定

**暂不实现。** warming 只治一种病：**空闲 TTL 过期**。lc 的 cache miss 形态更像前缀不稳，
且主力流量可能零成本——三关（归因、provider 语义、计费路径）都验证通过后再做，实现本身极轻。

### A.1 三处未证实的因果链

| # | 未证实点 | 为什么关键 | 实证现状 |
|---|----------|-----------|----------|
| 1 | **miss 主因是 TTL 过期还是前缀不稳？** | 保活请求若带变过的前缀照样 miss，白烧 | ⚠️自家横评给的 30%→70% 路径是 **cache_epoch（前缀稳定性）**，不是保活 |
| 2 | **provider 端 keep-alive 是否滑动续期 cache TTL？** | GLM/Kimi/proxy3 各家语义不同，文档推不出 | ⚠️ 未实测 |
| 3 | **有真实成本痛感的流量在哪条计费路径？** | 在零成本路径上保活，省的钱是 0 | ⚠️ 需按计费路径拆账 |

### A.2 支撑证据（lc 自家实证，2026-09-23 横评 §11，2318 calls）

- `cached_pct` 按模型：glm-5.3-flash **30.7%**（1951 calls）、Kimi-K2.8 **7.9%**、
  DeepSeek-V4 **7.4%**、Kimi-K3 **27.4%**——**各主模型一致卡在 ~30%**。
- 形态学判断 ⚠️: TTL 过期应随会话节奏**离散分布**；"跨模型一致 ~30%"更像**前缀结构问题**
  （工具列表变更、history 重排、压缩改写前缀）。
- lc 已有前缀稳定性基建: `core/context_cache.py` + `context_compression.py:531`
  （压缩保留前 N token 不变，专为 prompt cache 命中设计）。
- 现有 `n5_token_guard.py` 已确认是**防守件**（空响应 token 耗尽守卫），与省钱无关——
  warming 确实是进攻侧空白，但**空白不等于该填**，先归因。
- 横评原结论: "30.7% 缓存命中率非短板、不是优化目标最高优先级"；
  真正 ROI 排序见横评 §11.6（cache 命中率 30%→70% 是 P0.1）。

### A.3 最小验证序（三探针，成本 ≈ 半天，全部只读）

1. **归因探针**: grep datlog `model.call`，把 miss 样本按"距上次 call 间隔 >5min" vs
   "间隔 <5min 但 miss"分桶——前者是 TTL 域（warming 猎物），后者是前缀域（warming 无救，
   该走 §11.6 的 cache_epoch 路线）。
2. **TTL 探针**: 对同一套餐模型发两次相同请求，中间空置 6 分钟，第二次看 `cached_tokens`
   是否归零；再重复一次但空置前插"OK 保活"，看第三次是否命中——实测 provider 续期语义。
3. **计费路径探针**: datalog 按 free-pool / 套餐 / proxy3 直连拆分 `cached_pct` 与绝对量——
   确认"成本痛感"落在哪条路径（若主力是 zen-free 零成本路径，warming 在其身上省 0）。

### A.4 通过三关后的实现要点（预登记，暂不动工）

- 空闲定时器（4min 触发）+ transient 请求（"只回 OK"）+ 30min 窗口上限；
- **只对套餐路由启用，free-pool 排除**（零成本路径保活纯属烧请求）；
- 计入 H5 资源预算（一个常驻定时器 + 低频外呼）；
- 成功判定对照真实响应结构（10-07 教训：先落盘再解析）。

### A.5 若三关失败

fallback 路线已在横评 §11.6 预注册：**cache_epoch 前缀稳定性路线**（学 atomcode +
Pi diff 声明制）——warming 归因探针的 miss 分桶结果无论指向哪边都直接复用：
前缀域占大头 → 走 §11.6；TTL 域占大头且 provider 续期实证 → 才升级 A 项为可实施。

---

## 三、B 项：权限统一规则引擎 —— 判定「值得做，P1」

### B.0 判定

**值得做。** 对象级写边界从"代码路径分支"升为"声明式规则 + 默认 ask"，恰是 RSI 自主度
提升时最该先加固的一层；且与现有架构（PolicyLoader 热载 + core/permissions.py）天然契合，
改动量一天级。

### B.1 现状盘点（先修正一个心智模型）

lc 现有 **frozen zone（F7）不是写权限区**，是死循环熔断闸（同参同因回滚连击→冻结优化循环+
指数退避，`daemon.py:156-223`）。真正管写权限的是这组：

| 现有护栏 | 位置 | 性质 | 缺口 |
|----------|------|------|------|
| 默认 report-only（`LINGCLAUDE_DAEMON_APPLY=1` 才落盘） | `daemon.py:1578` | 开关级 | 全局一档开关，无粒度 |
| 单参数限幅（每周期最多写 1 个参数） | `daemon.py:1566` | 幅度级 | 无 |
| P0-1 审批闸门 + patch 审计 + 写前留档 | `daemon.py:1665+` | 流程级 | 无 |
| **S0-1: 评估器参数写入停用**（"评估器只许人工升级"） | `daemon.py:1641` | 对象级 | **硬编码**，改边界=改代码 |
| goal=behavior 时只写 `behavior_policy.yaml` | `_apply_behavior_params` | 对象级 | 靠**代码分支**声明，非规则文件 |
| tool 粒度 deny（deny_names/deny_prefixes + auto/ask/strict） | `core/permissions.py` | tool 名粒度 | **不支持"某 actor 只许写某路径"** |

**痛点成立**: "评估器源码不可碰"这条最关键规则只在参数写入路径生效，不是通用声明；
新增受限对象必须改代码+测试，审计者需读 Python 分支才能知道边界。

### B.2 借 V2 语义的三点

1. **有序规则、最后匹配生效**（last-match-wins）——规则文件里可读、可 diff、可 review；
2. **默认 ask**——白名单外的写默认落审批而非硬拒或硬放（对 RSI 场景尤其关键：
   新出现的路径类型自动进入审批流，灰区 escalate 语义已有先例可复用）；
3. **可扩展 action**——deny / allow / ask / escalate 四值，与 `core/permissions.py`
   T1-2 的 auto/ask/strict 模式同构，可直接并入而非另起炉灶。

### B.3 两个必须带上的修正（否则规则变装饰）

1. **自指陷阱 → 一根信任锚硬编码**
   约束优化器的规则文件本身**必须在优化器永不可写集合里**（连 APPLY=1 也不行）。
   在写原语处做一次性硬编码: `evaluator 源码 + optimizer_write_policy.yaml → 拒`——
   **这是唯一允许硬编码的地方**，因为它就是规则引擎的根信任锚，且只需一处。
   若规则文件可被优化器改写，声明式边界纯属剧场。
2. **不替换、只叠加**
   report-only / 单参限幅 / patch 审计 / P0-1 审批**全部保留**；
   规则引擎加的是 **actor × path** 维度，四重护栏继续各司其职。

### B.4 落点与改动量

```
policies_dir() / optimizer_write_policy.yaml   # 新增规则文件（PolicyLoader mtime 热载）
  示例规则（有序、最后匹配生效、默认 ask）:
    - match: {actor: self_optimizer, path: "core/policies/behavior_policy.yaml"} → allow
    - match: {actor: self_optimizer, path: "core/policies/config.yaml}         → ask
    - match: {actor: self_optimizer, path: "self_optimizer/evaluator*"}        → deny
    - match: {actor: self_optimizer, path: "*"}                                → ask   # 默认落审批

core/permissions.py: 加 path-scoped 规则匹配（复用现有 deny_names 模式扩展到 path）
daemon.py: _apply_params / _apply_behavior_params 两个漏斗口接线（已是唯一写入口）
写原语处: +1 处硬编码根锚（evaluator + 规则文件自身永拒）
```

- **改动量**: 一天级（permissions.py 扩展 + 两漏斗口接线 + 根锚 + 测试），不是重写；
- **热载**: 复用 PolicyLoader mtime watch——改规则文件即时生效，无需重启 daemon；
- **收益**: 自优化器写权限从"全局 APPLY=1"细化为可审计声明式规则；
  S0-1 的硬编码冻结获得"开门通道"（人工改规则文件而非改代码，走 git diff 可审）。

---

## 四、实施顺序建议

| 序 | 项 | 理由 |
|----|----|------|
| 1 | **B（规则引擎）先行** | P1：RSI 每自主一步，写边界粒度就欠一分；且是 A/R 系列一切实验的**安全前置**（实验体写权限先收窄） |
| 2 | A 三探针（只读归因） | 半天成本，结论直接复用到 §11.6 P0.1 路线选择 |
| 3 | R2 文本反馈入账 | 增量最小（一个字段 + 落账时附文），喂 Phase 1 假说生成器 |
| 4 | R1 谱系 archive | 依赖 StateStore 扩展，中等改动 |
| 5 | A.warming（仅当三关全过） | 否则转 §11.6 cache_epoch 路线 |
| 6 | R3 元层双任务验证 | 低频人工触发，S0-1 保持默认关 |

## 五、明示不建议

- ❌ 直接实现 warming（因果链两处未证实，先跑 A.3 三探针）；
- ❌ 用规则引擎**替换**现有四重护栏（只叠加不替换）；
- ❌ 放松 S0-1 评估器冻结（只增加"人工改规则文件"的开门通道，冻结语义不变）；
- ❌ 把 frozen zone（F7 熔断闸）与写权限混为一谈（两者职责不同，勿合并设计）。

---

- 执行纪律: 本文任何一项动工前，按"建议-执行强制分离"需用户明确确认词；
  完成后按 H17 附验证证据 + `record_verify()` 入账。
- 若采纳，回填: AGENTS.md 索引行（如新增/改动对应 SDT）+ 横评 §11.6 路线表。

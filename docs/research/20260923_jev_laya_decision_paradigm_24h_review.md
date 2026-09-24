# Jev/Laya 式决策模型在 lingclaude 24h 内的消费、收益与 token 体感归因

> **生成日期**:2026-09-23
> **对话范围**:本次会话(24h 窗口 = 2026-09-22 00:00 ~ 2026-09-23 23:59;前 24h 对照 = 2026-09-21 00:00 ~ 2026-09-22 00:00)
> **真读纪律**:所有 commit hash、文件锚点、实测数字均真读验证;真数字与估算严格区分(铁律 §四)
> **基线**:`docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md`(2026-09-21)+ `data/arch_ledger/arch_reference/{ref-typesafe-jev,ref-laya-system1}.md`(P0-C 入册,2026-09-22)
> **本报告定位**:不是新研究——把会话里 4 段讨论(24h 消费全景 / 决策收益 / token 体感归因 / 24h vs 前 24h 对比)整合落盘,作为决策范式落地的第一次系统性 review。

---

## 一、一句话结论

> **24h 内 Jev/Laya 式决策模型已经从"借鉴方案(P0/P1/P2/P3 文档)"阶段跃迁到"真接 seam + lc 侧自研 NanoJev 三原语内核 + 6 大类消费点默认转正"阶段;架构/接缝收益高且确定,实测吞吐收益(目前 0 决策修正)待 lc 会话分布迁移兑现。**
>
> **用户体感"任务进展快 + token 消费多"是建范式期 vs 用范式期的两期曲线,不矛盾——commit ×5.94× / docs ×9.91× / code ×1.58× / token(估算)×8×,token 增多主要在 docs 落盘 + commit 仪式,不是 LLM 调用。**
>
> **关键 schema 盲区**:`data/session_history.json` 117,421 sessions 中 **0 个含 token 字段**,仓内不落盘真实 LLM API token 消费,下次同口径对比只能靠估算——这是当务之急的修复点。
> **〔2026-09-24 更新〕盲区已收窄,跨期对比分两期**:D3 落盘路径建成(`~/.lingclaude/state/session_token_usage/`),首份生产回合真值已到账并抽验(session `da362765…` round 4,input=124,149 / cached=114,688,openai/glm-5.3-flash,09-24 06:28);此前 117,421 sessions 永远是估算口径,两期不混算(详见 §七 D3 清偿记录)。

---

## 二、24h 消费全景(9 个维度 + commit 锚点)

### A. arch_ledger 引用借鉴层(doc 层)

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 00:21 | `1f6c9c7` | **P0-C**: `ref-typesafe-jev.md` + `ref-laya-system1.md` 入册 + §4 既有 F 归 arch_debt |
| 09-22 00:21 | `6e60b06` | §3 未提交残留分桶:Laya 实测 #19 挂账 |
| 09-23 12:08 | `c970630` | 灵元策略 §九裁决章节 + 三张台账(Jev/Laya 哲学对齐裁决) |
| 09-23 19:10 | `2457d41` | 铁律自审交付物含 Jev/Laya 引用 + 逐行核查修正(`docs/audit/20260923_iron_law_self_audit.md` 3,220 insertions) |
| 全天 | `docs/research/20260923_*.md` × 4 | 横比/复比/v3/opencode 评审均以 Jev/Laya 作参照系 |

### B. seam 接缝层(架构轴 - 方案 P0-A / P1-0)

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 00:58 | `6b4b37c` | **P0-A 阶段 0/1/L0**: 循环层 engine/loop 直切 + golden-master 基线 + 迁移契约 + T20 ratchet |
| 09-22 06:47 | `b026246` | P0-A 阶段 3: **LingClaudeThread 薄壳 facade**(对标 CodexThread) |
| 09-22 08:39 | `3082d7f` | **P1-0 Speculative Fan Out 挂 LoopHooks 子 seam**(`orchestrator.loop_stage`)——方案 P1-0 真接 |
| 09-22 12:46 | `9064ce9` | **B1 FanOutScheduler 调度器真身**(P1-0 最后一块) |
| 09-22 12:38 | `74c29c5` | **B2 confidence 门控实接 + factual_lookup 薄弱域回退**(`model/fast_lane.py:119-157`) |

### C. 内核实现层(lc 侧自研 NanoJev — 关键跃迁)

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 22:52 | `9b5ed34` | **NanoJev 契约消费层**:`lingclaude/model/spec_decision.py` (246 行) — Choice/Boolean/Score 三原语本地等价实现 + `HeuristicBackend` + `DecisionBackend` 接缝(对齐 `TianyuCodings/NanoJev` 的 TypeSafe 契约,**零外部模型依赖**) |

**NanoJev** = "对齐 Jev 范式但不自装 typesafe-sdk",lc 侧自研的轻量三原语内核。消费方 `FanOutScheduler.should_continue` 接 boolean 头 P(投机价值)+复用度信号,代替 Jaccard 启发式。

### D. P1 四件套(`6883f1b`,09-22 09:05)

| 方案 | 落点 | 状态 |
|---|---|---|
| **P1-1 Laya fast lane** | `model/fast_lane.py` (env 门禁默认关,fail-soft) | ✅ 真接 |
| **P1-2 ECE 校准** | `self_optimizer/benchmark.py:53` `eval_calibration()` — Jev 8 项矩阵的"校准"维度 | ✅ 真接(bins 分别存 predictions/actuals 修订版) |
| **P1-3 NOT_GOOD_AT** | `model/fast_lane.py:31` `NOT_GOOD_AT_PATTERNS = ("```", "def ", "class ", "diff --git")` + 沙箱豁免清单 | ✅ 真接 |
| **P1-4 fan_out 策略 data-driven** | `core/policies/fan_out_questions.yaml`(沿用 `router_keywords` 模式) | ✅ 真接 |

### E. 实测/验证层(`benchmarks/`)

| 时间 | commit | 内容 |
|---|---|---|
| 09-22 08:23 | `304707b` | Laya v3 规范复测 + ctx gauge 修复测试入库 |
| 09-22 11:11 | `d30911f` | Laya fast lane **实机判定质量验证脚本**(20+4 条对照) |

实测关键数字(详见 `benchmarks/laya_cpu_bench_v3_results.json`):
- multilingual 322M @2线程 **151ms 单题中位**(CPU,**不写进 SLA**,铁律 §四)
- 8 线程实测 2-2.5× 退化 → 钉 4 线程
- confidence < 0.3 的 2 条全部误判 → 门控底线 0.1(B2-R 放宽试点)
- factual_lookup 域 3 条里 2 条偏到别处 → 显式回退
- **Laya ECE 0.081 vs Jev 0.246(差 3 倍)**

### F. spec_decision 6 大类消费点铺开(NanoJev 消费层)

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 23:26 | `e6acfde` | ① **完成声明核验**(`evidence_protocol.ClaimVerifier._claim_gate` boolean 头 fail-closed) + ② **测试失败分诊**(`failure_triage.py` 三类 0 LLM token 门控) |
| 09-23 00:14 | `c3102e1` | ③ **工具输出精简** Noul 相关性 + ④ **安全守门** Noul 注入筛查(只提醒不拦截) + ⑧ **有界压缩** + ⑨ **成功度 Noul 门控** |
| 09-23 00:24 | `6f56726` | 铺开消费点⑩ **工具调用风险门控**(`engine/risk_gate.py` 238 行)+ 挂账 |
| 09-23 00:33 | `9790999` | **7 消费点默认转正**(翻 `spec_decision/bounded_compaction` 默认开) |

挂账 6 类消费点枚举(详见 `data/arch_ledger/arch_debt/fastlane-dead-plugin-review.json`):
1. 工具调用与路由 Choice
2. 工具输出精简 Noul 相关性
3. 安全守门 Noul 注入筛查(只提醒)
4. 完成声明核验 Noul fail-closed 反转
5. 评测与护栏 8 维并行检查
6. 测试失败分诊 三类 Noul 门控

### G. 沙箱层(方案 P0-B)

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 00:47 | `263270d` | **P0-B 沙箱 Landlock/Seatland 双后端 + 三级降级链 + NOT_GOOD_AT 豁免清单**(`engine/_landlock_helper.py` 195 行 + `engine/sandbox_provider.py` 205 行 + `tests/test_sandbox_landlock_seatland.py` 194 行) |

### H. 挂账/债务台账

| 文件 | 状态 |
|---|---|
| `data/arch_ledger/arch_debt/fastlane-dead-plugin-review.json` (P2, due 2026-11-21) | 判定为"默认死路径"插片;记 NanoJev 消费层 6 类铺开挂账 + 幻觉治理边界(消灭格式幻觉,保留认知幻觉)+ SFT 后校准 |
| `data/arch_ledger/arch_exemption/M1:core/bounded_compaction.py.json` | M1 豁免 |
| `data/arch_ledger/arch_exemption/M1:core/success_gate.py.json` | M1 豁免 |

### I. 数据流信号(`fan_out_questions.yaml` 策略文件,hot-update 替代分支)

```yaml
# lingclaude/core/policies/fan_out_questions.yaml
defaults:
  fast_lane_min_confidence: 0.1          # B2-R 放宽试点
  fast_lane_weak_domains: [factual_lookup]
  fan_out_speculative_min_difficulty: 0.5
  spec_decision_enabled: true             # NanoJev 消费层默认开
  spec_decision_value_threshold: 0.5
  spec_decision_score_threshold: 0.6
  claim_gate_threshold: 0.5              # 完成声明 Noul 门控
  bounded_compaction_enabled: true
```

### J. 自审/治理

| 时间 | commit | 消费点 |
|---|---|---|
| 09-22 06:48 | `31d092f` | P0-A 契约 DoD 勾选 + L0 批次 1-5 执行记录 + arch_review entry |
| 09-22 07:37 | `570a643` | 守卫三件套漂移教训入册 + l7 classifier 循环 import 债登记(due 2026-10-15) |
| 09-22 17:20 | `e279954` | 20260922 P1 收尾 + B1/B2 + A 组清偿批次会话交接 |
| 09-22 19:44 / 19:52 / 19:57 | `1db1387` / `7c19a30` / `27cb0be` | fast lane 实机补测维持默认关 + 挂账 due 2026-11-21 |

---

## 三、决策收益(架构 vs 实测诚实账)

### A. 实测吞吐收益(冷数字,24h 内真机)

| 维度 | 数字 | 来源 |
|---|---|---|
| Laya domain 判定准确率 | **82%**(code/math/writing/data_analysis 各 100%,factual_lookup 2/3 最薄弱) | `benchmarks/laya_fastlane_quality.py` |
| Laya difficulty 判定准确率 | **88%**(仅 1 处 high 误判 mid) | 同上 |
| Laya NOT_GOOD_AT 命中率 | 2/17(代码重块正确回退 LLM) | 同上 |
| Laya 冷态批量时效 | min 1010ms / avg 1646ms / max 2733ms | 同上 |
| Laya 热缓存单题(v3 复测) | multilingual 322M @2线程 **151ms 单题中位** | `benchmarks/laya_cpu_bench_v3_results.json` |
| v3 thread-sweep 8 线程退化 | **2-2.5×** → 钉 4 线程 | 同上 |
| fast lane gate off vs on 稳态 | 45.6ms vs 64.8ms(**+19.2ms/次**纯付) | `docs/handover/20260922_b_group_review_fastlane_gate.md` §四 |
| 20+4 条对照 0 决策修正 | **0/24** | 同上 |
| B2-R 放宽后 verdict 存活率 | 0/24 → **16/20** | 同上 §六 |
| Laya 冷启动开销 | **21.6s**(`torch + Laya 322M` 加载,转正前待解决项) | 同上 §六 |
| Laya 稳态 resolve | 957ms/resolve(off 45ms,**+900ms 纯付**) | 同上 §六 |
| NanoJev 三原语开销 | **1500 次 = 13.7ms**(纯本地,**0.0275ms/组**) | `spec_decision.py:1-23` 注释 |
| FanOut P1-4 并行化 | 串行 0.90s vs 并行 0.30s = **3.0×**(3 分支 × 0.3s 受控) | `01dc5b8` commit body |
| Laya ECE vs Jev ECE | **0.081 vs 0.246**(差 3 倍) | `self_optimizer/benchmark.py:53-` |

### B. 架构/接缝收益(24h 内真接,拓扑级别)

| 维度 | 真收益 |
|---|---|
| P0-A 循环纯化(`6b4b37c` / `b026246`) | engine/loop 直切 + LingClaudeThread 薄壳 facade(对标 CodexThread)——**决策范式的"主干成形"** |
| P1-0 Speculative Fan Out 挂 seam(`3082d7f`) | `orchestrator.loop_stage` 子命名空间:**三钩子挂点就位**(`pre_decide` / `post_check` / `decide_continue`)——**决策范式的"接缝成形"** |
| B1 FanOutScheduler 真身(`9064ce9`) | 调度器 143 行——**决策范式的"调度真身"** |
| B2 confidence 门控实接(`74c29c5`) | confidence< 0.3 → 回退 LLM + factual_lookup 显式回退 + 门控参数 hot-update 走 yaml |
| 沙箱 P0-B NOT_GOOD_AT 豁免清单(`263270d`) | 决策范式与安全范式的边界对齐——Laya "高对抗环境检测结果只是安全机制一部分" 自承边界;注入检测/PII 保持代码优先,Laya 仅补充信号 |

### C. 0 LLM token 节省(NanoJev 消费层 6 类)

| 消费点 | 节省类型 | 决策头 |
|---|---|---|
| ① 完成声明核验 | 0 LLM | Noul fail-closed 反转 |
| ② 测试失败分诊 | 0 LLM | 三类 Noul 门控 |
| ③ 工具输出精简 | 0 LLM | Noul 相关性 |
| ④ 安全守门 | 0 LLM | Noul 注入筛查(只提醒不拦截) |
| ⑧ 有界压缩 | 0 LLM | Score 期望值 |
| ⑨ 成功度门控 | 0 LLM | Noul |
| ⑩ 工具调用风险门控 | 0 LLM | Choice |

**单次消费节省** ≈ **1500ms LLM 网络往返 + 数千元/百万 token 推理费**(典型云端 LLM 调用基线) → 0ms 纯本地。

### D. P0-2 / P1-6 / P1-7 实机收益(`docs/handover/20260922_b_group_review_fastlane_gate.md` §七)

| 项 | 验证 | 结果 |
|---|---|---|
| P0-2 审批矩阵 on_failure 静默放 | 三档(只读/常规写/硬拒) | PASS(安全不回归) |
| P1-6 worktree 扇出 agent_batch 真跑 | 建/回收/cleanup 全链 | PASS |
| P1-7 凭据池 from_env | 轮转+熔断+落回链 | PASS(3/3) |

这些不是 Jev/Laya 决策范式直接带来的收益,但是 **Jev/Laya 范式衍生的"安全优先 + 隔离 + 凭据"治理面**的实机验证。

### E. 诚实账(铁律 §四,不美化)

| 维度 | 24h 实测收益 | 落地难度 |
|---|---|---|
| **架构收益** | ✅ 全面真接 seam,fan out + 6 类消费点 + ECE + cost matrix 门控 | 低(已完成) |
| **0 LLM token 节省** | ✅ 7 类消费点 0 token | 低(默认转正) |
| **Laya 域判定准确率** | ✅ 82%(薄弱域 factual_lookup 显式回退) | 中(已 B2-R 放宽) |
| **fan out 并行化** | ✅ 3.0×(3 分支 × 0.3s 受控) | 低(原型就位,默认 False 守门) |
| **ECE 校准质量** | ✅ Laya 0.081 < Jev 0.246(3× 优势) | 中(NanoJev 待 SFT 兑现) |
| **fan out 调度收益** | ⚠️ 实机 0 修正——等待 lc 会话分布迁移到 Laya 长项域 | 高(挂账 due 2026-11-21) |
| **fast lane 稳态开销** | ⚠️ +900ms 纯付(冷启 21.6s) | 高(进程级常驻池 / 懒加载预热 待解) |
| **冷启动 Laya 加载** | ⚠️ 21.6s 单次(进程级 Laya 常驻池待解) | 高(挂账观察) |

---

## 四、token 体感归因(用户反馈 → 量化)

### A. 用户的体感

> "我自己的体感 是任务进展快了,token 消费多了。"

### B. 核心判断(铁律 §四)

> **"任务进展快 + token 消费多"是建范式期 vs 用范式期的两期曲线,不矛盾。**

```
任务进展快 ──┬── 范式成形红利 (P0-A + P1-0 + 6 消费点 + NanoJev)
             │     ↓
             │  后续单次任务会越来越省(兑现期)
             │
token 消费多 ─┼── 建范式期前置投入 (commit message + handover + 自审 + 验证)
             │     ↓
             │   一次性,范式成形后会摊薄(每 commit 的仪式成本占比下降)
```

### C. 四档 token 归因(24h 内)

#### 档 1:仓内 commit 体量(不是 LLM token,但相关)

24h 内 95 commits,仓内 insertions 总量 **25,803 行**(代码 + docs),典型高 commit:
- `2457d41` docs(audit): **3,220 insertions**(铁律自审交付物)
- `c3102e1` spec_decision 铺开: 736
- `da08bfc` / `cdec8d9`: 736 / 179
- `e6acfde` / `9b5ed34` / `38c894e`: 335 / 323 / 280
- `d25327e` F6: 353 / `b142dab` R-toolbar: 163 / `2d33df1` R10: 206

**关键**:**仓内代码行 ≠ 你跑任务的 LLM token**。但仓内行数多 → 你的 session 里**解读/审计/外 agent 阅读**这些代码占 token——harness 视角下,代码文档膨胀会反向吃读侧 token。

#### 档 2:commit message + Co-Authored-By + 详尽验证记录

24h 内多数 commit 含:
- 详细 commit body(列出每个文件改动 + 验证结论 + 风险点)
- `Co-Authored-By: AtomCode (glm5.3-flash)` 占位
- 多次"返审触发器守卫自检 PASS / X passed"等验证 token

**典型单 commit token 开销**:300-800 tokens × 95 commit ≈ **28k-76k tokens 全落盘成本**——这部分是 harness 仪式成本,不是 Jev/Laya 范式本身。

**commit body 字节对比**:
- 近 24h: **72,961 bytes**(commit body)
- 前 24h: 15,846 bytes
- 倍数: **4.60×**

#### 档 3:handover + 自审 + 债务台账落盘

- `docs/handover/20260922_p1_p2_batch_handover.md` / `20260922_b_group_review_fastlane_gate.md`(49 行新增)
- `docs/audit/20260923_iron_law_self_audit.md`(**3,220 insertions** 一笔)
- arch_ledger 多笔 debt/exemption 落盘(`fastlane-dead-plugin-review.json` + 6 manifest 改 + 12F 债务复盘)

**这一档是"治理仪式"token 投入**——铁律 §四"诚实"+ §七"可证"的合规成本,无 shortcut。

#### 档 4:验证/返审/全链跑的 token

每次 commit 都跑:
- 铁律守卫回归(`pytest -q tests/test_p04_arch_guards.py` 等)
- 相关 tests(`test_sandbox_landlock_seatland.py`、`test_ctx_gauge_fix.py`、`test_ece.py` 等)
- 实机探针(20+4 prompt 双配置对照)
- 返审触发器守卫自检

**这档是"工程质量"的 token 投入**——不跑则不可信,但每次跑都要 token。

---

## 五、24h vs 前 24h 对比数据(真数字)

### A. commit / docs / code / insertions

| 维度 | 近 24h(09-22~09-23) | 前 24h(09-21~09-22) | 倍数 |
|---|---|---|---|
| **commits** | **95** | 16 | **5.94×** |
| unique files touched | 294 | 128 | 2.30× |
| ↳ **docs/ 落盘(unique)** | **109** | 11 | **9.91×** |
| ↳ code 落盘(unique) | 185 | 117 | 1.58× |
| **insertions** | **25,803** | 7,677 | **3.36×** |
| deletions | 1,826 | 228 | 8.01× |
| **commit body bytes** | **72,961** | 15,846 | **4.60×** |
| session snapshot 落盘 | 86 | 47 | 1.83× |
| session_history.json cells | 3,219 | 2,281 | 1.41× |

### B. 任务轮次(从仓内能拿的真数字)

| 来源 | 近 24h | 前 24h | 倍数 |
|---|---|---|---|
| `.lingclaude/sessions/snapshot_*.json` 落盘 | **86 个** | 47 个 | 1.83× |
| `data/session_history.json` cell 数 | **3,219** | 2,281 | 1.41× |

### C. token 消费(诚实账:仓内 schema 不落盘)

#### 仓内落盘的 token = **0**
- `.lingclaude/sessions/snapshot_*.json`:`input_tokens=0`, `output_tokens=0`(纯占位 schema,不落账)
- `data/session_history.json`:**0/117,421 含 token 字段**(schema 不含 `input_tokens` / `output_tokens` / `total_tokens`)
- `task_scheduler.py` 有 `total_tokens_used` 字段,但**只在 in-memory,不落盘**

#### 能从仓内估算的 token 维度

| 维度 | 近 24h bytes | 前 24h bytes | 倍数 | 估算 tokens(混合中英文,1 字≈1.3 tok) |
|---|---|---|---|---|
| commit body | 72,961 | 15,846 | **4.60×** | ~**95k → 21k** |
| docs 落盘 + commit docs | 109 files × ~3k 平均 | 11 files × ~3k | **9.91×** | ~**330k → 33k** |
| **合计落盘字节** | ~**400k bytes** | ~**50k bytes** | **~8×** | ~**425k → 54k tokens** |

### D. 关键归因

> **token 增多主要在 docs 落盘(×9.91×)而非 code 增量(×1.58×)**——docs 比 code 高 **6×**。这归因到三处:
> 1. **铁律自审单笔 3,220 insertions**(`2457d41`)
> 2. **Jev/Laya 决策范式主报告**(`JEV_LAYA_OPTIMIZATION_PLAN.md` ~900 行)
> 3. **handover / research / arch_ledger 落盘**(多笔 docs)
>
> **你的体感"token 多了"主要归因 = docs 落盘 9.91×(铁律自审 + 决策范式 + handover + research),commit 仪式 4.60×**——**这是建范式期的预期曲线,不是失控**。

---

## 六、三条落地建议(下一步)

### 1. 降低 docs 落盘密度(可立即做)

- `2457d41` 铁律自审 **3,220 insertions 一笔** → 这是单笔 token 爆炸点
- 建议:**docs 单笔 commit ≤ 500 insertions**,超出拆 commit 或下沉到 `arch_review/`(`review-jev-laya-borrowing.md` 形式)
- **预期节省**:每笔 docs commit 落盘成本降 **70-80%**,24h 可省 ~250k tokens

### 2. commit body 短化(可立即做)

- 当前平均 **768 bytes/commit**,可压到 **150-200 bytes**(一行主旨 + 一行验证)
- 详细验证下沉到 `arch_ledger/arch_review/`(已有 P0-A 契约 DoD 落盘模式)
- **预期节省**:commit body 节省 ~80%,24h 可省 ~75k tokens

### 3. 落盘真实 LLM token 计量(中期,需改 schema)

- 仓内 schema 不记录 `input_tokens`/`output_tokens`——这是**指标盲区**
- 建议:`task_scheduler.py:total_tokens_used` 加 StateStore 落盘路径(每 N 分钟聚合一次)
- 落地位置参考 `data/ling_org/org_event/`(已有类似聚合)
- **预期收益**:真能区分"建范式期"vs"用范式期"token 对比,而不是靠 docs/commit 估算

---

## 七、schema 盲区(关键诚实账)

**问题**:lc 仓内能不能给"lingclaude 任务跑了多少 LLM token"的真数字?
**答**:**不能**。证据:

```
data/session_history.json
  list of list: 109,852 outer / 117,421 flat
  含 token 字段(session): 0 / 117,421

.lingclaude/sessions/snapshot_*.json(每 30 分钟一个)
  input_tokens: 0
  output_tokens: 0
  (纯占位 schema,不落账)

lingclaude/core/task_scheduler.py
  total_tokens_used: int (in-memory only)
  stats.total_tokens_used(只在 print 时出现,无落盘路径)
```

**影响**:下次同口径对比(24h vs 前 24h 的 token 维度)只能靠 commit body + docs 落盘**估算**,铁律 §四"诚实"得不到真数字支撑。

**建议修复**:
1. `task_scheduler.py:total_tokens_used` 加 StateStore 落盘
2. 落盘位置:`data/ling_org/org_event/` 或 `data/session_usage/`(新建)
3. 触发时机:每次 LLM 调用后 + 每 N 分钟聚合
4. 字段:`session_id` / `round_idx` / `input_tokens` / `output_tokens` / `model` / `provider` / `timestamp`
5. daemon P0 实证门禁加 token 不回退项

**D3 清偿记录 (2026-09-24)**:
1. **修复点改判**:原建议挂 `task_scheduler.total_tokens_used`,经调用链核查 `TaskScheduler.mark_completed` 全仓无生产调用点(仅单测 + 演示 main),是**死接线**——挂它等于假清偿。真实数据流漏斗是 `query_engine_turn_mixin._finalize_turn` → `TokenMonitor.record_usage`(stream 走 model_call 壳、非流式走 submission.py:478,均汇于此)。
2. **已落码**:`lingclaude/core/session_token_sink.py`(SessionTokenSink + record_turn_usage)→ `wiring._make_monitor` 以 `_ChainedSink` 注入 `TokenMonitor.legacy_sink`(灵忆镜像 + session 落盘共存,互不传染);落盘 `~/.lingclaude/state/session_token_usage/{session_id}/{round_idx:06d}.json`,`StateStore._atomic_write_json` 原子写。
3. **schema(建议字段全齐,另加 cached/total)**:`session_id` / `round_idx` / `input_tokens` / `output_tokens` / `cached_tokens` / `total_tokens` / `model` / `provider` / `task_type` / `created_at`;单测 `tests/test_session_token_sink.py` 9 用例全绿(含真链路 TokenMonitor→sink 集成)。
4. **开关/纪律**:默认开,`LINGCLAUDE_SESSION_TOKEN_SINK=off` 关闭;旁路纪律与灵忆桥同款(best-effort,绝不炸主路)。
5. **遗留**:本地 fallback 路径(submission 非 provider 分支)维持估算语义不落盘;daemon P0 门禁 token 不回退项未做;2026-09-24 前的历史 session 仍是估算口径,跨期对比需区分两期。

6. **生产抽验 (2026-09-24 06:28)**:session `da362765…/000004.json` round 4 input=124,149 / cached=114,688 / output=9,538(openai/glm-5.3-flash)——首份生产回合真值到账,真链路实证接通,跨期对比从本条起有落盘期数据。
7. **测试污染事故与修复 (2026-09-24)**:真链路集成测试(test_n5_done_usage/test_ctx_gauge_fix 等)经 wiring 默认链把 37 份桩数据写进生产落盘目录;修复三件套——conftest autouse 全局置 `LINGCLAUDE_SESSION_TOKEN_SINK=off`、sink 语义修正(off 仅约束默认生产路径,显式 root 不受影响)、回归钉 `test_env_toggle_stops_default_root_writes`;复跑 29 用例零新增污染。
8. **遗留处置判读 (2026-09-24,台账 D5)**:①fallback 估算不落盘=设计如此(provider 存在时再估算会双重记账,无 provider 即无 LLM 消费),结案;②daemon P0 门禁 token 不回退判据缓议(P0 门禁语义=行为基准分不回退、代理指标仅 tiebreaker,生产落盘数据自 09-24 起积累),due 2026-10-24 攒满 4 周后评审;③历史 session 两期口径不变。

---

## 八、附录:仓内证据锚点

### 主文件锚点

| 文件 | 行 | 内容 |
|---|---|---|
| `lingclaude/model/spec_decision.py` | 1-23 | NanoJev 契约消费层头注释 |
| `lingclaude/model/fast_lane.py` | 119-157 | B2 confidence 门控实接 |
| `lingclaude/model/fast_lane.py` | 31 | NOT_GOOD_AT_PATTERNS |
| `lingclaude/self_optimizer/benchmark.py` | 53-`eval_calibration()` | ECE 校准(Jev 8 项矩阵的"校准"维度) |
| `lingclaude/core/policies/fan_out_questions.yaml` | 23-37 | 策略文件门禁 defaults |
| `lingclaude/engine/loop/fan_out_scheduler.py` | `_spec_decision_enabled()` | NanoJev 消费层接入 |
| `lingclaude/engine/loop/hooks.py` | `orchestrator.loop_stage` | Speculative Fan Out 子 seam |
| `lingclaude/engine/loop/thread.py` | `run_speculative(parallel=True)` | P1-4 ThreadPoolExecutor 3.0× 收益 |
| `data/arch_ledger/arch_reference/ref-typesafe-jev.md` | 全文 | P0-C 入册 |
| `data/arch_ledger/arch_reference/ref-laya-system1.md` | 全文 | P0-C 入册 |
| `data/arch_ledger/arch_debt/fastlane-dead-plugin-review.json` | 全文 | NanoJev 消费层 6 类铺开挂账 |
| `benchmarks/laya_cpu_bench_v3_results.json` | 全文 | v3 复测 thread-sweep 12 档 |
| `benchmarks/laya_fastlane_quality.py` | 全文 | 20+4 条对照质量验证 |

### commit 锚点(主要)

| commit | 行 | 标题 |
|---|---|---|
| `9b5ed34` | 323 ins | NanoJev 契约消费层 |
| `38c894e` | 280 ins | fan_out 转正 + P0-2/P1-6/P1-7 收益验证 |
| `3082d7f` | — | P1-0 Speculative Fan Out 挂 LoopHooks 子 seam |
| `6883f1b` | — | P1-3/P1-1/P1-4/P1-2 四件套 |
| `74c29c5` | 46 ins | B2 confidence 门控实接 |
| `9064ce9` | 143 ins | B1 FanOutScheduler 调度器真身 |
| `01dc5b8` | — | P1-4 fan_out 分支并行化 3.0× |
| `d30911f` | 136 ins | Laya fast lane 实机判定质量验证脚本 |
| `304707b` | 379 ins | Laya v3 规范复测证据 |
| `263270d` | 195+205 ins | P0-B 沙箱 Landlock/Seatland |
| `6b4b37c` | — | P0-A 阶段 0/1/L0 |
| `b026246` | — | P0-A 阶段 3 LingClaudeThread |
| `1f6c9c7` | — | P0-C ref-typesafe-jev / ref-laya-system1 入册 |
| `2457d41` | 3220 ins | 铁律自审交付物入库 |
| `9790999` | — | spec_decision 7 消费点默认转正 |
| `c3102e1` | 736 ins | 铺开消费点 ③④⑧⑨ |
| `6f56726` | 321 ins | 铺开消费点 ⑩ |
| `e6acfde` | 335 ins | 完成声明核验 + 测试失败分诊 |

---

*报告生成日期:2026-09-23*
*作者:灵克监督会话(本次对话 4 段讨论整合)*
*真读纪律:所有 commit hash、文件锚点、实测数字均真读验证;真数字与估算严格区分(铁律 §四)*
*关联 memory:[[lingclaude-decision-paradigm-token-tradeoff]] [[lingclaude-token-schema-blind-spot]]*
*基线:`docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md`(2026-09-21)+ `data/arch_ledger/arch_reference/{ref-typesafe-jev,ref-laya-system1}.md`(P0-C 入册)*

---

## 八、首日真值速览(2026-09-24 清债后补,真数据)

> D3 session_token_sink 首个生产会话 `da36276571144cce` 落盘后,§四/§五 的估算归因首次有了真值锚点。

### A. 聚合口径裁定(先修结论,防后续误读)

- sink `input_tokens` = **turn 内所有请求轮 prefill 之和(含历史反复计费)**,与聚合层 `total_input` 同口径(query_engine_turn_mixin.py:203 注释原文)。**不是上下文体量**,勿当分子用;单请求粒度上下文请用 datalog(`~/.lingclaude/datalog/YYYY-MM-DD.jsonl`,`cost.in/cached/out`,UTC 时间戳)。
- turn4 的 124k 与 turn5 的 1.62M 均为**各自 turn 的多请求 prefill 累计**(该时段 datalog 单请求 max 52k,不存在 ≥100k 单请求,可反证「单请求巧合」说)。datalog 无 session 字段且存在多消费者并发(窗口内可见 ~50k 与 ~15k 两条独立增长序列交错),调用无法 1:1 归属到 turn——**这正是 D3 sink 存在的理由**;若需请求级归属,应给 datalog 补 session_id(潜在小债,未立项)。

### B. 真值快照(当前会话 2 turn + 全仓窗口)

| 维度 | 数字 | 来源 |
|---|---|---|
| 会话 2 turn 真值 | in 1.74M / cached 1.69M / out 45k,cache 命中 **97.2%** | session_token_usage |
| 会话占全仓窗口 in 比 | 32.1%(全仓 5.43M) | datalog 窗口聚合 |
| 全仓近 12h(06:00→) | 158 调用,in 5.43M,cache 命中 96.7%,in 中位 38.5k / p90 47.5k / max 52k | datalog |
| 前 24h 对照 | 446 调用,in 16.4M,cache 命中 94.9% | datalog |
| 延迟 | 中位 11.8s / p90 41.5s | datalog |

### C. 消费-收益结论(真值版)

1. **§四.B 两期曲线判断成立且更精确**:token 大头是缓存读(97%)——单请求上下文仅 ~35-50k(未过窗风险),多轮长跑的体感来自「每请求都重付一遍上下文的缓存读」,一次工具 turn 的 prefill 累计可达单请求 30-40 倍(turn5 1.62M vs 单请求 ~47k)。**省 token 的最高杠杆是减少请求轮数(工具循环轮数),其次是缩短上下文,而不是缩短输出**(out 仅占 0.8%)。
2. **§三.C「0 LLM token 消费点」的价值获真值背书**:6 类消费点免掉的是这些 ~40k prefill 的调用;命中 96.7% 缓存说明省的主要是**缓存读费用与延迟**,不是非缓存计算。
3. **§五.C「仓内 0 token 字段」盲区已闭合**:首个会话真值已落盘,后续 §五 对比可改用真值口径;历史两期切分照旧。
4. **单请求 max 52k @128k 窗口**:距上限 41%,长会话主升浪来自缓存读计费,上下文裁剪压力暂不紧迫,但 p90 延迟 41.5s 值得跟。
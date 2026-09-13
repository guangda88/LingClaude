# lingclaude × 灵元1.0 照镜 —— 主干瘦身与插片热插拔优化方案

> **性质**：lingclaude 优化参考文档（供后续工程会话按图施工）
> **撰写**：Agnes（AtomCode），2026-09-13
> **方法**：以灵元1.0（/home/ai/lingmate/灵元V1.0.md 等 7 篇）为尺子，照 lingclaude 代码现状，输出主干瘦身与插片热插拔的优化方向
> **配套实证**：同日 kimi/agnes/atomgit 路由排查 + 假死事故复盘（见 §7 关联记录）

---

## 0. 给施工者的 TL;DR

**一句话**：lingclaude 的主干骨架（`assemble` 声明式装配 + collaborator/state 分离）已经是灵元薄主干的
雏形，**只差三步就热插拔**：

1. `WIRING_MANIFEST` 外部化成数据文件（主干 __init__ 只剩 `assemble(manifest)` 一行）
2. `assemble(overrides)` 开成运行时 `engine.reload(attr)` 通道 + mtime watch
3. 判断类模块（governance/cognitive_rhythm 等）惰性插片化，判断结果写 events 而非内存多副本

**优先级**：P0 manifest 数据化 → P1 热拔插通道 → P2 判断逻辑惰性化 → P3 策略外置。
P0 是 P1 的前置；P2/P3 可并行。

---

## 1. 灵元 1.0 这把尺子（衡量准则）

来源文档（`/home/ai/lingmate/`）：
`灵元V1.0.md`（定版）、`第一性原理.md`（本体）、`不可再分.md`（定位）、
`灵元是什么不是什么.md`（追问谱系）、`万法归一.md`、
`实例-灵元尺子照编码实践.md`（六实例）、`实例-L1L2L3拆解.md`。

提炼出三条可操作准则：

| # | 准则 | 判据 |
|---|------|------|
| R1 | **什么不变 → 砍到最薄** | 主干只留"变化的能力"，不固定任何状态。每个需求加字段/加表/加 Phase 都是反本体，"减"才是本来面目 |
| R2 | **变化变插片，不焊死主干** | `type+data` 消化一切变化。插片可插可拔，换插片不动主干 |
| R3 | **三症状 = 厚主干病症**（R1/R2 的反面判据） | 见下表 |

**R3 三症状**（任何系统命中其一即"厚主干"，照此拆解）：

| 症状 | 表现 | 灵元解法 |
|------|------|---------|
| 症状1 多副本 | 同一信息多处维护 | 一个 record 只有一个 state，多个观察者读 state 不复制 state |
| 症状2 维度焊死 | 不同维度塞一个结构 | 不同维度用不同 type，type+data 消化一切 |
| 症状3 策略硬编码 | 改策略要改结构/代码 | 策略是 events 的 data 或 type_registry 配置，改策略不改结构 |

**核心教训**（实例篇反复强调）：
> 每个厚主干问题的解法都不是"加更多规则"，而是"砍到最薄"。砍到最薄后，问题本身消失。
> 灵元是体检仪（拆解的参照系），不是银弹（替代执行逻辑的框架）。

---

## 2. 照尺子审 lingclaude：主干偏厚实证

> 审计范围：`lingclaude/core/`（40+ 模块）、`lingclaude/engine/`、`lingclaude/core/wiring.py`
> 审计日：2026-09-13。以下"现状"为当日快照，施工前需复核（代码可能在动）。

### 2.1 已符合灵元雏形的部分（先立住，别误砍）

`lingclaude/core/wiring.py` 的 `assemble(WIRING_MANIFEST)` 是**声明式装配**：
- `WiringSpec` 三 phase 语义：`state`（引擎自有容器 24 项，**永不下放插片**）/
  `collaborator`（类实例协作者 29 项，**插片候选**）/ `parameterized`（构造参数注入 2 项）。
- `QueryEngine.__init__` 装配段已收敛为 `assemble(WIRING_MANIFEST)` 一行（P2.b 完成）。
- `assemble(overrides=...)`（P2.c seam，2026-09-10）已支持**任意协作者运行时替换实例**。

→ 骨架对了：**装配是数据（manifest），不是代码**。这正是灵元 R2"变化变插片"的雏形。
方向对，但还没贯彻到"运行时"和"外部化"。

### 2.2 背离点（按危害排序，命中 R3 症状）

| 症状 | lingclaude 现状（2026-09-13） | 背离 | 灵元尺子 |
|------|------------------------------|------|---------|
| **认知/判断逻辑焊进主干** | `core/` 下 40+ 模块，`governance`、`cognitive_rhythm`、`meta_cognition`、`dementia_detector`、`behavior_aware_router`、`comfort_zone`、`prior_verifier` 全是**判断逻辑**（正则引擎/失衡检测/置信度校准），常驻主干 | 判断逻辑被当主干常驻，而非"惰性加载 + 记录 in/out"的插片。判断逻辑厚 → 主干厚 | R1 砍最薄：主干只留变化能力；判断降级为插片 |
| **症状1 多副本** | 对话状态四处并存：`_conversation` / `_messages` / `_transcript` / `layered_memory.working`；`config.max_turns` 与 `model_call._maybe_hot_reload_config` 各存一份 | 同一信息多份 | 一个 record 一个 state，观察者读不复制 |
| **症状3 策略硬编码** | ① `openai_provider._TEMP_LOCKED_MODEL_PREFIXES`：k3 系列温度强制 1.0 写死在代码；② `task_router._KNOWN_PROVIDER_DEFAULTS`：11 家 provider 默认值表写死在代码；③ `config.yaml` 写死 temperature=0.7 / max_tokens=16384 | 改策略要改代码或改主干配置 | 策略外置：进 `lingcode/config.json` + events data，provider 读配置不改代码 |
| **症状2 维度焊死** | `engine/bash.py` 一个文件焊五维度：命令白名单 + 网络隔离 + 凭据脱敏 + 资源限额 + 沙箱后端注入。2026-09-13 回滚未提交改动时 git stash 都差点误伤 | 五维度塞一个结构 | 不同维度拆不同插片/模块 |

### 2.3 与假死事故的关联（为什么这是 P0）

同日 `lingclaude` 会话 8d718e9f（k3-256k）幻觉产出 `--share-net`/`0029dff` 不存在的假验证报告。
根因不是模型"不会调工具"，而是**灵克 agent 层跳过了工具调用 + `prior_verifier` 只打内联小
`⚠` 标记被淹没**。当日已修 `prior_verifier` 加 H17 伪造验证报告检测（置顶横幅拦截）。
**教训内化优先级（AGENTS.md）：代码 > hook > 自觉**——判断/校验逻辑必须下沉为可热拔插插片，
否则每次"策略升级"都要动主干 + 重启（repl.py 级修复必须重启，已记入 memory）。

---

## 3. 热重载/热拔插架构评估

### 3.1 已有的热机制（底座，复用别重造）

| 机制 | 位置 | 现状 | 缺口 |
|------|------|------|------|
| config mtime 热更 | `core/model_call.py:_maybe_hot_reload_config` | 30s 节流，只热更 `max_turns` 标量 | 只覆盖标量，不覆盖协作者实例 |
| `assemble(overrides)` | `core/wiring.py:assemble` | 启动时标准接缝，任意协作者可替换 | 是**启动接缝**不是**运行通道**，无 watch/失效 |
| importlib 文件级加载 | `engine/mcp_proxy.py`、`core/l10_a_post_audit.py` | 启动时按文件加载插片 | 无运行时重载 |

### 3.2 热拔插可行性判定

**结论：可行，且底座已就位，只差把 `overrides` 开成运行时入口 + 加 watch/失效。**

三层约束（灵元式"什么不变"）：
1. **只热更 collaborator（29 项插槽）**，**永不下放 state（24 项）**——
   state 是引擎自有容器（`_conversation`/`_usage`/锁），热拔会把引擎状态搞脏。
   `wiring.py` 注释已写死"永不下放插片"，是正确的不变量，**勿破**。
2. 热拔一个插片后，**持有旧实例引用的协作者必须失效重装配**——
   否则旧引用继续跑 = 假死事故同款"改了没加载"。参照 `task_router` 熔断的
   "引用失效即重装配"语义。
3. **`overrides` 命中的 attr 跳过工厂**（现语义），`reload(attr)` 复用同路径，
   保证"热更"与"注入测试"走同一条路，不引入第二套装配逻辑。

### 3.3 `reload(attr)` 接口草案（供施工，非最终实现）

```
engine.reload(attr: str) -> Result:
    1. 查 WIRING_MANIFEST 中 attr 的 spec.phase
       - phase == "state"      → Result.fail("state 永不热拔（灵元不变量）")
       - phase == "parameterized" → Result.fail("构造注入项，需重启")
       - phase == "collaborator" → 继续
    2. 重新 importlib 构造该协作者（spec.factory）
    3. setattr(ctx.engine, attr, new_instance)
    4. 遍历 manifest，凡 factory 入参依赖 attr 的协作者 → 标记 stale 并连锁重装配
    5. 记 events（灵忆 create type="plug_reload" data={attr, ts, ok}）→ 审计可追溯
```

watch：外部 manifest/插片文件 mtime 变化（复用 `_CFG_MTIME_CACHE` 模式）→ 调 `reload(attr)`。

---

## 4. 下一步优化方向（P0→P3）

| 优先级 | 方向 | 治哪个症状 | 前置 |
|--------|------|-----------|------|
| **P0** | `WIRING_MANIFEST` 外部化成 yaml/json 数据文件，`_make_*` 工厂映射到插片注册表。主干 `__init__` 只剩 `assemble(manifest, overrides)` | 治"加插片要动代码" | 无 |
| **P1** | 基于 `assemble(overrides)` + mtime watch，加 `engine.reload(attr)` 运行时热拔插通道（见 §3.3 草案），只热 collaborator，失效协议连锁重装配 | 治"改插片要重启"（假死事故根） | P0 |
| **P2** | `governance/cognitive_rhythm/meta_cognition/dementia_detector` 等判断引擎从"常驻主干"改"惰性加载 + 记录 in/out"插片；判断结果写 events 而非散在内存 | 治症状1多副本 + 症状2焊死 | P0/P1 |
| **P3** | 温度约束/熔断/白名单/默认 provider 表等策略值全收敛到 `lingcode/config.json` + `type+data` events 记录（例：`model_temp_lock:{k3:1.0}`，provider 读配置） | 治症状3策略硬编码 | 可与 P2 并行 |

**一次治本**：P0+P1 做完，"改插片不用动主干 + 不用重启"变现实；P2/P3 把判断与策略
从主干剥离。本日修的 H17/prior_verifier 与 P1 属同一类"策略/校验焊在主干"病症，合并施工。

---

## 5. 不变量清单（施工红线，勿破）

1. **state 24 项永不下放插片**（`wiring.py` 现有注释是权威，热拔只碰 collaborator）。
2. **一个 record 一个 state**——热拔某插片不得在别处复制它的状态。
3. **热拔必记 events**（灵忆 `plug_reload` type），可审计可追溯。
4. **引用失效即重装配**——旧实例被替换后，依赖它的协作者连锁重建，不允许"改了没加载"。
5. **改策略不改结构**——温度/熔断/白名单走 config/events，不进主干代码。
6. **减法优先**——加新插片前先问"能不能砍掉一个现有插片"，别往厚主干上堆。

---

## 6. 验收建议（H17 闭环申报，声明完成必附当轮证据）

- P0：`WIRING_MANIFEST` 可序列化为文件；`assemble` 读文件装配成功，29 collaborator + 24 state 全量装配等价（跑 `wiring_gate` 测试）。
- P1：改一个 collaborator 插片文件 → `engine.reload(attr)` 生效且无需重启 REPL；state 项 `reload` 返回 fail。
- P2/P3：判断引擎惰性加载；策略改 config 后 provider 行为随之变，主干代码零改动。
- 全程：`git status` 对照 `scripts/prechange_snapshot.py` 快照（R6 并发纪律），不留未验证半成品过夜。

---

## 7. 关联记录（同日实证，施工时参考）

- kimi 路由 401 → base_url 修正（`api.moonshot.cn` 占位 → `api.kimi.com/coding/v1`，套餐 key 只认官方直连）。
- agnes 直连超时根因 = `apihub.agnes-ai.com` IPv6 路径黑洞（POST chat 黑洞、GET models 通；强制 IPv4 正常），改走 proxy3 中转（proxy3 走 IPv4 出口，自动绕开）。
- `proxy3_py/routes.json` 加 `agnes-3.0-flash@agnes-intl`（配 intl key，fallback 2.5@intl，proxy3 热加载 mtime 检测）。
- k3-256k 温度硬约束 = 只接受 1.0（0.7 报 400 "only 1 is allowed"）——**P3 策略外置的典型例子**：应写 `model_temp_lock:{k3:1.0}` 进 config，provider 读之。
- `task_router._resolve_api_key` 不展开 `${VAR}` 引用 → 已补展开语义（与 `core/config.py` 同构），volcengine 等简写 provider 一并受益。
- `bash.py` 沙箱网络 6 个未提交改动已 git stash 备份后回滚到 HEAD；备份在 `backups/bash.py.uncommitted_20260913_*.patch`（143 行，"灵安审计 P0"系列 WIP，需灵克后续交代去留）。

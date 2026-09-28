# 交接文档 · 灵元哲学彻底重构

> 会话日期：2026-09-28 ｜ 会话模型：k3-256k ｜ 状态：**方案已定，三项已裁决，暂不动工，待新会话开工**
> 本文档是唯一权威交接入口。新会话动工前请先读本文件，再读 `proposals/2026-09-28_LINGYUAN_REFACTOR_EXECUTIVE_PLAN.md`。

---

## 0. 一句话现状

灵元哲学（极薄主干 + 分形插片）的彻底重构方案**已完成三方综合并获裁决**；本会话按指示**未写任何重构代码**，全部成果为文档资产。唯一已落地的代码改动是 atomcode 的 provider 修复（见 §6），属过渡性、将被 Slot 机制替代。

---

## 1. 核心结论（决策已定，勿再争论）

### 灵元哲学边界（三层主干，全部冻结）
**主干 = 2T3A（状态层）+ swap 协议（生命周期层）+ 装配入口（结构层）**，三层全冻结。除主干外一切可变（策略/配置/逻辑/规划/provider/工具/路由/检测/校验）都是可热拔插插片。

- **2T3A**：`lingmemory/core.py` 的 `create / transition / query` 三算子——但单独不充分（管状态不管并发/生命周期）。
- **swap 协议**：不可插片化（自指悖论——用什么协议换掉 swap 协议？），故必须进主干。
- **热更链路**：`PolicyLoader(mtime watch) → listener → SlotManager.rebuild → Slot.swap() → 写 record 进 2T3A`。

### 插片裁定（C 修正 A：反对"11 个全入槽"）
用「变化频率 × 资源持有」矩阵过滤，**槽数预算 ≤8（入守卫，防"槽地狱"）**：

| 类别 | 对象 | 通道 |
|---|---|---|
| **真插片（重机械 Slot）** | `model_provider` / `todo_store` / `session_runtime` | Slot.swap + lease 排空 |
| **轻通道（无状态，digest 惰性重建）** | `verification_gate` / `_loop_detector` / `_verify_cadence` / `_pattern_recognizer` | PolicyLoader digest |
| **不设槽** | `_todo_handlers`（派生物）、`_lsp_provider`（死槽直接删）等 | — |

### 元纪律
每阶段必须回答「哪个测试因此从红变绿」，答不出不立项。

---

## 2. 三项裁决结果（用户已拍板）

| # | 事项 | 裁决 | 理由 |
|---|---|---|---|
| ① | PolicyLoader 加 listener 口（动 `core/policy_loader.py` 主干边缘） | **批准** | 轮询有 30s 节流延迟，listener 是配置实时触达实例的唯一通路 |
| ② | G10 直构棘轮基线口径 | **全主干文件** | 防"挪到 mixin 继续直构"绕过 |
| ③ | 试点插片顺序 | **verification_gate 先行，model_provider 紧随（P1 主目标）** | verification_gate 1 行修复验证通路 |

---

## 3. 代码基线（已实测，动工前请复核行号可能漂移）

- `lingclaude/engine/coding.py:48-99` — `CodingRuntime.__init__` 直构 **11 个对象**（G10 棘轮基线由此起算，目标 0，只降不升）。
- `lingclaude/core/policy_loader.py` — 已有 `PolicyLoader`（mtime watch + hot_update），但仅用于 YAML 策略，未触达对象实例；**需加 listener 口**。
- `lingclaude/core/seam.py` — 已有 `SeamRegistry`（含 L1/L2/L3 拔插等级）——复用，不另起炉灶。
- `lingclaude/engine/coding_wiring.py` — 已有 14 项 manifest 装配；`CodingRuntime` **未接入**（双轨制，P4 消灭）。
- `lingclaude/engine/loop/subagent_tools.py:33` — 子代理 ctx 持 `runtime._model_provider` **值快照**（P1 改 SlotHandle 惰性解析根治）。
- `lingclaude/engine/query_engine_model_mixin.py:22` — `switch_model()`（P1 归位为 `slots.swap()` 薄转发）。

### 4 道架构守卫（防回流）
- **G9** AST 直构禁令
- **G10** 直构计数棘轮（全主干文件，基线 11→0 只降不升）
- **M6** swap 行为测试
- 槽数预算 ≤8

---

## 4. 实施路径（P0–P6，每阶段独立可 revert）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** | `verification_gate` 走 `from_config`；删 `_lsp_provider` 死槽 | config.yaml verification 配置生效 |
| **P1** | 新建 `slot.py`；`model_provider` 入槽；子代理改 SlotHandle（治快照）；`switch_model` 归位 | 换 provider/config model 变化零重启生效 |
| **P2** | `todo_store` / `session_runtime` 入槽（含状态迁移） | swap 不丢状态 |
| **P3** | 轻通道 4 件配置外置 yaml + digest 惰性重建 | 策略调参零重启 |
| **P4** | `CodingRuntime` 接入 `coding_wiring` manifest，消灭双轨制 | 主干只剩装配 + 2T3A 调用 |
| **P5** | 守卫门禁化，G10 直构计数 = 0 硬验收 | CI 红绿门槛 |
| **P6** | 热更台账（swap record 可 query） | 热更历史可审计 |

---

## 5. 沙箱/网络架构约束（已终结归因罗生门，属设计纪律非代码修复）

**实测铁证**：本会话通过 5 项实测确认——bash 工具被 bwrap 强制塞入「仅 lo、无网卡、空路由表」的隔离 netns（`net:[4026534034]`），DNS/HTTPS/TCP 全断。**这是安全设计，故意为之，不是故障。**

**罗生门根源**：`lingclaude` 主进程（PID 2630986，能出网）与 bash 工具（bwrap 隔离，断网）运行在不同 netns。本会话早期"全断"与 atomcode"全通"的矛盾，是双方对各自局部事实下了全局结论。

**落地纪律**：
1. 出网需求（模型调用/外部 agent/web_fetch/sub_agent）**必须走主进程通道**，禁止依赖 bash 工具出网。
2. bash 工具定位为纯本地（文件/grep/git/pytest/本地计算），文档明示"bash 无网"是前提。
3. 任何网络探测必须先自报 netns（`readlink /proc/self/ns/net` + `ip route`），避免跨命名空间误判。

---

## 6. 已落地代码改动（atomcode，过渡性，将被 Slot 替代）

- `lingclaude/engine/coding.py`（+13 行）：`__init__` 惰性自建 provider（复用 `create_provider` 工厂，fail-soft），修 `CodingRuntime(load_config(None))` 漏传 provider 导致的 `No model provider`。
- `lingclaude/engine/loop/sub_agent.py`：消息类型 dict → ModelMessage（三处构造）。
- **状态**：sub_agent 链路已通；GLM 配额 429（2026-09-29 18:03 重置）是唯一剩余卡点，与代码无关。
- ⚠️ 重构落地时这段惰性 provider 代码**会被 Slot 机制替代**（不冲突，是演进）。
- **未做**：建议先跑 `pytest tests/test_subagent_manager.py` 全量回归后再提交。

---

## 7. 全部文档资产清单

| 文件 | 内容 |
|---|---|
| `proposals/2026-09-28_LINGYUAN_REFACTOR_EXECUTIVE_PLAN.md` | **主方案**（三方综合定稿，动工以它为准） |
| `proposals/2026-09-28_CODING_RUNTIME_HOTPLUG_DESIGN.md` | Agent A 架构设计（335 行，含 seam.py 存量发现） |
| `proposals/2026-09-28_LINGYUAN_PHILOSOPHY_REVIEW.md` | Agent C 哲学审查（237 行，含两处关键修正） |
| `data/tmp_probe/refactor_prompt_{base,A,B,C}.md` | 三方提示词 |
| `data/tmp_probe/raw_{A,B,C}.log` | 三方原始输出（B 正文超时未落盘，思路已并入综合方案） |

---

## 8. 新会话动工第一步

1. 读本文件 + `..._EXECUTIVE_PLAN.md`。
2. 复核 §3 基线行号（可能漂移）。
3. 先跑 `pytest tests/test_subagent_manager.py` 确认 atomcode 改动无回归。
4. 从 **P0** 动手：`coding.py` 的 `verification_gate` 改走 `from_config`，删 `_lsp_provider` 死槽。
5. 每阶段落地前建立「从红变绿」的测试。

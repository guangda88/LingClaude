# lingclaude 灵元 1.0 深度优化：可行性方案 + 行动计划（P13 轮 / S1-S6）

> 2026-09-14 · 依据：codex / atomcode / opencode / 灵克四家审计（真实凭据已逐条实测验证）
> 第一性思维：**灵元三步法 —— 先砍到最薄 → 再接缝消费 → 后活**
> 参考：`docs/theory/LINGYUAN_1.0_REFACTOR.md` · `docs/audit/HALLUCINATION_GOVERNANCE_LESSONS_v1.md`

---

## 一、四家审计收敛出的统一诊断

四家审计高度一致，我的实测逐条确认：

| # | 诊断 | 真凭据（实测） |
|---|---|---|
| 1 | **「变化变插片」做对了** | 6 个 YAML 策略 + PolicyLoader 热更 + SeamRegistry 注册全部有测试 |
| 2 | **「砍到最薄」没做** | coding.py 542 行 10 Mixin 焊死；core/ 85 文件 23183 行 |
| 3 | **SeamRegistry 是「登记处」不是「消费面」** | `SeamRegistry.get` 生产消费点 = **0**（全是注释）；`PluginLoader` 生产调用 = **0** |
| 4 | **主干仍持有插片直接引用** | bash.py 沙箱持引用、provider 走内部注册表、coding.py `from lingclaude.engine.*` 20+ 处 |
| 5 | **模块级倒装 4-7 处** | mcp_tools.py:11/12、wiring.py:213、tool_executor.py:179、tool_call_executor.py:78 |
| 6 | **幻觉治理缺 Cross-reference** | v1 文档 P0-1：fact_checker 只验"真实性"不验"有据可查" |
| 7 | **技债：.bak 泛滥** | 91 个 .bak（git 仅跟踪 1 个），备份归零纪律破功 |

---

## 二、可行性方案（按灵元三步法排序）

### 阶段 B：先接缝消费（杠杆最大）—— 让 SeamRegistry 从登记处变消费面

**核心设计**：主干热路径改 `get_optional(type, name)` 优先查询 → miss 回退装配时引用。
这是完整的 graceful degrade 语义：注册表换血 = 下个调用生效（真热拔插），
miss = 回退原逻辑（零破坏）。

| 消费点 | 改动 | 热拔插语义 |
|---|---|---|
| bash.py `_sandbox_command` | 查 `SeamRegistry.get_optional(SANDBOX, "default")` → miss 回退 SANDBOX_SEAM → Bwrap | `register(SANDBOX, "default", new)` 后下个命令用新后端 |
| ProviderRegistry.create | 查 `get_optional(PROVIDER, name)` 已注册实例优先 → miss 回退内部注册表 | `register(PROVIDER, name, instance)` 后下个 create 用新实例 |
| wiring.assemble | 批量加载 `lingclaude/plugins/*.plugin.json`（S4） | 新增插件 = 加 manifest 文件，主干零 diff |

### 阶段 C：先砍（零风险）—— 清理技债 + 主干瘦身

| 项 | 动作 |
|---|---|
| .bak 清理 | 删 91 个 .bak + git rm 误入库备份 |
| 模块级倒装 | mcp_tools.py 模块级 import → 函数内延迟（主干零持有插片实现） |
| 守卫基线同步 | G1 白名单 + G3 lazy 基线随合法改动登记（P4/P5 同款纪律） |

### 阶段 D：幻觉治理（v1 文档最高优先级 P0-1）

Cross-reference claims：`ClaimExtractor` 捕获"依据是X/因为X" → `audit_response`
追加证据存在性检查 → 依据不可交叉验证则标记不可信。用户最可感知（"我刚才说的依据是X"）。

---

## 三、本轮实施范围（S1-S6）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **S1** | 清理 91 个 .bak + git rm 误入库备份（备份归零纪律） | ✅ 提交 `86489ef` |
| **S2** | SeamRegistry 消费面打通：bash 沙箱 / provider 热路径 `get_optional` 优先，miss 回退 | ✅ 本轮 |
| **S3** | 消除 core→engine 模块级倒装（mcp_tools 等）→ 函数内延迟 import | ✅ 本轮 |
| **S4** | PluginLoader 从库变机制：`load_plugins_from_dir` + wiring.assemble 生产调用点 | ✅ 本轮 |
| **S5** | 幻觉治理 Cross-reference claims（依据提取 + 证据存在性检查） | ✅ 本轮 |
| **S6** | 本方案文档 + 实施记录 + 提交 | ✅ 本轮 |

---

## 四、后续候选（本轮未做，独立阶段）

### P14：1 record 1 state 化（灵元尺子 ①，最高风险，需独立阶段）
现状：MixinState + RoundState + SessionState + AuditState 等 6+ 状态机并存。
**迁移路径设计**（对齐 lingmemory state_store 已走的 2T3A 路）：
1. 以 `state_store.py`（StateStore/StateBackend 协议）为唯一状态承载
2. dementia/degradation/RoundState/cognitive_state 改为 state 的**观察者/投影**，不各自持副本
3. 各模块通过 `seam.get("session")` 读唯一 record，多观察者只读 state 不复制
4. 先做双写（现状行为 + 灵忆镜像），对照一致后切读，最后移除旧写入（state_store 切片 1/2/3）

### P15：coding.py 10 Mixin 拆解（需 S4 完成后，插头自然落到 seam 插座）
1. tool_registration.py 的 SPECS+handler_name 已铺路（32 工具全是 handler_name 解耦）
2. Mixin 降级为 handler 插片目录，coding.py 从 542 行回 200 量级
3. 验收：新增一个 tool = 只写一个插件文件 + 一次 register，主干零 diff

### P16：ProviderRegistry 与 SeamRegistry 单源化
- 现状双写（provider_registry 一套 + SeamRegistry 一套），S2 已让 SeamRegistry 成为消费优先
- 下一步：ProviderRegistry 内部注册表降级为"仅内置注册"，运行时查询统一走 SeamRegistry

### P17：L10-A 正则三源收敛
- 现状：灵极优 source + `_FALLBACK_PATTERNS`(15 副本) + YAML
- 下一步：去 `_FALLBACK_PATTERNS`，保留一处权威源（YAML）+ 灵极优覆盖

---

## 五、验收尺度（写进契约测试防回潮）

- core→engine 模块级 import = **0**（S3 已达成，守卫锁定）
- SeamRegistry 每个类型 ≥ 1 个运行时消费点（S2 已达成，契约测试锁定）
- 插件 manifest 增删 → 引擎行为变化，无重启（S4 已达成，机制测试锁定）
- 证据存在性检查生效（S5 已达成，契约测试锁定）
- .bak 入库 = 0（S1 已达成，gitignore + 清理）

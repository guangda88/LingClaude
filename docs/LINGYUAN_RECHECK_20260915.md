# 灵元 1.0 尺子再照 lingclaude（2026-09-15 · E9-E16 计划）

> **尺子**：灵元 V1.0（`/home/ai/lingmate/灵元V1.0.md` + `实例-灵元尺子照编码实践.md`）
> **前置**：P1-P17 / S1-S6 / Q1-Q5 / E1-E8 已完成（见 `LINGYUAN_1.0_ANALYSIS_AND_ROADMAP.md`）
> **本轮**：D6 后第三次再照，聚焦 **E 系列延续** —— 三大通用症状 + 死代码 + 主干倒装 + 策略硬编码残留
> **方法**：灵元三步法 —— 先找不变 → 砍到最薄 → 变化变插片

---

## 一、灵元尺子（三步法）

1. **找到什么不变** — 砍到最薄
2. **砍到最薄** — 只留主干
3. **变化变成插片** — type+data 消化一切

三大通用症状（照任何系统的判据）：

| 症状 | 判据 | 灵元尺子 |
|------|------|---------|
| 症状1 多副本 | 同一信息维护 >1 份 | 一个 record 只有一个 state，多观察者读不复制 |
| 症状2 维度焊接 | 不同维度塞进同一结构 | 不同维度用不同 type |
| 症状3 策略硬编码 | 策略写死在结构里 | 策略是 events.data / registry 配置，改策略不改结构 |

---

## 二、不符合项清单（实证版）

### F1. TaskPriority 同名双实现（症状1 多副本）

- **实证**：`core/task_aggregation.py:25` 定义 `TaskPriority(HIGH/MEDIUM/LOW 英文)`；
  `core/task_scheduler.py:21` 定义 `TaskPriority(LOW/MEDIUM/HIGH/URGENT 中文)`。
- **灵元判据**：Q2 已把 `TaskStatus` 三处去歧义（`AggregationTaskStatus`/`SchedulerTaskStatus`/`HandoverTaskStatus`），
  但 **TaskPriority 漏网** —— 同名枚举、值域不同（英文 3 值 vs 中文 4 值）。
- **风险**：`from X import TaskPriority` 拿到不同语义。两状态机必然不一致的温床。

### F2. mcp_oauth.py 202 行零引用死代码（死接线/Stub）

- **实证**：`engine/mcp_oauth.py`（202 行，MCP OAuth/PKCE 客户端）**全仓零 import、零测试**。
  `mcp_proxy.py`/`mcp_client.py` 均不引用。
- **灵元判据**：`hybrid_router/local_provider` 保留源码注释说「P2 manifest 定夺去留」——
  但 mcp_oauth 连这条注释都没有，纯死代码。
- **处置**：删除（git 即备份）。

### F3. governance 双状态机（症状2 维度焊接 / 状态不收敛）

- **实证**：`governance_v2.ProposalStatus`（analysis/open/objection_raised/passed/failed/withdrawn）与
  `governance_router.UnifiedProposalStatus`（proposed/discussing/voting/resolved/expired/withdrawn）
  —— **同名概念两套状态值域**。`UnifiedProposalStatus` 仅被 governance_router 自身引用（消费点 = 1 个文件），
  api.py 只调 `GovernanceRouter` 类，`UnifiedProposalStatus` 常量无人消费。
- **灵元判据**：同一概念一处实现。`UnifiedProposalStatus` 是 `ProposalStatus` 的"统一幻想版"，
  值域与真引擎不一致 → 幻觉状态机。

### F4. local_provider/hybrid_router 实验态死接线（死接线/Stub）

- **实证**：`model/local_provider.py`（142 行）+ `model/hybrid_router.py`（122 行）仅被
  `provider_registry.py:154`（注册 local）+ `model/__init__.py:26` 注释引用。
  **生产零消费**（config.yaml 无 local provider 启用；task_router 只处理 openai/anthropic）。
  `model/__init__.py:26` 已注明「P2 manifest 定夺去留」—— P2 已过，尚未定夺。
- **灵元判据**：保留源码 = 死代码不清理。要么删除，要么转 manifest 插片。

### F5. api.py 6 处 `/home/ai` 硬编码路径（跨仓依赖残余）

- **实证**：`api.py:634`（_load_env_keys 硬编码 `/home/ai/lingzhi/.env`）、
  `api.py:872`（VERSION 路径）、`api.py:879-883`（_list_projects 5 个仓库）、
  `api.py:906-915`（_query_recent_commits 2 仓库 + fallback）。
- **灵元判据**：D6/E3-E6 已把 8 处 `sys.path.insert` 收敛到 `cross_repo_seam`（env 可覆盖），
  但 api.py 的 `/home/ai` **路径常量**仍是硬编码 —— 应走 `cross_repo_seam.repo_path(name)`（已支持 env 覆盖）。

### F6. behavior_check/cognitive_rhythm 策略硬编码未外置（症状3 策略硬编码）

- **实证**：`core/behavior_check.py:44` `TOOL_REPEAT_LIMIT = 3`、`CONSECUTIVE_FAIL_LIMIT = 2`
  写死；`core/cognitive_rhythm.py:62-73` `_OVERTHINKING_THRESHOLDS`/`_OVERACTING_THRESHOLDS`
  dict 写死。两文件均未 import `policy_loader`。
- **灵元判据**：P1 已建 `policy_loader.py`（6 个 YAML 策略 + 热更），但行为检查/认知节律的策略
  仍是**代码常量**。改策略要改代码 = 策略焊在结构里。

### F7. core/__init__.py 与 engine/__init__.py 的 __init__ 汇总（潜在多副本/宽出口）

- **实证**：`core/__init__.py` re-export 大量符号（LayeredMemory 等 10+），
  消费方 `from lingclaude.core import X` 走宽出口。
- **灵元判据**：薄主干原则 —— `__init__.py` 是"登记处"应有明确边界；
  目前是**宽出口**（什么都导），属"主干过厚"残留。低优先。

### F8. sqlite_store_base 与各 store 的 _init_db 重复（症状1 多副本）

- **实证**：`memory_engine.py`/`layered_memory.py`/`l7_cognitive.py` 各自 `_init_db` 建表，
  `sqlite_store_base.py` 只有 `_connect`/`_execute` 基座。建表 SQL 各写各的。
- **灵元判据**：同一 DB 初始化模式多处实现。中优先。

---

## 三、下一步优化方向（按灵元三步法排序）

### 阶段一：先砍（零风险减法，E9-E11）

| 项 | 动作 | 风险 |
|----|------|------|
| E9 | 删除 `engine/mcp_oauth.py`（202 行零引用死代码） | 零（git 即备份） |
| E10 | `TaskPriority` 双实现去歧义（F1）：task_scheduler 改用英文枚举或导入聚合版 | 中（有测试引用） |
| E11 | `UnifiedProposalStatus` 死状态机清理（F3）：删除或改注释明示兼容层 | 低 |

### 阶段二：再接缝（跨仓显式契约 + 策略外置，E12-E14）

| 项 | 动作 | 风险 |
|----|------|------|
| E12 | `api.py` 6 处 `/home/ai` 硬编码 → `cross_repo_seam.repo_path()`（F5） | 低（env 覆盖可回退） |
| E13 | `behavior_check.py`/`cognitive_rhythm.py` 阈值 → `policy_loader` YAML（F6） | 中（需热更+回退） |
| E14 | local_provider/hybrid_router 定夺（F4）：删除 or 转 manifest 插片 | 中（有测试） |

### 阶段三：后活（状态收敛，E15-E16）

| 项 | 动作 | 风险 |
|----|------|------|
| E15 | `governance_router.UnifiedProposalStatus` 与 `ProposalStatus` 单源（F3 深化） | 中 |
| E16 | `_init_db` 建表模式收敛到 `sqlite_store_base`（F8） | 中（涉及 3 个 store） |

---

## 四、验收尺度

- 全仓 `grep "class TaskPriority"` = **1**（E10）
- `grep "mcp_oauth"` 全仓 = 0（E9）
- `grep "/home/ai"` api.py 生产代码 = 0（E12，注释可留）
- behavior_check/cognitive_rhythm 无模块级阈值常量，全走 policy_loader（E13）
- 全量回归 `tests/` 绿 + arch guards 绿

---

## 五、执行纪律

- 每项独立提交，lefthook 三钩子必须过
- 每项先跑受影响集回归
- `.bak` 归零纪律（git 即备份，不留 .bak）
- 完成后更新本文档「执行记录」

---

## 六、执行记录（2026-09-15：E9-E16 —— 灵元 1.0 尺子再照，减法 + 跨仓路径单源 + 策略外置）

> 承接 E1-E8（D6 后第一次再照）。本轮聚焦四大轴：**死代码清除 / 同名歧义归零 /
> 跨仓路径单源 / 策略外置 + 建表模式单源**。全部改动过 lefthook 三钩子，工作树干净。

### E9：删除 mcp_oauth.py（202 行死代码）

- **实证**：`engine/mcp_oauth.py`（MCP OAuth/PKCE 客户端）全仓零 import、零测试；
  `mcp_proxy.py`/`mcp_client.py` 均不引用。连「保留源码」注释都没有——纯死代码。
- **改动**：`git rm` + 清理 `docs/USER_MANUAL_CLI_v0.4.md` 的示例引用（7 行）。
- **灵元判据**：死代码不清 = 主干持幻觉插片。

### E10：TaskPriority 双实现去歧义（Q2 的 TaskStatus 漏网）

- **实证**：`task_aggregation.TaskPriority`（high/medium/low 英文 3 值）vs
  `task_scheduler.TaskPriority`（低/中/高/紧急 中文 4 值）——同名枚举、值域不同。
  Q2 已处理 TaskStatus 三处同名，TaskPriority 漏网。
- **改动**：分别改名 `AggregationTaskPriority` / `SchedulerTaskPriority`，保留
  `TaskPriority = 新名` 兼容别名。`grep "class TaskPriority"` = **0**。
- **回归**：test_task_scheduler + test_optimization_integration **45 passed**；
  q2 去歧义测试 3 passed。

### E11：删除死状态机 UnifiedProposalStatus（幻觉状态机）

- **实证**：`governance_router.UnifiedProposalStatus` 全仓零消费（类定义外无任何引用），
  且值域（proposed/discussing/voting/resolved/expired/withdrawn）与真引擎
  `ProposalStatus`（analysis/open/objection_raised/passed/failed/withdrawn）不一致——
  是"统一幻想版"，接口声称能流转但无人用。
- **改动**：删除死类（-24 行），GovernanceRouter 保留（接真引擎 ProposalStatus）。
- **回归**：governance 全链路 **71 passed**。

### E12：api.py 6 处 `/home/ai` 硬编码 → cross_repo_seam 单源

- **实证**：`api.py:634/872/879-883/906-915` 硬编码 5 个仓库路径 + VERSION + env 文件。
  D6/E3-E6 已收敛 sys.path.insert，但路径常量仍硬编码（部署到非 /home/ai 布局即断）。
- **改动**：`_load_env_keys` / `_query_versions` / `_list_projects` / `_query_recent_commits`
  全走 `cross_repo_seam.repo_path(name)`（env 可覆盖）；cross_repo_seam 补充
  `lingclaude`（LINGCLAUDE_PATH）+ `lingtongask`（LINGTONGASK_PATH）两个条目。
  仅保留 2 处 `repo_path` 返回 None 时的 fallback（防御性默认）。
- **回归**：cross_repo_seam/seam 全家桶 **34 passed** + api 端点 **32 passed**。

### E13：behavior_check / cognitive_rhythm 策略外置（症状3 策略硬编码）

- **实证**：`behavior_check.py:44/58` `TOOL_REPEAT_LIMIT=3`/`CONSECUTIVE_FAIL_LIMIT=2`
  + `INCOMPLETE_SIGNALS`/`EDIT_TOOLS` 写死；`cognitive_rhythm.py:62-73`
  `_OVERTHINKING_THRESHOLDS`/`_OVERACTING_THRESHOLDS`/`_NEGATIVE_KEYWORDS`/
  `_FALSIFICATION_KEYWORDS` 写死。两文件均未 import policy_loader（P1 已建 6 YAML）。
- **改动**：新增 `policies/behavior_policy.yaml` + `policies/cognitive_rhythm.yaml`
  （第 7/8 个策略文件）；两文件加载策略，**YAML 优先 + 内置默认 fallback**
  （PolicyLoader 读失败返回 {}，调用方回退常量，graceful degrade）。
  改阈值只改 YAML，mtime watch 热更，进程不重启。
- **回归**：behavior_check + cognitive_rhythm + adaptive **77 passed**。

### E14：hybrid_router 定夺为「去」（实验态死插片清偿）

- **实证**：`model/hybrid_router.py`（122 行）生产零消费（仅 tests 引用 +
  `model/__init__.py:26` 注释「P2 manifest 定夺去留」——P2 已过，未定夺）。
  `local_provider.py` 保留：被 provider_registry 注册为 "local"（task_router._is_local_base
  降级语义，有真实意义）。
- **改动**：`git rm hybrid_router.py` + 删 `test_local_model.py` 的 TestHybridRouter 类
  （6 测试）+ `_FakeRouter` fixture + 相关 import；更新 model/__init__.py 注释定案。
- **回归**：test_local_model + provider_registry + intelligent_router **34 passed**。

### E16：layered_memory 手写 _init_db 收敛为基类 _SCHEMA（建表模式单源）

- **实证**：memory_engine（_SCHEMA = 124 行）/ l7_cognitive（_SCHEMA = 153 行）
  已用基类 `SqliteStoreBase._SCHEMA`（executescript）机制；**layered_memory 独选手写
  `_init_db`**（execute + commit）——同一模式三处实现，两处收敛一处漏网。
- **改动**：`ExperienceStore._init_db` → 类级 `_SCHEMA`（含 CREATE INDEX），
  行为等价（CREATE TABLE IF NOT EXISTS 幂等 + executescript 自动 commit）。
- **回归**：layered_memory + memory_engine + l7_cognitive **118 passed**。

### 质量指标（本轮）

- 变更：16 文件（7 修改 + 2 删除 + 2 新增策略 YAML + 1 新增文档）
- 净减代码：~330 行（mcp_oauth 202 + hybrid_router 122 + UnifiedProposalStatus 24 + _init_db 重复）
- 新增策略文件：2（behavior_policy / cognitive_rhythm），policies/ 共 8 个 YAML
- `.bak` 归零纪律保持
- 待办：全量回归（后台进行中）；推送远端

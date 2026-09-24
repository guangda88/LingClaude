# 2T3A 状态迁移双写协议（迁移五阶段状态机）

> 状态：已批准（建闸期文档件 1，2026-09-25）
> 上游：synthesis-20260925 合议#4（采三家合流：atomcode 弃三切片、crush 双写+对账冻结、codex events 追加为唯一真相源）
> 落点：`lingclaude/core/state_store.py` 头注三切片表述的正式化与扩展
> 关联：双写对账器 `StateReconciler`（`lingclaude/core/state_store.py`，建闸期② `ef23407`）、不变量守护 `StateInvariantGuard`（`2aed076`）

## 0. 一页摘要

15 个状态模块从 JsonFileBackend（事实标准）迁往 LingYiBackend（2T3A records/events）的过程中，
每个模块的迁移不是「一步到位的换源」，而是经过 **五阶段显式状态机**：

```
register → double-write → read-source → read-target → drop
```

- 每阶段有**显式 entry 判据**与**revert transition**（回滚=replay 事件，不回滚 json）
- **对账失败即冻结迁移**（绝不带病双写，冻结是显式状态而非隐式停滞）
- 一致性仲裁以 **lineage（事件追加顺序）** 而非时间戳
- 主干约束：任何后端故障 best-effort，绝不炸主流程

## 1. 五阶段定义

| 阶段 | 名称 | entry 判据（前序满足才进入） | 动作 | exit 判据 | revert |
|---|---|---|---|---|---|
| 0 | **register** | 模块已列入 15 状态模块清单 | 登记迁移意图：写入 `migration_registry`（record_type=migration，state=register） | 登记成功、无并发迁移冲突 | 删登记记录（未产生数据） |
| 1 | **double-write** | register 完成 | 写双份：json（事实标准）+ lingyi 镜像；读仍走 json | 连续 N 个周期对账 **consistent**（`StateReconciler` 三值判定：consistent/drift/error，error 视为不可达≠写偏，不计入连续一致） | 停 lingyi 写、仅留 json（数据未损） |
| 2 | **read-source** | 对账稳定一致 | 读切 lingyi（json 降级 fallback），影子读 json 对照 | 影子读偏差 < 阈值且无 error | 读回 json（读切不丢数据） |
| 3 | **read-target** | 影子读达标 | 读只走 lingyi，json 仅作灾难 fallback 保留 | 观察期（≥1 版本周期）无回归 | 读回 json + 保留 lingyi 双份 |
| 4 | **drop** | 观察期满 | 移除 json 写入，迁移完成；`migration_registry` 置 state=done | 迁移完成记录入账 | 重建 json（从 lingyi 全量导出） |

## 2. 状态机规则

```
迁移状态机（合法转移表，与 StateInvariantGuard 同构）：
  register ──→ double-write ──→ read-source ──→ read-target ──→ drop(完成)
    │               │                │                │
    └───────────────┴────────────────┴────────────────┴──→ reverted（任一阶段可回滚）
```

- **冻结（frozen）**：对账器判定 drift/error 连续超过阈值 → 迁移进程进入 `frozen` 态。冻结不是回滚，是**暂停**：双写继续但不再推进阶段；人工裁决（或对账恢复 consistent）后才解除。
- **revert（回滚）**：任一阶段显式回滚到 `json-only`（阶段 0 语义），以事件表追加 `revert` 事件留痕——**回滚=replay 而非物理删除**，保证可审计。
- 仲裁以 **lineage**（ly_state_events 追加顺序）为准：同一 key 的双写冲突，以事件顺序最后的写入为真，不比较 wall-clock 时间戳。

## 3. 与既有代码的映射

| 机制 | 落点 | 状态 |
|---|---|---|
| 双写开关 | `StateStore.__init__` `dualwrite` 参数 / `LINGCLAUDE_MEMORY_DUALWRITE=1` | ✅ 已实现 |
| 读切开关 | `read_from_lingyi` 参数（读 lingyi→json 降级） | ✅ 已实现 |
| 对账器 | `StateReconciler`（consistent/drift/error 三值） | ✅ 已落地（ef23407） |
| 迁移登记 | `migration_registry`（待建：record_type=migration） | ❌ 待建（本协议第 5 节） |
| 冻结判定 | 对账失败冻结（待建：挂 `StateReconciler` 输出） | ❌ 待建 |
| revert 入账 | `ly_state_events` 追加 `revert` 事件 | ❌ 待建（依赖 sink 接线） |

## 4. 阶段推进的实际执行（与清账/迁缝期合一）

本协议不独立排期——它是 **迁缝期（15 模块双写 × MEMORY/GOVERNANCE 实体迁缝）** 的执行细则。
每迁一个模块 = 该模块走完一次五阶段状态机，每阶段 entry/exit 判据达标才推进，
每个 `revert`/`frozen` 以 transition 入账。`migration_registry` 是全局迁移进度表（record_type=migration）。

## 5. 待建件清单（随迁缝期落地）

1. **migration_registry**：record_type=migration 的登记表（模块名、当前阶段、entry 时间、状态）
2. **冻结闸**：对账器连续 drift/error ≥ 阈值 → 迁移 `frozen`（显式状态，可查询）
3. **revert 事件**：`ly_state_events` 追加 `event_type=revert`（actor=data 含回滚阶段）
4. **影子读对照器**：read-source 阶段的 json↔lingyi 双读偏差统计

## 6. 迁移清单（15 模块，来源：V3 重构计划 §五 P3）

待迁缝期盘点补齐（与 2T3A 卷宗迁缝期任务书共用清单，此处不重复维护）。

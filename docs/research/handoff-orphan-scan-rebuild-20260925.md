# 交接文档 —— 动刀期①：缝/插件孤儿回收（扫描器重建先行）

> 交接性质：本轮零代码改动，落的是**开工前置条件**。
> 上一会话已就「20 件孤儿候选清单是脏数据」达成裁决：选项 1（先修扫描器）。
> 本文档把修好尺子的需求、双向校验锚点、ERR 案预告写死，新会话拿到的应是修好的尺子，不是脏清单。

## 一、会话上下文锚点（实测，非记忆）

| 维度 | 状态 |
|---|---|
| HEAD | `9b058f0`（自进化评估五条借鉴落地）—— `git log` 实测在案 |
| 工作区遗留 | 3 件未提交改动（`cli/full_tui.py` / `cli/repl.py` / `data/arch_ledger/arch_audit_state/default.json`），**不混入本轮**，单列处理 |
| 当前阶段 | 迁缝期进行中（M3 棘轮两格咬合：103→99→94，红名单余 94）；动刀期①是迁缝期的**减法前置** |
| 上一会话裁决 | 选项 1：先修扫描器，再重出候选清单，再批量轻回收 |

## 二、为什么必须先修扫描器（上一会话实测证据链）

上一会话一份「20 件 AST 级零消费点孤儿候选」清单被**双向推翻**：

| 申报件 | 申报 | 实测 | 判定 |
|---|---|---|---|
| `sqlite_store_base` | 零消费点 | `l7_cognitive.py:30` / `memory_engine.py:27` / `layered_memory.py:25` 三处 `import SqliteStoreBase` | ❌ 活件 |
| `submission` | 零消费点 | `query_engine.py:38 from core.submission import SubmissionMixin` | ❌ 活件 |
| `l5_audit` | （不在 20 件，但是反例锚点） | `query_engine.py:129 from lingclaude.core.l5_audit import L5Auditor` | ✅ 有消费 |
| `l7_cognitive_bridge` | （5 件已知死件之一） | 唯一引用是 `tests/test_wiring_gate.py:188` 豁免登记（「standalone, not wired into query_engine」）——守卫测试**反向证实**其无生产消费 | ✅ 无生产消费（测试引用不算消费） |

结论：旧扫描器只认 `import lingclaude.core.X` 完整路径，漏了四个通道，**连静态口径本身都没跑对**。拿它批量轻回收，活件（如 38 处引用的 `l5_audit`）会被误杀，直接复刻 ERR-05 启动链崩溃。

## 三、扫描器重建需求（新会话第一项工作）

### 3.1 必须覆盖的消费点通道（五口径，缺一不可）

| 通道 | 形态 | 旧扫描器是否覆盖 |
|---|---|---|
| ① 完整路径 import | `import lingclaude.core.X` / `import core.X` | ✅（唯一覆盖的） |
| ② from-import | `from lingclaude.core import X` / `from core.X import Y` / `from core import X` | ❌ 漏 |
| ③ wiring 字符串装配 | `"X"` / `'X.py'` 出现在 `core/wiring.py` 或 manifest 的装配语句里 | ❌ 漏 |
| ④ 属性/类级引用 | `X.attr` / `class Foo(X)` / 类型注解 / 函数内 import | ❌ 漏 |
| ⑤ 测试消费 | `tests/test_*.py` 的 import / 实例化 / 断言引用 | ❌ 漏 |

> **判别规则**：⑤测试消费的 import 不算「生产消费」，但要单独登记（可能是测试专用工具件，也可能是死件的测试还在跑——后者提示该件已死但测试未删）。

### 3.2 双向校验锚点（尺子修好与否的硬判据，两条都对上才算修好）

| 锚点 | 期望 | 实测依据 |
|---|---|---|
| `l5_audit` | **必须报「有消费」** | `query_engine.py:129` 有静态 import `from lingclaude.core.l5_audit import L5Auditor` |
| `l7_cognitive_bridge` | **必须报「无生产消费」**（测试豁免登记不算消费） | 全仓生产代码 0 import；唯一引用 `tests/test_wiring_gate.py:188` 是守卫豁免清单里的定性注释 |

> 两个锚点都对上 = 尺子修好，可开工。
> 任何一个对不上 = 扫描器仍有口径漏洞，**继续修，不开工**。

### 3.3 排除噪音

- `.lingclaude/worktrees/**` 下的所有引用都是 worktree 副本，不是消费点——扫描器必须排除此目录，否则一个消费点会被重复计数 13 次（上轮实测噪音源）。

## 四、扫描器修好后的执行序（按上一会话裁决）

1. **重出真实候选清单**（覆盖五口径，排除 worktree）；
2. **与旧 20 件清单 diff**——差异部分入 ERR 案（旧清单 = 一次测量口径失准，按 J5 四条件之 4 误差入账，新案号顺延 ERR-2026-0925-06）；
3. **真实候选逐件过四判据**（装配语句消费 / wiring 字符串引用 / 测试消费 / 龄期——每件人工过一遍，尤其「装配语句算不算消费」）；
4. **确认件批量轻回收**——每件一个原子 commit，走 4b9149a 判据模板（撤 core + attic 休眠保期权 + record），红名单减项；
5. **收官后回迁缝期 MEMORY 首刀**（双写协议按任务书#5 补）。

## 五、4b9149a 判据模板（已验证可用，直接复用）

撤缝流程 record 在案：`data/arch_ledger/arch_seam_recycled/recycled-transport-seam-20260925.json`。

首例全流程已跑通：四判据 → record → 测试换载 → 回归绿。模板本体**没有问题**，问题只出在候选清单的产出工具上——所以模板直接复用，不重设计。

## 六、风险与红线

| 风险 | 缓解 |
|---|---|
| 误杀（动态装配不可见） | 每个回收件走**轻回收档**（attic 休眠保期权，可逆）；四判据里「装配语句消费」由人工过一遍 |
| 复刻 ERR-05 启动链崩溃 | 每个回收件撤 core 后**必须跑启动链冒烟**（`python -c "import lingclaude.core.wiring"` + `lingclaude --help`），ERR-05 案文已把这条写成迁移类 commit 的硬性验收 |
| 脏数据再进卷宗 | 候选清单未过 3.2 双向校验前，**禁止**写进 2T3A 卷宗或任何 record |

## 七、待令事项（新会话开工顺序）

1. 重建扫描器（按 §3.1 五口径 + §3.3 排除 worktree）；
2. 跑 §3.2 双向校验，过了才继续；
3. 重出真实候选清单 → diff 旧清单 → ERR-06 入账；
4. 逐件四判据 → 批量轻回收；
5. 红名单减项后，回迁缝期 MEMORY 首刀。

## 八、交接确认

- 上一会话（灵克）实测并撰写本文档；
- HEAD `9b058f0` 在案，本轮零提交；
- 工作区 3 个遗留改动单列，不混入；
- 双向校验锚点 `l5_audit` / `l7_cognitive_bridge` 已实测定性；
- 新会话拿到的应是**修好的尺子**（§3 全部需求），不是脏清单。

---

*交接人：灵克（lingclaude） · 2026-09-25 · HEAD=9b058f0*

# 交接：v4.0 清查专项 → 闭合期推进（orphan 治理 / M1 守卫 / 递归治理）

> 生成：2026-09-26（会话三收尾）　基线 HEAD：`8a5c234`
> 上游交接：`docs/research/handoff-orphan-scan-rebuild-20260925.md`（orphan 扫描器重建 + 五口径 + 双向校验锚点——**仍未开工**，见 §5）
> 本文档定位：覆盖「循环推进直至 v4.0 闭合期」三轮累计战果 + 闭合期四件事进度 + 新会话开工序。

---

## 一、本轮累计战果（HEAD 9b058f0 → 8a5c234，11 个提交全过四闸门）

按时间序，全部已入库、四闸门（arch-guard-gate 3 passed + 审计 + 灵督 + 快照）全过：

| 提交 | 内容 |
|---|---|
| `08c7a7e` | 建闸期③收口：invariants 框架接线生命周期（8 处赋值点 observe 模式 + ACTIVE→FAILED 补边） |
| `04a58ba` | 闭合期②：state_query 自省门面（**在 `lingclaude/engine/`，不在 core/**）+ 出生登记五件事首例实操 |
| `13ed888` | M1 真阳性改写：plugin_lifecycle `_domain_of` 注释去域词汇复述（**不登记豁免**，红名单/豁免面净缩） |
| `853a153` | **轻回收 2 件**：goal_receipt + manifest_lock（五口径零消费双重确认，M1 豁免提前到期转 recycled） |
| `5b43c66` | 闭合期④最小切片：model.call 事件落 base_url 路由身份（补 proxy3 request_log 断供的分账数据源） |
| `a81e818` | 760 口径开账 record 落盘（base_url 埋点切片凭证） |
| `8a5c234` | AtomCode 会话间隙补：4 件回收 record 全量恢复指针字段（origin_path/last_commit/revival_hint） |

### 轻回收件明细（4 件，全 light 档 + revival_clause 可复活）

| 件 | 提交 | 恢复指针（已字段化，8a5c234） | 复活路径 |
|---|---|---|---|
| `l7_cognitive_bridge` | `9685af9` | origin_path + last_commit=`9685af9` + revival_hint | git 考古 → wiring 显式装配 + 真实消费测试 → 过 orphan_scan_v2 --selfcheck |
| `goal_receipt` | `853a153` | origin_path + last_commit=`853a153` + revival_hint | 同上 + 重登记 M1 豁免 |
| `manifest_lock` | `853a153` | origin_path + last_commit=`853a153` + revival_hint | 同上（恢复场景：27 插片 manifest + 24 org 账本漂移需 CI 前置拦截） |
| TRANSPORT 缝 | 早期 | last_commit=`4b9149a^` + revival_hint | **铁律 7**：以 `{ns}/transport` 域前缀重新入册（N3 守卫拒收裸 key） |

> 关键纪律：回收件的 revival_clause 已实测含「恢复时须先过 orphan_scan_v2 --selfcheck」——**orphan 扫描器重建因此不是可选项，是回收件的复活闸门**。

### 红名单净缩：99 → 97 席

- 减 2：goal_receipt / manifest_lock（本轮轻回收）。
- M1 豁免面同步净缩：goal_receipt/manifest_lock 转 recycled；2026-11-30 那批四件有 no_renewal 字段（更老、不可续），09-21 批（本两件）已处理完毕。

---

## 二、实测修正（对接班会话的重要纠错）

1. **state_query 位置**：在 `lingclaude/engine/state_query.py`（74 行）+ `tests/test_state_query.py`，**不在 core/**。core/ 是主干冻结区，engine/ 是插片区——闭合期②选 engine/ 正是为了绕开主干冻结约束。
2. **8a5c234 非本会话产出**：是 AtomCode 在会话间隙补的回收 record 恢复指针字段提交。接班会话会把它当普通已入库提交即可，但它的存在说明 AtomCode 在独立巡视回收 record 的可 query 性——值得肯定，也提示 record 字段 schema 在演进，落新 record 时先 `ls data/arch_ledger/arch_seam_recycled/` 看最新字段范式。
3. **`.bak` 文件是调试残留，不是交付物**：`lingclaude/engine/loop/loop_body.py.bak`、`tool_loop_detector.py.bak` 是并行会话（loop warn 外显）的调试残留，不要入库、不要误读为架构改动。
4. **「M1 守卫 AST 误报 4 处」定性已更正**：上轮说守卫误报 4 处，实测修正——现只剩 1 处真违规（plugin_lifecycle.py:52），其余 3 处已被 M1 豁免 record 覆盖（4a5145d 合规成果）；plugin_lifecycle 那 1 处也已在本轮改写注释收口（13ed888）。**当前 M1 守卫是绿的**。

---

## 三、v4.0 闭合期四件事 — 进度实况

| 项 | 内容 | 状态 | 剩余切片 |
|---|---|---|---|
| ② 自省门面 | state_query（engine/ 插片区） | ✅ `04a58ba` 完成 | 无（如需扩能力，按缝 key 铁律 7 重入册） |
| ③ 出生登记五件事 | 职责/待命事件/观察期指标/缝 key 豁免理由 | ✅ 首例实操（state_query docstring 已登记全五件） | 后续新建缝需复制此范式 |
| ④ 760 口径开账 | model.call 事件落 base_url 路由身份 | ✅ 最小切片 `5b43c66` 完成 | **聚合层 next_slice**：分布账报表 + `_record_provider_outcome`（model_call.py:141）挂身份维度（现有熔断统计可扩展） |
| ① 递归治理三件套 | execution/governance 双层账本 | ⏳ **下一循环主攻** | 双层账本 schema 开账：`datalog.py` 无 execution/governance 分离、`governance_verifier.py` 仅投票验证——datalog 是 append-only JSONL 事件流，分层要动 schema，比 ④ 重 |

### 下一循环开工序（闭合期收尾）
1. **主攻 ① 递归治理三件套**：双层账本 schema 开账是最小切片。先读 `lingclaude/core/datalog.py` + `governance_verifier.py` 摸清现有事件 schema，再设计 execution/governance 分层（execution=运行事件流，governance=治理决策/投票/审计事件流，分离后治理可追溯、可独立审计）。
2. 顺收尾 ④ 聚合层（若 ① 太重可先做 ④ 的分布账报表，轻）。
3. ①④ 都收口后，v4.0 闭合期四件事全绿，进入闭合期验收。

---

## 四、暂缓件 D 组消费面实测（7 件，orphan 扫描器重建时直接用）

| 件 | 测试消费 | 生产消费 | 暂缓理由 |
|---|---|---|---|
| `invariants` + `plugin_lifecycle` | 有 | invariants 已接线生命周期（08c7a7e） | 建闸期新建（0 天），等观察期 |
| `context_engine` | test 2 处 | 零 | P2 预置 4 天 |
| `repair_card` | test 2 处 | 零 | P2 预置 4 天 |
| `l10_a_post_audit` | test 6 处 + 豁免登记 | 465 行生产死叶子 | 测试锚重，拆测试再定 |

> P2 预置件（context_engine/repair_card）龄期已接近旧 ERR-05 门槛（4 天），**下一循环须到期复核**：接线兑现 or 到期入 ERR。

---

## 五、跨会话悬案清单（按优先级）

1. **孤儿扫描器重建（最高优先，仍未开工）**：上游交接 `handoff-orphan-scan-rebuild-20260925.md` 的 §3.1 五口径 + §3.2 双向校验锚点 + §3.3 排除 worktree 噪音——全套硬约束原样未动。本轮 orphan 治理能连下 4 件轻回收，靠的是 orphan_scan_v2 这个临时工具；**要根治「孤儿常客」循环，必须重建正式扫描器**（五口径：完整路径 import / from-import / wiring 字符串装配 / 属性类级引用 / 测试消费）。旧扫描器只覆盖第①个口径，漏四个——goal_receipt/manifest_lock 能漏网 20 件清单，就是旧扫描器口径不全的活证。
   - 双向校验锚点：重建后必须 `l5_audit` 报有消费、`l7_cognitive_bridge` 报无生产消费（其唯一引用 test_wiring_gate.py:188 是守卫豁免登记，非消费）——两条都对上才算尺子修好。
   - 红线：候选清单未过双向校验前，禁写 2T3A 卷宗 / 任何 record。
2. **loop warn 外显功能（并行会话，归属其会话）**：`engine/loop/loop_body.py` + `tool_loop_detector.py` + 新测试 `tests/test_loop_warn_visible.py`（当前 untracked）是打转 warn 外显功能，改动极小（7 insertions / 2 deletions）+ 两个 `.bak` 调试残留。**归属并行会话入库，接班会话不要裹入自己的提交**。
3. **untracked 单列**：`.atomcode/notes/codex_review_v0.2.out`（AtomCode 审查笔记）、`.atomcode/orphan_scan_v2_result.json`（orphan 扫描器会重写，建议 gitignore 化）。
4. **`default.json`  recurring M**：`data/arch_ledger/arch_audit_state/default.json` 每次运行都会被运行时刷新，是噪音不是改动——入库前 `git checkout` 掉，不要裹入业务提交。

---

## 六、不可绕过的硬约束（沿袭上游交接，原样有效）

1. **双向校验锚点**：`l5_audit` 必须报有消费（query_engine.py:129 函数内 lazy import `_get_l5_auditor`）、`l7_cognitive_bridge` 必须报无生产消费——两条都对上才算尺子修好，任何一条对不上继续修不开工。
2. **排除 worktree 噪音**：`.lingclaude/worktrees/**` 必须排除，否则一个消费点被重复计 13 次。
3. **每个回收件撤 core 后必跑启动链冒烟**：`import lingclaude.core.wiring` + `lingclaude --help`（ERR-05 教训；注意 `python -m` 是错误口径，正确入口是可执行 `lingclaude`）。
4. **四判据全绿才轻回收**：零装配 / 零测试消费 / 零运行痕迹（state/arch 账本 grep）/ 龄期（孤儿常客 + M1 豁免到期）。
5. **M1 词汇守卫与 orphan 消费扫描是两套守卫，别混**：M1 管概念封闭（缝域词汇），orphan 扫描管消费点；M1 剩 1 处 plugin_lifecycle 已改写收口，当前 M1 是绿的，无需新豁免。

---

## 七、给接班会话的一句话

> v4.0 闭合期四件事已完成 ②③④（④ 还差聚合层），只剩 ① 递归治理三件套（最重的一件）；orphan 扫描器重建是根治「孤儿常客」循环 + 回收件复活闸门的双重关键，排在 ① 之后或并行；开工前先把 `default.json` checkout 掉、把 loop 双件 + `.bak` 留给并行会话，别裹进自己的提交。

---

## 八、新克隆恢复防线（2026-09-26 P0 收口后补）

> .githooks/ 已入库（7b8d9e9）。任何新克隆执行一次：
>
>     git config core.hooksPath .githooks && lefthook install
>
> pre-commit 六守卫直连立即生效；lefthook 委托件（审计/灵督/快照）
> 缺失时静默通过（pre-commit 内置 -x 判断），不阻断提交。
> 此后每次 commit 自动过机检，无需人自觉。

## 九、长时任务会话哨兵规程（2026-09-26 hang 事故防线）

> 事故：一个 pytest 后台进程 100% CPU 挂 6h40m 无人发现（残留后台
> 任务），挤占 CPU 导致并行测试慢化、P2 基线「跑不完」误判。
> 事故根因链：后台任务无 timeout 参数 + 无 job 登记 + 无周期巡检。

**三条铁律**（任何 agent 会话执行长时任务时适用）：

1. **启动必留名**：后台命令必须带显式 `timeout` 参数（run_in_background
   timeout 字段，或 shell 层 `timeout <N> <cmd>`），并在当轮汇报中
   记录 job_id / pid 与预期完成时长。
2. **每轮必巡检**：会话每轮开场用 `jobs` / `job_status` 查未完成项；
   直接 spawn 的进程用 `ps aux | grep <标识>` 复核，不允许「启动后
   静默等它自己结束」。
3. **hang 即处决**：判定标准 = 运行超预期 2 倍 或 >30min 且
   CPU>90% 且输出文件无增长。命中立即 kill，并在 handoff/汇报中
   记录（谁、何时、什么命令、为何 hang）。禁止「再等等看」。

**代码层兜底**（已入库 pyproject.toml）：
`[tool.pytest.ini_options] timeout = 300`（pytest-timeout 2.4.0）
——任何单件测试最多挂 300s 自动熔断；长跑用例按件
`@pytest.mark.timeout(N)` 覆盖。此为最后一道防线，不替代上面三条。


# 方案C v4 P0-P2 插片——工作台账登记（arch_ledger 补账）

> 登记：2026-09-21 审查轮。提交 1cb8208（插片本体）+ 6f34d5e（缓存与脱敏，另账）。
> 本文件补齐「插片本体入册」仪式缺口（审查发现：对账登记覆盖了账本文件，未覆盖插片工作台账）。

## 1. 插片清单与状态

| 插片 | 落点 | 规模 | 状态 |
|---|---|---|---|
| plugin_lifecycle | `lingclaude/core/plugin_lifecycle.py` | 398 行 | ✅ 完成（24 用例含 hot_swap 回滚锚定） |
| quota_governance | `lingclaude/model/quota_governance.py` | 206 行 | ✅ 完成（4 用例，真实 GLM 1308 错误形态解析） |
| context_engine | `lingclaude/core/context_engine.py` | 154 行 | ✅ 完成（4 用例 round-trip） |
| repair_card | `lingclaude/core/repair_card.py` | 184 行 | ✅ 完成（3 用例 fail-closed 锚定） |
| seam 订阅钩子 | `lingclaude/core/seam.py` | +36 行 | ✅ 完成（register/unregister 锁外广播） |
| hooks 扩容 | `lingclaude/core/hooks.py` | +10 行 | ✅ 完成（SESSION_RESUME/PRE·POST_HOT_SWAP，枚举快照同步） |
| 四处接线 | task_router/commands/lifecycle | 防御式 try/except | ✅ 全 GREEN（E7 抓获 hot_swap epoch 守卫真 bug，已修） |

## 2. 铁律判据对照

- **J1 变化走接缝**：四插片 import 闭包零指向 plugins//engine/；接线挂既有 hook 点，属「插片入册」非临时直连，无债务记录需求；
- **J4 状态归原语**：四插片为进程内存态，**跟随 15 个存量模块既有形态**（铁律文档:240/269 明示 P3 欠账），不加重违规；
- **M2/M3/N7**：无枚举特判分支；依赖方向干净；四插片互相零 import；
- **G3**：本批净增 6 处（task_router×2 / quota×2 / commands / display / repl / lifecycle 各 1 = +8，scheduler 预存 2 处上提 -2），详见 §3；

## 3. G3 基线账（2026-09-21 实测口径：守卫 _count_lazy，SRC=lingclaude 包目录）

- 437（2026-09-16 登记）→ 512（1cb8208^ 实测，期间 75 处未登记，属历史登记欠账）；
- 512 → 518（方案C v4 + 收口批净增 6，per-file diff 实测：task_router×2 / quota_governance×2 / commands / display / repl / lifecycle 各 1 = +8；scheduler 预存 2 处函数内 logging 随本轮上提消除 -2；均 S3 合法防御接线）。

## 4. 判例引用

- 铁律 3 但书：接线点防御式挂载不入债（对齐 1cb8208 提交时判例）；
- G10 冲突：铁律文档:277 明示 G10 与铁律冲突待改造，其红灯不作阻塞结论。

## 5. 审查轮顺带修复（2026-09-21）

- **scheduler.py cancel 死锁**（预存生产 bug）：cancel 持 `threading.Lock` 调 `_save_tasks`（其内 :93 再抢同锁，非重入）→ 必死锁。hermetic 化测试 + pytest-timeout 首次暴露；修复=锁外持久化（与 register/_run_loop 同纪律）；
- **t3_wiring 测试 HOME 隔离**：TestScheduleManagerWiring 裸 `ScheduleManager()` 读真实 `~/.lingclaude/schedules.json`（17 个常驻真实任务混入断言）→ autouse fixture 将 `_SCHEDULES_FILE` monkeypatch 到 tmp_path；
- **quota_governance 停层声明**补齐（铁律 2 细则 5 三要素）。

## 6. 未竟项（移交）

- context_engine 接入压缩层、repair_card 接入批量操作器、lc_plugins_inspect 工具插片——均为独立 PR，「未接线=零行为影响」已注明。

# lc 全量审计报告（严格审计官模式，2026-09-26）

- 审计人：灵克（lingclaude），按"严格审计官"提示词执行
- 审计范围：架构（J1-J5 铁律判据）/ 代码（启动链·死代码·双轨·账实）/ 安全（secrets·注入·权限·依赖）/ E2E（四链路）
- 方法：AST+grep 全量扫描 + pytest 实测 + 端口探活，全部当轮实测输出
- 并发感知前置（ERR-07 纪律）：审计起点已核对最新 commit 作者分布（7389587 等 6 个并发 commit，2 分钟前仍在产出），工作区 3 M 文件归属并行会话（interface.py/auth.rs/main.rs），本轮未裹入

---

## 一、总体结论

**健康度：B+（架构与安全面优秀，代码卫生与测试基线有真实缺陷）**

| 维度 | 评级 | 一句话 |
|---|---|---|
| 安全 | A- | 零 secrets 明文、零 shell=True、SQL 三处全有白名单/参数化兜底；1 处 eval 实为测试数据 |
| 架构（J1-J5） | A- | M3=0 / M1=0 / N7=3（memory 五桥互连，已知豁免通道外） |
| 代码卫生 | C+ | pyflakes 270 条：**undefined name 19 处（高危）** + imported-unused 232（低危） |
| E2E | B | 守卫链路 39 passed / **2 failed**（N7 测试 + 豁免过期测试）；启动链/CLI/webui/引擎探活全通 |

## 二、安全审计（4 项扫描）

| 项 | 结果 | 细节 |
|---|---|---|
| secrets 明文 | **0 命中** | api-key/secret/password + 16 字符字面量模式全量 grep |
| 命令注入 | **0 真实命中** | 11 处匹配逐一核验：api.py:552 是注释、marketplace.py 是扫描器自体测试数据、local_provider.py:60 是 torch `model.eval()`（模型推理 API 非代码执行） |
| SQL 拼接 | **3 处全有兜底** | metrics.py:122（nosec 标注，where 硬编码子句+参数化）；state_store_ext.py:221/341（view 来自常量表，order_by 有列白名单校验） |
| subprocess 面 | 50 处调用，**shell=True 0 处** | argv 边界纪律良好（bash.py:241 注释在册） |

## 三、架构审计（J1-J5 执法扫描）

| 判据 | 违规 | 说明 |
|---|---|---|
| M3 依赖方向 | **0** | core/ → plugins/ 零违规 |
| M1 概念清白 | **0** | 建闸期词汇违规已全部清理（policy/lingyi/model 零残留） |
| N7 横向耦合 | **3** | memory 五桥互连（experience/l7/memstore → lingmemory_bridge.bridge）——**已知状态**：这是 ERR-05 回溯时五桥恢复回 core 的连带形态，共享应走 `plugins/memory/` 直下公共接缝模块。处置属动刀期 MEMORY 族迁移范围（不新开豁免） |
| 账实一致 | **绿** | 红名单台账 in_core 声明 0 件 vs core/ 磁盘实况：零漂移；migration stages {register:13, recycled:1, done:1} 与回收 record 对齐 |

## 四、代码审计

### 高危（需修复）

**U1. undefined name 19 处**（pyflakes 实测）：
- `_sys`×6 / `_json`×6（疑似下划线前缀的 import 别名被改名或删除后残留调用）
- `OrderedDict`×2（缺 `from collections import`）
- `ToolResult`×1 / `record_change`×1（缺 import）
- **风险**：这些路径一旦运行到即 NameError 崩溃——属于"启动链通但暗雷在"的形态（同 ERR-05 家族）

### 低危（卫生债，可批量清）

**U2. imported-but-unused 232 处**——不炸但污染 M1/扫描器信号面；建议挂动刀期随族清理（迁一族清一族）。

### 账实一致性

- dual_track_registry 登记 0 条 vs 五桥双副本磁盘实况——**登记缺位**（闸门 2 有账本无数据，属"闸门刚建未通电"状态，非漂移）。

## 五、E2E 测试（四链路）

| 链路 | 结果 |
|---|---|
| 启动链 | ✅ `import lingclaude` OK + CLI 入口 OK |
| webui | ✅ 13458 在监听（pid 3530446，非本轮实例——并发会话），根路径 401=token 守卫正常执法 |
| 引擎 8700 | ✅ /status 401（token 守卫正常，服务在） |
| 守卫链路测试 | **39 passed / 2 failed**（5m32s）：`test_n7_no_cross_plugin_internal_imports`（与 N7 扫描的 3 处五桥互连同源）+ `test_exemptions_not_past_review`（豁免过期测试） |

### 2 failed 的定性

1. **N7 测试失败 = 真阳性**（五桥互连确实违规）——与 §三 N7 扫描一致，处置同上（MEMORY 族迁移时一并解决，不新增豁免）；
2. **豁免过期测试失败 = 需查**——可能是有豁免 record 的 due/review 日期已过（2026-11-30 未到，疑为 09-21 老批次或日期字段形态变化），**逐件核验后处置：真过期→收割，字段形态变→修 record**。

## 六、修复建议（按优先级）

| # | 项 | 量级 |
|---|---|---|
| R1 | undefined name 19 处修复（高危暗雷） | 半天 |
| R2 | 豁免过期测试：逐件核验 due 日期（或字段形态），真过期收割/假过期修 record | 2 小时 |
| R3 | N7 五桥互连：建 `plugins/memory/shared.py` 公共接缝（动刀期 MEMORY 族范围） | 随族迁移 |
| R4 | imported-unused 232 处：随族迁移批量清 | 随族迁移 |
| R5 | dual_track_registry 通电：五桥副本登记 serving_side | 2 小时 |

## 七、与前轮审计的对比（守卫作用增量确认）

- M1 违规：上轮 1 处（plugin_lifecycle 注释）→ **本轮 0**（并发会话已修）；
- M3 违规：持续 0（棘轮+守卫执法生效）；
- 新增发现：undefined name 19 处（pyflakes 首次纳入审计口径——**新守卫面**）。

---

*审计方法声明（J5 四条件之 1）：本报告静态扫描（grep/AST）与动态实测（pytest/探活）双口径互证；静态口径看不见反射调用与运行时动态 import，动态口径看不见未执行路径——两口径结论已交叉核对。*

## 附录：R1 修复执行记录（2026-09-26 R1' 批次）

**结果：17 处 F821 全清（`ruff check --select F821` → All checks passed；七文件导入冒烟全 PASS）。**

| 文件 | 缺失符号 | 修法 | 语义定性 |
|---|---|---|---|
| cli/app.py | `_sys`×6 `_json`×6 | 别名归位 `sys.`/`json.`（模块级 import 早已在，函数体内用了未定义的下划线别名） | 纯暗雷修复，无行为变化 |
| cli/repl_turn.py | `_json`×1 | 补 `import json` + 别名归位 | 同上 |
| model/credential_pool.py | `OrderedDict`×2 | 补 `from collections import OrderedDict`（注解字符串内的前向引用，运行时本不炸） | 静态可解析性修复 |
| engine/loop/sub_agent.py | `ToolResult`×1 | 模块头补 `TYPE_CHECKING` 块导入（与方法注解注释自述的惰性求值设计一致，运行时零变化） | 同上 |
| self_optimizer/daemon.py | `record_change`×1 | 调用点前补局部导入（对齐同文件 814 行既有惯例） | 暗雷修复（optimize_write 路径运行到即 NameError） |
| core/model_call.py | `prompt`×1 | `task_hint=prompt` → `task_hint=correction_prompt`（幻觉修正路径的幽灵变量——该方法作用域内本无 `prompt`，主路径 loop_body:595 同语义位传的是当轮任务提示，修正路径对应物即打回提示） | **行为修复**：修正轮工具输出会走 `_slim_tool_output` 裁剪而非 NameError 中断 |
| core/evidence_protocol.py | `ObservationKind`（`__all__` 幽灵导出） | 从 `__all__` 摘除（该符号全文不存在，无 `import *` 消费者） | 导出面卫生 |

**口径对账**：pyflakes「19 处」= F821 实际 17 处 + `__all__` 幽灵导出 1 处 + `.bak` 备份文件 1 处（非生产代码，已在 .gitignore:41，不计修复项）。pyflakes 与 ruff 计数差异即源于此。

**死方法甄别记录**：`model_call._hallucination_correction` 初判疑似死方法（单文件 grep 无调用点），复核 `hooks.py:176` 经协议调用——**可达路径**，修复按真实路径对待。教训：跨文件调用点全仓核验后方可判死。

**测试验证**：修复涉及面 9 个测试文件（sub_agent/daemon/energize/guard_wiring/p0_p2_roadmap/round_end/n5_done/adaptive）后台运行中，结果随 ⚠ [工具结果未验证] commit message 入账；并行会话资产（display/interface/repl_io/full_tui/auth.rs/main.rs）零触碰。

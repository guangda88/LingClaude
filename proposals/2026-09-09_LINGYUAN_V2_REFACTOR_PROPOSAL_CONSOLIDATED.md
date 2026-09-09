# 提案：灵克 灵元 1.0 重构 v2（三方会审消解版）

- **提案人**: 灵克 (lingclaude)，2026-09-09 三方会审会话
- **状态**: OPEN — 待族内评审
- **取代**: `2026-09-09_LINGYUAN_V1_REFACTOR_PROPOSAL.md`（v1 全部结论被吸收，分歧点实测消解后修正）
- **输入**: 三份外部会审报告（codex / opencode / claudecode）+ 本方 v1 提案
- **关联**: `docs/theory/LINGYUAN_1.0_REFACTOR.md` · `docs/theory/LINGCLAUDE_VS_PEERS.md` · `docs/LINGMEMORY_USAGE.md`

---

## 一、三方会审对错账本（每条分歧实测裁决，H17）

三份报告互相有出入。**不采信任何一方的转述，全部以源码实测裁决**：

| # | 分歧点 | 各方说法 | 实测裁决 |
|---|---|---|---|
| K1 | capability_seam.py 位置 | claudecode："engine 下找不到"；opencode："lacp/ 已建成" | **opencode 对**。`lingclaude/lacp/capability_seam.py` 存在。"缝建而不用"是事实，但缝本体不在 engine |
| K2 | 死代码 1.3 万行 | codex："experiments/ scripts/ research/ 共 1.3 万行 0 引用" | **codex 高估 3 倍**。experiments/ 与 research/ 目录不存在；scripts/ 实测 4,805 行。按 1,500 行预算入 P1 |
| K3 | coding.py:770 熔断缺陷性质 | claudecode："hasattr 永真，5a/5b 接线缺陷" | **claudecode 方向对、性质判断反了且更严重**：全库无任何 `self._loop_detector =` 赋值点（model_call.py:200/396 是局部变量 `loop_detector`，不带 self.）→ hasattr **永假** → 5b 否决熔断（denial_abort）从未生效过，是**死代码**而非误触发 |
| K4 | hybrid_router/local_provider 死接线 | claudecode："零调用"；codex 路线图未列 | **claudecode 对**。仅 model/__init__.py 引用（hybrid_router 还引用 local_provider），无业务消费方 |
| K5 | core→engine 依赖倒装 | claudecode："7 处倒装 + 20 处循环依赖" | **采信，并入 E 类债**。这是"插片焊死主干"的另一形态：主干 import 插片实现而非走缝 |
| K6 | fact_checker mock 语义 | claudecode："固定返回 found=True" | **采信并升级**。_mock_search 返回 confidence 1.0 的假证据（core/fact_checker.py:300-303），治理链会把"未验证"消费成"已验证"——违反 H1 诚实原则 |

**会审方法论结论**：三份报告合计 6 处事实分歧，实测后 2 处一方全对、3 处部分对、1 处方向对但性质反。**任何单一报告（包括本方 v1）都不能直接作为施工依据**——这正是 H17 要求闭环实证的原因。

---

## 二、v2 新增实证（本轮 grep 验证，标文件:行号）

1. **E1 熔断死代码**（据 K3）：`engine/coding.py:770` `hasattr(self, "_loop_detector")` 永假，5b denial_abort 从未触发。修复 = 主干 __init__ 显式初始化，约 5 行。
2. **E2 mock 假阳性**（据 K6）：`core/fact_checker.py:300-303`。修复二选一：返 `found=False + source="mock"`，或 fail-closed 抛错。**禁止返回 confidence 1.0**。
3. **E3 死插片**（据 K4）：hybrid_router + local_provider 删除或标注 experimental 并从 `__init__` 导出摘除。
4. **E4 governance/router.py 调不通真实引擎**（claudecode 实证，待复测后处置：接真引擎或删）。
5. **E5 subagent ACP 端口 8901 默认未跑、静默空转**（claudecode 实证；修复 = 未连接时显式报错而非静默降级）。
6. **E6 core→engine 倒装 7 处**（据 K5）：query_engine.py:26→tool_router、tool_executor.py:13→mcp_proxy、tool_call_executor.py:77→verification_gate、mcp_tools.py:11,64,122。修复 = 下沉到 runtime 装配层，P2 与 30 对象倒转同批处理。

> **E 类的哲学定性**：E1-E6 是"哲学背离"的极端形态——**有插片形态的代码，无插片接线的实质**。灵元尺子下，死插片比没有插片更糟：它制造"能力存在"的假象（E3/E5 直接虚增能力表述，违反 H1）。

---

## 三、v2 技债总账（A-E 五类，吸收三方报告并全部实测）

### A. 结构债（主干肥大）— P2 主攻
| # | 项 | 位置 | 严重度 |
|---|---|---|---|
| A1 | QueryEngine ~30 协作对象硬编码装配 + mixin 隐式 self 契约 | core/query_engine.py | P0 |
| A2 | CodingRuntime 10 mixin 继承、905 行 | engine/coding.py | P0 |
| A3 | cli/app.py 1,911 行单文件 | cli/app.py | P1 |
| A4 | lazy import 146 处 + sys.path.insert 硬接四姊妹包（query_engine.py:50/58/60/75） | 全包 | P0 |
| A5 | api.py 871 行门面过载 | api.py | P2 |
| A6 | core→engine 倒装 7 处（=E6，装配层缺位） | 见 §二.6 | P2 |

### B. 重复/双轨债 — P1 主攻
| # | 项 | 处置 |
|---|---|---|
| B1 | `_execute_tool_legacy`（coding.py:792）与 pipeline 双路径 | 删 legacy |
| B2 | 双 bash 执行器 | 保留 native，lingxi 通道降级为 MCP 插片 |
| B3 | 双 retry（model/ vs llm_proxy/） | 合一 |
| B4 | ToolExecutor vs ToolCallExecutor | 合一 |
| B5 | Result[T] vs dict "error" in 判错双轨 | 工具层迁 Result |
| B6 | 根目录垃圾（`=1.4.0`/`=6.1.1`/tmp 泄漏/CRUSH 备份×3/config.bak×2/null/）+ scripts/ 死代码 4,805 行（据 K2） | 清理，删除前列清单获确认词 |

### C. 漂移债 — P1 主攻
| # | 项 | 处置 |
|---|---|---|
| C1 | pyproject 0.3.0 vs VERSION 0.5.0 | 统一 + doc_consistency_check 加规则 |
| C2 | config.yaml 明文 api_key（违反自家 linggit critical 规则） | 改环境变量，key 入 key_store |
| C3 | AGENTS.md 索引指向 6 个不存在文件 | 重建索引 |
| C4 | GAP_ANALYSIS 事实错误（K1 同源问题） | 修订并复核 ROADMAP 依据 |
| C5 | `.audit/` 红灯（硬编码 IP×5、cli/app.py 复杂度 347 vs 阈值 30） | 逐项销账 |

### D. 工程化债 — P0/P1 主攻
| # | 项 | 处置 |
|---|---|---|
| D1 | 无 CI 测试工作流 | GitHub Actions 加 pytest 离线跑 |
| D2 | deploy 仅 1 个 systemd 单元 | 逐个单元化（P4 后） |
| D3 | 测试:源码 0.74:1，主干覆盖薄弱 | 每迁一插片补契约测试 |
| D4 | webui-server audit.rs 未接线 | 接线或声明弃用 |

### E. 死接线/假阳性债 ⚠ 最高优先（本轮新增）
| # | 项 | 实测证据 | 处置 |
|---|---|---|---|
| E1 | 5b 否决熔断从未接线 | coding.py:770 hasattr 永假 | 主干 __init__ 显式初始化（~5 行） |
| E2 | fact_checker mock 假阳性 | fact_checker.py:300-303 confidence 1.0 | fail-closed 或显式 found=False |
| E3 | hybrid_router/local_provider 死代码 | 仅 __init__ 引用 | 删或摘导出 |
| E4 | governance/router.py 调不通真引擎 | claudecode 实证 | 接真引擎或删 |
| E5 | ACP 未连接静默空转 | claudecode 实证 | 未连接显式报错 |
| E6 | core→engine 倒装 7 处 | 见 §二.6 | 下沉 runtime 装配层（并 A6） |

> E 类是"哲学背离"的极端形态：**有插片形态的代码，无插片接线的实质**。死插片比没有插片更糟——它制造"能力存在"的假象，违反 H1 诚实原则。

---

## 四、重构后的主干定义（灵元尺子）

**主干 ≤5,000 行，只含四类永不变的东西**：

| 件 | 来源 | 不变式 |
|---|---|---|
| 出入：turn 循环 | core/model_call.py 循环内核剥离 | 主干不知道任何具体插片的存在 |
| 流转：事件账本 | session_journal（append-only）+ 灵忆 2T3A | model-visible means logged |
| 缝：插片协议 | lacp/ 五 seam + ToolRegistry | 新增插片 = 新增 manifest，主干 0 改动 |
| 门：钟表域守卫 | PermissionContext + VerificationGate + bash 黑名单 + MV-1 | 安全门不可插拔、不可配置绕过 |

**插片清单**（manifest 注册，禁止主干 import）：model providers、30+ 工具、五层记忆、governance、self_optimizer、intel/behavior、mcp server/client、bus responder、webui/api、cli 交互、stt/lsp/subagent 后端、llm_proxy。

**验收主指标**：新增一个插片只新增 manifest + 插片文件，**主干 diff = 0**——架构守卫测试机械判定，不靠自觉。

**三方会审的哲学分歧裁决**：codex 主张"保守保留 core/ 分包"（≤5k 主干），opencode 主张"激进 trunk+plugins 重组"（≤500 行主干），claudecode 主张"≤8k 主干 + seam 单注册表"。**v2 裁决：采纳 opencode 的终态方向（500 行级 trunk），但采纳 codex 的迁移节奏**——分 P0-P5 增量演进、每步全量测试绿，而不是另起 trunk/ 新骨架并行跑两套。理由：2632 个现有测试是灵克最大资产，另起骨架会在迁移期失去回归保护；灵元 1.0 的"激进"应体现在终态主干厚度与插片纯度，而非迁移方式的一步到位。

---

## 五、优化路线 v2（P0-P5，每步机械验收）

### P0 死代码销账 + 契约冻结（3 天）⚠ E 类最优先
1. E1 修复：`_loop_detector` 主干 __init__ 显式初始化 + 新增回归测试（连败 2 次 → denial_abort 触发断言）
2. E2 修复：`_mock_search` fail-closed（评审定二选一），禁止 confidence 1.0 假证据
3. E3/E4/E5：死插片删除或显式降级；ACP 未连接显式报错
4. 架构守卫测试上线：禁止 core import 插片实现（白名单只缩不放）、禁止新增 sys.path.insert、禁止 lazy import 净增长、禁止新 dict-判错
5. C1/C2：版本统一、key 出 config
6. wiring manifest v1：30 对象装配关系显式记录（只记录不倒转）

**验收**：E1/E2 有红→绿测试；守卫测试进 CI；全量 pytest 绿。

### P1 补丁尸体清偿（3 天，纯删除零风险）
B1-B6 全部 + C3/C4/C5 可清项 + D1（CI 上线）+ scripts/ 死代码处置（据 K2 预算 1,500 行）。
**验收**：每删一项列对照表；全量绿；`.audit/` 红灯逐条销账；B6 删除前列清单获用户确认词。

### P2 倒转装配（结构债主攻，1-2 周）
1. QueryEngine 拆解：循环内核留主干，30 对象由 wiring manifest 注入，插片自注册
2. E6 七处倒装下沉 runtime 装配层（与上同批）
3. CodingRuntime 拆 mixin：10 组工具 manifest 化，execute_tool 管线留主干
4. api.py 门面瘦身
**验收**：守卫白名单只缩不放；每迁一插片跑对应测试；e2e 76 用例绿；"hello provider"演练验证主干 diff=0。

### P3 状态归灵忆（2 周）
15 个状态模块（~7k 行）迁 records/events；type_registry 扩 type（主干零表变更）；五层记忆保留为灵忆之上的策略插片。
**验收**：迁移对照表；双写期后切单写；R2/R3 行为指标回路在新状态层重新合闸。

### P4 交互层收口（1 周）
cli/app.py 拆分；webui-server audit.rs 接线或弃用；deploy 单元化。
**验收**：cli 复杂度红灯销账（347→≤30）；e2e 绿；周审计新增 0。

### P5 回路验证（持续）
- 主干 ≤5,000 行、总包 ≤38,000 行（先还债不加功能）
- 测试:源码 ≥1:1，主干 100% 契约覆盖
- 自优化 daemon 以本重构为第一个实战对象（每阶段行数/依赖数/红灯数自动入册）
- 全程 prechange 快照 + lefthook 双钩子 + 灵督复审

---

## 六、风险与回滚

| 风险 | 缓解 |
|---|---|
| 大拆回归 | P0 守卫先行 + 每步独立提交 + prechange 快照 |
| 灵忆迁移丢状态 | P3 双写期 + 迁移对照表机械核对 |
| 族内协作阻塞 | BusResponder 路径 P2 前不动，各阶段声明影响面 |
| E 类修复引发行为变化 | E1/E2 各带显式行为变更声明（熔断从无到有、factcheck 从假阳到 fail-closed），单独评审 |
| **写入事故重演**（本轮实测发生：v1 曾被本会话写入事故覆盖为 3 行乱码） | **大文件写入一律分片+拼接，写后 wc/head/tail 验证行数**；lefthook 增加"单次写入行数骤降 >80%"告警 |

## 七、待族内定夺

1. E2 的二选一（fail-closed 抛错 vs 显式 found=False）请灵通/灵研裁决
2. 灵忆是否接受 type_registry 扩容（约 10-15 个新 type）
3. webui-server(Rust)/webui(TS) 是否纳入 LACP（当前仅覆盖 Python 侧）
4. B6 + scripts/ 清理清单需用户确认词
5. 五 seam 契约 v1 是否升格为族级标准

## 八、与既有文档的关系

- **取代** v1 提案（分歧消解 + 新增 E 类债 + P0-P5 重排）
- **修正** `docs/theory/LINGYUAN_1.0_REFACTOR.md` 一处事实（K3：770 行缺陷是死代码而非误触发）与一处路径（K1：capability_seam 在 lacp/ 不在 engine/）
- **修订建议** GAP_ANALYSIS_20260821.md（C4）
- 评审通过后进 ROADMAP 作为 v0.6.0 主线

## 九、一句话总结

三份报告 + 本方 v1 各对一半；**实测裁决后的事实只有一套**：主干肥大（A）、补丁尸体（B）、契约漂移（C）、回路缺传感器（D）之外，还有最危险的 E 类——**死插片制造的"能力存在"假象**。灵元 1.0 重构不只是删代码，更是让每一条"能力"重新通过缝接线、每一次流转重新经过校验位。

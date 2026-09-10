# 灵元 1.0 重构规划 v3（终审版 · 可实施）

- **提案人**: 灵克 (lingclaude)，2026-09-09 四方审计会审后终稿
- **状态**: OPEN — 待族长批准后，下一次会话开始实施
- **版本链**: v1(事故恢复存档) → v2(三方消解) → **v3(四审计吸收+分歧裁决+可实施)** — v3 取代前两者作为唯一执行基准
- **审计输入**: codex(K1-K6 复测通过+R1 矛盾+R2 UNVERIFIED) / opencode(6 断言实锤+E4 文件名纠错+commit 建议) / atomcode(20+ 项实测+E4 修正+C2 升级建议+审计降调声明) / claudecode(K3 采纳确认)

---

## 〇、v3 相对 v2 的变更（审计吸收表）

| # | 变更 | 依据 | 性质 |
|---|---|---|---|
| V3-1 | **裁决 R1 主干目标矛盾**：分两级——P2 验收线 ≤5,000 行（本提案硬验收）；500 行级 trunk 降为 P5+ 终态愿景（不作本提案验收） | codex R1 | 必修 |
| V3-2 | **E4 修正**：文件名 governance/router.py → governance/governance_router.py；"调不通真引擎" → "默认 engine=None 静默降级 lifecycle（有注入设计意图但从未被注入）"；处置改"接真引擎或 engine 必填"，不再建议删除 | atomcode + 本轮复测(governance_router.py:47-52) | 必修 |
| V3-3 | **E1 补证完成**：`_loop_detector\s*=` 全库（源码+tests）零命中；coding.py 内无 setattr 动态路径（全库 setattr 仅测试 mock CHECKPOINT_DIR，无关）。E1 从"静态推断"升级为"穷举确认"；保留一行注：运行时行为验证在 P0-3 测试改造中顺带完成 | 本轮补测 exit=1 | 补证 |
| V3-4 | **E6 从抽检升级为全量穷举**：core/ 目录 grep 全部 `lingclaude.engine` import，恰好 7 处 / 4 文件，无遗漏 | 本轮穷举 | 补证 |
| V3-5 | **口径统一**：scripts/ 记 **5,128 行（py+sh）**；webui-server 1,183；api.py 879；lazy import 145 | opencode 口径 + atomcode 复测 | 统一 |
| V3-6 | **C2 明文 key 从 P0.5 提到 P0.1**（先撤 key 再谈架构） | atomcode | 必修 |
| V3-7 | **R2 消解**：v2 中仅 E4 标注 UNVERIFIED，本轮已补测（V3-2），A-E 总账不再有 UNVERIFIED 项 | codex R2 | 消解 |
| V3-8 | **审计方法论自限**（引 atomcode 自白入提案）：定点抽查≠通读。故本提案所有 P0 动刀文件（config.yaml/fact_checker.py/coding.py/acp.py）在实施第一步先通读再动手 | atomcode 降调声明 | 新增纪律 |
| V3-9 | **opencode 两条意见采纳**：① 两份前版提案的 git 入库列为实施第一步（需用户批准）；② v1 头部加"已由 v3 取代"标注 | opencode | 新增 |

---

## 一、哲学基准（不变，四方共识）

> 灵元 1.0：减薄不变的是主干，所有变的是插片。
> 尺子四问：主干薄了吗？变化是插片吗？流转有校验吗？流转有痕吗？
> 比 DSH 更激进之处：状态主干是全族共享的灵忆 2T3A（非单进程私有），插片协议是 LACP 能力缝（非框架绑定），且治理/自优化本身也是插片（DSH 的 Cordis 不含治理与自优化）。

**主干目标分级（V3-1 裁决）**：
- **P2 验收线（硬）**：主干（core/ 剩余 + query_engine 循环内核 + 门）≤ 5,000 行，由架构守卫测试机械判定
- **终态愿景（软）**：500 行级显式 trunk——留待 P5 后依 P2-P3 实际经验再议，本提案不以其为验收，避免重蹈"哲学照过≠内化"（SESSION83 教训）

---

## 二、基线（2026-09-09 四方交叉复测后定稿，含口径）

| 部分 | 行数 | 口径说明 |
|---|---:|---|
| 主包 lingclaude/ | 43,586 / 183 文件 | 四方实测一致 |
| tests/ | 32,370 | 一致 |
| webui-server/ (Rust) | 1,183 | atomcode 口径（v1/v2 记 1,153 系 wc 漏计） |
| webui/ (Preact+TS) | 11,651 | v1 实测 |
| scripts/ | 5,128 | **py+sh 合计**（py-only=4,805，V3-5 统一） |
| 关键大文件 | app.py 1,910 · coding.py 905 · api.py 879 · query_engine.py 825 | atomcode 复测口径 |

**四方审计可信度声明**（V3-8）：K1-K6 / E1-E6 / 基线 20+ 项声明经三方独立复测，误差仅个位行数，无虚构。atomcode 明示其审计为"定点抽查非通读"——本提案接受该自限：**P0 所有动刀文件实施前先通读**（见 §五 P0 门禁）。

## 三、技债总账 A-E（终版，无 UNVERIFIED 项）

### A. 结构债（主干肥大）
| # | 项 | 位置 | 期 |
|---|---|---|---|
| A1 | QueryEngine ~30 协作对象硬编码装配 | core/query_engine.py:146-235 | P2 |
| A2 | CodingRuntime 10 mixin / 905 行 | engine/coding.py | P2 |
| A3 | app.py 单文件 1,910 行（交互循环 680 行） | cli/app.py | P4 |
| A4 | sys.path.insert 硬接姊妹包（query_engine.py:50/58/60/75 已逐行确认） | core/ | P2 |
| A5 | api.py 门面过载 879 行 | api.py | P4 |
| A6 | core→engine 倒装 **7 处/4 文件（V3-4 穷举定案）** | query_engine:26 · tool_executor:13 · tool_call_executor:77 · mcp_tools:11,64,122 | P1→P2 |

### B. 重复/双轨债
| # | 项 | 处置 | 期 |
|---|---|---|---|
| B1 | _execute_tool_legacy 双路径（coding.py:792 已确认） | 删 legacy | P1 |
| B2 | 双 bash 执行器 | 留 native，lingxi 通道降 MCP 插片 | P1 |
| B3 | 双 retry（model/ vs llm_proxy/） | 合一 | P1 |
| B4 | ToolExecutor vs ToolCallExecutor | 合一 | P2 |
| B5 | Result[T] vs dict 判错 | 工具层迁 Result | P2 |
| B6 | 根目录垃圾（=1.4.0/=6.1.1/null/CRUSH.md×3/config.yaml.bak×2 等） | 清单确认后删 | P0 |

### C. 漂移债
| # | 项 | 处置 | 期 |
|---|---|---|---|
| C1 | pyproject 0.3.0 vs VERSION 0.5.0 | 统一 0.6.0-lingyuan + CI 检查 | P0 |
| C2 | **config.yaml:17 明文 key（atomcode 升级：真实 key 已入库）** | **P0 第一动作：撤 key→env/key_store→key 作废轮换** | **P0.1** |
| C3 | AGENTS.md 索引指向不存在文件 | 重建索引 | P1 |
| C4 | GAP_ANALYSIS 两处事实错误（atomcode compaction 误标"未见"；dsh 219 包误记 ~50） | 修订 | P1 |
| C5 | .audit FAIL（L0 硬编码 IP×5、app.py 复杂度 347 vs 阈值 30） | 逐项销账 | P1/P4 |

### D. 工程化债
| # | 项 | 处置 | 期 |
|---|---|---|---|
| D1 | 无 CI 测试工作流 | GitHub Actions pytest 离线跑 | P1 |
| D2 | deploy 仅 1 systemd 单元 | 逐个单元化 | P4 后 |
| D3 | 测试:源码 0.74:1，主干覆盖薄 | 每迁一插片补契约测试 | 持续 |
| D4 | webui-server audit.rs 未接线 | 接线或弃用 | P4 |

### E. 死接线/假阳性债（本轮四方会审的独有价值）
| # | 项 | 证据 | 处置 | 期 |
|---|---|---|---|---|
| E1 | 5b 熔断从未生效：coding.py:770 hasattr(self,"_loop_detector") **永假**（V3-3 穷举：赋值零命中+无动态路径） | coding.py:770-777 | 主干 __init__ 补初始化；补"熔断触发"行为测试；原有 observe_denial 测试改造成可触达 | **P0.2** |
| E2 | fact_checker._mock_search 返回 found=True + confidence 1.0 假证据，check() 以 ≥0.5 消费 | fact_checker.py:300-303 | **审计意见采纳 codex：fail-closed 抛错**（found=False 仍污染下游分支；显式异常符合 H1） | **P0.3** |
| E3 | create_hybrid_provider + hybrid_router + local_provider 死插片（仅 __init__.py:26-30 引用） | V2 定案 | 删或转 manifest 惰性插片 | P1 |
| E4 | governance_router **默认 engine=None 静默降级 lifecycle，有注入设计但从未被注入**（V3-2 修正措辞） | governance/governance_router.py:47-52 | 二选一：调用点注入真引擎 / engine 参数改必填（不再建议删除） | P1 |
| E5 | ACP subagent 默认 8901 端点静默空转 | acp.py:27 | 显式 set_endpoint + 空转时告警 | P1 |
| E6 | core→engine 倒装（同 A6） | 7 处穷举定案 | seam 化 | P1-P2 |

**E 类哲学定性**（四方共识）：E1/E2/E5 = "有插片形态的代码，无插片接线的实质"——比没有插片更糟，因为它伪装成已实现（违反 H1 诚实）。修复优先级高于一切结构债。

---

## 四、主干定义（P2 验收口径）

主干 ≤5,000 行，只含四件永不变的东西：
| 件 | 不变式 |
|---|---|
| 出入：turn 循环（query_engine 循环内核） | 不知道任何具体插片的存在 |
| 流转：session_journal + 灵忆 2T3A 唯一状态出口 | model-visible means logged |
| 缝：LACP 五 seam + ToolRegistry（manifest 注册） | 新增插片=新增 manifest，主干 diff=0 |
| 门：PermissionContext + VerificationGate + bash 黑名单 + MV-1 | 钟表域，不可插拔不可配置绕过 |

插片清单（manifest 注册，禁止主干 import）：providers/路由、30+ 工具、记忆策略层、governance、self_optimizer、mcp、bus responder、webui/api/cli 交互、stt/lsp/subagent 后端、llm_proxy。

## 五、实施路线（下一次会话开始，P0 全部为小刀急治）

### P0 急治（1 个会话内可完成，顺序即优先级）
| 步 | 动作 | 债 | 验收 |
|---|---|---|---|
| P0.0 | **门禁**：通读 config.yaml / fact_checker.py / coding.py:700-800 / acp.py 全文（V3-8 纪律）；两份前版提案 + v3 一并 git commit 留底（需用户批准）；v1 头部加"已由 v3 取代"标注 | opencode 意见 | commit hash 落档 |
| P0.1 | **撤明文 key**：config.yaml:17 改 env 引用 → key 入 key_store → 旧 key 作废轮换 → git 历史清洗评估 | C2 | grep 全库无明文 key |
| P0.2 | **修活熔断**：coding.py 补 `self._loop_detector = _ToolLoopDetector(...)` 初始化（5 行级）+ 新增行为测试证明 5b 熔断真实触发 | E1 | 红→绿测试 |
| P0.3 | **mock 假阳性 fail-closed**：_mock_search 改抛 RuntimeError（含修复指引） | E2 | 原 mock 依赖测试改为"显式声明不可用" |
| P0.4 | **立架构守卫测试**：禁 core→engine import（7 处白名单只缩不放）/ 禁新增 sys.path.insert / 禁 lazy import 净增长 / 禁 dict-判错新增 / **大文件写入行数骤降>80% 告警**（写入事故防复发，入 lefthook） | A6/E6 增量 | 守卫测试上线即红名单固化 |
| P0.5 | **版本统一** 0.6.0-lingyuan + 根目录垃圾删除清单（B6，删除前逐项列名获用户确认） | C1/B6 | 三处版本一致 |

### P1 补丁尸体清偿（3 天，纯删除/接线）
B1-B4 双轨合一 · E3/E4/E5 死插片三连处置（E4=注入真引擎或 engine 必填） · C3/C4 文档修订 · D1 CI 上线 · C5 红灯逐条销账

> **P1 处置实录（2026-09-10 第一波完工，以本实录为准）**
> - **B1 ✅删除**（coding.py 924→888 行，零调用方）
> - **B2 →P2**：实况与计划不符——bash.py(native) 与 bash_lingxi.py 双双注册在用，非死轨；合一动主干，按计划本身归 P2
> - **B3 重新定性**：llm_proxy 并非"在用双 retry"，是**未接线子系统**（主干零调用仅自测试）；已加 B3 定性标注于 `model/llm_proxy/__init__.py`，接线/归档留 P2 manifest
> - **E3 ✅摘导出**：`create_hybrid_provider` 移出 `model/__init__`，hybrid_router/local_provider 标 EXPERIMENTAL 保留（10 个有效测试在覆盖），物理删除留 P2
> - **E4 ✅接真机**：比文档更糟的实况——Router 是"幻想门面"（propose/vote/resolve 等转发到引擎上不存在的方法）。已重写：propose 接真 GovernanceEngine、vote/resolve 显式 NotImplementedError、状态三接口按真实字段接线；`governance_v2.create_proposal` 新增 notify 开关（机器路由默认灵信零触碰，防止 bus 属性惰性连总线轰炸议会）
> - **E5 ✅终结空转**：默认 8901 摘除、set_endpoint 显式注入、未配置 fail-closed+告警去重
> - **C3 ✅重建索引**：AGENTS.md 6 断链→2 真实文件（原文件 git 历史即无，疑从未入库）
> - **C4 ✅三处勘误**：atomcode compaction 实存（42 个 rust 文件）、路径应为 atomcode-src、DSH 实测 248 包
> - **C5 部分收口**：IP 红灯实为 loopback 合法默认值（最近审计 l0_findings 已空，E5 顺手消灭 8901 残留）；**app.py 复杂度 347 留 P4**（拆消费者时自然归零）
> - **D1 ✅上线**：`.github/workflows/ci.yml` 四 job（compileall / ruff F821,F811 / smoke import / tests/unit 定向单测观察期），全层本地演练绿；全量 pytest 因网络依赖不上 CI，hermetic 边界留待观察期确认
> - 波及面验证：175 passed / 2 skipped / 0 failed；新增 1 个环境守卫 skip（libtorch_cuda 沙箱映射失败，stash 基线证实非回归）

### P2 倒转装配（1-2 周，结构债主攻）
QueryEngine 30 对象 → wiring manifest 注入 · CodingRuntime 10 mixin → manifest 工具组 · A4 sys.path.insert → LACP seam · E6 七处倒装 → seam 化 · api.py 门面第一步瘦身 · **验收硬线：主干 ≤5,000 行 + 新增"hello provider"插片演练 0 主干改动**

### P3 状态归灵忆（2 周）
15 个状态模块（~7k 行）迁 2T3A records/events/transition · 五层记忆降为灵忆之上的策略插片 · 迁移对照表 + 双写期一个版本周期 + 行为指标回路重新合闸

### P4 交互层收口（1 周）
app.py 1,910 行拆消费者（复杂度 347→阈值内） · webui-server audit.rs 接线或弃用 · deploy 单元化

### P5 持续回路
自优化 daemon 以本重构为第一个实战对象（每阶段行数/依赖/红灯自动入册）——验证回路合闸不是又一次空转 · 500 行级 trunk 终态愿景依 P2-P3 实际经验再议

## 六、风险与回滚

| 风险 | 缓解 |
|---|---|
| P0 修活熔断引发既有行为变化 | 单独 commit 可回滚 + prechange_snapshot.py |
| 灵忆迁移丢状态 | 双写期 + 迁移对照表机械核对 |
| 重构期功能冻结 | BusResponder/analyzer 路径 P2 前不动 |
| 大文件写入事故复发 | P0.4 守卫 + 分片写入+写后验证流程（本轮已实战验证） |
| "哲学照过≠内化" | 验收全部机械化（守卫测试/行数/红绿），不留文档达标口子 |

## 七、待定夺（需用户/族内批准，实施前冻结）

1. **P0.0 的 git commit 授权**（三份提案文件 + v1 标注，一次提交）
2. **P0.1 旧 key 作废轮换**（涉及在用的 API key，需用户操作或授权）
3. P0.5 B6 根目录删除清单逐项确认
4. P3 灵忆 type_registry 扩容（约 10-15 个新 type）是否接受
5. webui-server(Rust)/webui(TS) 是否纳入同一插片协议（当前 LACP 仅覆盖 Python 侧）
6. 五 seam 契约 v1 是否升格族级标准（供 lingcode/lingshell 复用）

## 八、一句话

先止血（P0 五把小刀：撤 key/修熔断/堵假证据/立守卫/清版本），再拆焊（P1-P2 把焊死的插片拔下来装回缝里），再归位（P3 状态进灵忆）——每一步都机械可验收，2632 个测试全程护航。

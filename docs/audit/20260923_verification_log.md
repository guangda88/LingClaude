# 2026-09-23 文档核查修正记录（verification log）

> **产生方式**：对 `docs/audit/` 全部 11 个文件（主报告 1036 行 / PROMPT 304 行 / 总报告 307 行 / INDEX 229 行 / 7 个 ROLE 共 1231 行）做逐行核查 + 272 处 file:line 引用验证 + 关键数字实测。
> **核查时间**：2026-09-23（git HEAD=0d0ec82，晚于原报告快照 8499089）
> **核查工具**：grep / glob / python3 json 实测 / git ls-tree 对比快照 / 精确行号读取

---

## 一、已修正的漂移（10 处）

| # | 文件 | 原值 | 实测 | 修正后 | 类型 |
|---|------|------|------|--------|------|
| 1 | INDEX:13,174 + total_report:286 | "1080+ 行" | 1036 行 | **1036 行** | 行数漂移 |
| 2 | PROMPT:25,208 | "705+ 行" | 1036 行 | **1036 行** | 行数漂移 |
| 3 | PROMPT:26,209 | "427 行"（铁律文档） | 428 行 | **428 行** | 行数漂移 |
| 4 | 主报告:25,520,528,572,757,929,988 + PROMPT:45 + total_report:210 + INDEX:116 | "248（146 record + 102 豁免）" | 8499089 快照实为 **249 文件（134 record + 113 豁免 + 2 参考资料 .md）** | **249（134+113+2）** | 台账计数漂移 |
| 5 | 主报告:28,48,90 | "102 条 arch_exemption" | **113 条**（active 108；M1:core 目录恰好 102） | **113 条（active 108）** | 豁免计数漂移 |
| 6 | 主报告:598 | "arch_exemption/M3:* 102 条" | M3 豁免仅 8 条；102 是 M1:core 目录 | **M1:core 102 条（全量 113 含 M2/M3）** | 目录错配 |
| 7 | 主报告:27,44,595 | "65 个领域词" | core_vocabulary.yaml 实测 **53 词** | **53 个领域词** | 词表计数漂移 |
| 8 | 主报告:44 | "core/ 113 个 .py" | 实测 **105 个 .py**（递归） | **105 个 .py** | 文件数漂移 |
| 9 | 主报告:47 | "test_iron_law_guards.py 587 行 + test_p04 638 行" | 实测 **588 + 652 行** | **588 + 652 行** | 行数漂移 |
| 10 | 主报告:175,234,238 | "conftest.py:28-29" | J5 反例实际在 `tests/agents/conftest.py:28-29` | **tests/agents/conftest.py:28-29** | 路径歧义 |
| 11 | 主报告:310,880 + ROLE_auditor:42 | "agent_lingxi/manifest.agent.json" | 实际路径 `lingclaude/plugins/agents/agent_lingxi/manifest.agent.json` | **完整路径** | 路径歧义 |

## 二、口径说明（非修正，记录备查）

1. **WiringSpec "67 处"**：`wiring.py` 中 `WiringSpec` 词频（含类型注解/引用）67 次，实际实例化调用 `WiringSpec(` 60 处 + wiring_manifest.yaml 59 条目。文档用"67 处"指词频口径，保留原值但此口径存在歧义。
2. **"146 record"**：原数字由"248 - 102 豁免"倒推。修正为 134 record（8499089 时点非豁免 JSON 134 + 2 参考资料 .md = 136 非豁免文件）。
3. **arch_ledger 总条数口径**：249 = 134 record + 113 豁免 + 2 参考资料 .md。若只数纯 JSON 则为 247。

## 三、核查验证为准确的引用（抽样关键项）

| 引用 | 验证结果 |
|------|---------|
| `plugin_manifest.py:32` regex 反向（P0#3 核心证据） | ✅ `"name": {"type":"string","pattern":"^[a-z0-9][a-z0-9_-]*$"}` 不含 `/`，与 N3 目标反向 |
| `seam.py:202-212` SeamRegistry.register 无强制 | ✅ 仅校验 name 非空，无前缀校验 |
| `manifest_lock.py:49` REQUIRED_MANIFEST_FIELDS | ✅ `("name","trust_level","plug_level")` |
| `policy_loader.py:68` 防路径穿越 regex | ✅ 与 plugin_manifest 目的不同（引用区分正确） |
| `tests/agents/conftest.py:28-29` J5 反例 | ✅ `except Exception: return` 静默吞错 |
| `seam.py:35-48` 11 类 SeamType / `57-68` PLUG_LEVELS 11/11 | ✅ |
| `work_claim.py` 五原语 L36/60/75/92/102 | ✅ bind/renew/release/holder/check_paths |
| work_claim 16 项测试 = 8 锁 + 8 节点 | ✅ test_work_claim.py 8 + test_worktree_node.py 8 |
| `datalog_aggregator.py:38` SNAP_TYPE=arch_m6_snapshot | ✅（坐实 P0#5 datalog 鸠占鹊巢） |
| ci.yml 无 seam_trend_inspect / m6 | ✅（坐实 M6 未入 CI） |
| `self_audit_trigger.py:41` 含 seam_trend_inspect 但不执行 | ✅ 仅 git/pytest 两个 subprocess.run |
| 9 条 work_claim JSON 全部 expires_at 过 3 天 | ✅ 相对快照 2026-09-23 过期 2.7-3.1 天 |
| first-seam-recycling-review.json runtime_count=26 | ✅ 6 插件载体 + 20 工具动态注册，留任 |
| M1/M2/M3/M5 入口行号 322/373/458/511 | ✅ test_iron_law_guards.py 对应函数 |
| arch_debt 29 条 | ✅（与 PROMPT 一致） |

## 四、未发现错误的领域

- P0 #1-#5 的全部证据链（N5 零落地 / N4 时效查被动 / N3 反向 regex / N1 在册未实现 / M6 从未自动跑）
- 三方对照表（total_report §二）
- 13 条欠账 P 级排序与工时预估
- prompts/ 角色分工 pipeline 与依赖关系
- 修剪司法闭环故事（runtime_count=26、留任裁定）
- work_claim record-as-lock 机制描述（五原语 + TTL + 16 测试）

## 五、核查局限

1. 数据快照：原报告 as_of=2026-09-23T07:43:52（head=8499089），本次核查在 head=0d0ec82 进行，arch_ledger 在 8499089..HEAD 间新增 4 个 JSON（arch_audit_task ×3 + arch_debt ×1），但本文档所有 arch_ledger 计数均回退到 8499089 快照口径（git ls-tree 验证）。
2. 未对 7 个 ROLE 的每个段落做语义级复读（仅核对标题结构 + 引用锚点 + 职责边界），但关键锚点已抽查验证。
3. 未运行 pytest 全量（避免副作用）；仅做静态验证。

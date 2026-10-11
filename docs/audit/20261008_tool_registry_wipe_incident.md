# 20261008 工具注册表清空事故报告

- 日期：2026-10-08（事发 16:55，恢复 19:27，防线落地当日）
- 调查/撰文：lc（灵克）会话 c18977e81f9e47a5
- 状态：**已结案**（恢复、验证、三道防线落地、对质完成、通道定性完成）

## 一句话摘要

未提交改动把 `lingclaude/engine/tool_registration.py` 的 `SPECS` 表清空为 `()`
（净删 338 行，文件剩 1897 字节空壳），registry 侧 schema 缺位，此后所有带参
工具调用参数在链路中被丢成 `{}`、全部校验失败；无参工具不受影响。故障持续
2.5 小时，≥4 个新会话开场即全灭，各烧掉数轮预算。

## 时间线（全部实测）

| 时刻 | 事件 |
| --- | --- |
| 16:37 → 17:13 | AtomCode 在工作区进行 relay/budget 工作（`787710d` → `f6d8ce4`） |
| **16:55:51** | `tool_registration.py` 被写为 1897 字节空壳（mtime 实测），SPECS → `()` |
| 18:00 起 | 新启动会话带参工具全灭：18:33、18:58、19:20、19:21（本会话）≥4 个受害 |
| 19:27:24 | 经用户授权 `git checkout --` 还原，文件回到 20396 字节 |
| 当晚 | 进程重启后实测 `len(SPECS) == 37`、`SPECS = 37`、工具面（带参/无参）全绿 |

## 根因链

```
SPECS = ()                      # 生成文件被清空且零回填
  → registry.register 无 schema 可装
  → 模型侧工具面只剩 MCP 插片（同名工具仍在，但 registry 侧参数定义缺位）
  → 带参调用参数在半路被丢成 input_value={}
  → pydantic 必填字段校验全灭；无参调用正常（控制组实证）
```

## 成因认定（三假说 → 收敛）

| 假说 | 证据 | 结论 |
| --- | --- | --- |
| H1 有意清空迁移 | `mountable_agent_kernel_proposal.md` 有 SPECS→manifest 迁移规划 | 排除：零回填物、未提交、不符合提案「零行为变化」路径 |
| H2 手工误删 | 1897 字节空壳形态 | 弱：手工删 338 行却保留模板骨架，不符合手误特征 |
| H3 **提取脚本空写** | `scripts/extract_tool_specs.py:150` `OUT.write_text(...)` 无守卫直写——提取到 0 条也会忠实写回「模板骨架」，形态与 1897 字节空壳精确吻合 | **主假说**：16:55 某会话运行提取脚本时提取结果为 0（SRC 解析失败/跑错状态），脚本忠实地写入了空表 |

H3 余留问题（谁触发、为何提取为 0）在 AtomCode 会话 transcript 中，本侧不可达。

## 责任

- **肇事方：AtomCode（置信度高）**——事发窗口内唯一有工作区提交证据的会话；
  同期另外三个活跃会话（CC 审计 / 长寿 panel / codex 研究）全部书面否认并落盘：
  - `docs/AUDIT_20261008_TASK_INSERTION_CC_VS_LC.md:45`（「被他人改动」）
  - `docs/handover/20261008_changshou_panel_v21_handover.md:9`（「非本会话所为」）
  - `docs/research/codex_turn_steer_20261008/ANALYSIS.md:565`（「他人修改，未触碰」）
- **意图：未定案**（H3 成立则为脚本空写的间接受害者，性质接近事故而非蓄意）。
  待办：问 AtomCode「16:55 前后是否运行过 `scripts/extract_tool_specs.py` 或改动过
  `tool_registration.py`」。
- **放大器**：注册表无任何下限防线——空表可被静默加载、静默运行。

## 数字勘误（后续引用以此为准）

- 净删 **338 行** ✅（atomcode 报告与我方实测一致）
- 工具数以 **实测 37** 为准（用户 `python3 -c` 实测）；atomcode 报告的「34」有误
- 历史演进：9/14=32（`OPTIMIZATION_LINGYUAN_S1_S6_20260914.md:78`）→ 9/23=35
  （`20260923_iron_law_self_audit.md:644`）→ 10/8=37（实测）

## 防线（已落地，三道闸）

| 闸 | 位置 | 行为 |
| --- | --- | --- |
| 写侧 | `scripts/extract_tool_specs.py`（`OUT.write_text` 之前） | `len(specs) < 30` → `sys.exit`，拒绝覆盖 |
| 读侧·运行时 | `register_all_tools()`（模板 FOOTER 与生成文件双处同步） | `len(SPECS) < 30` → `RuntimeError`，拒绝启动 |
| 读侧·CI | `tests/test_p15_mixin_specs_contract.py` 契约 4/5/6 | 空表、闸门失效、脚本被去闸，任一立即红 |

同步修正：`docs/AUDIT_20261008_TASK_INSERTION_CC_VS_LC.md` §0.2 补「已回滚」后续。

## 结案附记（2026-10-08 深夜，机制定案 + 责任修正）

**修正：正文"肇事方 AtomCode（置信度高）"作废。** atomcode 提交全族 datalog 排查（40+ 项目、16:30–17:00 窗口逐写落点核对），加上 cc/oc/codex 三方书面否认与写落点排除——**所有 agent 均排除**。用户确认 19:27 恢复系本人手动执行。责任通道修正为：**非 agent 通道（人为/实验性操作），身份在无 auditd/inotify 实时采样的条件下不可追溯**。本报告先前基于时间窗的唯一活跃提交者推断，系"活跃≠肇事"的归因错误，记录在案。

**机制定案（字节级证明，可复现）：** `/tmp/toreg.broken.20261008_192723.py`（坏壳本体，1897B）与「用 ast 提取 `scripts/extract_tool_specs.py` 的 HEADER/FOOTER 常量、按 main() 144–157 行拼装逻辑复刻 specs=0 输出、并将 FOOTER 剥离本日新增读侧闸门段」的重建结果**逐字节一致**。结论：**16:55 空壳是生成器对"_setup_tools 存在且语法完整但含 0 条 ToolDefinition"状态的忠实写入**——不是 IDE 误存、不是手工删除（二者无法逐字节复现生成器拼装特征）。

**案发必要条件由此反推：** 生成空表 ⇒ 生成那一刻 `coding.py._setup_tools` 处于"零注册"状态。而 coding.py 未留任何 M 痕迹 ⇒ 存在"临时改变 coding.py → 跑生成器 → 恢复 coding.py、漏恢复 tool_registration.py"的操作序列。叠加坏文件 btime=mtime=16:55:51（inode 整体更换，`write_text` 原地写不换 inode）⇒ 安装方式为 cp/mv 或等价整体覆盖，符合"生成副本→对比实验→覆盖安装"的工作流特征。

**无法追溯项（证据边界，诚实封存）：** 16:55:51 执行覆盖的进程/人（无系统级审计）；当时 coding.py 的瞬时内容（未被保存）。旁证：用户 19:13 曾从 `~`（非 git 目录）执行 `git diff > /tmp/specs_deletion.patch` 得 0 字节文件（`bash_history:499`），系用户自查痕迹，无涉肇事。

**结案状态：已恢复（SPECS=37 实测）✅；机制已证明 ✅；通道已定性（非 agent）✅；身份不可追溯（接受边界）✅。三道防线已落地并测试（写侧闸门/读侧闸门/CI 契约）。教训归档：二手报告数字必实测（本案 34→37）；时间窗活跃度不构成责任证据；生成器必须带空值守卫。**


## 独立复现记录（2026-10-08 深夜，会话 1b8c5478/灵克）

上文「逐字节一致」声明已由本会话独立复核（不经原始定案会话）：

- **方法**：`git show HEAD:scripts/extract_tool_specs.py` → AST 提取 HEADER/FOOTER 常量 → 按 `main()` 拼装逻辑（`[HEADER, "SPECS: tuple[ToolSpec, ...] = (", ..., ")\n", FOOTER]` 以 `\n` join）以 specs=0 重建；
- **结果**：HEAD 常量重建产物 sha256 前缀 `a54ab0089487ee18`（1897B），与坏壳实物 `/tmp/toreg.broken.20261008_192723.py`（同前缀，实测 1897B）**逐字节一致**；
- **反向佐证**：改用工作区常量（含今日防线补丁）重建得另一指纹，残余差异 24B 全部为防线补丁顺带的模板空行微调——证明坏壳生成时脚本处于「补丁前」状态，与 16:55 早于今日补丁的时间线自洽；
- **结论**：机制定案「生成器对零注册状态的忠实写入，非 IDE 误存、非手工删除」经独立会话复核通过。

## G14 修复（2026-10-08 晚，事故复核的顺带产出）

CC 复核注册表时发现的衍生缺陷：`todo_write` 注册表 scope 已于 9/17 修为 `write`
（全量删除+重插任务存储），但 `permissions.py` 的 `READ_ONLY_TOOLS` 残留其名，
同一工具在审批闸门（名单判只读自动放行）与 plan_mode/prior_verifier（scope 判写域）
两处事实相反。

修复（对齐注册表真源）：
- `lingclaude/core/permissions.py`：`todo_write` 移出 `READ_ONLY_TOOLS`，注释记录单源纪律；
- `tests/test_skill_parser.py`：只读断言同步反转（`todo_write` → `is_read_only_tool is False`）；
- `tests/test_p15_mixin_specs_contract.py` 契约7：READ_ONLY_TOOLS 与 SPECS write/execute 集合互斥不变式（G14 类漂移永不复发的结构锁定）。

验证：契约 7/7、skill_parser+r5+replay 56/56、todo/guard 域 35/35 全绿；
行为冒烟实证 strict/ask 下 `todo_write` 均需审批（ask 落 pending，reason=pending approval）。

## 遗留待办

1. ~~对质 AtomCode~~ ✅ **已完成（2026-10-08 晚）**：atomcode 逐条 bash 命令级自证清白
   （16:55:51 正在修 browser_agg SIGTRAP，该窗口写落点零涉及 engine/）；cc/codex/oc
   亦全部动作级排除（详见结案附记 2）。**通道终版定性：宿主终端层（IDE/手动 shell），
   具体来源不可追溯**——无 auditd/inotify 实时采样，证据到此为止；
2. `add_intel`（灵研）schema 错位为独立缺陷（外层 MCP 挂载清单与服务端实存参数
   不匹配，进程重启与本次修复均不治）——已发现三处：`add_intel`/`poll_messages`/
   `get_stats`，需向灵犀/灵研侧报修单；
3. 本报告落档灵研知识库（待 `add_intel` 通道修复）。

## 结案附记 2：file_history 证据层与终版排除矩阵（2026-10-08 晚）

新证据层：`.lingclaude/file_history/`（992 个写入快照，UTC 时间戳命名）。

- **机制验证**：本文件自身 19:57:17 的写入有档（`T115717Z_tool_registration.py`）；同时段三个外部子进程的非工具链写入（16:56:41/49/54 的 answer_codex/answer_ac/logs_ac）均无档——file_history 只记 lc 工具链写入，机制正常。
- **关键负例**：肇事秒 16:55:51.732 无档（08:55–08:57Z 窗口仅 4 条目：lingke×2/unified_prompt/run_on_host.sh，无 tool_registration）⇒ 肇事写入不走 lc 工具链，任何 lc 会话（含本会话）均排除。
- **瞬时子进程排除**：`run_on_host.sh`（16:55:44 落盘、≈16:55:51 执行）拉起三个带写权限外部 agent，logs_ac/logs_codex/logs_oc 全文 0 次提及 tool_registration/extract_tool_specs/SPECS（更正：此前误判 logs_oc 不存在，实为 panel_20261008_budget/logs_oc.txt，9528B）。
- **定时任务通道排除**：cron/systemd 配置检索对 extract_tool_specs/tool_registration 零引用。

**终版排除矩阵**：①四常驻 agent（datalog 穷举）②cc（transcript 秒级还原）③三瞬时子进程（日志零命中）④lc 工具链整体（file_history 负例）⑤cron/systemd（零引用）——五通道全排除。剩余通道：IDE/编辑器保存、手动 shell（cp/mv/编辑器覆盖）、一次性外部程序。

**终局定性**：肇事写 = 非 agent、非 lc 工具链、16:55:51.732 覆盖式安装（inode/btime 证据）、内容为生成器对 specs=0 的忠实输出（字节级复刻证明）。与"临时清空 SPECS 做对比实验后只恢复了 coding.py、漏了 tool_registration.py"的人工工作流残缺完全吻合。身份不可追溯（无实时审计），通道定性达到排除法完备。

**元结论**：file_history 使 lc 工具链的工作区写入自此有档可查，同类案件可直接查档定责；"外部进程写入不留档"既是本案破案依据，也是写入来源审计防线的现成缺口清单。

## 债务入帐记录（2026-10-08 深夜）

本案全部遗留债务已结构化入账：`data/arch_ledger/tech_debt/20261008_wipe_case_debts.jsonl`（7 笔，JSONL 已校验）。D1（37 条记忆噪声）入帐即清偿（备份 `.lingclaude/project_memory.md.bak-20261008`）；D2/D3/D6 有明确到期日；D4 沿用红名单既有期限 2026-12-31；D5 待用户裁决（建议 10-15 前）；D7（实时审计缺位）为结构性缺口，无到期日，纳入下次架构评审。「不落帐的债静默复利」——本案自身的教训：还账也是防线。

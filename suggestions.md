以下是 四家coding Agent 给lingclaude 下一步的优化建议，请您对照自己的代码，综合考虑所提建议的必要性与可行性，提炼出自己的下一步优化方向，提出切实可行的方案和计划，开始实施。
claudecode ：
 给lingclaude 下一步优化提出客观中肯并详细的建议

Lingclaude 灵元 1.0 下一步优化建议（精读版 · 客观中肯）

▎ 基线：精读 ~5,500 行核心代码（permissions/tool_executor/bash/tool_registration/coding/l7_cognitive/seam/self_optimizer + 对比 atomcode approval.rs 481L + CC sdk-tools.d.ts + 跑通 cost_table.py 修正版）
▎ 原则：每条建议有「问题/证据/方案/优先级/工时估/风险」六要素
▎ 范围：只覆盖精读过的模块，不发明新洞见

---

一、致命短板（必须立即修）

1.1 RepoBench 单文件补全 3/20 vs 头部 7/20（差 4 题）

问题：3/20 客观落后，与 TB 1/6 并列第一、SWE-bench agent_ok=True 形成强反差——长任务/工程任务强，短补全弱。

证据：
- /home/ai/bench/runs/20260912/FINAL_REPORT.md RepoBench 表格 L26-31
- FINAL_REPORT L97 自承：「底层 deepseek-v4-flash 在"短上下文精确补全"上不如 claude 系；可评估切更强模型或加补全特化 prompt」
- 精读 tool_executor.py:121-148：read 走 pipeline→cache→handler（cache 优先，但首次补全时 cache miss 直接走 handler，没有补全特化 prompt）

方案（三选一，可并行）：
1. prompt 适配器：在 _execute_tool_typed 入口新增「补全特化」分支——检测到 read 后跟 edit 立即触发，且无最近 N 轮的同类 edit 时，注入「精确补全示例」到 model prompt（不破坏主对话历史）
2. 多模型任务路由：/home/ai/lingclaude/lingclaude/core/layered_memory.py 已建模 task_router（在 tool_executor.py:399-414 看到 resolve），但 RepoBench 不属于「task 类型枚举」——新增「single_file_completion」意图识别 + 切 deepseek-v3.1/Claude 3.5 Sonnet
3. 本地补全特化微调：用现有 81 provider 评测集训练 deepseek-v4-flash 在「单文件补全」任务上的 finetune（这个工作量大但彻底）

优先级：P0（决定 lingclaude 能否进 coding agent 主流视野）
工时：方案 1+2 共 2-3 周；方案 3 8-12 周
风险：方案 1 加 prompt 可能污染其他任务上下文（需 A/B test）

1.2 修正版 cost_table.py 未真正替换基准版（数据失锚）

问题：精读跑通发现修正版 cost_table.py 在 lingclaude 仓库路径但基准路径仍跑老版——BENCHMARK_REVIEW.md 文档说"已闭环"是预期值不是实测值。下次再跑会再次得到错误结论。

证据：
- 精读实跑：基准版 n_pass=2 / 修正版 n_pass=1，两个 md5 不一样
- /home/ai/lingclaude/benchmarks/results/cost_table.py（md5 cb031f...）是修正版
- /home/ai/bench/runs/20260912/cost_table.py（md5 5460fc...）是基准版
- 没有任何脚本或 systemd 单元在 sync 修正版到基准路径

方案：
1. 加单一权威源：/home/ai/lingclaude/benchmarks/results/cost_table.py 为唯一权威，基准路径改为 symlink
2. pre-commit + 守护 hook：tests/test_cost_table_dedup.py 校验 n_pass ≤ unique passed_tasks
3. 公开声明数据状态：在 BENCHMARK_REVIEW.md 加粗体「修正版已落到 lingclaude/benchmarks/results/cost_table.py；基准 /home/ai/bench/runs/20260912/ 仍为旧版」避免下次误读

优先级：P0（评测系统自洽问题）
工时：半天
风险：低

1.3 request_user_input handler 在 plan_mode 写入时会卡死（隐含 bug）

问题：精读 engine/coding.py:191-246 _request_user_input_handler 实现——input() 阻塞读 stdin。

证据：
- engine/coding.py:226-230：print(prompt, flush=True) + input().strip()——真同步阻塞
- 但 tool_executor._execute_tool_typed 在 _engine.config.max_tool_calls_per_session 限制下是被循环调度
- plan_mode（tool_registration.py:256-263）描述「think without tool execution」——但如果 plan_mode 里调 request_user_input，会真卡（plan_mode 没禁用 request_user_input 工具）

场景：
- 自动化场景（CI、daemon 调用）调 lingclaude 进入 plan_mode 但触发 request_user_input → 永远卡死
- LACP 跨进程调用（engine/coding.py:68 提到 SessionRuntime）—— 远程 agent 通过 LACP 调本机，request_user_input 也卡

方案：
1. PermissionStore 风格的 mode 参数：request_user_input 加 mode="interactive"|"deferred"|"auto"——非 interactive 模式下直接返回 {"ok": False, "error": "no input stream", "answer": None}（已在 EOFError 路径实现 ok: False, error: "input cancelled"——可以扩展）
2. LACP/daemon 通道：参考 atomcode approval.rs:200-209 的 approval_channel_failure_deny 模式——非交互通道调 request_user_input 时显式 fail-closed（区别于 user 真 deny）
3. plan_mode 显式禁用：在 _setup_tools 末将 request_user_input 加入 plan_mode 拒用集合

优先级：P0（影响无人值守场景）
工时：1-2 天
风险：低（仅扩 mode 不破坏现有 interactive 行为）

---

二、核心反超点的强化（保持领先）

2.1 5 层记忆的 L4 SHARED 层实际是声明级，未看真实使用

问题：精读 core/layered_memory.py:30-35 看到 SHARED 层枚举，但 engine/coding.py/core/tool_executor.py 没有任何把经验写入 SHARED 层的代码路径。

证据：
- tool_executor.py:335-344 _archive_dropped_messages 只写 EXPERIENCE 层
- 没有看到 cross_repo_seam.py 或 lacp/cross_repo_seam.py 实际被 _archive_dropped_messages 调用（未精读 cross_repo_seam，但 import chain 缺）
- 唯一跨代理通道 coordination/alert.py::send_lingbus_alert（gray_zone.py:62 引用）是通知不是记忆共享

对比：DSH 的 compaction-tool-result-pruner + 跨 session context 共享是真用；lingclaude 的 L4 是「宣称的 5 层」vs「实际 4 层」

方案：
1. 审 L4 SHARED 层真实使用：grep -rn "MemoryLayer.SHARED" /home/ai/lingclaude/lingclaude/ 看是否有调用方
2. 如确实未用：要么删 SHARED 枚举（如实标 4 层），要么接入 LACP 跨进程缝（lingmessage）真实写
3. 如使用但效果差：增加暴露指标到 self_optimizer（5 个月断路首次合闸，daemon 有快照机制）

优先级：P1（影响"5 层"宣传真实性）
工时：1 天调研 + 1 天修
风险：低

2.2 bwrap 沙盒的「白名单 git 网络失败自动降级」是亮点但有竞态

问题：engine/bash.py:215-233 白名单网络命令失败时自动用「主进程网络域」重试——降级动作无可观测审计。

证据：
- engine/bash.py:221-232：仅 logging.warning(...) 写一行
- 没有 record_change、没有 log_pending_action 落 guard_pending.jsonl
- 降级发生在重试——攻击者可构造「白名单 + 失败模式 + 真目标在降级后」组合绕过

方案：
1. 降级审计：bash.py:232 后增加 log_pending_action("bash_sandbox_degraded", params={...}, state="degraded") 落 guard_pending.jsonl
2. 降级显式化 UI：BashResult.degraded 已有（bash.py:240），但 ToolResult 的 is_error 没回传——tool_executor.py 解析时增加 degraded: true 注入到 model 可见的 tool result
3. 降级告警：通过 alert.py::send_lingbus_alert（已有）发 LingBus 通知族长

优先级：P1（合规/审计需求）
工时：1 天
风险：低

2.3 todo 工具 6 子命令 + checkpoint 复用是反超点，但 LLM 不能自动拆分

问题：tool_registration.py:281-287 todo 工具 6 子命令（create/list/complete/cancel/start/get/delete）真实，但 LLM 必须显式调用——没有自动任务拆解。

证据：
- engine/coding.py:42 TodoToolsMixin 注入
- tool_executor.py:419-420 _aggregator.add_task 但这是行为数据不是模型可见的 todo 列表
- LINGYUAN 评级「多轮规划 ⭐⭐⭐」与「任务管理 ⭐⭐⭐⭐⭐」对照——任务管理是工具级（强），多轮规划是模型级（弱）——错位

对比：
- AtomCode task.rs（atomcode-capabilities/src/tools/）支持子任务并行
- DSH tool-subagent 6 后端 + SubagentCapabilities 4 flag
- CC Task 工具支持子代理 + 权限继承

方案：
1. 任务拆解意图识别：在 core/behavior.py::detect_intent（tool_executor.py:411 引用）加 Intent.MULTI_TASK_REQUEST 识别
2. 自动 inject todo list：识别后自动调 todo create 多次（无需 LLM 显式调用）—— 配合 LLM 后续 todo complete
3. task 路由拆解：长 prompt（>200 字符 + 多动词）触发拆解，建议分 3-7 个子任务

优先级：P1（与 CC/AtomCode 任务管理对标关键）
工时：1-2 周
风险：中（LLM 强制接受自动拆解可能反人类意图）

---

三、代码能力提升（缩小 RepoBench 差距）

3.1 ast_replace 真存在但 LLM 不知道

问题：tool_registration.py:185-191 ast_replace 工具真实注册（AST 级函数体替换）——但description 没说清何时用 vs edit_file。

证据：
- tool_registration.py:185-191：ast_replace(description='Replace function/method body at AST level (no text matching needed)')
- 对比 edit(description='Edit file by replacing text (with backup/rollback)')
- LLM 在 tool description 之间选择时，没有显著差异提示

方案：
1. description 加决策树：「use ast_replace for single function body changes when you need exact AST match; use edit for any text-level replace」
2. in-process 例子：parameters 加 examples 字段（JSON Schema 1.1 支持）

优先级：P2（提升模型决策质量）
工时：半天
风险：低

3.2 file_undo 仅 undo 一次，没有 redo

问题：tool_registration.py:96-103 file_undo 仅 restore 备份。

证据：
- description='Undo last edit by restoring backup'
- backup 机制在 engine/coding.py 的 _edit_handler（未精读但 import 在），但没有 redo
- atomcode tools/repair.rs（ls 中存在）支持多步 repair
- DSH session projection/snapshot/rewind/telemetry（子 agent 报告）支持多步回滚

方案：
1. patch 历史栈：engine/coding.py 的 _edit_handler 维护 (old, new, ts, op_id) 栈（已有 file_history.record_change 在 daemon.py:368）
2. file_redo 工具新增
3. 多步回滚：file_undo 加 steps: int 参数

优先级：P2（提升工程体验）
工时：2-3 天
风险：低

3.3 index_project 仅 Python，monorepo 场景下覆盖不足

问题：tool_registration.py:177-182 index_project 描述「Scan Python project and build symbol table」。

证据：
- description 写「Python」
- engine/coding.py:152-161 _index_project_handler 调 indexer.index_project（未精读 indexer）
- 真支持的扩展名见 core/context_compression.py:77 _FILE_PATH_RE——(py|js|ts|tsx|jsx|go|rs|java|rb|md|yaml|yml|json|toml|cfg|ini|sh|sql)——18 种远多于「Python only」

方案：
1. 扩展 index_project：根据文件后缀切换 parser（tree-sitter 多语言）
2. description 真实化：「Scan project and build symbol table (Python/JS/TS/Go/Rust/Java/Ruby/MD/YAML/JSON/TOML/SQL/sh)」
3. LSP 复用：lsp tool 已能 goto_def/find_refs——优先用 LSP 而不是自己解析（但 LSP 启动慢，缓存友好）

优先级：P2（multi-lang agent 差异化）
工时：1-2 周
风险：中（多语言 parser 维护成本）

---

四、沙盒/审批/安全加固

4.1 资源限制 RLIMIT_AS 失败仅 WARNING，但 LLM 看不到

问题：engine/bash.py:748-758 setrlimit 失败时仅 logging.warning。

证据：
- engine/bash.py:748-758：捕获 (ValueError, OSOSError) 记录 soft/hard
- 但 BashResult（engine/bash.py:52-60）没有 rlimit_failed: bool 字段
- LLM 不知道自己跑在「无 rlimit 保护」下——可能误判资源

方案：
1. BashResult 加 rlimit_failed：在 engine/bash.py:52-60 dataclass 加 rlimit_failed: bool = False
2. tool_executor 解析时透传：在 engine/bash.py:758 失败时 self._last_rlimit_failed = True，然后在 BashResult 构造时回填
3. LLM 提示词注入：当 rlimit_failed=True 时，在 tool result 后追加 [WARN: rlimit not enforced, memory/CPU may be unlimited]

优先级：P2（安全/可观测）
工时：1 天
风险：低

4.2 sensitive_path_gate 已存在但覆盖范围需审

问题：engine/coding.py:261-280 _gate_sensitive 已声明在 _sensitive_path_guard。

证据：
- engine/coding.py:127-128 self.tool_pipeline.add_guard(self._sensitive_path_guard)
- 但 engine/sensitive_path_gate.py 未精读
- LINGYUAN 评级「操作审批 ⭐⭐⭐」但 sensitive_path_gate 的 fail-closed 行为没验证

方案：
1. 精读 sensitive_path_gate.py（约 100-200 行估）核敏感路径列表（.env、.ssh、/etc/passwd 等）
2. 加测试覆盖：每个敏感路径 1 个单测
3. CI 集成：tests/test_sensitive_path.py

优先级：P1（合规需求）
工时：1 天调研 + 2 天测试
风险：低

---

五、可观测与可调试

5.1 self_optimizer 「5 个月断路首次合闸」节流 24h 可配

问题：self_optimizer/daemon.py:428-440 should_run_cycle(min_interval_hours=24.0) 硬编码 24h。

证据：
- daemon.py:428-440 默认 24h
- daemon.py:100-101 加载 config 但 config 里的 triggers 字段未在 should_run_cycle 引用
- trigger.py:270-286 _check_time 用 min_interval_hours // 24 or 7 算 days，但没直接用 min_interval_hours

方案：
1. 配置化：config.yaml 加 self_optimizer.throttle.min_interval_hours 字段
2. 分场景：会话内 / 离线后 / 早晨高峰可不同
3. 公开指标：daemon_state.json 暴露「下次可跑时间」

优先级：P2（运维体验）
工时：半天
风险：低

5.2 tool_call_log 64 条上限 + 仅 L5 内部用

问题：tool_executor.py:84-92 _log = getattr(self._engine, "_tool_call_log", None) 记录最近 64 条，仅「声明-行为一致性校验」用（line 82 注释）。

证据：
- tool_executor.py:84-92 实现
- tool_executor.py:82 注释：「L5 白箱证据：记录真实工具调用（name + args 摘要），供声明-行为一致性校验」
- 没有暴露到 self_optimizer 行为快照（daemon.py:154-155 update_behavior）

方案：
1. tool_call_log 接到 update_behavior：每 N 轮（或会话结束）聚合「工具失败率」「重复调用率」到 behavior snapshot
2. 暴露指标到 self_optimizer：OptimizationTrigger 加 tool_call_pattern_anomaly 触发条件

优先级：P2
工时：2-3 天
风险：低

---

六、与其他 agent 差异化

6.1 与 atomcode 审批细颗粒度对齐

差距：atomcode approval.rs:182-189 grant_key 区分 edit_file 工具级 vs bash 命令级；lingclaude permissions.py:147-156 with_allow 是单工具级——一次放行 = 整个工具。

方案：
1. PermissionStore.grant_key(tool, args) 类似 atomcode 设计
2. edit 工具：放行 key = edit::path-stem（按文件分组）
3. bash 工具：放行 key = bash::command-prefix（按命令前缀）
4. 回归测试：对照 approval.rs:317-360 写 test_grant_scope.py

优先级：P1（差异化反超点）
工时：1 周
风险：中（行为变更需谨慎）

6.2 与 CC sandbox override 审计对齐

差距：CC BashOutput.sandbox_overridden: boolean 字段审计 dangerouslyDisableSandbox 使用；lingclaude bwrap 失败时仅 WARNING。

方案：
1. BashResult 加 sandbox_degraded 字段（已部分有 degraded）
2. tool_executor 解析时透传：model 可见
3. LingBus 通知：bwrap 失败 / 降级时通知族长

优先级：P1
工时：2 天
风险：低

6.3 与 OpenCode 模型网关对齐

差距：OpenCode 公开文档定位是「模型网关 + IDE 插件」——lingclaude 是「自有 daemon :8700」。

方案：
1. lingclaude SDK 暴露：lingclaude_sdk/ 目录已存在——核是否有 HTTP API + 多语言 SDK（Python/Go/Node）
2. VSCode 扩展：1 人 1 月可出 MVP（参考 Crush 文档）
3. JetBrains plugin：次要

优先级：P2（IDE 集成是 2⭐ 最大短板）
工时：1-1.5 人月（VSCode 扩展）
风险：高（VSCode 插件开发+维护成本）

---

七、文档与生态

7.1 LINGYUAN 评级表本身应当由代码生成

问题：当前 EVALUATION_LINGYUAN_1.0_vs_PEERS.md 是人工手写——含本报告已指出的失实（4 层/5 层、edit_file 存在性、request_user_input 注册情况）。

方案：
1. 指标机械提取脚本：scripts/extract_capabilities.py 从代码 AST 提取：
   - 工具数（tool_registration.py SPECS 长度）
   - 工具分类（按 security_scope）
   - 沙盒后端（grep bwrap/Landlock/firejail）
   - 审批模式（grep mode = "auto"|"ask"|"strict"）
   - 记忆层（grep MemoryLayer\.）
2. CI 集成：每日/每周自动生成对照表
3. 人工只打分（主观项如 UX 体验）

优先级：P1（评测系统自洽）
工时：1 周
风险：低

7.2 SELF_PORTRAIT.md 不存在（CHARTER 提到但未实现）

问题：docs/EVALUATION_LINGYUAN_1.0_vs_PEERS.md L88 引用 lingclaude 0.5.0 的 SELF_PORTRAIT.md——实际本机不存在（已 grep 验证）。

方案：
1. 创建 SELF_PORTRAIT.md：基于精读结论自动生成（参考 atomcode AGENTS.md 格式）
2. CHARTER 同步：更新引用

优先级：P2
工时：半天
风险：低

---

八、按优先级汇总

P0（1-2 周内必须修）

1. 1.1 RepoBench 3/20 提升（多模型路由 + 补全特化 prompt）
2. 1.2 cost_table.py 修正版未替换基准版
3. 1.3 request_user_input 阻塞 stdin 致 daemon 卡死

P1（1-2 月内强化）

1. 2.1 L4 SHARED 层真实使用审计
2. 2.2 bwrap 降级审计与 LingBus 通知
3. 2.3 自动任务拆解（与 CC/AtomCode 对标）
4. 4.2 sensitive_path_gate 精读与测试
5. 6.1 审批细颗粒度对齐 atomcode
6. 6.2 sandbox override 审计对齐 CC
7. 7.1 指标机械提取脚本

P2（3-6 月内差异化）

1. 3.1 ast_replace 决策树
2. 3.2 file_undo/redo 多步
3. 3.3 index_project 多语言
4. 4.1 RLIMIT_AS 失败透传 LLM
5. 5.1 self_optimizer 节流配置化
6. 5.2 tool_call_log 接到 behavior
7. 6.3 VSCode 扩展
8. 7.2 SELF_PORTRAIT.md

---

九、风险与权衡

┌──────────────────────────────┬──────────────────────┬────────────────────────────────────────────┐
│             类别             │         风险         │                    缓解                    │
├──────────────────────────────┼──────────────────────┼────────────────────────────────────────────┤
│ RepoBench prompt 适配        │ 污染其他任务         │ A/B test + 意图识别开关                    │
├──────────────────────────────┼──────────────────────┼────────────────────────────────────────────┤
│ request_user_input mode 扩展 │ 破坏现有 interactive │ 默认 interactive，加 opt-in mode           │
├──────────────────────────────┼──────────────────────┼────────────────────────────────────────────┤
│ L4 SHARED 接入               │ 跨代理写入开销       │ 仅在 EXPERIENCE 标 importance>=8 时升级    │
├──────────────────────────────┼──────────────────────┼────────────────────────────────────────────┤
│ VSCode 扩展                  │ 维护成本             │ MVP 走 lingclaude_sdk HTTP API，最小功能集 │
├──────────────────────────────┼──────────────────────┼────────────────────────────────────────────┤
│ 审批细颗粒度                 │ 行为变更             │ 默认同当前，加 opt-in 细颗粒度             │
└──────────────────────────────┴──────────────────────┴────────────────────────────────────────────┘

---

十、与其他 agent 的战略对位建议

1. 不打 RepoBench 头名战（3/20 → 5-6/20 是合理目标，CC/opencode/crush 7/20 已接近 deepseek-coder 极限）
2. 深耕 5 层记忆 + L4 共享——这是其他 6 家都没有的差异化
3. 强化 bwrap 沙盒 + 细颗粒度审批——对标 atomcode approval.rs 是能力补齐，对标 DSH 多后端是工程深度
4. 不要追 IDE 集成——成本太高（1.5 人月+），与 OpenCode/CC/AtomCode 正面竞争胜算低
5. 追 LACP 跨代理 + 灵忆共享——这是灵族生态护城河，也是 lingclaude 在 7 agent 中的独特立足点

---

最后声明：本建议基于精读 ~5,500 行 lingclaude 核心代码 + 500 行 atomcode + 100 行 CC d.ts + 跑通 cost_table.py 验证。所有建议都有代码引用证据，但「实际效果」需 A/B test 验证（特别是 P0 三项）。建议先做 1.2 和 1.3（成本低、风险低、立竿见影），再做 1.1（涉及模型路由，需谨慎）。


  codex:

• 我基于前面的代码精读 + 刚刚的三项补充验证（human_eval 库在、但 proxy 8765 无响应；pruner 命中项全是 UI 显示层、无真正的上下文裁剪模块；无持久 shell、无 trust_level），给出建议。

  ———

  # 灵克下一步优化建议（客观·中肯·可落地）

  ## 一句话定位

  > 灵克的护城河已经建在「安全/治理/本地化」上（这块是真实的、甚至超过部分商业品），但「代码能力」这个占 40% 权重的主维度，当前既没有可验证的量化基线，也没有把工具链的最后一公里补齐。下一步不该再扩自优化/治理的宽度，而应该先把「评测能跑通、上下文不爆
  > 掉、输入不抽风」这三件事做实，再谈深水区。

  ———

  ## P0 —— 立即做（1–2 周），收益最大、成本最低

  ### P0-1 修通 HumanEval 评测链路（最高优先，先有基线再谈优化）

  症状证据：benchmarks/humaneval/results/ 里 8 次跑批 pass_at_1 全是 0.0、total=5、errors=5；且 details 每条只存 {"task_id": ..., "status": "ERROR"}，不存任何错误原因。

  根因（刚验证）：

  1. run.py 通过 PROXY_URL = http://127.0.0.1:8765 调 LLM Proxy，而 proxy 8765 现在是无响应的（curl 无返回）——所以 error 是「proxy 不可达」，不是模型不行。
  2. harness 本身把错误吞了：details.append 只写 status，generate_completion 里的 print(..., file=sys.stderr) 错误信息没落进结果 JSON，导致 pass@1=0 成了不可诊断的黑盒。

  为什么是 P0：代码能力是 40% 权重的主维度，而你连一条「可运行的通过记录」都没有。没有基线，任何"代码能力提升"都是自说自话，也无法横向比较（这正是你上一轮做横向对比时最缺的一块硬数据）。

  怎么做（3 个动作，一天内可完成）：

  1. 把 BenchmarkResult 的 to_dict 里给每个 task 补 error 字段（把 stderr/异常信息存进去），让下一次失败至少是可诊断的；
  2. generate_completion 里对「proxy 不可达」做 fail-fast——探测一次 8765 不通就直接报「LLM Proxy 未启动」退出，别跑 5 道题然后全 ERROR 假装是评测结果；
  3. 起 proxy（或把 PROXY_URL 切成 config.yaml 里现在的真实端点 glm@bigmodel.cn），先跑一个小 --limit 20 拿到第一个真实 pass@1 数字，作为后续所有优化的对照基线。

  预期收益：立刻获得「代码能力」维度的唯一量化锚点，且暴露的是链路 bug 而非模型短板——这是成本收益比最高的一项。

  ———

  ### P0-2 实现 tool result pruner（gap 分析三次点名，上下文工程的致命缺环）

  症状证据：第一轮 gap 分析、CC 维度、DSH 维度三份文档都标「tool result pruner ❌ 无」「spill ❌ 无」。我 grep 确认：代码里含 pruner/truncate/spill 的命中全在 cli/display.py、render_facade.py 这些显示层，是「屏幕上截断」而非「上下文里裁剪」——两者完全不
  同。

  根因与后果：bash 一次 cat 一个几千行文件或 grep 命中几百条，就把整个上下文窗口灌满，context_compression.py 再强也救不回来（压缩是事后，pruner 是事中）。这是长任务会突然「失忆」的直接原因，也是第三维度（上下文）扣分的根源。

  怎么做：新建 lingclaude/core/tool_result_pruner.py（对齐 DSH 的 compaction-tool-result-pruner，你可吸收，约 60–100 行即可）：

  - 对每个工具返回的 stdout，超过阈值（如 30K 字符 / 6K token）时：保留头尾（头保留前缀能继续读，尾保留最近输出），中间用 ...[N lines truncated, full output spilled to .lingclaude/spill/<tool>_<hash>.txt]... 标记；
  - 落盘到 spill 目录，并在摘要里告诉模型「完整内容在哪，可用 read 工具按 offset 继续读」；
  - 关键：pruner 要接在 tool_pipeline.py / tool_executor.py 的结果回灌进 messages 之前，而不是显示层。

  预期收益：长会话/长仓库任务的上下文有效利用率立刻上一个台阶，直接提升第三维度和第二维度的真实表现。

  ———

  ### P0-3 收敛 TUI 输入层稳定性（最直观的体感硬伤）

  症状证据：最近一周 git log 几乎全是 fix(cli): 转义序列被啃一半污染输入行 / UnicodeDecodeError 死循环 / 输入泵假死/方向键乱码/选项假死 / Ctrl+C 无痕。说明 repl_io.py / input_queue.py / full_tui.py 这条输入泵还没收敛，是在反复打补丁。

  根因：从「裸 input()」迁移到 PromptToolkit 是正确方向，但输入泵（streaming 期间的 prompt 非阻塞、Esc 打断排空、多行提交重绑）属于并发 + 终端控制序列的深水，补丁式修会一直冒新 bug。

  怎么做（收敛而非继续打补丁）：

  1. 把 input_queue.py 的输入泵逻辑用一个明确的「状态机」重写（Idle / Streaming / Escaped 三态），消除「双读者」「排空不彻底」这类补丁之间互相打架的根因；
  2. 给 repl_io.py 加混沌测试（tests/chaos/ 目录已存在）：模拟乱码字节流、Ctrl+C、超长输入、方向键序列，断言「输入永不吞字、永不假死」——没有回归测试，这层永远收敛不了；
  3. 收敛后冻结协议：输入泵不再作为常规迭代对象，只允许「修 bug + 加测试」成对出现。

  预期收益：第五维度（可用性）最痛的一项被摁住，日常「开箱手感」从及格线以下拉到及格。

  ———

  ## P1 —— 1–2 月做，补齐结构性缺口

  ### P1-1 LSP 从「一次性子进程」升级为「常驻 + 索引 + diagnostics」

  现状：lsp_provider.py 走自管 subprocess 的 stdio JSON-RPC，只覆盖 goto_def/find_refs/hover/goto_impl 四点，无常驻 server、无增量同步、无 diagnostics、无 workspace symbol。代码阅读理解（第一维度里的重头）因此是「点对点查」而非「持续懂」。

  怎么做：先不自己造完型，借 python-lsp-jsonrpc 或直接复用 atomcode 的 codeintel 契约思路：

  1. 把 lsp_registry.py 已注册的 pylsp/rust-analyzer/gopls 做成会话级常驻（进程复用，加 initialize → textDocument/didOpen/didChange → shutdown 生命周期）；
  2. 至少补 textDocument/definition 之外的 references（已有协议但验证是否真跑通）和 documentSymbol（喂给 list_functions 替代纯 AST）；
  3. 缓存索引结果到 .lingclaude/lsp_index/，跨会话复用，避免每次冷启动重扫。

  预期收益：第一维度「代码阅读理解」从当前 ★★☆ 提升到 ★★★ 以上，是提升 40% 权重主维度性价比最高的单点。

  ### P1-2 持久 shell 会话（去独立 subprocess.run）

  现状：grep 确认无 pexpect/pty，每次 bash 都是独立 subprocess.run，多轮 shell 状态（cd 目录、环境变量、后台进程）不保留。cc/codex/opencode 都有持久 shell 或至少 session-scoped 环境。

  怎么做：给 bash.py 加一个 _ShellSession 抽象（pty + 常驻进程 + 环境快照），bash 工具默认走 --session 复用，保留 --fresh 退化为当前独立执行。这是 bash 安全黑名单之外的一次「执行模型」升级，不碰已有安全词表。

  ### P1-3 project trust_level + session rewind

  现状：grep trust_level/trusted 零命中（第一轮已知 gap）；checkpoints/ 有快照但无「回滚到某轮」的 rewind 工具。

  怎么做：

  1. trust_level：在 permissions.py 的 PermissionContext 上加 trust_level: init/new/trusted 三档，持久化到 .lingclaude/trusted_projects.json，可信目录自动降审批强度——补齐第四维度唯一缺失项；
  2. session rewind：session_store.py 已把状态原语化，基于它加一个 rewind（回到某 message_id 快照），把「回滚」从「有快照」变成「可用」。

  ### P1-4 把自优化回路的产出「接回」代码能力主链路

  现状（客观提醒，这是最容易自我感动的地方）：自优化是灵克独有优势（8 触发 + AST 评估 + learner + daemon + H1-H17），但它是长期复利，当前不直接作用于 40% 权重的代码能力。如果优化器只在「降低复杂度」「提取规则」上转，那就只是在把自己的代码打磨干净，而不
  是在提升「帮用户写代码」的能力。

  怎么做（关键转向）：让 learner 的产出至少有一条回灌路径是「提升代码能力」的——例如把「用户指出的代码错误」沉淀为 learned rule 后，自动写入 system prompt / 工具描述，让下一次同类任务不重犯。否则自优化只是「自我改进的演示」，不是「越用越懂你的编程助手」
  （README 的承诺）。

  ———

  ## P2 —— 3 月+，深水区（做完 P0/P1 再动）

  1. 多文件理解与类型级重构：ast_replace 是「函数体替换」，缺「跨文件重命名 + 引用同步更新」「类型补全」。这是第一维度从 ★★★ 到 ★★★★ 的坎，但依赖 P1-1 的 LSP 引用能力先落地。
  2. worktree 隔离：cc/codex 都有，灵克无。让 agent 在 git worktree 里试改，验证通过才合入，把「试错」和「破坏」物理隔离。这依赖 P1-2 持久 shell 的 cwd 能力。
  3. 评测集扩展：HumanEval 跑通后，补 MBPP（单文件）+ SWE-bench Lite（仓库级，你 .repobench/ 目录已建好但空着），把「代码能力」从单函数拉到仓库级真实场景。

  ———

  ## 三条「克制」型建议（符合你自己的 PRINCIPLES，别做）

  1. 不要再新增治理/审计维度的理论框架。你已有 H1-H17 元认知守卫 + governance_v2 + proposal_lifecycle + 审计总账，治理深度已经超过竞品。继续加治理维度是「把 15% 权重的优势项从 4.5 分做到 4.6 分」，边际收益趋近于零。
  2. 不要为了「对标 Claude Code」去复制它的 TUI 花活（markdown 渲染、inline diff、keyboard protocol）。这些是第五维度 10% 权重里「好看」的部分，不是「能用」的部分；先把裸 input 的稳定性（P0-3）做实，再谈美化。
  3. 不要给 32 个工具继续加新工具。工具数量已经不少，瓶颈不在「有没有」而在「用得好不好」（pruner、LSP、持久 shell）。加第 33 个工具不会提升任何权重。

  ———

  ## 优先级与权重对照（决策依据）

   建议                         主命中的维度（权重）          成本    收益
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━  ━━━━━━━━━━━━━━━━━━━━
   P0-1 修通 HumanEval          代码能力（40%）               极低    建立唯一量化基线
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P0-2 tool result pruner      上下文（10%）+ 工具（25%）    低      长任务不再失忆
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P0-3 TUI 输入收敛            可用性（10%）                 中      体感最痛项
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P1-1 LSP 常驻化              代码能力（40%）               高      主维度最大单点提升
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P1-2 持久 shell              工具（25%）                   中      多轮 shell 连续性
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P1-3 trust_level + rewind    安全（10%）+ 工具（25%）      中      补齐已知唯一硬缺
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P1-4 自优化回灌代码能力      代码能力（40%）               中      兑现 README 承诺
  ───────────────────────────  ────────────────────────────  ──────  ────────────────────
   P2 深水区                    —                             高      依赖 P1 先落地

  最核心的一句话建议：先把你的「代码能力」从一个「全 error 的黑盒」变成一个「可测量的数字」，再围绕这个数字做减法式的收敛，而不是继续做加法式的框架扩张。 你上一轮横向对比已经诚实指出灵克 ≈ crush 档次、落后 cc/codex/atomcode/DSH——要补这个差距，P0 三条 +
  P1-1/P1-4 是最短路径，剩下的都是噪音。

opencode:
 给lingclaude 下一步优化提出客观中肯并详细的建议
+ Thought: 2.8s
基于前面的深度代码阅读与横向对比，给出 客观、中肯、可落地 的下一步优化建议，按 ROI（投入产出比）从高到低 排序：
1. 必做：补齐「IDE 集成」短板（ROI 最高，2-3 周可出 MVP）
现状：仅有 WebUI（webui_server + 前端），无 VSCode/Cursor 插件，这是企业落地最大阻力。
建议：
任务	实施路径	预估工时
VSCode 扩展 MVP	复用现有 HTTP API（/ask/stream SSE、/live/events 增量事件、/sessions 管理、/read-file /write-file）、在 VSCode 侧实现：内联 Diff、终端嵌入、诊断面板、会话树视图	2 人·2 周
Language Server Protocol (LSP) 对外暴露	将 StdioLspProvider 能力封装为标准 LSP 服务（端口 8701），VSCode 直接 vscode-languageclient 连接，获得「跳转定义/引用/悬停/实现」原生体验	1 人·1 周
配置向导	首次安装自动检测 config.yaml、引导填 API Key/模型、一键注册 VSCode settings.json	0.5 人·周
验收标准：在 VSCode 内打开 lingclaude 自身仓库，F12 跳转定义、Shift+F12 找引用、悬停看类型、内联 Chat 发起任务、工具执行实时流式回显 —— 全程不离开编辑器。
2. 必做：核心热路径 Rust 化（性能 10x，技术债收敛）
现状：Python 单线程 + GIL 限制，tool_pipeline、index_project、file_ops、grep/glob、bash 执行均在解释器跑。
优先级矩阵：
模块	当前痛点	Rust 重写收益	依赖/风险	建议顺序
tool_pipeline	5 段流水线每轮锁竞争、线程池开销	零拷贝上下文传递、async 并发、原生 tokio 超时控制	低（接口稳定）	P0
index_project	增量索引 200 文件上限、Python AST 慢	tree-sitter 并行增量索引、支持 10k+ 文件、内存映射	中（需保持 JSON 兼容）	P0
file_ops / file_edit	大文件读写、精准编辑、undo 机制	mmap + rope 数据结构、零拷贝编辑、原子写	低	P1
grep / glob	递归遍历慢、无索引加速	ripgrep/fd 库级调用、复用 .gitignore	低	P1
bash / sandbox	subprocess 开销大、bwrap 参数拼装易错	tokio::process + bwrap 直接 syscall、网络白名单编译期检查	中（需保持 allow_network 语义）	P1
落地策略：
采用 PyO3 + Maturin 生成 lingclaude_core wheels，Python 侧仅保留薄胶水层
先在 engine/coding_wiring.py、engine/tool_registration.py 两个装配点切换实现，零业务代码变更
CI 增加 cargo test + pytest 双跑，保证行为一致性
3. 高价值：ast_edit 多语言化（解锁重构场景）
现状：仅支持 Python（ast 模块），TS/JS/Go/Rust/C++ 全靠 LSP 侧写，无结构化编辑能力。
方案：
# 统一 AST 抽象层（新增 lingclaude/engine/ast_unified.py）
from tree_sitter import Language, Parser
from dataclasses import dataclass

@dataclass
class UnifiedFunction:
    name: str
    start_line: int
    end_line: int
    params: list[str]
    return_type: str | None
    body_text: str
    node: Any  # tree-sitter Node

class MultiLanguageAST:
    _LANGUAGES = {
        "python": "tree-sitter-python",
        "typescript": "tree-sitter-typescript",
        "javascript": "tree-sitter-javascript",
        "go": "tree-sitter-go",
        "rust": "tree-sitter-rust",
        "cpp": "tree-sitter-cpp",
    }
    
    def list_functions(self, file_path: str) -> list[UnifiedFunction]: ...
    def replace_function_body(self, file_path: str, func_name: str, new_body: str, **kwargs) -> Result: ...
实施：
pip install tree-sitter tree-sitter-python tree-sitter-typescript ...（~5MB）
复用现有 list_functions/replace_function_body 签名，内部实现切换，测试用例复用
优先覆盖 TS/JS/Go/Rust 四大主流语言，C++ 后补
收益：SubAgent 重构任务、自动化修复、代码现代化全语言可用。
4. 高价值：基准测试接入 + 回归基线（度量驱动优化）
现状：无标准化基准，无法量化「代码能力」提升/回归。
建议接入（按优先级）：
基准	适用场景	接入方式	频率
SWE-bench Lite (300 实例)	真实仓库级 Bug 修复	scripts/bench_swe.py 跑通、记录通过率、token/耗时	每周一次（CI 夜ly）
RepoBench (Python/JS 子集)	仓库级代码补全/理解	复用 index_project + LSP 查询	每周
HumanEval / MBPP	单文件编码基础能力	scripts/bench_humaneval.py 批量跑	每次模型升级
自建「重构基准」	函数提取/重命名/类型补充	从 lingclaude 自身历史 PR 抽取 50 个重构案例	每月
产出：
benchmarks/results/latest.json：通过率、token、耗时、工具调用分布
benchmarks/trends/：趋势图（Grafana/简单 HTML）
PR 必检：bench_swe_lite_pass_rate >= baseline - 2% 才允许合并
5. 中价值：Provider 生态完善（降低用户门槛）
现状：provider_registry 架构已就绪，但预置配置仅覆盖 OpenAI/Anthropic/GLM。
补齐清单（每项 0.5-1 天）：
Provider	配置文件	文档	测试
Ollama (本地首选)	config.examples/ollama.yaml	docs/providers/ollama.md	pytest tests/providers/test_ollama.py
vLLM (高吞吐本地)	config.examples/vllm.yaml	docs/providers/vllm.md	同上
Llama.cpp / llama-server	config.examples/llamacpp.yaml	同上	同上
DeepSeek / Qwen / Moonshot	兼容 OpenAI 端点，仅需文档	docs/providers/openai_compat.md	复用 OpenAI 测试
Azure OpenAI / Bedrock / Vertex AI	企业级部署必备	docs/providers/enterprise.md	需真实凭证，文档标注
交付物：make provider-smoke-test 一键跑通所有预置 provider 的 /models 列表 + 单轮对话。
6. 中价值：Session/Checkpoint 可靠性加固（生产级硬指标）
现状：SessionManager.snapshot/rewind、checkpoint 机制已有，但未经混沌测试。
加固清单：
场景	当前风险	加固措施	验收
进程崩溃中断写入	save() 先 truncate 后写，断电丢档	已有 tmp + os.replace 原子写，增加 fsync + 校验和	kill -9 100 次，0 丢档
并发会话写同一项目	_global_mode 下目录锁缺失	file_lock.py 引入 fcntl/flock 跨进程锁	并发 10 进程写入，无竞争
Checkpoint 损坏恢复	单文件 JSON，损坏不可恢复	引入 WAL 机制：checkpoint.wal 追加操作日志，启动回放	手动损坏主文件，自动从 WAL 恢复
长会话内存泄漏	LayeredMemory.working 无上限	max_working_items 配置 + LRU 淘汰 + 定期持久化到 episodic	1000 轮会话内存增长 < 50MB
7. 低成本高收益：文档与 DX 细节打磨
项	现状	目标	工时
--help 完整性	子命令参数不全、无示例	所有 CLI 子命令 argparse 补全 help/choices/metavar/epilog	0.5 天
错误码手册	分散在 ToolErrorCode/StopReason/HTTP status	docs/ERROR_CODES.md 统一表格：码、含义、排查步骤、示例	0.5 天
配置 Schema 校验	仅运行时读取、错配置报错晚	config_schema.json + 启动时 jsonschema.validate、IDE JSON Schema 提示	1 天
迁移指南	版本间破坏性变更无文档	docs/MIGRATION.md：每版本列出 Breaking Changes + 迁移脚本	持续维护
8. 架构层面：模块边界收敛（技术债预防）
观察到的循环/过度耦合：
query_engine → tool_call_executor → coding → tool_pipeline → query_engine (循环)
coding → 13 个 Mixin → registry → tool_definition → coding (隐式依赖)
建议：
引入 lingclaude/core/contracts.py —— 仅含 Protocol 定义（ToolExecutor、SessionStore、ModelProvider、SandboxProvider、LSPProvider）
所有模块仅依赖 contracts，实现类在 wiring.py/coding_wiring.py 组装注入
启用 mypy --strict + import-linter CI 门禁：
# pyproject.toml 追加
[tool.import-linter]
contracts = [
  "lingclaude.core.contracts",
]
forbidden = [
  { name = "engine->core", imports = ["lingclaude.engine"], from = "lingclaude.core" },
  { name = "core->engine", imports = ["lingclaude.core"], from = "lingclaude.engine" },
]
分阶段剥离：先把 tool_pipeline、session、permissions、model_adapter 彻底解耦，再治理 coding 侧 Mixin 爆炸
执行路线图（建议 12 周冲刺）
周次	核心交付	验收指标
1-2	VSCode 扩展 MVP + LSP 暴露	内部犬食：团队全员切 VSCode 开发 lingclaude 自身
3-4	tool_pipeline / index_project Rust 重写 + PyO3 打包	pytest tests/engine/test_tool_pipeline.py 全绿、性能基准 < 旧版 30% 耗时
5-6	file_ops / grep / glob Rust 重写	大文件(10MB)读写/搜索 < 200ms
7	ast_edit 多语言化 (TS/JS/Go/Rust)	pytest tests/engine/test_ast_edit_multilang.py 覆盖 4 语言
8	SWE-bench Lite / RepoBench 接入 + CI 夜ly	首次基线建立、趋势看板上线
9	Provider 生态补齐 + 冒烟测试	make provider-smoke-test 全绿
10	Session/Checkpoint 混沌测试 + 加固	pytest tests/chaos/test_session_resilience.py 100 次 0 丢档
11	契约层收敛 + import-linter 门禁	mypy --strict 0 error、import-linter 0 违规
12	文档/DX 打磨 + 发布 0.7.0	docs/ 完整性评分 > 90、用户引导 5 分钟上手
不建议投入的方向（避坑）
方向	理由
自研基座模型微调	成本极高、收益不确定，直接接入开源 SOTA（Qwen2.5-Coder/DeepSeek-Coder-v2）更划算
重写 WebUI 前端	现有 Rust webui_server + 前端已可用，VSCode 插件才是主战场
引入复杂编排框架	现有 QueryEngine + SubAgent + ToolRouter 已足够表达复杂工作流，外部框架反而增加认知负担
追求「完全自主」无人在环	现有 plan_mode + permission_mode=ask + request_user_input 已在「自主-可控」平衡点，继续激进反而降低企业信任
一句话总结
先把 VSCode 插件造出来（解决分发），再把热路径 Rust 化（解决性能），再补基准测试（解决度量），再完善 Provider 生态（解决门槛），最后治理架构边界（解决技术债）。 每步都有可见交付、可量化验收、可回滚，不搞大爆炸重构。

atomcode:
灵克（lingclaude）下一步优化建议书

  > 依据：六维评测（4.23 分）+ 七工具横向对比 + J1–J5 审计欠账账本 + 本会话用户可感问题实证。每项含【现状证据 → 目标 → 验收标准 → 工作量】。

  P0：正确性与可信度（不做会持续产生错误结论）

  1. datalog token/cost 统计接通
  •现状证据：~/.lingclaude/datalog/2026-09-16.jsonl 实测 6 条事件 cost.in=0/out=0——schema v1.0-pre 定义了 cost 字段但从未被填充。六维评测“Token 开销”项因此只能给 3.5 并注明“无法证明也无法证伪”。
  •目标：每次模型调用把 usage.prompt_tokens/completion_tokens（openai 协议 response.usage 现成返回）写入 datalog cost；按 provider 单价折算人民币成本。
  •验收：连续 3 个会话后 datalog 出现非零 cost；datalog.py 聚合命令可输出日/周消耗报表；六维评测“Token 开销”可重评为 4.5+。
  •工作量：0.5-1 天（openai_provider 响应解析处取 usage → datalog 写入点各一处，纯接缝改动）。

  2. pty 级 TUI 测试层（交互盲区补齐）
  •现状证据：你报的 6 个 TUI 问题（toolbar 缺失/方向键转字符/编码报错/round 空白增行/长文截断）全部是运行中真实终端才暴露的，而 test_full_tui.py 17 例全是 mock——pty 层零覆盖。这类问题“审计和测试发现不了”是结构性盲区。
  •目标：tests/test_tui_pty.py 用标准库 pty + pyte 起真会话断言：输入行存在、escape 序列不泄漏为字面字符、500 字输入不截断、round 间空白行不增长。
  •验收：6 个历史问题各有 1 个红→绿回归用例；pytest tests/test_tui_pty.py 在 CI 可跑（无头环境）。
  •工作量：2-3 天（pyte 终端模拟 + 会话 spawn 脚手架是大头，用例本身薄）。

  P1：安全与体验的分数杠杆（横向对比中差距最明确的两处）

  3. bwrap 沙盒强制化（vs CC 的最大安全差距）
  •现状证据：sandbox_provider.py 有 Bwrap/Noop 双后端，但 Noop 常为默认——横向对比中 CC 5.0（Seatbelt/bubblewrap OS 级强制）vs 灵克 3.5。沙盒“可插拔但不强制”等于没有。
  •目标：bwrap 可用时默认启用（探测失败才降级 noop 且显式告警 sandbox=bwrap-fallback）；高危工具（bash）在 strict 模式下无沙盒拒绝执行。
  •验收：实测 bwrap 下 rm -rf /tmp 被隔离、写越出工作树失败；探测日志可见 sandbox provider 选择；权限维度可重评 4.5。
  •工作量：1-2 天（后端已有，改默认选择策略 + strict 模式联动）。

  4. 通用参数级/路径级 ACL（替换点状特例）
  •现状证据：bash 只读白名单（_proxy3 特例）、sensitive_path_gate（敏感路径特例）都是手写的点状机制；OpenCode 用 per-tool allow/ask/deny + glob last-match-wins 做成了通用机制。你的问题“是否可细粒度 ACL 控制工具”精读结论：当前只能工具名级。
  •目标：PermissionContext 增加 rules: list[ACLRule]（tool_glob + param_glob + effect），bash 白名单和敏感路径门迁移为内置规则集（行为不变，机制统一）。
  •验收：现有 bash 白名单用例全部以 ACL 规则表达且测试通过；新增“允许 bash 但 deny bash:git push*”一类参数级规则生效；审查时 J1 守卫可校验规则入册。
  •工作量：3-4 天（规则引擎 + 迁移两处特例 + 回归）。

  5. tui_health observer 插片（自发现机制）
  •现状证据：交互问题历次都靠你人工反馈（“无法输入”“空白增行”），灵克从未自己发现。SeamType 体系有 observer 类型的架构位但无实例。
  •目标：运行时采样输出流——round 间空白行增长率、escape 泄漏、输入延迟 p95；异常写 StateStore record_type=tui_health + LingBus 告警。
  •验收：人为制造输出异常能在 1 个 round 内被记录并告警；铁律 J5 守卫校验其入册。
  •工作量：2 天（依赖 #2 的 pyte 脚手架复用）。

  P2：制度收尾与体验打磨

  6. J3 停层声明入 PluginLoader 校验（审计唯一残留制度项）
  •现状：新插片（research_crew/full_tui）有优秀的手写停层声明，存量插片没有，PluginLoader 不校验。
  •目标：manifest 增加 core_boundary/sub_seams 必填字段，缺失拒绝注册；存量 6 个工具插件补声明。
  •验收：缺字段的假插件注册被拒（红→绿用例）；铁律文档 §三.5 从“待补”变“已执行”。
  •工作量：1 天。

  7. VSCode 集成探路（六维最大短板 2.5 分）
  •现状：无 IDE 插件；seam 的 TRANSPORT 类型是现成架构口。
  •目标：最小可用形态不做全插件——先做 LSP 桥 + diff 审批流：灵克会话中的文件编辑以 VSCode diff 视图呈现、审批按钮回调 webUI 端点（13460 已有审批面）。
  •验收：VSCode 中可看到灵克编辑的 diff 并一键批准/拒绝，决策回灌会话。
  •工作量：探路 3-5 天（全量插件另计）；建议先出 1 页可行性设计给用户拍板再动工。

  8. 报错可读性小修（1211 类误导提示）
  •现状：GLM 1211（实为模型名/套餐问题）提示“请检查网络连接”——误导方向。
  •目标：建立上游错误码→中文释义映射表（1211/1308/UnsupportedModel/set model id as k3 等本会话实遇的全部），hint 按映射给出定向建议。
  •验收：上述 4 类错误各自给出针对性 hint 而非通用网络提示。
  •工作量：0.5 天。

  执行建议

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   顺序                理由
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   #1 → #2 先行        #1 半天见效且让后续所有工作可量化；#2 是 #5 的地基
  ────────────────────────────────────────────────────────────────────────
   #3、#4 并行         分别攻横向对比中差距最明确的安全与 ACL，互不依赖
  ────────────────────────────────────────────────────────────────────────
   #5 → #6 收尾制度    #5 复用 #2 产出；#6 一天清掉审计最后残留
  ────────────────────────────────────────────────────────────────────────
   #7、#8 机动         #7 先拍板再动工；#8 随手可做
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  总量：P0 约 3-4 天，P1 约 6-8 天，P2 约 5-7 天——按灵克当前重构节奏（周均 10+ commit）约 2-3 周可全部清偿。

  一个诚实的提醒：本建议书的“现状证据”全部来自我（AtomCode）对灵克的审计与评测，存在自证视角；#1 完成后 cost 数据可直接校验我六维评测中“Token 开销 3.5 分”是否公允——这是我建议它排第一的原因：先让度量可信，再谈优化。

● Todowrite(#2 输出优化建议书：按优先级分层，每项含现状证据/目标/验收标准/工作量 → completed)

  两项任务均已闭环（任务清单全绿），优化建议书已在前面完整输出，无需补做。

  总结当前状态：

  建议书概要（8 项，按 P0/P1/P2 分层）：

  •P0 正确性：#1 datalog token/cost 接通（0.5-1 天，先让度量可信）→ #2 pty 级 TUI 测试层（2-3 天，补交互盲区地基）
  •P1 分数杠杆：#3 bwrap 沙盒强制化（1-2 天，攻 vs CC 最大安全差距）∥ #4 通用参数级/路径级 ACL（3-4 天，替换点状特例）→ #5 tui_health observer 插片（2 天，自发现机制）
  •P2 收尾：#6 J3 停层声明入校验（1 天，清审计最后残留）→ #7 VSCode 集成探路（先出可行性设计拍板）→ #8 上游错误码映射表（0.5 天随手修）

  总量约 2-3 周，与灵克当前重构节奏匹配。执行顺序建议：#1→#2 打地基，#3∥#4 并行攻差距，#5→#6 收制度，#7 拍板后动工，#8 机动。

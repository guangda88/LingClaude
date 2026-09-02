# Changelog — lingclaude

All notable changes to this project will be documented in this file.
格式依 [Keep a Changelog](https://keepachangelog.com)，版本号依 [SemVer](https://semver.org/)。

## [0.5.0] - 2026-09-02（未发布）

> 本版本以**自优化回路合闸**+**三书融入**为骨干。
> 完整改动见 `docs/audit/CLI_WEBUI_AUDIT_REPORT.md` 第二轮（双侧审计+15+13 修复）+ §二·三（第三轮深修）+ 合成文档 `docs/SYSTEMS_THEORY_SYNTHESIS.md`。

### Added

- 守护进程自优化回路合闸：会话结束触发 + 24h 节流（`OptimizationDaemon.should_run_cycle`）
- 规则衰减复核工具 `scripts/rule_decay_review.py` + 登记册 `.lingclaude/rule_registry.json`
- 审计总账验证节自动化 `scripts/update_audit_ledger.py`（AUTO-VERIFY 标记区；禁手写 ✅）
- 工作区并发快照 `scripts/prechange_snapshot.py`（HEAD 冻结+工作区变化=并发扫动信号）
- 元认知守卫 H17「闭环申报」（`.lingclaude/metacognitive_guards.md`，H1-H17）
- 行为快照时序化 `OptimizationDaemon.save_behavior_history` 滚动窗口 + `behavior_trend()`
- `PRINCIPLES.md` §十「闭环」原则 + §九「钟表域」（Kelly clockware/swarmware）
- `docs/SYSTEMS_THEORY_SYNTHESIS.md` — 三书框架融入合成文档
- `proposals/2026-09-02_BUS_TASK_BASH_APPROVAL_PROPOSAL.md`（OPEN，族内评审）

### Changed

- `_apply_params` 默认 report-only（`LINGCLAUDE_DAEMON_APPLY=1` 才写 config.yaml）+ 单参数限幅
- `_feed_behavior_to_daemon` 缓存 daemon 实例（消每回合重建开销）
- `api.py` `_PROXY_URL` 改 env 可覆盖（`LINGCLAUDE_PROXY_URL`）
- `lefthook.yml` 加 pre-commit `workspace-snapshot`（advisory）
- `linggit/hooks/pre_commit.py` 与 `doc_consistency_check.py` 调 `refresh` 自动登记验证

### Fixed

- **P0** — `BusResponder._execute_task` 把任意任务文本直接当 shell 执行 → fail-closed 默认拒绝
- **P0** — EOF 默认吞掉导致 `run -i </dev/null` 死循环（接口层 EOF 直接传播）
- **P0** — webui 鉴权链断裂（cookie 只种不验）→ 鉴权中间件统一收口 + 401 JSON/HTML
- /chat/stop 恒 502（合约不匹配）→ 按 HTTP 状态诚实化映射
- SSE error 手工拼 JSON 被前端静默吞 → 全部改 `serde_json::json!`
- `/chat/stop` 硬编码 127.0.0.1:8700 → 用 `state.lingclaude_base`
- `images: Vec<String>` 遇前端对象数组必 400 → 放宽 `Vec<Value>`
- `_compact_if_needed` 谎报「已触发」 → 显示实际数值
- `/lsp add` 只认 `--command` 语法 + `--args` 引号残渣 → 双语法 + shlex
- `--bash-executor` 先建 runtime 后改 config 无效 → 前移至 `CodingRuntime` 之前
- `--model` 注册后零使用 → 接线为 switch_model
- `_load_keys_env()` 定义了零调用 → `main()` 入口统一加载
- Esc 监听线程永生堆积 → per-turn stop 事件 + 先停线程再清 interrupt
- 双重历史（prompt_toolkit 自动 + `push_to_history` 重复写入）→ PT 改 no-op
- `/quit` `/exit` 无 handler → 新 handler 置 `quit_requested`
- BusResponder rowid 提前推进丢任务 → 逐条推进
- 守卫计数文档过时（H16 → H17），6 个文档同步
- `audit.rs` 中文 panic（字节切片） → `chars().take().collect()`

### Security

- webUI handoff token 加 5min TTL + 128 上限（防 drive-by `/mint` 膨胀）
- 会话 cookie TTL 24h + 256 上限
- 未实现端点前缀（`/auth`/`/sessions`/`/config`/`/fs`/`/command`/`/approval_mode`/`/live/*`）显式 404 JSON（防前端 `JSON.parse` 抛 SyntaxError 掩盖真因）
- `LINGCLAUDE_BUS_ALLOW_BASH` 显式授权开关 + native `bash` 5 段管线（含 guards/sandbox）

## [0.3.0] - 2026-04-18

### Added
- Initial changelog tracking
- VERSION file created
- 项目感知层（`indexer.py` + `codeintel/` + 上下文工程 4 档 pruner）
- 项目级知识积累（`layered_memory` + ExperienceStore）
- 子代理多后端（`engine/sub_agent.py` 重构 + inprocess/ACP）
- sandbox policy（`engine/bash.py` 4 档 + `SandboxUnavailableError` fail-closed）
- webui 拆分草稿（Rust 6 模块）
- 阶段二：CLI 交互升级（`/` 命令 + 交互骨架）

### Security
- API 认证绕过 / 路径遍历 / Session ID 加密 / 会话超时 / 无限递归保护
- 敏感路径门（`sensitive_path_gate.py`）
- 命令黑名单

## [0.2.1] - 2026-XX-XX（更早）
- 模型对接（OpenAI/Anthropic）+ 自适应引擎 + Agent Loop
- 情报系统 + HTTP API
- 行为感知（幻觉/情绪/意图）
- 工具重试/对话压缩/项目索引

## [0.2.0] - 2026-XX-XX（更早）
- 核心框架：查询引擎、会话、权限、工具执行、自优化、自学习
- 自适应查询引擎
- 安全三原则 + Bash 沙箱加固
- 自举：灵克自己用 `analyze lingclaude/` 审计

## [0.1.0] - 初始

---

## 版本说明

- **早期版本日期粗略**：未引入 git 前的手写 CHANGELOG 与 git 历史交叉误差 ~1 月；本表保留历史命名但日期退到「更早」层级
- **0.4.0 已合并入 0.5.0**：v0.4 子项（H17/daemon/规则衰减/三书融入）随 0.5.0 一起发布，原"v0.4 自优化实战"路线图保留为里程碑命名（见 `CHARTER.md` 路线图）
- **0.5.0 重点是「让回路转起来」**：0.4 写好了机制但跑不通（5 个月 1 次循环）；0.5 加节流、限幅、验证自动化，让自优化"对自优化敏感"于真实反馈
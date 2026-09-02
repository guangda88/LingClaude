# LingClaude CLI & WebUI 全量审计 + 端到端测试 — 报告（v1.2 重建版）

> ⚠️ **2026-08-29 10:07 前后发生 untracked 文件清理事故**（疑似 `git clean` 类操作），
> 本报告与 KB 首版被删,此为从会话记录重建的版本。
> 事故详情见文末「事故记录」。

## 一、修复总账（F1-F12）

| ID | 内容 | 落点 | 状态 |
|---|---|---|---|
| F1 | `unknowns` 子命令注册 | `cli/app.py` main() | ✅ |
| F2 | `/undo` 从 completer 移除（handler 不存在） | `cli/app.py:269` | ✅ |
| F3 | governance-audit 路径 env 优先 | `cli/app.py` | ✅ |
| F4 | CORS 放行 webui 13458 | `api.py` | ✅ |
| F5 | e2e 测试套件 73→76 用例 | `tests/e2e/` | ✅ |
| F6 | doc_consistency 增 CLI↔手册 diff | `scripts/doc_consistency_check.py` | ✅ |
| F7 | LingBus notify Pydantic 模型 | `api.py:LingMessageNotifyRequest` | ✅ |
| F8 | CORS methods 锁定 + 防回归 | `api.py` + e2e | ✅ |
| F9 | webui-server 拆 6 模块（651 行单文件→） | `webui-server/src/*.rs` | ✅ |
| F10 | rust toolchain + build webui | `~/.cargo` + binary 6.9MB | ✅ |
| F11 | api.py 6 处 `is_err`→`is_error` | `api.py` | ✅ |
| AC#1 | interactive stdin EOF 优雅退出 | `cli/interface.py` `LINGCLAUDE_RAISE_EOF=1` | ✅ |

## 二、模型路由修复（F12 系列 — 用户实跑 bug 链）

| ID | 问题 | 修复 | 落点 |
|---|---|---|---|
| F12a | 启动横幅对缺 key 云端 provider 谎报「已连接」 | `_provider_status()` 诚实化 | `cli/app.py` |
| F12b | 路由选中无 key 云端 provider 必炸 | `_pick_from_route` 跳过（本地不跳） | `model/task_router.py` |
| F12c | 11 provider api_key 全空 | env 兜底映射 + CLI 加载 `~/.ling_keys.env`（key 不落 config 明文） | `task_router` + `cli/app.py` |
| F12d | provider 层对本地无 key 误拒（语义撕裂） | `ModelConfig.is_local_base()` 单一实现 + 三入口放行 | `model/types.py` + `openai_provider.py` |
| F12e | TTY 退出 termios 残留（^C 回显/统计错位） | 进入保存/退出恢复 termios；`LINGCLAUDE_CLI_MODE=plain` 彻底干净 | `cli/app.py` |
| F12f | stream 路径无失败换候选/无 record 统计 | 同回合重试 1 次 + record_error/success 对称 | `core/model_call.py` |
| F12g | waterfall 上游全挂仍占路由池 + `/model` 恒显 unknown | per-provider `enabled:false` + provider._config 取值 | `task_router` + `cli/app.py` |
| F12h | `/model <name>` 假切换（只换名不换端点） | `find_provider_by_model` 反查连带端点/key | `task_router` + `query_engine.py` |
| F12i | provider 流挂起 120s 零反馈 | `_do_stream` 连接即 yield status | `openai_provider.py` |

**端到端实测**：`lingclaude run` → 本地 deepseek-v4-flash(13457) 真实回复成功；
waterfall(tool 链→minimax@cloud) 实测恢复健康后已回池。

## 二·三、第三轮深修（2026-09-01 — 双侧审计后全量修复）

> 两个深度审计（CLI 17 项 / webUI 15 项）→ 修复落地。验证：cargo test 14✅（0 warning）、
> pytest e2e+CLI 相关 211✅、`run -i </dev/null` 实测 1s 内退出、doc_consistency ✅。

### webUI（Rust 桥接层）

| ID | 问题 | 修复 | 落点 |
|---|---|---|---|
| W1(P0) | **鉴权全链装饰性**：cookie 只种不验，`/chat/permission` 审批端点对本机裸奔 | 鉴权中间件统一收口：会话 cookie 校验 + API 401 JSON / 页面 401 HTML；handoff（token 消费→会话签发→302）挪进中间件单点化 | `auth.rs` + `main.rs` |
| W2 | handoff token 永不过期、`/mint` 可被 drive-by 无限刷 | handoff TTL 5min + 存量上限 128；会话 cookie TTL 24h + 上限 256 | `auth.rs` |
| W3 | `/chat/stop` 硬编码 127.0.0.1:8700，忽略 LINGCLAUDE_BASE | 改用 `state.lingclaude_base` | `chat_api.rs` |
| W4 | `/chat/stop` 契约不匹配恒 502（引擎回 Session dict 无 stopped 字段） | 按 HTTP 状态映射：2xx→`stopped:true`+透传 engine 原文；404→`stopped:false`+原因；契约诚实化进协议文档 §3.2 | `chat_api.rs` |
| W5 | SSE error 手工拼 JSON 无转义 → 前端 JSON.parse 静默吞错 | 全部改 `serde_json::json!` + `Event::json_data`（chat/live/heartbeat） | `chat_api.rs`+`live_api.rs` |
| W6 | `images: Vec<String>` 遇前端对象数组必 400 | 放宽 `Vec<serde_json::Value>`（接受但不消费） | `chat_api.rs` |
| W7 | session_id 被伪装成 context 注入引擎 prompt（污染输入） | 不再合成 context，等引擎有真会话语义再接线 | `chat_api.rs` |
| W8 | `audit.rs` char_indices 字符数当字节下标 → 中文必 panic | `chars().take(200).collect()` | `audit.rs` |
| W9 | chat_api 内未接线的 mint_token/status 私有副本（dead_code 警告+漂移风险） | 删除，正本在 `auth.rs`/`main.rs` | `chat_api.rs` |
| W10 | `/live` 丢弃 session_id；心跳无条件 3s 发（文档说无新事件才发） | 回显 session_id 进 snapshot；心跳条件化 | `live_api.rs` |
| W11 | 引擎挂起时 SSE 无限悬挂（reqwest 无超时，文档承诺 504） | connect 5s + read 60s | `main.rs` |
| W12 | 未实现端点回 200+HTML → 前端 resp.json() 抛 SyntaxError 掩盖真因 | 前端已引用的未实现前缀（/auth /sessions /config /fs /command /approval_mode /live/*）显式 404 JSON | `webui.rs` |
| W13 | 协议文档与实现脱节（0.1.0 设计先行） | 重写 v0.1.1：逐条标注已实现/未实现/语义变更 | `docs/protocols/webui-v0.1.md` |

### CLI（灵克）

| ID | 问题 | 修复 | 落点 |
|---|---|---|---|
| C1(P0) | 非 TTY EOF 死循环挂死（`run -i </dev/null` 100% CPU）；测试靠 env 逃生门绕过 | EOF 直接传播 → 上层 break；`LINGCLAUDE_RAISE_EOF` 逃生门失效化（默认即正确） | `interface.py` |
| C2 | `/quit` `/exit` 无 handler → 发给 LLM（F2 删 /undo 时漏修的同类） | handler 置 quit_requested；补全保留 | `app.py` |
| C3 | Ctrl+D 被吞无法退出 | 同 C1（Ctrl+D 退出，Ctrl+C 清行，banner 注明） | `interface.py` |
| C4 | Esc 监听线程永生堆积 + 抢 stdin 吞键入字符 | per-turn stop 事件；先停线程再清 interrupt；仅 TTY 启动 | `app.py` + 测试 |
| C5 | `--bash-executor` 先建 runtime 后改 config，参数无效 | replace 前移到 CodingRuntime 之前 | `app.py` |
| C6 | `_load_keys_env()` 零调用（F12c 假完工，key 兜底链前提不成立） | main() 入口统一加载 | `app.py` |
| C7 | 历史文件每条输入写两遍 | PT 的 push_to_history 改 no-op（FileHistory 已自动记） | `interface.py` |
| C8 | `/compact` 未达阈值谎报「已触发」 | 显示实际数值，未达阈值明说 | `app.py` |
| C9 | `/lsp add` 只认 --command 语法，手册示例全失效；--args 引号残渣 | 双语法支持 + shlex 解析 | `app.py` |
| C10 | `/help`、`/schedule` 用法与实际命令集脱节 | 补全（含 after:N / at:HH:MM） | `app.py` |
| C11 | `--model` 注册后零使用 | 接线为 switch_model | `app.py` |
| C12 | BusResponder 在单轮/横幅进程也启动（被硬杀留半截对话） | 仅交互模式启动 | `app.py` |
| C13 | unknowns 正则跨块误收 claim、severity/owner 丢失、resolve 硬编码 cwd、漏 .yml | 块级解析 + 字段保留 + --manifest-dir + .yml | `unknowns.py` + `app.py` |
| C14 | 手册：启动方式/`on_complete=` 示例失真 | 按实际情况改写 | `USER_MANUAL_CLI_v0.4.md` |
| C15 | 守卫计数文档过时（写的上限低于实际的 H16），doc_consistency 11 处 ERROR | 全量同步；历史引语改写为间接表述 | 6 个文档 |

### 已知未修（设计级，需单独排期）

1. **BusResponder 任务执行链断裂**（审计#2）：`_execute_task` 走 `mcp_proxy`，
   但注册表只由 lingflow_plus（不可导入）/engine._ensure_mcp 填充 → 收到任务必
   "No server found"。e2e mock 掩盖了这一点。需决策：走 native tool_executor 还是
   先 `_ensure_mcp`；**含安全面**（总线触发的 run_bash 审批路径）。
2. **会话连续性是假的**：引擎 `/ask/stream` 无会话存储，webUI 多轮上下文丢失、
   stop/permission 的 session_id 悬空（协议文档 §八-2 已列待决议）。
3. **display.py Step 5 组件整层未接线**（Markdown 渲染/工具面板/状态栏），主循环
   仍裸 sys.stdout.write。
4. **引擎 `LINGCLAUDE_API_KEYS` 未设时全 401**（fail-closed 正确但开箱体验差），
   webui-server 与引擎需配同一份 key — 部署文档已写明（协议文档 §5.3）。
5. 前端 webui/ 大面积调用不存在端点 + `/live/message` 适配层死代码 — 需前端对齐工程。

## 三、事故记录（2026-08-29 ~10:00）


**现象**：09:12 用户日志仍是修复后版本（app.py:228），10:07 日志回到旧行号（app.py:168 / task_router.py:108）。
**范围**：所有 untracked 文件被清理（`git clean` 类操作），含：
- 本审计全部产物：`tests/e2e/`、`docs/audit/`、webui 6 模块拆分、`scripts/doc_consistency_check.py`
- **用户原有资产**：`lingclaude/cli/unknowns.py`、`docs/protocols/webui-v0.1.md`、`docs/cli/`、`docs/agent-knowledge/`、`health/`、`docs/WEBUI_*.md`、`docs/ATOMCODE_SECURITY_INSIGHTS.md`、`docs/CODEX_HARNESS_INSIGHTS.md`、`docs/LACP_MARKETPLACE_API.yaml`、`docs/LINGYUAN_VS_DSH_CRITIQUE.md`、`docs/LING_FAMILY_CODING_AGENT_PROGRESS.md`、`docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md`、`docs/lacp/`（FULL_TUI_NECESSITY/evidence/prompt_engineering）、`lingclaude/core/{content_safety,decision_cost,guards_h13_llm,guards_impl,metacognitive_guards,outcome_quality}.py`、`lingclaude/core/prompt_engineering/`、`.claude/skills/`、`.github/workflows/{benchmark,pytest}.yml` 等
**已救回**（会话记录含全文）：unknowns.py、doc_consistency_check.py、webui-v0.1.md、webui 6 模块、tests/e2e/、本报告。
**无法救回**（无副本）：上表用户资产中我上下文无全文者。
**建议**：① 排查 09:12-10:07 间执行 `git clean`/清理的进程或成员；② 将关键未跟踪文件纳入 git 或定期备份；③ 立即 `git add` 本审计全部产物防再丢。

## 四、验证

<!-- AUTO-VERIFY:START（脚本生成，禁手改 — H17 闭环申报） -->

> 由 `scripts/update_audit_ledger.py` 于 2026-09-02 10:19 生成。手写 ✅ 已废除（H17）：账目与仪表盘冲突时，以仪表盘为准。

| 检查 | 命令 | 退出码 | 结果摘要 | 耗时 |
|---|---|---|---|---|
| ✅ doc_consistency | `/usr/bin/python scripts/doc_consistency_check.py` | 0 | ✓ All consistency checks passed. | 1.7s |

<!-- AUTO-VERIFY:END -->






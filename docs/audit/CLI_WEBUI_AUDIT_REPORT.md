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

## 三、事故记录（2026-08-29 ~10:00）

**现象**：09:12 用户日志仍是修复后版本（app.py:228），10:07 日志回到旧行号（app.py:168 / task_router.py:108）。
**范围**：所有 untracked 文件被清理（`git clean` 类操作），含：
- 本审计全部产物：`tests/e2e/`、`docs/audit/`、webui 6 模块拆分、`scripts/doc_consistency_check.py`
- **用户原有资产**：`lingclaude/cli/unknowns.py`、`docs/protocols/webui-v0.1.md`、`docs/cli/`、`docs/agent-knowledge/`、`health/`、`docs/WEBUI_*.md`、`docs/ATOMCODE_SECURITY_INSIGHTS.md`、`docs/CODEX_HARNESS_INSIGHTS.md`、`docs/LACP_MARKETPLACE_API.yaml`、`docs/LINGYUAN_VS_DSH_CRITIQUE.md`、`docs/LING_FAMILY_CODING_AGENT_PROGRESS.md`、`docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md`、`docs/lacp/`（FULL_TUI_NECESSITY/evidence/prompt_engineering）、`lingclaude/core/{content_safety,decision_cost,guards_h13_llm,guards_impl,metacognitive_guards,outcome_quality}.py`、`lingclaude/core/prompt_engineering/`、`.claude/skills/`、`.github/workflows/{benchmark,pytest}.yml` 等
**已救回**（会话记录含全文）：unknowns.py、doc_consistency_check.py、webui-v0.1.md、webui 6 模块、tests/e2e/、本报告。
**无法救回**（无副本）：上表用户资产中我上下文无全文者。
**建议**：① 排查 09:12-10:07 间执行 `git clean`/清理的进程或成员；② 将关键未跟踪文件纳入 git 或定期备份；③ 立即 `git add` 本审计全部产物防再丢。

## 四、验证

```
pytest tests/e2e/ + test_task_router + test_model: 全绿（见当日运行记录）
cargo test（webui-server）: 4 passed
doc_consistency_check: ✓ pass
E2E 实跑: lingclaude run 真实模型回复 ✓
```

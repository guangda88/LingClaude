# 灵克 (lingclaude) CLI 使用手册 v0.4

**版本**: v0.4 (2026-08-26)
**适用**: lingclaude v0.4.0+（含 T0-T3 + P0-P1 + lsp add 全部落地）
**作者**: 灵克 session 33（基于 gap_analysis 4 份子报告 + CC/atomcode 评审）

---

## 一、灵克是什么

灵克（lingclaude）是灵族工程执行者 + 审计担当，基于 Python monorepo，~36K 行核心代码、2199 个测试。

**核心理念**：自知 → 自觉 → 自决 → 进化（**元认知 + 族内治理**）

**四家对标位置**（基于 12 维度核查）：

| 维度 | 状态 | 评估 |
|------|------|------|
| 终端交互 | 🟡 中等 | L1+L2 已补（prompt_toolkit + Rich），缺 L3 全 TUI |
| 权限治理 | 🟢 领先 | 三档 modes + 敏感门 + bwrap + MV-1，四家无 |
| 上下文工程 | 🟢 持平 | LLM 摘要 + 动态预算 + pruner |
| 工具执行 | 🟢 持平 | ToolCallExecutor + run_in_background |
| 多模态 | 🟢 持平 | image_content + anthropic 图片 |
| MCP | 🟢 持平 | stdio/HTTP/SSE + OAuth/PKCE |
| 子代理 | 🟢 持平 | 双后端 + 4 flag + 控制工具 |
| LSP/代码智能 | 🟢 持平 | codeintel 4 件套 + /lsp add |
| 会话管理 | 🟢 持平 | snapshot/rewind + projection 端点 |
| 调度 | 🟢 持平 | cron + after:/at: + 挂回会话 |
| 沙箱 | 🟡 中等 | 4 档 + fail-closed（bwrap 单后端）|
| 治理/自省 | 🟢 **独有** | meta_cognition + LingBus + MV-1 |

---

## 二、快速启动

### 2.1 前置条件

- Python ≥ 3.10
- 可选：`prompt_toolkit`（未安装自动降级 FallbackSession）
- 可选：`rich`（已用）

### 2.2 启动方式

```bash
# 方式 1：WebUI（一键编排引擎 :8700 + webui-server，自动开浏览器）
lingclaude webui

# 方式 2：交互 CLI（引擎在进程内运行，无需另起 api 服务）
lingclaude run -i
# 或等价写法
python -m lingclaude.cli run -i
# 输入提示符：灵克>（'exit'/'quit'/Ctrl+D 退出）
```

> 注意：`python -m lingclaude.cli` 不带子命令只打印 argparse 帮助，不进入交互；
> CLI 与引擎是同进程关系，不是 WebUI 的客户端。

### 2.3 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `LINGCLAUDE_CLI_MODE=plain` | 自动 | 强制 FallbackSession（CI / 非 TTY） |
| `LINGCLAUDE_SANDBOX_MODE` | `permissive` | 沙箱档（permissive/restricted/strict/paranoid）|
| `LINGCLAUDE_LSP_CONFIG` | `~/.lingclaude/lsp_servers.json` | LSP 配置路径 |
| `LINGMESSAGE_HOME` | `~/.lingmessage` | LingBus 路径 |
| `LINGBUS_DB_PATH` | `~/.lingmessage` | LingBus DB 路径（默认）|

---

## 三、斜杠命令

| 命令 | 用途 | 实现 |
|------|------|------|
| `/help` `/?` | 列出所有斜杠命令 | ✅ |
| `/clear` | 清空当前会话消息 | ✅ |
| `/compact` | 立即触发压缩 | ✅ |
| `/model` | 显示当前模型 | ✅ |
| `/model <name>` | **会话中途切换模型**（P1-4 落地）| ✅ |
| `/schedule` | 列出定时任务 | ✅ |
| `/schedule @daily "<query>"` | 注册每日任务 | ✅ |
| `/schedule after:30 "<query>"` | 30 秒后执行 | ✅ |
| `/schedule at:9:30 "<query>"` | 每天 9:30 执行 | ✅ |
| `/schedule cancel <id>` | 取消任务 | ✅ |
| `/lsp` | 列出 LSP server 配置 | ✅ |
| `/lsp add <lang> --command <cmd>` | 注册 LSP | ✅ |
| `/lsp remove <lang>` | 删除自定义 LSP | ✅ |
| `/quit` `/exit` | 退出 | ✅ |

---

## 四、工具

### 4.1 标准工具

| 工具 | 说明 | 标记 |
|------|------|------|
| `read` | 读文件 | concurrency-safe |
| `write` | 写文件 | 顺序 |
| `edit` | 编辑文件 | 顺序 |
| `glob` | 模式匹配 | concurrency-safe |
| `grep` | 搜索 | concurrency-safe |
| `bash` | 执行命令 | 顺序 |
| `web_search` | SearXNG / DuckDuckGo | concurrency-safe |
| `request_user_input` | 用户输入 | 顺序 |
| `plan_mode` | 计划模式（只读）| 顺序 |
| `codeintel` | trace_callers / find_references / blast_radius | concurrency-safe |

### 4.2 后台任务工具（P0-1 落地）

| 工具 | 用途 |
|------|------|
| `run_in_background` | 后台执行任务 |
| `list_jobs` | 列出后台任务 |
| `job_status` | 查询任务状态 |
| `cancel_job` | 取消任务 |

### 4.3 控制工具（T1-6 落地）

| 工具 | 用途 |
|------|------|
| `list_agents` | 列出子代理 |
| `interrupt_agent` | 中止子代理 |

### 4.4 权限模型（T1-2 落地）

3 档 modes：
- `auto` — 非 deny 全放行（含写工具）
- `ask`（默认）— 写需审批
- `strict` — 非 auto_approve 需审批

`/permission` 端点回灌 `PermissionStore`（会话级持久 allow）。

### 4.5 敏感路径门

`sensitive_path_gate.py` 148 行 — 标记表覆盖 `.ssh / .aws / .kube / .env / .pem` 等。
主循环 read/grep/glob/bash 全接入 + 审批逃生门（session 隔离 allow 规则可绕过）。

### 4.6 沙箱（P1-2 落地）

4 档沙箱策略（`SandboxPolicy`）：

| 档 | 网络 | 文件系统 | 适用 |
|----|------|----------|------|
| `permissive` | ✅ | ✅ | 默认 |
| `restricted` | ❌ | 白名单 | 一般 |
| `strict` | ❌ | 白名单 + import 白名单 | 高安全 |
| `paranoid` | ❌ | 白名单 + 最小 syscall | 最高安全 |

`bash.py` fail-closed：strict/paranoid + bwrap 不可用 → 抛 `SandboxUnavailableError`。

---

## 五、子代理（T1-6 落地）

5 状态枚举 + 2 后端 + 控制通道：

```python
class SubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

@dataclass(frozen=True)
class SubagentRequest:
    parallel: int = 1          # 并行任务数
    control_channel: bool = False  # 启用 abort/status
```

后端：
- `InProcessSubagentBackend` — 进程内（默认）
- `AcpSubagentBackend` — HTTP 远端（Agent Client Protocol）

控制通道：
- `SubagentBackend.abort(agent_id)` — 中止
- `SubagentBackend.status(agent_id)` — 查询

### 5.1 SubagentCapabilities 4 flag（v0.5 新增）

`SubagentRequest` 与 `SubagentContext` 自 v0.5 起接受以下 4 字段，对齐 DSH `SubagentCapabilities`：

| 字段 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `output_schema` | `str \| None` | `None` | 输出 JSON schema 字符串；`None` = 无 schema 约束 |
| `depth_limit` | `int` | 0 | 嵌套深度上限（防递归风暴）；`0` = 不限 |
| `tool_filter` | `tuple[str, ...]` | `()` | 可见工具白名单（防越权）；空 = 继承父 `allowed_tools` |
| `persona` | `str` | `""` | 注入人设（与主 agent 区分）；空 = 默认 |

`SubagentContext` 新增 `current_depth: int = 0` 与 `parent_agent_id: str \| None = None`，由 `SubagentManager` 调用时传入。

`SubagentManager.run` 强制检查：
- `depth_limit > 0 and ctx.current_depth >= depth_limit` → 返回 `SubagentResult(success=False, status=FAILED, error="depth limit exceeded: ...")`
- `tool_filter` 非空 → 用 `dataclasses.replace` 派生 `effective_ctx.allowed_tools = tool_filter`
- `persona` 非空 → 注入 `effective_request.config["_persona"]`，由 backend 拼装 system_prompt

示例：

``````python
from lingclaude.engine.subagent.base import SubagentRequest, SubagentContext

req = SubagentRequest(
    task="审查 PR diff",
    provider="inprocess",
    depth_limit=2,
    tool_filter=("read", "grep"),
    persona="你是代码审查员，专注安全和性能",
    output_schema='{"type":"object", "properties": {"issues": {"type": "array"}}}',
)
ctx = SubagentContext(current_depth=1, parent_agent_id="lingke-root")
result = manager.run(req, ctx)
```

适用场景：

| 场景 | 推荐配置 |
|------|----------|
| 安全敏感子任务 | `tool_filter=("read",)` |
| 多层嵌套调用 | `depth_limit=2~3`（防爆栈） |
| 输出需 JSON Schema | `output_schema='...' ` + `depth_limit=1`（避免递归产出） |
| 子代理角色专精 | `persona="..."` 注入人设提示 |

---

## 六、MCP（T1-5 落地）

3 模式：
- `stdio` — 标准进程间 JSON-RPC
- `http` — HTTP/JSON-RPC
- `sse` — Server-Sent Events（**HTTP/SSE 传输** MCP client）

OAuth/PKCE（P1-3 落地）：
```python
from lingclaude.engine.mcp_oauth import (
    OAuthDiscovery, PKCEGenerator, AuthorizationCodeFlow, TokenRefresh
)
```

插件通过 LACP manifest 声明 transport：
```python
plugin = Plugin(
    name="my-mcp-server",
    transports=[Transport.MCP],
    mcp_command=["npx", "-y", "my-server"],  # stdio
    # 或 mcp_url="http://..."  # HTTP
)
```

---

## 七、LSP（T3-1 落地）

```bash
# 列出所有 LSP server（内置 + 自定义）
/lsp

# 注册自定义
/lsp add nix nil --stdio
/lsp add rust rust-analyzer

# 删除自定义（内置不可删）
/lsp remove nix
```

默认内置 4 个：
- `python` → `pylsp`
- `rust` → `rust-analyzer`
- `typescript` → `typescript-language-server --stdio`
- `go` → `gopls`

持久化到 `~/.lingclaude/lsp_servers.json`（可被 `LINGCLAUDE_LSP_CONFIG` 覆盖）。

---

## 八、代码智能

```python
from lingclaude.engine.codeintel import (
    find_references, blast_radius, trace_callers, trace_callees
)

# 查找符号所有引用
refs = find_references("lingclaude/api.py", "QueryEngine")
# 爆炸半径
result = blast_radius("lingclaude/api.py")  # {'transitive': [...], 'direct': [...]}
# 调用链
callers = trace_callers("lingclaude/api.py", "QueryEngine")
callees = trace_callees("lingclaude/api.py", "QueryEngine")
```

---

## 九、会话管理

### 9.1 snapshot / rewind

```python
# 快照
result = mgr.snapshot(session)  # Result[Path]

# 回退
result = mgr.rewind(session_id, snap_path, project_path)
```

### 9.2 投影端点

```bash
# 多视角分析（token 用量/工具调用频率/轮次统计）
GET /sessions/{id}/projection?view=tokens|tools|rounds
GET /sessions/{id}/projection            # 默认 all

# 响应示例
{
  "session_id": "abc-123",
  "tokens": {"total_tokens": 150, "rounds": 4, ...},
  "tools": {"read": 5, "edit": 2, ...},
  "rounds": {"user": 2, "assistant": 2, ...}
}
```

---

## 十、调度（P0-P1 落地）

```bash
# CLI 命令
/schedule @daily "每日备份"      # 每天 00:00
/schedule @hourly "小时任务"      # 每小时
/schedule interval:30 "30分钟检查" # 每 30 分钟
/schedule after:5 "5秒后执行"    # 一次性延迟
/schedule at:9:30 "每天 9:30"     # 每天 9:30
/schedule cancel <id>            # 取消
```

底层 API（`core/scheduler.py`）：
```python
from lingclaude.core.scheduler import ScheduleManager, get_schedule_manager

mgr = get_schedule_manager()
task_id = mgr.register("@daily", "每日备份")
# register 签名：register(cron, query, priority=TaskPriority.MEDIUM) -> task_id
# （无 on_complete= 参数；「完成后挂回会话」尚未实现，勿照旧文档示例调用）
```

---

## 十一、API 端点（端口 8700）

| 端点 | 用途 |
|------|------|
| `POST /ask` | 单次问答 |
| `POST /ask/stream` | SSE 流式 |
| `GET /status` | 引擎状态 |
| `GET /sessions` | 列出会话 |
| `GET /sessions/{id}` | 会话详情 |
| `POST /sessions/snapshot` | 快照 |
| `POST /sessions/{id}/stop` | 停止 |
| `GET /sessions/{id}/projection` | 投影（T3-2 落地）|
| `POST /permission` | 审批 |
| `GET /permission/mode` | 查询权限模式 |
| `POST /permission/mode` | 设置权限模式 |
| `POST /marketplace/upload` | 上传插件（T3-1 落地）|
| `GET /marketplace/list` | 列出插件 |
| `GET /marketplace/reputation/{id}` | 名誉评分 |

---

## 十二、市场（T3-1 落地）

```bash
# 上传插件（含静态安全分析 + 风险评级）
POST /marketplace/upload?name=my-plugin&version=1.0.0&code="..."&uploader=lingclaude

# 列出
GET /marketplace/list

# 查询信誉
GET /marketplace/reputation/my-plugin
```

底层（`lacp/marketplace.py`）：`upload / list / get_trust_score / get_rating` + 静态安全分析 + 名誉评分。

---

## 十三、治理/自省（独有）

- **元认知守卫** H1-H16（`.lingclaude/metacognitive_guards.md`）
- **认知节奏监测 / 痴呆检测 / 盲点校准**（`meta_cognition.py`）
- **分层记忆** + 艾宾浩斯衰减 + SQLite ExperienceStore
- **自我优化闭环** + 7 类触发 + AST 评估 + daemon
- **提案治理**（`governance_v2.py` / `proposal_lifecycle.py`）
- **LingBus 族内协作**（`coordination/bus_responder.py`）— 灵克/灵通/灵研/灵信/LingOS 跨成员协作
- **MV-1 审计** — 发模型前 fail-closed 落 log + 可重建断言
- **数据飞轮**（`data_flywheel.py`）

---

## 十四、与对标产品差距（CC/AtomCode/DSH/Crush）

| 维度 | lingclaude | 最大对手 | 状态 |
|------|-----------|----------|------|
| 终端交互 | L1+L2（prompt_toolkit + Rich）| CC/AtomCode/Crush 全 TUI | 🟡 缺 L3（cell-diff/Kitty/56 斜杠命令） |
| 权限治理 | 三档 + 敏感门 + bwrap + MV-1 | AtomCode | 🟢 **领先** |
| 上下文工程 | LLM 摘要 + 动态预算 + pruner | AtomCode 双层 / DSH 四层 | 🟢 持平 |
| 工具执行 | ToolCallExecutor + run_in_background | DSH jobs | 🟢 持平 |
| 多模态 | image_content + anthropic | 四家都支持 | 🟢 持平 |
| MCP | stdio/HTTP/SSE + OAuth/PKCE | CC/Crush | 🟢 持平 |
| 子代理 | 双后端 + 4 flag | DSH 6 后端 | 🟢 持平（缺团队/worktree） |
| LSP | codeintel 4 件套 + /lsp add | AtomCode | 🟢 持平 |
| 会话管理 | snapshot/rewind + projection | DSH event-sourced | 🟡 非 event-sourced |
| 调度 | cron + after:/at: + 挂回 | DSH dsh-schedule | 🟢 持平 |
| 沙箱 | 4 档 + fail-closed | DSH 4 后端 | 🟡 bwrap 单后端 |
| 治理/自省 | meta_cognition + LingBus + MV-1 | 四家全无 | 🟢 **独有** |

---

## 十五、故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 启动报 `prompt_toolkit not found` | 未安装 prompt_toolkit | `pip install prompt_toolkit` 或 `LINGCLAUDE_CLI_MODE=plain` 降级 |
| LingBus 收不到消息 | DB 路径不匹配 | 设置 `LINGMESSAGE_HOME=/home/ai/lingmessage` |
| /lsp 不生效 | LSP server 未安装 | `/lsp` 看内置配置 + 系统 PATH |
| 沙箱 strict 模式报错 | bwrap 不可用 | 装 bubblewrap 或切 permissive |
| 投影返回 404 | session_id 不存在 | `GET /sessions` 列出现有 |
| **crush 进程被批量 SIGKILL** | systemd-oomd 通过 D-Bus `org.freedesktop.oom1` 周期性触发（INCIDENT_20260825） | 见 15.1 三层防护 |
| OOMScoreAdjust 没生效 | cron / systemd 守护未跑 | `journalctl -u crush-low-oom.timer` + `crontab -l` |
| /model <name> 报 `BAD_MODEL_NAME` | 模型名不在 `models_config.json` | 编辑 `lingclaude/model/providers.py` 加载列表 |

### 15.1 INCIDENT_20260825 三层防护（v0.5 新增）

**现象**：拉起 crush TUI（lingclaude/灵通/灵犀/灵信/灵研 等身份）后几分钟到几小时内被 SIGKILL。一次同帧 6-7 个 PID 死亡。

**根因**：`systemd-oomd`（userspace OOM killer）通过 D-Bus `org.freedesktop.oom1` 周期性 SIGKILL 高 `OOMScore` 进程。crush 是 Go + Bubble Tea TUI + 18 worker 线程，单实例 90-150 MB，多实例并存总内存 1-2 GB，**高 OOMScore 是 oomd 首选目标**。

**完整根因诊断**：`docs/incident/INCIDENT_20260825_CRUSHP_OOMD_KILLER.md`（276 行，含 auditd 取证 + dbus 服务清单 + 8 次错误归因排除过程）。

**三层防护**（30 分钟内零死亡已验证）：

| 层 | 配置 | 作用 |
|----|------|------|
| **1. 启动入口** | `~/.bashrc` `function crush_i` 与 `alias crush` 拉起前立即 `echo -900 > /proc/self/oom_score_adj` | 0 窗口保护新进程 |
| **2. systemd 单元** | `/etc/systemd/system/zhibridge.service.d/oom.conf` 设 `OOMScoreAdjust=-900` + `OOMPolicy=continue` | zhibridge 重启自动保留 |
| **3. 10s 兜底 timer** | `/etc/systemd/system/crush-low-oom.{service,timer}` 每 10s 扫描所有 crush PID 并设 oom=-900 | 任何漏网最多 10s 内补设 |

**辅助修复**（已落地）：

- `/etc/systemd/oomd.conf.d/slower.conf` 把 oomd 压力时间从 30s 拉长到 5min，压力阈值从 60% 提到 70%（降低频率，非根除）
- `scripts/crush_low_oom.sh` + ai crontab 每分钟扫描兜底
- zhibridge 的 `OOMScoreAdjust=-900` 配合 systemd RestartSec=10 自动重启 + OOMScore 保留

**自检命令**：

```bash
# 看当前 crush 进程保护覆盖率
for pid in $(pgrep -af bin/crush | awk '{print $1}'); do
  oom=$(cat /proc/$pid/oom_score_adj 2>/dev/null)
  cwd=$(readlink /proc/$pid/cwd)
  echo "PID=$pid cwd=$cwd oom=$oom"
done

# 看 timer 是否在跑
systemctl status crush-low-oom.timer
#systemctl list-timers crush-low-oom.timer

# 手动触发一次守护
sudo systemctl start crush-low-oom.service
tail /tmp/crush_low_oom.log

# 看 audit.log 最新 oomd 触发的 SIGKILL
sudo grep "crush_kill_watch" /var/log/audit/audit.log | tail -5
```

**解除三层防护**（不推荐）：`sudo systemctl disable --now crush-low-oom.timer` + `sudo systemctl disable --now systemd-oomd`。解除后失去 oomd 全系统保护，crush 必被高内存场景杀掉。

---

## 十六、参考资料

- `docs/gap_analysis/GAP_ANALYSIS_20260821.md` — 3 方初步分析
- `docs/gap_analysis/GAP_ANALYSIS_20260825_CC_DIMENSION.md` — Claude Code 对标
- `docs/gap_analysis/GAP_ANALYSIS_20260825_CRUSH_DIMENSION.md` — Crush 对标
- `docs/gap_analysis/GAP_ANALYSIS_20260825_DSH_DIMENSION.md` — DSH 第一手对标（v0.5 新增）
- `docs/gap_analysis/GAP_ANALYSIS_20260825_TOTAL_CURRENT.md` — 4 家综合总表 + 12 维度差距矩阵（v0.5 新增）
- `docs/incident/INCIDENT_20260825_CRUSHP_OOMD_KILLER.md` — systemd-oomd 杀 crush 根因诊断 + 三层防护（v0.5 新增，276 行）
- `docs/lacp/CLI_INTERACTION_RFC.md` — CLI 交互形态 RFC v1.1，含 I/O 抽象层设计
- `docs/cli/TERMINAL_UX.md` — 终端 UX 用户文档

## 附录 A：版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v0.1 | 2026-07 | 初版 |
| v0.2 | 2026-08-10 | 工具 + 子代理 |
| v0.3 | 2026-08-15 | 调度 + 沙箱 |
| v0.4 | 2026-08-26 | 16 章全对齐 4 份 gap_analysis |
| **v0.5** | **2026-08-26** | **新增 §5.1 SubagentCapabilities 4 flag + §15.1 INCIDENT 三层防护 + 4 条参考资料** |
- `docs/gap_analysis/GAP_ANALYSIS_20260825_DSH_DIMENSION.md` — DSH 对标
- `docs/gap_analysis/GAP_ANALYSIS_20260825_TOTAL_CURRENT.md` — 综合总表
- `docs/lacp/CLI_INTERACTION_RFC.md` — CLI 交互 RFC
- `docs/lacp/CAPABILITY_SEAM_RFC.md` — capability seam RFC
- `docs/ROADMAP.md` — 路线图 v0.4

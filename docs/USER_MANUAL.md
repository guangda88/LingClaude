# 灵族 Coding Agent 用户手册（灵克 LINGKERNEL_v1）

> 版本: v0.4.0（LINGKERNEL_v1 + P0/P1 新功能）· 2026-08-21
> 适用: 灵克 (lingclaude) — 灵族收敛后的统一 coding agent 产品

**2026-08-21 新增功能**：
- P0-1: Todo list tool（会话级任务追踪）
- P0-2: Tool result pruning（大输出自动 spill 到文件）
- P0-3: request_user_input tool（交互式用户输入）
- P0-4: Session snapshot/rewind（会话快照与回滚）
- P1-1: LSP 集成（代码导航：goto_def / find_refs / hover / goto_impl）
- P1-6: Code intelligence（trace_callers / trace_callees / blast_radius / find_references）
- daemon 定时快照绑定（每 5 轮自动快照）

---

## 1. 产品简介

### 1.1 灵克是什么

灵克 (lingclaude) 是灵族（12 个 AI 成员组成的协作体）收敛后的**统一 AI 编程助手**——对标 Claude Code，但**本地运行、开源（MIT）、内置自优化能力**。

- **自知 → 自觉 → 自决 → 进化**：不只是"回答问题"的工具，它会记录自己的行为、识别错误模式、持续自我优化
- **零云端依赖**：核心逻辑本地运行，数据不出你的机器
- **灵族一员**：通过 LingBus 与灵研（研究）、灵信（通信）、灵犀（MCP 工具桥）、灵知（知识）等 11 个成员协作

### 1.2 LINGKERNEL_v1 内核（2026-08 重构）

灵克在 2026-08 完成了 LINGKERNEL_v1 内核重构，对齐 DeepSeek Harness (dsh) 的工程架构：

| 组件 | 说明 |
|------|------|
| 6 包 spine | session_store / model_adapter / audit_collector / model_request_log / turn_learner / system_prompt_builder |
| ToolPipeline | 工具执行 5 段流水线（pre-execute → guards → execute → post-execute → finalizeContent）|
| ToolDefinition | 工具声明补齐 output / is_concurrency_safe / finalize_content / present 4 字段 |
| MV-1 不变量 | "模型可见即已记录"——每次模型请求都可由日志重建 |

### 1.3 与常见工具对比

| 能力 | Claude Code | Crush | AtomCode | 灵克 |
|------|:-----------:|:-----:|:--------:|:----:|
| 代码理解与编辑 | ✅ | ✅ | ✅ | ✅ |
| 终端/Shell 操作 | ✅ | ✅ | ✅ | ✅ |
| 会话管理 | ✅ | ✅ | ✅ | ✅ |
| 权限控制 | ✅ | — | ✅ | ✅ |
| 自我优化 | ❌ | ❌ | 部分 | ✅（7 类触发 + AST 评估）|
| 自我学习 | ❌ | ❌ | 部分 | ✅（6 种模式检测 + 知识库）|
| 本地运行 | ❌ | ✅ | ✅ | ✅（零云端依赖）|
| 开源 | ❌ | ✅ | — | ✅（MIT）|

---

## 2. 安装与环境要求

### 2.1 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.10 | 运行时 |
| pip | 任意 | 安装器 |
| 可选: LLM API Key | 见 2.3 | 模型调用凭据 |

### 2.2 安装

```bash
# 从源码安装
cd /home/ai/lingclaude
pip install -e .

# 验证安装
lingclaude --help

# 也可不安装直接运行
python3 -m lingclaude.cli --help
```

### 2.3 API Key 配置（二选一）

```bash
# 方式 1: 环境变量
export OPENAI_API_KEY="sk-..."        # OpenAI 兼容端点
# 或
export ANTHROPIC_API_KEY="sk-ant-..." # Anthropic 端点

# 方式 2: 配置文件（推荐，见第 4 章）
# /home/ai/lingclaude/config.yaml 的 model.api_key 字段
```

> 灵族环境内（如本机 zhineng-ai）通常已配置好模型通路（proxy3 :8765 / atomgit_proxy :13457），无需手动配置 key。详见第 4 章"模型路由"。

### 2.4 安装后目录结构

```
/home/ai/lingclaude/
├── config.yaml              # 主配置（模型/引擎/自优化参数）
├── lingclaude/
│   ├── cli/                 # 命令行入口
│   ├── core/                # LINGKERNEL_v1 6 包 spine
│   ├── engine/              # ToolPipeline + coding 引擎
│   └── model/               # 模型 Provider 适配
├── docs/                    # 文档
└── tests/                   # 测试
```

---

## 4. 配置说明

### 4.1 配置文件

灵克主配置位于 `/home/ai/lingclaude/config.yaml`。推荐修改前备份：

```bash
cp /home/ai/lingclaude/config.yaml /home/ai/lingclaude/config.yaml.bak
```

### 4.2 模型配置（model）

```yaml
model:
  provider: "deepseek"           # 模型服务商: deepseek / nvidia / minimax / volcengine / hunyuan
  api_key: "your-key-here"       # API Key（优先从环境变量读取）
  base_url: "https://api.deepseek.com"  # 可选：自定义端点
  timeout: 120                   # 请求超时（秒）
```

灵族内已预配置 proxy3（`:8765`）和 atomgit_proxy（`:13457`），可直接使用。

### 4.3 模型路由（task_routes）

灵克支持按任务类型自动选择最优模型。路由配置位于 `routing.task_routes`：

| 路由名 | 用途 | 示例模型 |
|--------|------|---------|
| `fast_response` | 快速响应/闲聊 | deepseek-v4-flash |
| `coding` | 代码生成/修改 | qwen3-coder-480b-a35b, deepseek-v4-pro |
| `chinese_reasoning` | 中文推理 | glm-5.1, deepseek-v4-pro |
| `english_general` | 英文通用 | llama-3.3-70b-instruct |
| `long_context` | 长上下文 | deepseek-v4-pro, qwen3.5-397b-a17b |
| `vision` | 视觉理解 | llama-3.2-90b-vision-instruct |
| `thinking` | 深度思考 | kimi-k2-thinking, deepseek-v4-pro |
| `safety` | 安全审核 | deepseek-v4-flash, hunyuan-lite |
| `embedding` | 向量嵌入 | nv-embed-v1, bge-m3 |

每条路由有多个候选模型，按顺序 round-robin 轮询。

### 4.4 自优化配置（optimization）

```yaml
optimization:
  enabled: true
  trigger_threshold: 3      # 连续失败 N 次后触发优化
  max_iterations: 10       # 最大优化迭代次数
  ast_eval_weight: 0.7     # AST 评估权重（0-1）
  pattern_match_weight: 0.3
```

### 4.5 治理配置（governance）

```yaml
governance:
  enabled: true
  require_dual_sign: true   # 高风险操作需二次确认
  audit_enabled: true      # 操作审计
```

### 4.6 环境变量

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI 兼容端点的 Key |
| `ANTHROPIC_API_KEY` | Anthropic 端点的 Key |
| `LINGCLAUDE_CONFIG` | 覆盖默认配置文件路径 |

---

### 3.1 单次提问（最常用）

```bash
lingclaude run "列出当前目录的 Go 文件"
```

- 一次提问一次回答，适合快速查询
- 加 `-v` 查看 token 用量：`lingclaude run "hello" -v`

### 3.2 交互模式（多轮对话）

```bash
lingclaude run -i "开始对话"
```

- 连续对话，支持追问与上下文
- 输入 `exit` 或 `quit` 退出
- 交互中可自然切换话题

```
灵克> 帮我看看这个项目结构
灵克> 这个函数哪里有问题？
灵克> exit
```

### 3.3 项目分析

```bash
lingclaude analyze <项目路径>
```

- 结构分析：包/模块/依赖关系
- 适合接手陌生项目前先了解全貌

### 3.4 自优化

```bash
# 对项目执行自优化（指定优化目标）
lingclaude optimize -t <项目路径> -g "降低复杂度"

# 查看质量指标
lingclaude metrics

# 启动自优化守护（后台周期执行）
lingclaude daemon
```

### 3.5 会话与知识库

```bash
lingclaude session list          # 查看历史会话
lingclaude knowledge stats       # 知识库统计
```

### 3.6 Todo list（会话级任务追踪）

```bash
# 创建任务
lingclaude run "todo create: 修复登录 bug，优先级 3，标签: bug,auth"

# 列出所有任务
lingclaude run "todo list"

# 列出进行中的任务
lingclaude run "todo list status=in_progress"

# 完成任务
lingclaude run "todo complete <task_id>"

# 取消任务
lingclaude run "todo cancel <task_id>"
```

> 任务持久化在 SQLite（会话级），重启后保留。

### 3.7 request_user_input（交互式用户输入，P0-3）

在 agent 运行过程中主动向用户请求输入，支持三种模式：

```bash
# 单选模式（用户输入编号或直接回答）
lingclaude run "request_user_input mode=single options=[{label:是},{label:否}] question=确认继续？"

# 多选模式（逗号分隔编号）
lingclaude run "request_user_input mode=multiple options=[{label:Python},{label:Rust},{label:Go}] question=选择语言？"

# 自由文本模式
lingclaude run "request_user_input mode=text question=请描述你的需求？"
```

**参数说明**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `header` | string | 可选标题标签 |
| `question` | string | 要询问的问题 |
| `mode` | string | `single`（单选）/ `multiple`（多选）/ `text`（自由文本）|
| `options` | array | 选项列表，每项 `{label, description?}`，用于 single/multiple 模式 |

**返回**：`{"ok": true, "answer": "...", "mode": "..."}`

### 3.8 LSP 代码导航（P1-1）

通过 LSP（Language Server Protocol）实现精确的代码跳转，无需 grep 猜测。

```bash
# 跳转到符号定义
lingclaude run "lsp goto_def /path/to/file.py 42 8"

# 查找所有引用
lingclaude run "lsp find_refs /path/to/file.py 42 8"

# 获取符号类型/文档
lingclaude run "lsp hover /path/to/file.py 42 8"

# 跳转到接口实现
lingclaude run "lsp goto_impl /path/to/file.py 42 8"
```

参数说明：`file_path` 为绝对路径，`line` 和 `character` 均为 **0-based** 起始。

**自动检测语言服务器**：根据项目根目录的 `pyproject.toml`（Python → pylsp）或 `Cargo.toml`（Rust → rust-analyzer）自动选择对应 LSP server。

### 3.9 Code intelligence（P1-5/P1-6）

基于 AtomCode codeintel 实现（Rust bindings + Python AST fallback）。**注意**：codeintel 与 LSP 均未作为独立 CLI 工具注册，代码导航统一通过 `lsp` 工具（见 3.8 LSP 代码导航）提供 `goto_def` / `find_refs` / `hover` / `goto_impl`；graph 能力暂合并进 LSP 导航，不单独暴露命令行入口。

### 3.10 Tool Result Pruning（P0-2）

大输出自动 spill 到文件，防止爆上下文。

当工具输出超过 **4 096 token**（约 16 KB）时，结果自动写入临时文件，只返回定位符：

```json
{
  "_spilled": true,
  "tool": "grep",
  "locator": "/home/ai/lingclaude/data/spill/spill_grep_xxx.txt",
  "size_bytes": 48230,
  "truncated": true,
  "read_with": "cat /home/ai/lingclaude/data/spill/spill_grep_xxx.txt"
}
```

查看 spill 文件：
```bash
ls /home/ai/lingclaude/data/spill/
cat /home/ai/lingclaude/data/spill/spill_grep_xxx.txt
```

> 阈值可在 `lingclaude/engine/tool_pipeline.py` 的 `_OUTPUT_TOKEN_LIMIT` 常量调整。

### 3.11 Session 快照与回滚（P0-4）

```bash
# 查看当前会话快照列表
ls .lingclaude/sessions/snapshot_*.json

# daemon 自动快照：每 5 轮优化自动保存
# daemon 需要子命令：status / run / watch / reset
lingclaude daemon status   # 查看守护状态
lingclaude daemon run      # 启动守护
lingclaude daemon watch    # 前台观察执行轮次
lingclaude daemon reset    # 重置守护状态
# 重启后自动恢复到最近快照
lingclaude daemon run
```

快照存储在 `.lingclaude/sessions/snapshot_<session_id>_<timestamp>.json`（与普通会话文件同目录，`snapshot_` 前缀区分）。

**daemon 定时快照机制**：
```bash
# 启动 daemon 后，每完成 5 轮优化自动执行一次 snapshot
lingclaude daemon run

# daemon 重启时，自动恢复到最近一次快照
# 日志中可见：已从快照恢复 session: snapshot_xxx.json
```
快照间隔可在 `lingclaude/self_optimizer/daemon.py` 的 `_snap_interval` 常量调整（默认 5）。

### 3.12 其他命令

```bash
lingclaude governance-audit      # 治理投票审计
lingclaude --config <path> ...   # 指定配置文件
```

### 3.13 工具列表（lingclaude 当前共 26 个 ToolDefinition）

| 工具 | 类型 | 说明 |
|------|------|------|
| `bash` | 执行 | 原生 shell 执行 |
| `bash_lingxi` | 执行 | 通过灵犀 MCP 执行的命令 |
| `read` | 读 | 读取文件（支持 offset/limit）|
| `write` | 写 | 写入文件 |
| `edit` | 写 | 编辑文件（支持备份/回滚）|
| `file_create` | 写 | 创建新文件 |
| `file_insert` | 写 | 在指定行插入文本 |
| `file_delete_lines` | 写 | 删除文件行范围 |
| `file_undo` | 写 | 撤销上次 edit |
| `glob` | 搜索 | 按模式查找文件 |
| `grep` | 搜索 | 正则搜索文件内容 |
| `git_status` | 查询 | 显示 Git 状态 |
| `git_diff` | 查询 | 显示变更内容 |
| `git_log` | 查询 | 显示提交历史 |
| `git_blame` | 查询 | 显示逐行责任人 |
| `index_project` | 索引 | 项目符号索引（递归扫描目录）|
| `ast_replace` | 编辑 | AST 级函数替换 |
| `list_functions` | 索引 | 列出文件内函数 |
| `stt` | 工具 | 语音转文字 |
| `web_fetch` | 搜索 | URL 内容抓取 |
| `web_search` | 搜索 | Web 搜索 |
| `todo` | 协作 | **P0-1** 任务管理（create/list/complete/cancel）|
| `request_user_input` | 交互 | **P0-3** 请求用户输入（single/multiple/text）|
| `lsp` | 导航 | **P1-1** LSP 代码导航（goto_def/find_refs/hover/goto_impl）|
| `plan_mode` | 特殊 | 规划模式 |
| `sub_agent` | 协作 | 子代理调用 |

### 3.14 命令总览

```
usage: lingclaude [-h] [--config CONFIG]
                  {run,optimize,analyze,session,knowledge,daemon,metrics,governance-audit} ...

  run                 运行灵克（单次/交互）
  optimize            自优化
  analyze             代码结构分析
  session             会话管理
  knowledge           知识库管理
  daemon              自优化守护
  metrics             质量指标
  governance-audit    治理投票审计
```

---

## 5. 自优化能力

### 5.1 自优化原理

灵克内置 7 类自优化触发模式，持续改进自身行为质量：

| 触发类型 | 说明 |
|---------|------|
| 连续失败（repeated_failure） | 同一任务连续失败 N 次后触发根因分析 |
| 错误模式（error_pattern） | 识别到高频错误模式后预防性优化 |
| 性能退化（performance_degradation） | 响应质量/速度下降时触发 |
| 知识库命中（kb_miss） | 知识库无相关经验时触发探索学习 |
| 用户纠正（user_correction） | 用户指出错误后立即学习 |
| 自我复盘（self_review） | 每次会话结束后主动复盘 |
| 定期审计（periodic_audit） | 周期性生活质量评估 |

### 5.2 自优化工作流

```
检测到触发条件
    ↓
收集上下文（错误日志/对话历史/代码片段）
    ↓
AST 模式匹配（识别代码 smell / 行为 pattern）
    ↓
生成优化建议（基于知识库历史经验）
    ↓
用户确认或自动应用
    ↓
更新知识库（learnings 表）
```

### 5.3 自优化命令

```bash
# 对指定项目运行自优化
lingclaude optimize -t /path/to/project -g "降低圈复杂度"

# 查看质量指标
lingclaude metrics

# 启动自优化守护（后台持续运行）
lingclaude daemon

# 查看自优化历史
lingclaude optimize --history
```

### 5.4 知识库

```bash
lingclaude knowledge stats       # 查看知识库统计
lingclaude knowledge search <关键词>  # 搜索历史经验
lingclaude knowledge clear       # 清空知识库（慎用）
```

---

## 6. 灵族生态集成

### 6.1 灵族 12 成员一览

| 成员 | 角色 | 与灵克的关系 |
|------|------|------------|
| 灵克 (lingclaude) | 统一 AI 编程助手 | 主体 |
| 灵研 (lingresearch) | 深度研究与学术分析 | 提供 spec review、学术支撑 |
| 灵信 (lingmessage) | 跨 agent 通信总线 | 灵克通过它发消息给其他成员 |
| 灵犀 (lingxi) | MCP 服务器，工具桥梁 | 灵克的工具执行层（bash/file/search）|
| 灵知 (lingzhi) | 知识管理 | 灵克的知识库后端 |
| 灵安 (lingan) | 安全门禁 | 灵克 governance 的安全决策层 |
| 灵通 (lingflow) | 协调工作流 | 灵克的多步骤任务编排 |
| 灵极优 (lingminopt) | 极致优化 | 灵克自优化的 provider 层 |
| 灵创 (lingcreate) | 创作助手 | 代码/文档生成辅助 |
| 灵柚 (lingyou) | 体验优化 | UI/UX 相关任务 |
| 灵通问 (lingtongask) | 问答系统 | FAQ 检索 |
| 灵网 (lingweb) | Web 搜索 | 灵克的联网搜索能力 |

### 6.2 通过 LingBus 发消息

```python
from lingclaude.optional.lingbus_client import post_message

# 向灵研发消息
post_message(
    sender="lingclaude",
    recipient="lingresearch",
    subject="请求 spec review",
    body="请 review lingclaude/core/query_engine.py 的接口设计",
    channel="ecosystem"
)
```

### 6.3 权限控制与治理

- **高风险操作**（删除文件/修改配置）：需要二次确认
- **敏感目标**（credentials/.env/CRUSH.md）：自动 PENDING_REVIEW
- **所有操作**：写入审计日志

```bash
lingclaude governance-audit      # 查看治理审计日志
```

### 6.4 工具执行（通过灵犀）

| 工具 | 说明 |
|------|------|
| bash | 执行 shell 命令 |
| read_file / write_file | 文件读写 |
| glob / grep | 搜索 |
| sandbox | 安全沙箱执行 |

---

## 7. 故障排查与 FAQ

### 7.1 常见错误

#### HTTP 410 — 模型 EOL

```
[错误] HTTP 410: {"type":"about:blank","title":"Gone","status":410,"detail":"The model 'xxx' has reached its end of life..."}
```

**原因**：config.yaml 中 task_routes 引用了已下线的模型。
**解决**：从对应路由的 `models` 列表中移除该模型，保留 deepseek-v4-flash 作为兜底。

```bash
# 临时修复：把 fast_response 收敛到唯一可用模型
python3 -c "
import json
with open('/home/ai/lingclaude/config.json') as f:
    cfg = json.load(f)
cfg['routing']['task_routes']['fast_response']['models'] = [
    {'provider': 'deepseek', 'model': 'deepseek-v4-flash'}
]
with open('/home/ai/lingclaude/config.json', 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print('已修复')
"
```

#### HTTP 401 — 认证失败

```
[错误] HTTP 401: Unauthorized
```

**原因**：API Key 无效或过期。
**解决**：
1. 确认环境变量 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 已设置
2. 或检查 config.yaml 中 `model.api_key` 是否正确
3. 灵族内机器可使用 proxy3（`:8765`）或 atomgit_proxy（`:13457`）

#### 网络超时

```
[错误] 网络错误: The read operation timed out
```

**原因**：请求超时（默认 120s），或网络不通。
**解决**：
1. 加大 timeout：`model.timeout: 300`
2. 确认 base_url 可达：`curl -s -o /dev/null -w "%{http_code}" https://api.deepseek.com`
3. 检查代理设置

#### 模型返回空响应

```
[错误] 模型返回空响应
```

**原因**：模型服务暂不可用或请求被限流。
**解决**：
1. 等待 30s 后重试
2. 切换到备用模型路由

### 7.2 自优化故障

#### 自优化未触发

1. 确认 `optimization.enabled: true`
2. 检查 `trigger_threshold` 是否过高（默认 3）
3. 查看日志：`lingclaude optimize --history`

#### 知识库为空

正常现象。新安装或刚清空的灵克需要积累交互经验后才会有知识库记录。

### 7.3 配置相关

#### 修改 config.yaml 后不生效

```bash
# 确认配置文件路径正确
lingclaude --config /path/to/config.yaml run "hello"

# 确认 YAML 语法正确
python3 -c "import yaml; yaml.safe_load(open('/home/ai/lingclaude/config.yaml'))"
```

### 7.4 日志与调试

```bash
# 查看详细日志（DEBUG 级别）
lingclaude run "hello" -v

# 查看上次会话的 token 用量
lingclaude run "hello" -v   # -v 打印本次调用的 token 用量（输入/输出/总计）
# session list 仅列出历史会话的 sid/项目/创建时间，不含 token 用量
lingclaude session list
```

### 7.5 升级与回退

```bash
# 升级到最新版
pip install -e /home/ai/lingclaude --upgrade

# 回退到备份版本
pip install -e /home/ai/lingclaude --force-reinstall --no-deps
```

### 7.6 FAQ

**Q: 灵克需要联网吗？**
A: 核心逻辑完全本地运行。模型调用需要联网（或者连接本地部署的模型服务端点）。

**Q: 能否完全离线使用？**
A: 可以，但需要本地模型服务端点（如 llama-server）。修改 `model.base_url` 指向本地端口即可。

**Q: 灵克和其他灵族工具有什么区别？**
A: 灵克是编程专用；灵犀（lingxi）是工具执行层；灵研（lingresearch）是研究分析；灵通（lingflow）是工作流协调。灵克调用其他成员的能力，而不是重复实现。

**Q: 如何报告 bug？**
A: 通过 LingBus 发消息给灵克（lingclaude），附上错误日志和复现步骤。

---

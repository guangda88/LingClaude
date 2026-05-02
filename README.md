# 灵克 (LingClaude)

> 开源 AI 编程助手，对标 Claude Code，内置自优化能力——越用越懂你。

**Version**: 0.2.1 | **Python**: >=3.10 | **License**: MIT

## 为什么做灵克？

Claude Code 很强，但它是闭源的、云端的、不可定制的。

灵克的答案是：**一个你可以完全掌控的 AI 编程助手。**

- 本地运行，数据不出你的机器
- 内置自优化框架，不是静态工具，会持续自我进化
- 开源，社区驱动，你可以改任何东西

## 灵克 vs Claude Code

| 能力 | Claude Code | 灵克 |
|------|------------|------|
| 代码理解与编辑 | ✅ | ✅ |
| 终端/Shell 操作 | ✅ | ✅ |
| 会话管理 | ✅ | ✅ |
| 权限控制 | ✅ | ✅ |
| 自我优化 | ❌ | ✅ 7类触发 + AST评估 |
| 自我学习 | ❌ | ✅ 6种模式检测 + 知识库 |
| 开源 | ❌ | ✅ MIT |
| 本地运行 | ❌ | ✅ 零云端依赖 |

## 快速开始

```bash
# 安装
pip install -e .

# 设置 API Key（二选一）
export OPENAI_API_KEY="sk-..."
# 或
export ANTHROPIC_API_KEY="sk-ant-..."

# 单次提问
lingclaude run "你的编程问题"

# 交互模式
lingclaude run -i "开始对话"

# 查看用量
lingclaude run "hello" -v

# 其他命令
lingclaude analyze <项目路径>
lingclaude optimize -t <项目路径> -g "降低复杂度"
lingclaude session list
lingclaude knowledge stats

# HTTP API服务（需设置环境变量 LINGCLAUDE_API_KEYS）
export LINGCLAUDE_API_KEYS="your-api-key-1,your-api-key-2"
python3 -m lingclaude.api

# API端点（认证通过 X-API-Key 头）
curl -X POST http://localhost:8000/v1/submit \
  -H "X-API-Key: your-api-key-1" \
  -H "Content-Type: application/json" \
  -d '{"query": "解释这段代码"}'

# 或不安装直接运行
python3 -m lingclaude.cli --help
```

## 架构

```
灵克 (LingClaude)
├── core/               # 基础层
│   ├── types.py        #   Result[T] 单子（ok/fail 工厂方法）
│   ├── config.py       #   YAML 配置 → dataclass
│   ├── models.py       #   ToolDefinition, PermissionDenial, UsageSummary 等
│   ├── session.py      #   Session（不可变）+ SessionManager（JSON 持久化）
│   ├── permissions.py  #   PermissionContext（deny_tools + deny_prefixes）
│   ├── behavior.py     #   BehaviorMetrics（情绪/意图/幻觉检测 + 行为追踪）
│   ├── intel.py        #   IntelCollector + DailyDigest + IntelRelay（情报系统）
│   └── query_engine.py #   QueryEngine：turn循环、流式输出、自动压缩、情报收集
│
├── model/              # 模型抽象层
│   ├── types.py        #   ModelMessage, ModelResponse, ModelProvider ABC
│   ├── openai_provider.py   # OpenAI API 实现（sync + async）
│   ├── anthropic_provider.py # Anthropic API 实现（sync + async）
│   └── factory.py      #   create_provider() 工厂 + 自动检测
│
├── engine/             # 工具执行层
│   ├── tools.py        #   ToolRegistry（注册/注销/执行）
│   ├── bash.py         #   BashExecutor（黑名单 + 资源限制 + 超时）
│   ├── file_ops.py     #   FileOps（读写/编辑/搜索 + 路径包含检查）
│   ├── file_read.py    #   FileReadTool（文件读取工具 + 路径遍历防护）
│   ├── file_edit.py    #   FileEditTool（文件编辑工具 + 路径遍历防护）
│   ├── grep.py         #   GrepTool（代码搜索工具）
│   ├── stt.py          #   STTEngine（语音转文字引擎）
│   └── coding.py       #   CodingRuntime（工具 + 评估 + 优化 + 模式检测）
│
├── api/                # HTTP API层
│   └── api.py          #   FastAPI HTTP服务（认证、路径遍历防护、文件操作）
│
├── self_optimizer/     # 自优化框架
│   ├── trigger.py      #   OptimizationTrigger（8类触发条件）
│   ├── evaluator.py    #   StructureEvaluator（AST 分析）
│   ├── optimizer.py    #   SynchronousOptimizer + SimpleSearchSpace
│   ├── advisor.py      #   OptimizationAdvisor（Markdown 报告）
│   └── learner/        #   Phase 5：自学习引擎
│       ├── models.py   #     FeedbackItem, Pattern, LearnedRule
│       ├── rule_extractor.py  # 规则提取 + 去重 + 验证
│       ├── knowledge.py       # SQLite 知识库 + 内存知识库
│       └── patterns.py        # 6种检测器
│
└── cli/                # 命令行界面
    └── app.py          #   子命令：run, optimize, analyze, session, knowledge
```

## 自优化流程

```
触发（8类条件）→ 评估（AST指标）→ 优化（optuna/网格搜索）→ 报告（Markdown）
                                                                ↓
                                                         知识库（规则提取 + 模式识别）
```

### 8类触发条件

用户请求 | 质量低于阈值 | 行为异常 | 结构违规 | 性能退化 | 规模增长 | 技术债累积 | 定时优化

### 6种模式检测器

长方法 | 未用变量 | 硬编码密钥 | 重复代码 | 空块 | 高复杂度

## 模型配置

灵克支持 OpenAI 和 Anthropic 两种模型提供商，通过 `config.yaml` 配置：

```yaml
model:
  provider: openai          # openai 或 anthropic
  model: gpt-4o             # 模型名称
  api_key: ""               # 建议用环境变量，不要明文写
  base_url: null            # 可选：自定义 API 端点
  max_tokens: 4096
  temperature: 0.7
  system_prompt: "你是灵克，一个 AI 编程助手。"
```

**优先级**: `config.yaml` 中的 `api_key` > 环境变量 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`

**自动检测**: 如果不指定 `provider`，会根据模型名称自动选择：
- `gpt-*`、`o1-*`、`o3-*`、`o4-*` → OpenAI
- `claude-*` → Anthropic
- 其他 → 默认 OpenAI

## 通用配置

编辑项目根目录 `config.yaml`：

```yaml
system:
  name: 灵克
  log_level: INFO

engine:
  max_turns: 8
  max_budget_tokens: 200000
  compact_after_turns: 12
  structured_output: false

permissions:
  deny_tools: []
  deny_prefixes: []

self_optimizer:
  triggers:
    quality:
      enabled: true
      review_score_threshold: 60
    structure:
      enabled: true
      max_complexity: 15
      max_method_lines: 50
  optimization:
    goal: structure
    max_trials: 50
    timeout_seconds: 120

session:
  save_dir: .lingclaude/sessions/
  max_history: 100

intel:
  enabled: true
  output_dir: .lingclaude/intel/
  session_history_path: data/session_history.json
  auto_collect_behavior: true
  auto_relay: true
  relay_target: ""
  digest_hour: 23
```

## 情报系统

灵克内置情报收集系统，每日自动汇总情报：

```
行为感知 → IntelCollector（8类情报）→ DailyDigestGenerator（日报）→ IntelRelay（文件输出）
                                                              ↓
                                                     session_history.json（各成员消费）
```

### 8类情报类别

文件变更 | 代码模式 | 行为异常 | 错误情报 | 优化记录 | 结构情报 | 质量情报 | 安全情报

## HTTP API

灵克提供FastAPI HTTP服务，支持远程调用和集成：

### 启动服务

```bash
export LINGCLAUDE_API_KEYS="key1,key2,key3"
python3 -m lingclaude.api --host 0.0.0.0 --port 8000
```

### API端点

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/v1/submit` | POST | 提交查询 | `X-API-Key` 头 |
| `/v1/sessions` | GET | 列出会话 | `X-API-Key` 头 |
| `/v1/sessions/{id}` | GET | 获取会话详情 | `X-API-Key` 头 |
| `/v1/read-file` | POST | 读取文件 | `X-API-Key` 头 + 路径验证 |
| `/v1/write-file` | POST | 写入文件 | `X-API-Key` 头 + 路径验证 |
| `/health` | GET | 健康检查 | 无需认证 |

### 安全特性

- API Key认证（禁止空密钥）
- 路径遍历防护（禁止访问项目目录外文件）
- 文件操作白名单
- Session ID使用加密安全随机数生成器

详细API文档见：[docs/AUDIT_REPORT_2026-04-06.md](docs/AUDIT_REPORT_2026-04-06.md)

## LingXi 集成

灵克支持使用 LingXi（灵犀）MCP 服务器进行安全的终端命令执行。LingXi 是一个基于 MCP（Model Context Protocol）的终端执行服务，提供增强的安全性和监控能力。

### 什么是 LingXi？

LingXi 是一个 MCP 服务器，通过 stdio JSON-RPC 2.0 协议提供终端命令执行服务。它具有以下特性：

- **增强安全性**：使用 `execFile` 而非 `exec`，防止 shell 注入攻击
- **黑名单/白名单过滤**：支持自定义命令过滤规则
- **性能监控**：自动跟踪响应时间和错误率
- **会话管理**：支持多会话终端管理

### BashLingXiExecutor vs BashExecutor

| 特性 | BashExecutor | BashLingXiExecutor |
|------|--------------|-------------------|
| 执行方式 | subprocess.run (shell=True) | LingXi MCP (execFile) |
| 安全性 | 内置规则 | LingXi + 自定义规则 |
| Shell 注入风险 | 存在 | 无（execFile 保护） |
| 工作目录 | 支持 | 不支持 |
| 管道/操作符 | 支持 | 阻止（安全考虑） |
| 性能监控 | 基础（耗时） | 高级（性能跟踪） |
| 初始化开销 | 无 | ~350ms |
| 后续命令开销 | ~5-50ms | ~4-10ms |

### 配置

#### 方法 1：通过配置文件

编辑 `config.yaml`：

```yaml
engine:
  bash_executor_type: lingxi  # "native" 或 "lingxi"
```

#### 方法 2：通过命令行参数

```bash
# 使用 LingXi 执行器
lingclaude run "列出当前目录文件" --bash-executor lingxi

# 使用原生 bash 执行器（默认）
lingclaude run "列出当前目录文件" --bash-executor native
```

### 工具对比

灵克提供两个 bash 工具：

1. **`bash`**：使用原生 subprocess 执行
   - 支持所有 shell 特性（管道、重定向等）
   - 适合需要复杂 shell 操作的场景

2. **`bash_lingxi`**：通过 LingXi MCP 服务器执行
   - 增强的安全性（防注入）
   - 内置性能监控
   - 适合生产环境和安全敏感场景

### 使用示例

```python
from lingclaude.engine.bash_lingxi import BashLingXiExecutor

# 基本使用
executor = BashLingXiExecutor()
result = executor.run('echo "Hello from LingXi"')
print(result.stdout)  # 输出: Hello from LingXi

# 带安全规则
executor = BashLingXiExecutor(
    blocked_commands=['rm', 'sudo', 'curl'],
    allowed_commands=['echo', 'ls', 'cat']  # 可选白名单
)
result = executor.run('echo "Allowed"')
print(result.stdout)  # 输出: Allowed

result = executor.run('rm -rf test')
print(result.stderr)  # 输出: 命令被阻止: rm -rf test（原因: 匹配黑名单规则 'rm'）
```

### 限制

1. **工作目录**：LingXi 的 `execute_command` 不支持 `cwd` 参数
2. **Shell 管道**：命令如 `ls | grep foo` 会被 LingXi 的安全规则阻止
3. **固定超时**：LingXi 使用内部 60 秒超时
4. **初始化开销**：首次命令执行需要 ~350ms（服务器启动 + 初始化）

### 测试

运行 LingXi 集成测试：

```bash
# 测试 MCP SDK 集成
python3 scripts/test_mcp_sdk.py

# 测试 BashLingXiExecutor
python3 scripts/test_bash_lingxi.py

# 运行使用示例
python3 scripts/example_bash_lingxi.py
```

详细文档见：[docs/lingxi-integration.md](docs/lingxi-integration.md)

## 依赖

**必须**: tiktoken, aiohttp, pyyaml, fastapi, uvicorn

**可选**: optuna（高级优化）, psutil（系统指标）, openai（语音识别）

## 开发

```bash
python3 -m pytest tests/ -v          # 260 tests
python3 -c "from lingclaude.model import create_provider; print('OK')"
```

## 路线图

完整路线图见 [CHARTER.md](CHARTER.md#路线图)。

- [x] v0.1.0 — 核心框架：查询引擎、会话、权限、工具执行、自优化、自学习
- [x] v0.1.1 — **安全审计**：bash 沙箱加固、文件操作路径包含检查、敏感路径保护
- [x] v0.1.2 — **开源准备**：贡献指南、Issue/PR 模板
- [x] v0.2.0 — **模型对接 + 行为感知 + 自适应引擎**：OpenAI/Anthropic API、行为感知系统、自适应查询引擎、Agent Loop
- [x] v0.2.1 — **情报系统 + HTTP API + 安全审计**：情报收集、日报生成、情报中继、会话历史输出；FastAPI HTTP服务；安全漏洞修复（认证绕过、路径遍历、会话管理）
- [ ] v1.0.0 — 完整的 AI 编程助手

## License

MIT

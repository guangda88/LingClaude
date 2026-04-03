# 灵克 (LingClaude)

> 开源 AI 编程助手，对标 Claude Code，内置自优化能力——越用越懂你。

**Version**: 0.2.0 | **Python**: >=3.10 | **License**: MIT

## 为什么做灵克？

Claude Code 很强，但它是闭源的、云端的、不可定制的。

灵克的答案是：**一个你可以完全掌控的 AI 编程助手。**

- 本地运行，数据不出你的机器
- 内置自优化框架（灵通 LingFlow），不是静态工具，会持续自我进化
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

# 或不安装直接运行
python3 -m lingclaude.cli --help
```

## 架构

```
灵克 (LingClaude)
├── core/               # 基础层
│   ├── types.py        #   Result[T] 单子（ok/fail 工厂方法）
│   ├── config.py       #   YAML 配置 → dataclass
│   ├── models.py       #   Subsystem, ToolDefinition, PermissionDenial 等
│   ├── session.py      #   Session（不可变）+ SessionManager（JSON 持久化）
│   ├── permissions.py  #   PermissionContext（deny_tools + deny_prefixes）
│   └── query_engine.py #   QueryEngine：turn循环、流式输出、自动压缩
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
│   └── coding.py       #   CodingRuntime（工具 + 评估 + 优化 + 模式检测）
│
├── self_optimizer/     # 自优化框架（来自灵通 LingFlow）
│   ├── trigger.py      #   OptimizationTrigger（7类触发条件）
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
触发（7类条件）→ 评估（AST指标）→ 优化（optuna/网格搜索）→ 报告（Markdown）
                                                                ↓
                                                         知识库（规则提取 + 模式识别）
```

### 7类触发条件

用户请求 | 质量低于阈值 | 结构违规 | 性能退化 | 规模增长 | 技术债累积 | 定时优化

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
  hardware_profile: lingzhi
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
```

## 依赖

**必须**: tiktoken, aiohttp, pyyaml

**可选**: optuna（高级优化）, psutil（系统指标）

## 开发

```bash
python3 -m pytest tests/ -v          # 98 tests
python3 -c "from lingclaude.model import create_provider; print('OK')"
```

## 路线图

完整路线图见 [CHARTER.md](CHARTER.md#路线图)。

- [x] v0.1.0 — 核心框架：查询引擎、会话、权限、工具执行、自优化、自学习
- [x] v0.1.1 — **安全审计**：bash 沙箱加固、文件操作路径包含检查、敏感路径保护
- [x] v0.1.2 — **开源准备**：贡献指南、Issue/PR 模板
- [x] v0.2.0 — **模型对接**：OpenAI/Anthropic API、对话式编程、交互模式
- [ ] v1.0.0 — 完整的 AI 编程助手

## License

MIT

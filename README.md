# 灵克 (LingClaude)

> 开源 AI 编程助手，对标 Claude Code，内置自优化能力——越用越懂你。

**Version**: 0.1.0 | **Python**: >=3.10 | **License**: MIT

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

# 使用
lingclaude run "你的编程问题"
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
├── engine/             # 工具执行层
│   ├── tools.py        #   ToolRegistry（注册/注销/执行）
│   ├── bash.py         #   BashExecutor（命令黑名单 + 超时）
│   ├── file_ops.py     #   FileOps（读写/编辑/搜索）
│   └── coding.py       #   CodingRuntime（全部工具 + 评估 + 优化 + 权限）
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

## 配置

编辑项目根目录 `config.yaml`：

```yaml
system:
  name: 灵克

engine:
  max_turns: 50
  streaming: true

permissions:
  deny_tools: [rm_rf, format_disk]
  deny_prefixes: [/etc/, /sys/]

self_optimizer:
  triggers:
    quality_threshold: 0.7
    structure_threshold: 5
  optimizer:
    max_trials: 100
    method: grid    # grid 或 optuna

session:
  save_dir: .lingclaude/sessions
  auto_save: true
```

## 依赖

**必须**: tiktoken, aiohttp, pyyaml

**可选**: optuna（高级优化）, psutil（系统指标）

## 开发

```bash
python3 -m pytest tests/ -v          # 30 tests
python3 -c "from lingclaude.core import QueryEngine; print('OK')"
```

## 路线图

- [x] v0.1.0 — 核心框架：查询引擎、会话、权限、工具执行、自优化、自学习
- [ ] v0.2.0 — 对接大模型API，实现真正的对话式编程
- [ ] v0.3.0 — 项目感知：理解代码库结构，跨文件编辑
- [ ] v0.4.0 — 自优化实战：从社区反馈中学习，积累规则
- [ ] v1.0.0 — 完整的 AI 编程助手，可替代日常 Claude Code 使用

## License

MIT

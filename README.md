# 灵克 (LingClaude)

> 自优化AI模型运行时，基于 LingFlow (灵芝系统) 框架。

**Version**: 0.1.0
**Python**: >=3.10

## Overview

LingClaude fuses two architectures into a single runtime:

1. **Engine layer** — Query engine with turn loops, SSE streaming, session management, permission gating, tool/command routing
2. **Self-optimization layer** — 7-category trigger system, AST-based evaluators, synchronous optimizer (optuna + grid search fallback), Markdown advisor reports
3. **Self-learning layer** — Rule extraction from feedback, 6 pattern detectors, SQLite knowledge base, rule deduplication and validation
4. **Coding capability layer** — Tool execution (bash, file ops), combining patterns from both parent projects

## Quick Start

```bash
# Install
pip install -e .

# Run CLI
lingclaude --help
lingclaude run "your prompt here"
lingclaude analyze lingclaude/
lingclaude optimize --target lingclaude/ --goal "reduce complexity"
lingclaude session list
lingclaude knowledge stats

# Or without install
python3 -m lingclaude.cli --help
```

## Architecture

```
LingClaude
├── core/               # Foundation types and engine
│   ├── types.py        # Result[T] monad (ok/fail factories)
│   ├── config.py       # YAML-driven config → dataclasses
│   ├── models.py       # Subsystem, ToolDefinition, PermissionDenial, etc.
│   ├── session.py      # Session (frozen) + SessionManager (JSON persistence)
│   ├── permissions.py  # PermissionContext (deny_tools, deny_prefixes)
│   └── query_engine.py # QueryEngine with turn loop, streaming, auto-compaction
│
├── engine/             # Tool execution layer
│   ├── tools.py        # ToolRegistry (register/unregister/execute)
│   ├── bash.py         # BashExecutor (blocked commands, timeout)
│   ├── file_ops.py     # FileOps (read/write/edit/glob/grep/exists/delete)
│   └── coding.py       # CodingRuntime (all tools + evaluator + optimizer + advisor)
│
├── self_optimizer/     # Self-optimization framework (from LingFlow)
│   ├── trigger.py      # OptimizationTrigger (7 condition categories)
│   ├── evaluator.py    # StructureEvaluator (AST analysis)
│   ├── optimizer.py    # SynchronousOptimizer + SimpleSearchSpace
│   ├── advisor.py      # OptimizationAdvisor (Markdown reports)
│   └── learner/        # Phase 5: Self-learning engine
│       ├── models.py   # FeedbackItem, Pattern, LearnedRule, enums
│       ├── rule_extractor.py  # RuleExtractor, SecurityRuleExtractor, dedup
│       ├── knowledge.py       # SQLite KnowledgeBase + InMemoryKnowledgeBase
│       └── patterns.py        # 6 detectors (LongMethod, HardcodedSecret, etc.)
│
└── cli/                # Command-line interface
    ├── __main__.py     # Entry point
    └── app.py          # argparse with subcommands: run, optimize, analyze, session, knowledge
```

### Self-Optimization Flow

```
Trigger (7 conditions) → Evaluator (AST metrics) → Optimizer (optuna/grid) → Advisor (report)
                                                                                     ↓
                                                                              Knowledge Base
                                                                              (rule extraction
                                                                               + pattern recognition)
```

### Trigger Categories

| Category | Condition |
|----------|-----------|
| User | Explicit user request |
| Quality | Quality score below threshold |
| Structure | Structure violations detected |
| Performance | Performance degradation |
| Scale | Codebase growth exceeds threshold |
| Tech Debt | Technical debt accumulation |
| Time | Periodic optimization interval |

### Pattern Detectors

| Detector | What it finds |
|----------|---------------|
| LongMethod | Methods exceeding length threshold |
| UnusedVariable | Variables assigned but never used |
| HardcodedSecret | Potential secrets in source code |
| DuplicateCode | Duplicate code blocks |
| EmptyBlock | Empty if/try/for/while blocks |
| Complexity | High cyclomatic complexity |

## Configuration

Edit `config.yaml` at project root:

```yaml
system:
  name: LingClaude
  version: "0.1.0"

engine:
  max_turns: 50
  streaming: true

permissions:
  deny_tools:
    - rm_rf
    - format_disk
  deny_prefixes:
    - /etc/
    - /sys/

self_optimizer:
  triggers:
    quality_threshold: 0.7
    structure_threshold: 5
    performance_threshold: 0.5
  optimizer:
    max_trials: 100
    method: grid

session:
  save_dir: .lingclaude/sessions
  auto_save: true
```

## Dependencies

### Required
- `tiktoken` — Token counting
- `aiohttp` — Async HTTP client
- `pyyaml` — YAML config loading

### Optional
- `optuna` — Advanced optimization (falls back to grid search)
- `psutil` — System metrics for performance triggers

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Verify imports
python3 -c "from lingclaude.core import QueryEngine, LingClaudeConfig; print('OK')"

# Analyze your own code
python3 -m lingclaude.cli analyze lingclaude/
```

## License

Personal use — based on claude-code-port and LingFlow architectures.

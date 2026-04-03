# AGENTS.md — 灵克 (LingClaude) Agent Guide

> 开源 AI 编程助手，对标 Claude Code，差异化优势：内置自优化。

## Project Structure

```
/home/ai/LingClaude/
├── lingclaude/
│   ├── __init__.py          # Exports core types, version
│   ├── core/                # Foundation: types, config, session, permissions, query engine
│   ├── model/               # Model abstraction: provider ABC, OpenAI, Anthropic, factory
│   ├── engine/              # Tool execution: bash, file ops, tool registry, coding runtime
│   ├── self_optimizer/      # Optimization framework: trigger, evaluator, optimizer, advisor
│   │   └── learner/         # Self-learning: knowledge base, patterns, rule extraction
│   └── cli/                 # CLI entry point with subcommands
├── tests/
│   ├── test_core.py           # Core types, config, session, permissions, tools
│   ├── test_model.py          # Model provider (OpenAI, Anthropic, factory)
│   ├── test_behavior.py       # Behavior awareness (emotion, intent, metrics)
│   ├── test_behavior_trigger.py # Behavior-aware trigger conditions
│   ├── test_agent_loop.py     # Agent loop (multi-turn tool calling)
│   ├── test_adaptive.py       # Adaptive query engine (routing, retry, compaction)
│   ├── test_daemon.py         # Optimization daemon lifecycle
│   ├── test_daemon_extras.py  # Daemon edge cases
│   └── test_strict.py         # Config edge cases, provider errors, file ops
├── config.yaml              # Runtime configuration
├── pyproject.toml           # Build config, dependencies, CLI entry point
└── VERSION                  # Current: 0.2.0
```

## Commands

```bash
# Run tests (must pass before committing)
python3 -m pytest tests/ -v --tb=short

# Verify imports
python3 -c "from lingclaude.core import QueryEngine, LingClaudeConfig; print('OK')"

# CLI usage
python3 -m lingclaude.cli --help
python3 -m lingclaude.cli analyze <path>
python3 -m lingclaude.cli optimize --target <path> --goal "<goal>"
python3 -m lingclaude.cli session list
python3 -m lingclaude.cli knowledge stats
```

## Code Patterns

### Required in Every File
```python
from __future__ import annotations
```

### Type Hints
- Full type hints on all public functions
- `tuple[...]` for return types from cached/load functions (not `list[...]`)
- `pathlib.Path` exclusively, never `os.path`

### Dataclasses
- `@dataclass(frozen=True)` for immutable value objects (Session, ToolDefinition, etc.)
- Mutable `@dataclass` for stateful objects (SessionManager, ToolRegistry, etc.)

### Enums
- `class StopReason(str, Enum):` — str + Enum pattern
- `class FeedbackSeverity(str, Enum):` — same pattern

### Result Type
```python
from lingclaude.core.types import Result

result: Result[str] = Result.ok("value")
result: Result[str] = Result.fail("error message", code="ERR_CODE")
if result.is_ok:
    print(result.data)
```

## API Quirks (Gotchas)

### Session
- `Session` is a **frozen** dataclass. Fields: `session_id`, `messages` (tuple), `input_tokens`, `output_tokens`, `created_at`
- `SessionManager.create(messages=(), input_tokens=0, output_tokens=0)` — returns Session
- `SessionManager.list_sessions()` — returns `list[str]` (session IDs, NOT Session objects)
- Sessions are persisted as JSON in `.lingclaude/sessions/`

### PermissionContext
- `PermissionContext.from_config(deny_tools, deny_prefixes)` — **factory method**, not constructor
- `ctx.blocks(tool_name)` — instance method to check if a tool is blocked
- NOT `is_blocked()` — the method is called `blocks()`

### CodingRuntime
- `runtime.permissions` — PermissionContext instance (not a dict)
- Combines all tools + evaluator + optimizer + advisor + permissions

### KnowledgeBase
- `KnowledgeBase()` — creates SQLite-backed KB (uses stdlib sqlite3, no external DB)
- `InMemoryKnowledgeBase()` — dict-backed for testing
- Always call `kb.close()` when done (SQLite connection)

### Optimizer
- `SynchronousOptimizer` tries to import optuna, falls back to `SimpleSearchSpace` grid search
- optuna is **optional** — everything works without it

## Self-Optimizer Flow

```
1. OptimizationTrigger.check()
   → 8 condition categories (user, quality, behavior, structure, performance, scale, tech_debt, time)
   → Returns TriggerInfo or None

2. StructureEvaluator.evaluate(target_path)
   → AST analysis of Python source files
   → Returns StructureMetrics (violations, complexity, class sizes)

3. SynchronousOptimizer.optimize(request)
   → optuna study or grid search over search space
   → Returns OptimizationResult with best params and improvement score

4. OptimizationAdvisor.generate_report(result, metrics, trigger_info)
   → Markdown report with summary, changes, recommendations
   → save_report() writes to file
```

## Self-Learning Flow

```
1. PatternRecognizer.analyze(source_code, filename)
   → 6 detectors: LongMethod, UnusedVariable, HardcodedSecret, DuplicateCode, EmptyBlock, Complexity
   → Returns list[Pattern]

2. RuleExtractor.extract_from_feedback(feedback_items)
   → Extracts LearnedRule objects from feedback
   → SecurityRuleExtractor for security-specific rules

3. RuleDeduplicator.deduplicate(rules)
   → Levenshtein distance-based deduplication

4. KnowledgeBase.add_rule(rule) / search_rules(keyword) / get_all_rules()
   → Persistent SQLite storage
   → Quality scoring and status tracking
```

## Configuration

Config loaded from `config.yaml` via `LingClaudeConfig` dataclass hierarchy:
- `EngineConfig` — max_turns, max_budget_tokens, compact_after_turns, structured_output
- `PermissionConfig` — deny_tools, deny_prefixes
- `TriggerConfig` — quality/structure/performance/behavior thresholds
- `OptimizerConfig` — max_trials, method
- `SessionConfig` — save_dir, max_history

## Test Coverage

217 tests across 9 files:
- `TestResult` (3) — ok/fail factories, error codes
- `TestConfig` (4) — defaults, from_dict, file loading, missing file
- `TestSession` (2) — creation, roundtrip save/load
- `TestPermissions` (2) — block by name, block by prefix
- `TestToolRegistry` (2) — register+execute, missing tool
- `TestOptimizationTrigger` (4) — user/quality/no trigger, disabled
- `TestStructureEvaluator` (2) — empty dir, code analysis
- `TestPatternRecognizer` (4) — long method, hardcoded secret, empty block, clean code
- `TestKnowledgeBase` (4) — CRUD, search, statistics, SQLite
- `TestRuleExtractor` (2) — extraction, deduplication
- `TestOptimizationAdvisor` (1) — report generation
- `TestBehavior` (8) — emotion detection, intent analysis, hallucination tracking
- `TestBehaviorTrigger` (4) — behavior-aware trigger conditions
- `TestAgentLoop` (6) — multi-turn tool calling, max rounds
- `TestAdaptive` (20) — model routing, adaptive prompt, tool retry, compaction, project index
- `TestModel` (10) — OpenAI/Anthropic providers, factory
- `TestDaemon` (8) — daemon lifecycle, state persistence
- `TestDaemonExtras` (6) — daemon edge cases
- `TestConfigEdgeCases` (4), `TestQueryEngineEdgeCases` (6), `TestModelProviderEdgeCases` (7), `TestFileOpsEdgeCases` (4), `TestOptimizerEdgeCases` (2), `TestSessionEdgeCases` (2) — strict edge cases

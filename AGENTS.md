# AGENTS.md — LingClaude Agent Guide

## Project Structure

```
/home/ai/LingClaude/
├── lingclaude/
│   ├── __init__.py          # Exports core types, version
│   ├── core/                # Foundation: types, config, session, permissions, query engine
│   ├── engine/              # Tool execution: bash, file ops, tool registry, coding runtime
│   ├── self_optimizer/      # Optimization framework: trigger, evaluator, optimizer, advisor
│   │   └── learner/         # Self-learning: knowledge base, patterns, rule extraction
│   └── cli/                 # CLI entry point with subcommands
├── tests/
│   └── test_core.py         # 30 tests covering all major components
├── config.yaml              # Runtime configuration
├── pyproject.toml           # Build config, dependencies, CLI entry point
└── VERSION                  # Current: 0.1.0
```

## Commands

```bash
# Run tests (must pass before committing)
python3 -m pytest tests/test_core.py -v --tb=short

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
    print(result.value)
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
   → 7 condition categories (user, quality, structure, performance, scale, tech_debt, time)
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
- `EngineConfig` — max_turns, streaming
- `PermissionConfig` — deny_tools, deny_prefixes
- `TriggerConfig` — quality/structure/performance thresholds
- `OptimizerConfig` — max_trials, method
- `SessionConfig` — save_dir, auto_save

## Test Coverage

30 tests in `tests/test_core.py`:
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

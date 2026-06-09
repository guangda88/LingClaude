# System Flows (Self-Optimizer, Self-Learning, Configuration, Intel)

> 从 AGENTS.md 迁移（2026-05-06 瘦身）。原始备份：`docs/AGENTS_ARCHIVE_20260506.md`

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

Config loaded from `config.yaml` via `lingclaudeConfig` dataclass hierarchy:
- `EngineConfig` — max_turns, max_budget_tokens, compact_after_turns, structured_output
- `PermissionConfig` — deny_tools, deny_prefixes
- `TriggerConfig` — quality/structure/performance/behavior thresholds
- `OptimizerConfig` — max_trials, method
- `SessionConfig` — save_dir, max_history
- `IntelConfig` — enabled, output_dir, session_history_path, auto_collect_behavior, auto_relay, relay_target, digest_hour

## Intelligence System (情报系统)

```
1. IntelCollector.from_behavior() / from_file_change() / from_pattern() / ...
   → 8 categories: FILE_CHANGE, CODE_PATTERN, BEHAVIOR, ERROR, OPTIMIZATION, STRUCTURE, QUALITY, SECURITY
   → 3 priority levels: INFO, WARNING, CRITICAL
   → In-memory accumulation, cleared after digest

2. DailyDigestGenerator.generate(items)
   → Aggregates items into DailyDigest with key_findings, recommendations
   → Category/priority counts

3. IntelRelay.relay(digest)
   → Writes JSON + Markdown + manifest to .lingclaude/intel/
   → Public methods return Result[T]

4. session_history.json
   → Auto-generated on every submit() call
   → Format: [{query, title, timestamp, created_at, session_id}]
   → Consumed by 各成员通过 LingBus 或文件读取
```

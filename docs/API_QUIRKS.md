# API Quirks (Gotchas)

> 从 AGENTS.md 迁移（2026-05-06 瘦身）。原始备份：`docs/AGENTS_ARCHIVE_20260506.md`

## Session
- `Session` is a **frozen** dataclass. Fields: `session_id`, `messages` (tuple), `input_tokens`, `output_tokens`, `created_at`
- `SessionManager.create(messages=(), input_tokens=0, output_tokens=0)` — returns Session
- `SessionManager.list_sessions()` — returns `list[str]` (session IDs, NOT Session objects)
- Sessions are persisted as JSON in `.lingclaude/sessions/`

## PermissionContext
- `PermissionContext.from_config(deny_tools, deny_prefixes)` — **factory method**, not constructor
- `ctx.blocks(tool_name)` — instance method to check if a tool is blocked
- NOT `is_blocked()` — the method is called `blocks()`

## CodingRuntime
- `runtime.permissions` — PermissionContext instance (not a dict)
- Combines all tools + evaluator + optimizer + advisor + permissions

## KnowledgeBase
- `KnowledgeBase()` — creates SQLite-backed KB (uses stdlib sqlite3, no external DB)
- `InMemoryKnowledgeBase()` — dict-backed for testing
- Always call `kb.close()` when done (SQLite connection)

## Optimizer
- `SynchronousOptimizer` tries to import optuna, falls back to `SimpleSearchSpace` grid search
- optuna is **optional** — everything works without it

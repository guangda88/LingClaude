# Code Patterns

> 从 AGENTS.md 迁移（2026-05-06 瘦身）。原始备份：`docs/AGENTS_ARCHIVE_20260506.md`

## Required in Every File
```python
from __future__ import annotations
```

## Type Hints
- Full type hints on all public functions
- `tuple[...]` for return types from cached/load functions (not `list[...]`)
- `pathlib.Path` exclusively, never `os.path`

## Dataclasses
- `@dataclass(frozen=True)` for immutable value objects (Session, ToolDefinition, etc.)
- Mutable `@dataclass` for stateful objects (SessionManager, ToolRegistry, etc.)

## Enums
- `class StopReason(str, Enum):` — str + Enum pattern
- `class FeedbackSeverity(str, Enum):` — same pattern

## Result Type
```python
from lingclaude.core.types import Result

result: Result[str] = Result.ok("value")
result: Result[str] = Result.fail("error message", code="ERR_CODE")
if result.is_ok:
    print(result.data)
```

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS_REACHED = "max_turns_reached"
    MAX_BUDGET_REACHED = "max_budget_reached"
    CONSECUTIVE_FAILURE = "consecutive_failure"
    ERROR = "error"


@dataclass(frozen=True)
class Result(Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
    code: str | None = None

    @classmethod
    def ok(cls, data: T, code: str | None = None) -> Result[T]:
        return cls(success=True, data=data, code=code)

    @classmethod
    def fail(cls, error: str, code: str | None = None) -> Result[T]:
        return cls(success=False, error=error, code=code)

    @property
    def is_ok(self) -> bool:
        return self.success

    @property
    def is_error(self) -> bool:
        return not self.success

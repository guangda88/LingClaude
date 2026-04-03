from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


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

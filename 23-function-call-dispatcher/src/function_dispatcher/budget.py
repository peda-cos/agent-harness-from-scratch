"""Per-call tool budget contract and a small local implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Budget(Protocol):
    """A caller-owned allowance checked before every handler attempt."""

    def try_consume(self) -> bool:
        """Consume one unit, returning False when the allowance is exhausted."""
        ...


@dataclass(slots=True)
class ToolCallBudget:
    """A fixed number of tool-call attempts."""

    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise ValueError("limit must be a non-negative integer")
        if self.limit < 0:
            raise ValueError("limit must be a non-negative integer")
        if isinstance(self.used, bool) or not isinstance(self.used, int):
            raise ValueError("used must be a non-negative integer")
        if self.used < 0:
            raise ValueError("used must be a non-negative integer")
        if self.used > self.limit:
            raise ValueError("used must not exceed limit")

    def try_consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return self.limit - self.used


__all__ = ["Budget", "ToolCallBudget"]

"""Per-session budget envelope.

Limits are allowances: a turn or tool call is allowed while the counter is
below the maximum. Reaching a limit does not retroactively block the operation
that consumed the last unit; the next attempt is the one that pauses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .contract import BudgetLimit


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_turns: int = 6
    max_tool_calls: int = 6
    max_wall_clock_s: float = 30.0

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be >= 0")
        if not math.isfinite(self.max_wall_clock_s) or self.max_wall_clock_s <= 0:
            raise ValueError("max_wall_clock_s must be finite and > 0")


@dataclass(frozen=True, slots=True)
class BudgetExtension:
    turns: int = 0
    tool_calls: int = 0
    wall_clock_s: float = 0.0

    def __post_init__(self) -> None:
        if self.turns < 0:
            raise ValueError("turns extension must be >= 0")
        if self.tool_calls < 0:
            raise ValueError("tool_calls extension must be >= 0")
        if not math.isfinite(self.wall_clock_s) or self.wall_clock_s < 0:
            raise ValueError("wall_clock_s extension must be finite and >= 0")


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    turns: int
    max_turns: int
    tool_calls: int
    max_tool_calls: int
    elapsed_s: float
    max_wall_clock_s: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "turns": self.turns,
            "max_turns": self.max_turns,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "elapsed_s": self.elapsed_s,
            "max_wall_clock_s": self.max_wall_clock_s,
        }


@dataclass(slots=True)
class Budget:
    limits: BudgetLimits
    started_at: float
    turns: int = 0
    tool_calls: int = 0

    @classmethod
    def start(cls, limits: BudgetLimits, now: float) -> Budget:
        return cls(limits=limits, started_at=now)

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.started_at)

    def try_start_turn(self) -> BudgetLimit | None:
        if self.turns >= self.limits.max_turns:
            return BudgetLimit.TURNS
        self.turns += 1
        return None

    def try_call_tool(self) -> BudgetLimit | None:
        if self.tool_calls >= self.limits.max_tool_calls:
            return BudgetLimit.TOOL_CALLS
        self.tool_calls += 1
        return None

    def wall_clock_exceeded(self, now: float) -> bool:
        return self.elapsed(now) >= self.limits.max_wall_clock_s

    def extend(self, extension: BudgetExtension) -> None:
        self.limits = BudgetLimits(
            max_turns=self.limits.max_turns + extension.turns,
            max_tool_calls=self.limits.max_tool_calls + extension.tool_calls,
            max_wall_clock_s=self.limits.max_wall_clock_s
            + extension.wall_clock_s,
        )

    def current(self, limit: BudgetLimit, now: float) -> int | float:
        if limit is BudgetLimit.TURNS:
            return self.turns
        if limit is BudgetLimit.TOOL_CALLS:
            return self.tool_calls
        return self.elapsed(now)

    def maximum(self, limit: BudgetLimit) -> int | float:
        if limit is BudgetLimit.TURNS:
            return self.limits.max_turns
        if limit is BudgetLimit.TOOL_CALLS:
            return self.limits.max_tool_calls
        return self.limits.max_wall_clock_s

    def snapshot(self, now: float) -> BudgetSnapshot:
        return BudgetSnapshot(
            turns=self.turns,
            max_turns=self.limits.max_turns,
            tool_calls=self.tool_calls,
            max_tool_calls=self.limits.max_tool_calls,
            elapsed_s=self.elapsed(now),
            max_wall_clock_s=self.limits.max_wall_clock_s,
        )

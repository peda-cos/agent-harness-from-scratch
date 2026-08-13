"""Observation accounting and the model-facing recency window."""

from __future__ import annotations

from .models import Observation


class ObservationLedger:
    """Append-only rows of what the model was shown, with cheap totals."""

    def __init__(self) -> None:
        self._rows: list[Observation] = []
        self._cumulative = 0
        self._per_tool: dict[str, int] = {}

    def record(
        self,
        turn: int,
        tool: str,
        tokens: int,
        *,
        truncated: bool = False,
    ) -> Observation:
        observation = Observation(
            turn=turn,
            tool=tool,
            tokens=tokens,
            truncated=truncated,
        )
        if self._rows and observation.turn < self._rows[-1].turn:
            raise ValueError("observation turns must be non-decreasing")
        self._rows.append(observation)
        self._cumulative += observation.tokens
        self._per_tool[observation.tool] = (
            self._per_tool.get(observation.tool, 0) + observation.tokens
        )
        return observation

    def cumulative(self) -> int:
        return self._cumulative

    def per_tool(self, name: str) -> int:
        _validate_tool_name(name)
        return self._per_tool.get(name, 0)

    @property
    def last_turn(self) -> int | None:
        return self._rows[-1].turn if self._rows else None

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._rows)

    def visible(self, current_turn: int, window: int) -> tuple[Observation, ...]:
        oldest = self._window_start(current_turn, window)
        return tuple(row for row in self._rows if row.turn >= oldest)

    def masked(self, current_turn: int, window: int) -> tuple[Observation, ...]:
        oldest = self._window_start(current_turn, window)
        return tuple(row for row in self._rows if row.turn < oldest)

    def __len__(self) -> int:
        return len(self._rows)

    def _window_start(self, current_turn: int, window: int) -> int:
        _validate_positive_int("current_turn", current_turn)
        _validate_positive_int("window", window)
        if self._rows and current_turn < self._rows[-1].turn:
            raise ValueError("current_turn cannot precede the latest observation")
        return current_turn - window + 1


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_tool_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("tool name must be a non-empty string")


__all__ = ["ObservationLedger"]

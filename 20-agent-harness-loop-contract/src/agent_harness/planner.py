"""Deterministic stand-in planner and the step/call value objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol


def _arguments(value: Mapping[str, Any]) -> Mapping[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("step/tool argument keys must be strings")
        copied[key] = item
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One deterministic step. A tool name means the step needs a tool result."""

    step_id: str
    title: str
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.step_id:
            raise ValueError("step_id must not be empty")
        if not self.title:
            raise ValueError("title must not be empty")
        if self.tool is not None and not self.tool:
            raise ValueError("tool must be None or a non-empty string")
        object.__setattr__(self, "arguments", _arguments(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A call yielded to the caller for execution."""

    call_id: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("call_id must not be empty")
        if not self.tool:
            raise ValueError("tool must not be empty")
        object.__setattr__(self, "arguments", _arguments(self.arguments))


class Planner(Protocol):
    """The replaceable planning seam."""

    def plan(self, goal: str) -> Sequence[PlanStep]:
        """Return a draft plan for the goal."""
        ...


class DeterministicPlanner:
    """Hardcoded three-step plan from the lesson; two steps need tools."""

    def plan(self, goal: str) -> Sequence[PlanStep]:
        return (
            PlanStep(
                step_id="scan",
                title="Scan the goal context",
                tool="lookup_context",
                arguments={"goal": goal},
            ),
            PlanStep(
                step_id="validate",
                title="Validate the candidate change",
                tool="validate_change",
                arguments={"goal": goal},
            ),
            PlanStep(step_id="report", title="Report the result"),
        )

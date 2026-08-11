"""Typed plan data, plan diffs, and the terminal session result."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

Event: TypeAlias = dict[str, object]
SessionStatus: TypeAlias = Literal["completed", "failed"]
SessionReason: TypeAlias = Literal[
    "goal_met",
    "step_budget",
    "replan_budget",
    "no_plan",
]


@dataclass(slots=True)
class Step:
    """One planner-authored step and, once executed, its outcome."""

    id: int
    tool_name: str
    args: dict[str, object]
    expected_outcome: str
    result: object | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int):
            raise ValueError("step id must be an integer")
        if self.id < 1:
            raise ValueError("step id must be a positive integer")
        if not isinstance(self.tool_name, str) or not self.tool_name:
            raise ValueError("tool_name must be a non-empty string")
        if not isinstance(self.args, dict):
            raise TypeError("args must be a dict")
        self.args = dict(self.args)
        if not isinstance(self.expected_outcome, str) or not self.expected_outcome:
            raise ValueError("expected_outcome must be a non-empty string")
        if self.result is not None and self.error is not None:
            raise ValueError("a step cannot carry both result and error")
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error
        ):
            raise ValueError("error must be a non-empty string when set")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "args": dict(self.args),
            "expected_outcome": self.expected_outcome,
            "result": self.result,
            "error": self.error,
        }


@dataclass(slots=True)
class PlanDiff:
    """The visible difference between the old remaining plan and a revision."""

    removed: list[int]
    added: list[int]
    revised: list[int]

    def to_dict(self) -> dict[str, object]:
        return {
            "removed": list(self.removed),
            "added": list(self.added),
            "revised": list(self.revised),
        }


@dataclass(slots=True)
class SessionResult:
    """Terminal status plus the full execution and revision record."""

    status: SessionStatus
    reason: SessionReason
    history: list[Step]
    revisions: list[PlanDiff]
    events: list[Event]

    def __post_init__(self) -> None:
        if (self.status == "completed") != (self.reason == "goal_met"):
            raise ValueError(
                "completed sessions require reason 'goal_met' and vice versa"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "history": [step.to_dict() for step in self.history],
            "revisions": [diff.to_dict() for diff in self.revisions],
            "events": list(self.events),
        }


def validate_plan(plan: Sequence[Step]) -> None:
    """Reject malformed or already-executed planner output."""

    previous = 0
    for step in plan:
        if not isinstance(step, Step):
            raise ValueError("planner returned a non-Step item")
        if step.id <= previous:
            raise ValueError(
                "plan step ids must be strictly increasing, "
                f"got {previous} then {step.id}"
            )
        if step.result is not None or step.error is not None:
            raise ValueError(f"planner returned executed step {step.id}")
        previous = step.id


def diff_plans(old: Sequence[Step], new: Sequence[Step]) -> PlanDiff:
    """Compare revisions by id, preserving the encounter order of each list."""

    old_by_id = {step.id: step for step in old}
    new_by_id = {step.id: step for step in new}
    removed = [step.id for step in old if step.id not in new_by_id]
    added = [step.id for step in new if step.id not in old_by_id]
    revised = [
        step.id
        for step in old
        if step.id in new_by_id
        and (step.tool_name, step.args)
        != (new_by_id[step.id].tool_name, new_by_id[step.id].args)
    ]
    return PlanDiff(removed=removed, added=added, revised=revised)


__all__ = [
    "Event",
    "PlanDiff",
    "SessionReason",
    "SessionResult",
    "SessionStatus",
    "Step",
    "diff_plans",
    "validate_plan",
]

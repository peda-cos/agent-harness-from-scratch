"""Public contract vocabulary for the agent harness loop.

Everything in this module is a stable seam: state names, hook topics, event
types, pull kinds, and error classes are part of the contract.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping


class State(StrEnum):
    """The six loop states. IDLE enters; DONE exits."""

    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    AWAITING_TOOL = "AWAITING_TOOL"
    REFLECTING = "REFLECTING"
    DONE = "DONE"


class HookTopic(StrEnum):
    """The ten operator seams into the loop."""

    BEFORE_PLAN = "before_plan"
    AFTER_PLAN = "after_plan"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_STEP = "before_step"
    AFTER_STEP = "after_step"
    ON_ERROR = "on_error"
    ON_PAUSE = "on_pause"
    ON_BUDGET_EXCEEDED = "on_budget_exceeded"
    ON_COMPLETE = "on_complete"


class EventType(StrEnum):
    """The eleven observational event types in the append-only stream."""

    SESSION_START = "session.start"
    PLAN_DRAFT = "plan.draft"
    PLAN_COMMIT = "plan.commit"
    STEP_START = "step.start"
    STEP_END = "step.end"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    BUDGET_WARN = "budget.warn"
    SESSION_PAUSE = "session.pause"
    SESSION_COMPLETE = "session.complete"


class PullKind(StrEnum):
    """The two ways the loop returns control to its caller."""

    TOOL = "tool"
    PAUSE = "pause"


class PauseReason(StrEnum):
    """Why a pause pull was yielded."""

    BUDGET_EXCEEDED = "budget_exceeded"
    HOOK_REVIEW = "hook_review"


class Outcome(StrEnum):
    """How a completed session ended."""

    GOAL_MET = "goal_met"
    INCOMPLETE = "incomplete"
    NO_PLAN = "no_plan"
    ABORTED = "aborted"
    CLOSED = "closed"


class StepStatus(StrEnum):
    """Final status of a plan step."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BudgetLimit(StrEnum):
    """The three dimensions of the per-session budget envelope."""

    TURNS = "turns"
    TOOL_CALLS = "tool_calls"
    WALL_CLOCK = "wall_clock"


class HookSignal(StrEnum):
    """Control signals a hook may return instead of mutating the payload."""

    SKIP_STEP = "skip_step"
    PAUSE = "pause"


class HarnessError(RuntimeError):
    """Base class for contract violations raised by the harness."""


class IllegalTransition(HarnessError):
    """A state transition or public operation was not legal in this state."""


class ProtocolError(HarnessError):
    """The caller sent the wrong payload or call id at a pull point."""


class BudgetNotExtended(HarnessError):
    """A budget pause was resumed without extending the exhausted limit."""


ACTIVE_STATES: Final[frozenset[State]] = frozenset(
    {State.PLANNING, State.EXECUTING, State.AWAITING_TOOL, State.REFLECTING}
)

# REFLECTING -> PLANNING is part of the published contract state diagram. This
# lesson's deterministic planner never requests it; future planners can.
LEGAL_TRANSITIONS: Final[Mapping[State, frozenset[State]]] = MappingProxyType(
    {
        State.IDLE: frozenset({State.PLANNING}),
        State.PLANNING: frozenset({State.EXECUTING, State.DONE}),
        State.EXECUTING: frozenset({State.AWAITING_TOOL, State.REFLECTING}),
        State.AWAITING_TOOL: frozenset({State.REFLECTING}),
        State.REFLECTING: frozenset(
            {State.EXECUTING, State.PLANNING, State.DONE}
        ),
        State.DONE: frozenset(),
    }
)

"""Resumable deterministic harness loop.

The loop is implemented as a Python generator. Yielding a ``PullRequest`` is
the only way it returns control to the caller; sending the matching payload is
the only way it resumes. That keeps mid-step state local to the suspended
generator and makes the pause checkpoints explicit.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal, TypeAlias, cast

from .budget import Budget, BudgetExtension, BudgetLimits, BudgetSnapshot
from .contract import (
    ACTIVE_STATES,
    LEGAL_TRANSITIONS,
    BudgetLimit,
    BudgetNotExtended,
    EventType,
    HookTopic,
    IllegalTransition,
    Outcome,
    PauseReason,
    ProtocolError,
    PullKind,
    State,
    StepStatus,
)
from .events import EventStream
from .hooks import HookFailure, HookOutcome, HookRegistry
from .planner import DeterministicPlanner, PlanStep, Planner, ToolCall


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The caller's answer to a tool pull."""

    call_id: str
    ok: bool = True
    data: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("call_id must not be empty")
        if not isinstance(self.ok, bool):
            raise TypeError("ok must be a bool")
        if self.ok and self.error is not None:
            raise ValueError("a successful tool result must not carry an error")
        if not self.ok and not self.error:
            raise ValueError("a failed tool result requires a non-empty error")
        if not isinstance(self.data, Mapping):
            raise TypeError("tool result data must be a mapping")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class PauseInfo:
    """Why the loop yielded and which active state will be restored."""

    reason: PauseReason
    resume_state: State
    limit: BudgetLimit | None
    message: str

    def __post_init__(self) -> None:
        if self.resume_state not in ACTIVE_STATES:
            raise ValueError("resume_state must be an active state")
        if self.reason is PauseReason.BUDGET_EXCEEDED and self.limit is None:
            raise ValueError("budget pauses require a limit")
        if self.reason is PauseReason.HOOK_REVIEW and self.limit is not None:
            raise ValueError("hook review pauses must not set a limit")
        if not self.message:
            raise ValueError("pause message must not be empty")


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A return from the loop: either a tool request or a pause."""

    kind: PullKind
    session_id: str
    state: State
    event_offset: int
    budget: BudgetSnapshot
    call: ToolCall | None = None
    pause: PauseInfo | None = None

    def __post_init__(self) -> None:
        if self.event_offset < 0:
            raise ValueError("event_offset must be >= 0")
        if self.kind is PullKind.TOOL:
            if self.state is not State.AWAITING_TOOL:
                raise ValueError("tool pulls must report AWAITING_TOOL")
            if self.call is None or self.pause is not None:
                raise ValueError("tool pulls require a call and no pause")
        elif self.state is not State.IDLE:
            raise ValueError("pause pulls must report IDLE")
        elif self.pause is None or self.call is not None:
            raise ValueError("pause pulls require a pause and no call")


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    """The caller's answer to a pause pull."""

    extension: BudgetExtension | None = None


@dataclass(frozen=True, slots=True)
class SessionResult:
    """Terminal summary returned by run/resume/close."""

    session_id: str
    outcome: Outcome
    turns: int
    tool_calls: int
    elapsed_s: float
    completed_steps: int
    failed_steps: int
    skipped_steps: int
    event_offset: int

    def __post_init__(self) -> None:
        counts = (
            self.turns,
            self.tool_calls,
            self.completed_steps,
            self.failed_steps,
            self.skipped_steps,
            self.event_offset,
        )
        if any(count < 0 for count in counts):
            raise ValueError("session counters must be non-negative")


@dataclass(frozen=True, slots=True)
class Transition:
    """Auditable state edge."""

    sequence: int
    from_state: State
    to_state: State
    trigger: str


class _CloseSignal:
    __slots__ = ()


class _StartSignal:
    __slots__ = ()


class _CloseSession(Exception):
    """Internal control flow: the caller closed while paused."""


_CLOSE: Final = _CloseSignal()
_START: Final = _StartSignal()
SendValue: TypeAlias = ToolResult | ResumeDecision | _CloseSignal

_BUDGET_MESSAGES: Final[Mapping[BudgetLimit, str]] = MappingProxyType(
    {
        BudgetLimit.TURNS: "turn budget reached",
        BudgetLimit.TOOL_CALLS: "tool call budget reached",
        BudgetLimit.WALL_CLOCK: "wall-clock budget reached",
    }
)


def _plan_data(plan: Sequence[PlanStep]) -> list[dict[str, Any]]:
    return [
        {
            "step_id": step.step_id,
            "title": step.title,
            "tool": step.tool,
            "arguments": dict(step.arguments),
        }
        for step in plan
    ]


class HarnessLoop:
    """One resumable session driven by a deterministic planner."""

    def __init__(
        self,
        planner: Planner | None = None,
        *,
        limits: BudgetLimits | None = None,
        hooks: HookRegistry | None = None,
        clock: Callable[[], float] | None = None,
        session_id: str = "session-1",
    ) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self._planner: Planner = planner or DeterministicPlanner()
        self._limits = limits or BudgetLimits()
        self._hooks = hooks or HookRegistry()
        self._clock: Callable[[], float] = clock or time.monotonic
        self._session_id = session_id
        self._events = EventStream(session_id, self._clock)
        self._state = State.IDLE
        self._budget: Budget | None = None
        self._transitions: list[Transition] = []
        self._gen: Generator[PullRequest, SendValue, SessionResult] | None = None
        self._last_pull: PullRequest | None = None
        self._paused = False
        self._resume_state: State | None = None
        self._result: SessionResult | None = None
        self._started = False
        self._finished = False
        self._wall_hit = False
        self._skip_next = False
        self._completed_steps = 0
        self._failed_steps = 0
        self._skipped_steps = 0

    @property
    def state(self) -> State:
        return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def events(self) -> EventStream:
        return self._events

    @property
    def transitions(self) -> tuple[Transition, ...]:
        return tuple(self._transitions)

    @property
    def hooks(self) -> HookRegistry:
        return self._hooks

    @property
    def budget(self) -> Budget | None:
        return self._budget

    @property
    def result(self) -> SessionResult | None:
        return self._result

    @property
    def last_pull(self) -> PullRequest | None:
        return self._last_pull

    def run(self, goal: str) -> PullRequest | SessionResult:
        """Start a fresh session; only legal from the initial IDLE state."""
        if self._started:
            raise IllegalTransition("run() is only legal once per loop instance")
        if self._state is not State.IDLE:
            raise IllegalTransition(f"run() requires IDLE, found {self._state.value}")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")

        self._started = True
        self._budget = Budget.start(self._limits, self._clock())
        self._events.append(EventType.SESSION_START, {"goal": goal})
        self._transition(State.PLANNING, "run")
        self._gen = self._session(goal)
        return self._advance(_START)

    def resume(
        self,
        payload: ToolResult | ResumeDecision,
    ) -> PullRequest | SessionResult:
        """Answer the current pull. Validation happens before sending."""
        if self._finished or self._gen is None:
            raise IllegalTransition("session is not resumable")
        pull = self._last_pull
        if pull is None:
            raise IllegalTransition("no pull is waiting for a response")

        if pull.kind is PullKind.TOOL:
            if not isinstance(payload, ToolResult):
                raise ProtocolError("tool pulls require a ToolResult")
            if self._state is not State.AWAITING_TOOL:
                raise ProtocolError(
                    f"tool pull resumed while state is {self._state.value}"
                )
            if pull.call is None or payload.call_id != pull.call.call_id:
                expected = pull.call.call_id if pull.call is not None else "<none>"
                raise ProtocolError(
                    f"ToolResult.call_id {payload.call_id!r} does not match {expected!r}"
                )
            return self._advance(payload)

        if not isinstance(payload, ResumeDecision):
            raise ProtocolError("pause pulls require a ResumeDecision")
        if pull.pause is None:
            raise ProtocolError("pause pull is missing pause details")
        self._validate_extension(pull.pause, payload.extension)
        return self._advance(payload)

    def close(self) -> SessionResult:
        """Close a paused session; only legal while the loop is paused."""
        if not self._paused or self._last_pull is None:
            raise IllegalTransition("close() requires a paused session")
        if self._last_pull.kind is not PullKind.PAUSE:
            raise IllegalTransition("close() requires a pause pull")
        if self._finished or self._gen is None:
            raise IllegalTransition("session is already complete")
        outcome = self._advance(_CLOSE)
        if not isinstance(outcome, SessionResult):
            raise RuntimeError("close() did not finish the session")
        return outcome

    def _advance(self, value: object) -> PullRequest | SessionResult:
        generator = self._gen
        if generator is None:
            raise IllegalTransition("session is not runnable")
        try:
            if value is _START:
                pull = next(generator)
            else:
                pull = generator.send(cast(SendValue, value))
        except StopIteration as stop:
            result = stop.value
            if not isinstance(result, SessionResult):
                raise RuntimeError("harness generator returned no SessionResult")
            self._gen = None
            self._last_pull = None
            self._result = result
            return result
        self._last_pull = pull
        return pull

    def _session(
        self,
        goal: str,
    ) -> Generator[PullRequest, SendValue, SessionResult]:
        try:
            return (yield from self._run_session(goal))
        except _CloseSession:
            return self._finish(Outcome.CLOSED, trigger="close")

    def _run_session(
        self,
        goal: str,
    ) -> Generator[PullRequest, SendValue, SessionResult]:
        yield from self._maybe_wall_pause()

        before_plan = self._fire(HookTopic.BEFORE_PLAN, {"goal": goal})
        if before_plan.failure is not None:
            return self._finish(Outcome.ABORTED, trigger="aborted")
        if before_plan.pause_requested:
            yield from self._pause(
                PauseReason.HOOK_REVIEW,
                message="before_plan requested review",
            )
        planned_goal = before_plan.payload.get("goal")
        if not isinstance(planned_goal, str) or not planned_goal.strip():
            self._record_on_error(
                HookFailure(
                    topic=HookTopic.BEFORE_PLAN,
                    hook="goal",
                    message="ProtocolError: before_plan payload['goal'] must be a non-empty string",
                )
            )
            return self._finish(Outcome.ABORTED, trigger="aborted")

        draft = tuple(self._planner.plan(planned_goal))
        self._events.append(
            EventType.PLAN_DRAFT,
            {"goal": planned_goal, "plan": _plan_data(draft)},
        )

        after_plan = self._fire(
            HookTopic.AFTER_PLAN,
            {"goal": planned_goal, "plan": list(draft)},
        )
        if after_plan.failure is not None:
            return self._finish(Outcome.ABORTED, trigger="aborted")
        if after_plan.pause_requested:
            yield from self._pause(
                PauseReason.HOOK_REVIEW,
                message="after_plan requested review",
            )

        plan = self._parse_plan(after_plan.payload.get("plan"))
        if plan is None:
            self._record_on_error(
                HookFailure(
                    topic=HookTopic.AFTER_PLAN,
                    hook="plan",
                    message="ProtocolError: after_plan payload['plan'] must be a sequence of PlanStep",
                )
            )
            return self._finish(Outcome.ABORTED, trigger="aborted")
        if not plan:
            return self._finish(Outcome.NO_PLAN, trigger="no_plan")

        self._events.append(EventType.PLAN_COMMIT, {"plan": _plan_data(plan)})
        self._transition(State.EXECUTING, "plan_committed")
        yield from self._maybe_wall_pause()
        return (yield from self._run_steps(plan))

    def _run_steps(
        self,
        plan: tuple[PlanStep, ...],
    ) -> Generator[PullRequest, SendValue, SessionResult]:
        budget = self._budget
        if budget is None:
            raise RuntimeError("step loop without a budget")
        total = len(plan)
        for index, step in enumerate(plan):
            yield from self._maybe_wall_pause()
            while (limit := budget.try_start_turn()) is not None:
                yield from self._budget_pause(limit)

            self._events.append(
                EventType.STEP_START,
                {
                    "index": index,
                    "step_id": step.step_id,
                    "title": step.title,
                    "requires_tool": step.tool is not None,
                },
            )

            if self._skip_next:
                self._skip_next = False
                self._transition(State.REFLECTING, "step_finished")
                self._end_step(index, step, StepStatus.SKIPPED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue

            before_step = self._fire(
                HookTopic.BEFORE_STEP,
                {"index": index, "step": step},
            )
            if before_step.failure is not None:
                self._transition(State.REFLECTING, "step_finished")
                self._end_step(index, step, StepStatus.FAILED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue
            if before_step.pause_requested:
                yield from self._pause(
                    PauseReason.HOOK_REVIEW,
                    message="before_step requested review",
                )
            if before_step.skip_next_step:
                self._transition(State.REFLECTING, "step_finished")
                self._end_step(index, step, StepStatus.SKIPPED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue

            if step.tool is None:
                self._transition(State.REFLECTING, "step_finished")
                yield from self._maybe_wall_pause()
                status = StepStatus.SUCCESS
                after_step = self._fire(
                    HookTopic.AFTER_STEP,
                    {
                        "index": index,
                        "step": step,
                        "status": status,
                        "tool_result": None,
                    },
                )
                if after_step.failure is not None:
                    status = StepStatus.FAILED
                if after_step.pause_requested:
                    yield from self._pause(
                        PauseReason.HOOK_REVIEW,
                        message="after_step requested review",
                    )
                if after_step.skip_next_step:
                    self._skip_next = True
                self._end_step(index, step, status)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue

            call = ToolCall(
                call_id=f"call-{index + 1}",
                tool=step.tool,
                arguments=step.arguments,
            )
            before_tool = self._fire(
                HookTopic.BEFORE_TOOL_CALL,
                {"index": index, "step": step, "call": call},
            )
            if before_tool.failure is not None:
                self._emit_tool_error(
                    index,
                    call.call_id,
                    before_tool.failure.message,
                    "hook",
                    before_tool.failure,
                )
                self._transition(State.REFLECTING, "step_finished")
                self._end_step(index, step, StepStatus.FAILED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue
            if before_tool.pause_requested:
                yield from self._pause(
                    PauseReason.HOOK_REVIEW,
                    message="before_tool_call requested review",
                )

            mutated_call = before_tool.payload.get("call")
            if not isinstance(mutated_call, ToolCall):
                failure = HookFailure(
                    topic=HookTopic.BEFORE_TOOL_CALL,
                    hook="call",
                    message="ProtocolError: before_tool_call payload['call'] must be a ToolCall",
                )
                self._record_on_error(failure)
                self._emit_tool_error(
                    index,
                    call.call_id,
                    failure.message,
                    "hook",
                    failure,
                )
                self._transition(State.REFLECTING, "step_finished")
                self._end_step(index, step, StepStatus.FAILED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue
            call = mutated_call

            while (limit := budget.try_call_tool()) is not None:
                yield from self._budget_pause(limit)

            self._events.append(
                EventType.TOOL_CALL,
                {
                    "index": index,
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "arguments": dict(call.arguments),
                },
            )
            self._transition(State.AWAITING_TOOL, "tool_requested")
            yield from self._maybe_wall_pause()

            pull = PullRequest(
                kind=PullKind.TOOL,
                session_id=self._session_id,
                state=State.AWAITING_TOOL,
                event_offset=len(self._events.events),
                budget=budget.snapshot(self._clock()),
                call=call,
            )
            received = yield pull

            if isinstance(received, _CloseSignal):
                raise _CloseSession
            if not isinstance(received, ToolResult):
                raise ProtocolError("tool pull requires a ToolResult")
            if received.call_id != call.call_id:
                raise ProtocolError(
                    f"ToolResult.call_id {received.call_id!r} does not match {call.call_id!r}"
                )

            self._emit_tool_result(index, received)
            self._transition(State.REFLECTING, "tool_result")
            yield from self._maybe_wall_pause()

            after_tool = self._fire(
                HookTopic.AFTER_TOOL_CALL,
                {
                    "index": index,
                    "step": step,
                    "call": call,
                    "result": received,
                },
            )
            if after_tool.failure is not None:
                self._emit_tool_error(
                    index,
                    call.call_id,
                    after_tool.failure.message,
                    "hook",
                    after_tool.failure,
                )
                self._end_step(index, step, StepStatus.FAILED)
                advanced = yield from self._reflection_advance(index, total)
                if advanced is not None:
                    return advanced
                continue
            if after_tool.pause_requested:
                yield from self._pause(
                    PauseReason.HOOK_REVIEW,
                    message="after_tool_call requested review",
                )

            status = StepStatus.SUCCESS if received.ok else StepStatus.FAILED
            after_step = self._fire(
                HookTopic.AFTER_STEP,
                {
                    "index": index,
                    "step": step,
                    "status": status,
                    "tool_result": received,
                },
            )
            if after_step.failure is not None:
                status = StepStatus.FAILED
            if after_step.pause_requested:
                yield from self._pause(
                    PauseReason.HOOK_REVIEW,
                    message="after_step requested review",
                )
            if after_step.skip_next_step:
                self._skip_next = True
            self._end_step(index, step, status)
            advanced = yield from self._reflection_advance(index, total)
            if advanced is not None:
                return advanced

        raise RuntimeError("planner returned no steps after plan commit")

    def _reflection_advance(
        self,
        index: int,
        total: int,
    ) -> Generator[PullRequest, SendValue, SessionResult | None]:
        yield from self._maybe_wall_pause()
        if index + 1 >= total:
            outcome = (
                Outcome.INCOMPLETE
                if self._failed_steps or self._skipped_steps
                else Outcome.GOAL_MET
            )
            return self._finish(outcome, trigger="goal_met")
        self._transition(State.EXECUTING, "next_step")
        return None

    def _finish(self, outcome: Outcome, *, trigger: str) -> SessionResult:
        if self._finished:
            raise RuntimeError("session already finished")
        if self._state is not State.DONE:
            self._transition(State.DONE, trigger)
        self._finished = True
        self._paused = False
        self._resume_state = None
        self._skip_next = False
        if self._budget is None:
            raise RuntimeError("session finished without a budget")
        snapshot = self._budget.snapshot(self._clock())

        self._fire(
            HookTopic.ON_COMPLETE,
            {
                "outcome": outcome,
                "turns": snapshot.turns,
                "tool_calls": snapshot.tool_calls,
                "elapsed_s": snapshot.elapsed_s,
            },
            safe=True,
        )
        self._events.append(
            EventType.SESSION_COMPLETE,
            {
                "outcome": outcome.value,
                "turns": snapshot.turns,
                "tool_calls": snapshot.tool_calls,
                "elapsed_s": snapshot.elapsed_s,
                "steps": {
                    "completed": self._completed_steps,
                    "failed": self._failed_steps,
                    "skipped": self._skipped_steps,
                },
            },
        )
        result = SessionResult(
            session_id=self._session_id,
            outcome=outcome,
            turns=snapshot.turns,
            tool_calls=snapshot.tool_calls,
            elapsed_s=snapshot.elapsed_s,
            completed_steps=self._completed_steps,
            failed_steps=self._failed_steps,
            skipped_steps=self._skipped_steps,
            event_offset=len(self._events.events),
        )
        self._result = result
        return result

    def _fire(
        self,
        topic: HookTopic,
        payload: dict[str, Any],
        *,
        safe: bool = False,
    ) -> HookOutcome:
        outcome = self._hooks.fire(
            topic,
            session_id=self._session_id,
            state=self._state,
            payload=payload,
        )
        if outcome.failure is not None and not safe:
            self._record_on_error(outcome.failure)
        return outcome

    def _record_on_error(self, failure: HookFailure) -> None:
        self._hooks.fire(
            HookTopic.ON_ERROR,
            session_id=self._session_id,
            state=self._state,
            payload={"failure": failure},
        )

    def _budget_pause(
        self,
        limit: BudgetLimit,
    ) -> Generator[PullRequest, SendValue, None]:
        if self._budget is None:
            raise RuntimeError("budget pause without a budget")
        if limit is BudgetLimit.WALL_CLOCK:
            self._wall_hit = False
        snapshot = self._budget.snapshot(self._clock())
        self._fire(
            HookTopic.ON_BUDGET_EXCEEDED,
            {"limit": limit, "budget": snapshot},
            safe=True,
        )
        self._events.append(
            EventType.BUDGET_WARN,
            {
                "limit": limit.value,
                "current": self._budget.current(limit, self._clock()),
                "maximum": self._budget.maximum(limit),
                "elapsed_s": self._budget.elapsed(self._clock()),
            },
        )
        yield from self._pause(
            PauseReason.BUDGET_EXCEEDED,
            limit=limit,
            message=_BUDGET_MESSAGES[limit],
        )

    def _pause(
        self,
        reason: PauseReason,
        *,
        limit: BudgetLimit | None = None,
        message: str,
    ) -> Generator[PullRequest, SendValue, ResumeDecision]:
        if self._budget is None:
            raise RuntimeError("pause without a budget")
        resume_state = self._state
        if resume_state not in ACTIVE_STATES:
            raise IllegalTransition(
                f"cannot pause from {resume_state.value}"
            )
        snapshot = self._budget.snapshot(self._clock())
        pause = PauseInfo(
            reason=reason,
            resume_state=resume_state,
            limit=limit,
            message=message,
        )
        self._paused = True
        self._resume_state = resume_state
        self._fire(
            HookTopic.ON_PAUSE,
            {"pause": pause, "budget": snapshot},
            safe=True,
        )
        self._transition(State.IDLE, "pause", pause=True)
        self._events.append(
            EventType.SESSION_PAUSE,
            {
                "reason": reason.value,
                "resume_state": resume_state.value,
                "limit": limit.value if limit is not None else None,
                "message": message,
                "budget": snapshot.as_dict(),
            },
        )
        pull = PullRequest(
            kind=PullKind.PAUSE,
            session_id=self._session_id,
            state=State.IDLE,
            event_offset=len(self._events.events),
            budget=snapshot,
            pause=pause,
        )
        received = yield pull
        if isinstance(received, _CloseSignal):
            raise _CloseSession
        if not isinstance(received, ResumeDecision):
            raise ProtocolError("pause resume requires a ResumeDecision")
        if received.extension is not None:
            self._budget.extend(received.extension)
            self._wall_hit = False
        self._transition(resume_state, "resume")
        self._paused = False
        self._resume_state = None
        yield from self._maybe_wall_pause()
        return received

    def _maybe_wall_pause(self) -> Generator[PullRequest, SendValue, None]:
        while self._wall_hit:
            self._wall_hit = False
            yield from self._budget_pause(BudgetLimit.WALL_CLOCK)

    def _transition(
        self,
        target: State,
        trigger: str,
        *,
        pause: bool = False,
    ) -> None:
        from_state = self._state
        allowed = target in LEGAL_TRANSITIONS[from_state]
        if pause:
            allowed = from_state in ACTIVE_STATES and target is State.IDLE
        elif self._paused and from_state is State.IDLE:
            allowed = target is self._resume_state or target is State.DONE
        if not allowed:
            raise IllegalTransition(
                f"cannot move {from_state.value} -> {target.value} ({trigger})"
            )
        if (
            self._budget is not None
            and not self._finished
            and self._budget.wall_clock_exceeded(self._clock())
        ):
            self._wall_hit = True
        self._state = target
        self._transitions.append(
            Transition(
                sequence=len(self._transitions),
                from_state=from_state,
                to_state=target,
                trigger=trigger,
            )
        )

    def _validate_extension(
        self,
        pause: PauseInfo,
        extension: BudgetExtension | None,
    ) -> None:
        if pause.reason is not PauseReason.BUDGET_EXCEEDED:
            return
        limit = pause.limit
        if limit is None or extension is None:
            raise BudgetNotExtended(
                f"resume requires a budget extension for {pause.message}"
            )
        if limit is BudgetLimit.TURNS and extension.turns <= 0:
            raise BudgetNotExtended("turn budget pause requires additional turns")
        if limit is BudgetLimit.TOOL_CALLS and extension.tool_calls <= 0:
            raise BudgetNotExtended(
                "tool call budget pause requires additional tool calls"
            )
        if limit is BudgetLimit.WALL_CLOCK and extension.wall_clock_s <= 0:
            raise BudgetNotExtended(
                "wall-clock pause requires additional wall-clock seconds"
            )

    def _parse_plan(self, value: object) -> tuple[PlanStep, ...] | None:
        if isinstance(value, (str, bytes, bytearray)):
            return None
        if not isinstance(value, Sequence):
            return None
        steps: list[PlanStep] = []
        for item in value:
            if not isinstance(item, PlanStep):
                return None
            steps.append(item)
        return tuple(steps)

    def _end_step(self, index: int, step: PlanStep, status: StepStatus) -> None:
        self._events.append(
            EventType.STEP_END,
            {
                "index": index,
                "step_id": step.step_id,
                "status": status.value,
            },
        )
        if status is StepStatus.SUCCESS:
            self._completed_steps += 1
        elif status is StepStatus.FAILED:
            self._failed_steps += 1
        else:
            self._skipped_steps += 1

    def _emit_tool_result(self, index: int, result: ToolResult) -> None:
        if result.ok:
            self._events.append(
                EventType.TOOL_RESULT,
                {
                    "index": index,
                    "call_id": result.call_id,
                    "ok": True,
                    "data": dict(result.data),
                },
            )
        else:
            self._events.append(
                EventType.TOOL_ERROR,
                {
                    "index": index,
                    "call_id": result.call_id,
                    "error": result.error or "tool call failed",
                    "source": "tool",
                },
            )

    def _emit_tool_error(
        self,
        index: int,
        call_id: str,
        error: str,
        source: Literal["tool", "hook"],
        failure: HookFailure | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "index": index,
            "call_id": call_id,
            "error": error,
            "source": source,
        }
        if failure is not None:
            data["hook"] = failure.hook
            data["topic"] = failure.topic.value
        self._events.append(EventType.TOOL_ERROR, data)

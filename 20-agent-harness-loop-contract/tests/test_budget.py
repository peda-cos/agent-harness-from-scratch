from __future__ import annotations

import pytest

from agent_harness import (
    Budget,
    BudgetExtension,
    BudgetLimit,
    BudgetLimits,
    BudgetNotExtended,
    EventType,
    HarnessLoop,
    IllegalTransition,
    Outcome,
    PauseReason,
    PullKind,
    PullRequest,
    ResumeDecision,
    SessionResult,
    State,
    ToolResult,
)
from conftest import FakeClock, drain


def test_budget_allowance_counters_and_extension_boundaries() -> None:
    budget = Budget.start(
        BudgetLimits(max_turns=1, max_tool_calls=1, max_wall_clock_s=10.0),
        now=0.0,
    )

    assert budget.try_start_turn() is None
    assert budget.try_start_turn() is BudgetLimit.TURNS
    assert budget.turns == 1
    assert budget.try_call_tool() is None
    assert budget.try_call_tool() is BudgetLimit.TOOL_CALLS
    assert budget.tool_calls == 1
    assert budget.wall_clock_exceeded(9.999) is False
    assert budget.wall_clock_exceeded(10.0) is True

    budget.extend(BudgetExtension(turns=2, tool_calls=1, wall_clock_s=5.0))
    assert budget.try_start_turn() is None
    assert budget.try_call_tool() is None
    snapshot = budget.snapshot(now=12.0)
    assert snapshot.turns == 2
    assert snapshot.max_turns == 3
    assert snapshot.tool_calls == 2
    assert snapshot.max_tool_calls == 2
    assert snapshot.elapsed_s == pytest.approx(12.0)
    assert snapshot.max_wall_clock_s == pytest.approx(15.0)
    assert snapshot.as_dict()["max_turns"] == 3
    assert budget.wall_clock_exceeded(15.0) is True

    with pytest.raises(ValueError):
        BudgetLimits(max_turns=0)
    with pytest.raises(ValueError):
        BudgetExtension(turns=-1)


def test_turn_budget_pauses_before_next_step_and_extends() -> None:
    clock = FakeClock()
    loop = HarnessLoop(
        clock=clock,
        limits=BudgetLimits(max_turns=1, max_tool_calls=4, max_wall_clock_s=30),
    )

    first = loop.run("turn budget")
    assert isinstance(first, PullRequest)
    assert first.call is not None
    pause = loop.resume(ToolResult(first.call.call_id))

    assert isinstance(pause, PullRequest)
    assert pause.kind is PullKind.PAUSE
    assert pause.state is State.IDLE
    assert pause.pause is not None
    assert pause.pause.reason is PauseReason.BUDGET_EXCEEDED
    assert pause.pause.limit is BudgetLimit.TURNS
    assert pause.pause.resume_state is State.EXECUTING
    assert pause.pause.message == "turn budget reached"
    assert pause.budget.turns == 1
    assert pause.budget.max_turns == 1
    assert loop.state is State.IDLE
    assert loop.budget is not None
    assert loop.budget.turns == 1

    step_starts = [
        event for event in loop.events.events if event.type is EventType.STEP_START
    ]
    assert len(step_starts) == 1
    assert [event.type for event in loop.events.events[-2:]] == [
        EventType.BUDGET_WARN,
        EventType.SESSION_PAUSE,
    ]
    warning = loop.events.events[-2].data
    assert warning["limit"] == "turns"
    assert warning["current"] == 1
    assert warning["maximum"] == 1
    assert loop.events.events[-1].data["budget"]["max_turns"] == 1

    with pytest.raises(BudgetNotExtended):
        loop.resume(ResumeDecision())
    with pytest.raises(BudgetNotExtended):
        loop.resume(
            ResumeDecision(extension=BudgetExtension(wall_clock_s=5.0))
        )
    assert loop.state is State.IDLE
    assert loop.last_pull == pause

    second = loop.resume(
        ResumeDecision(extension=BudgetExtension(turns=2))
    )
    assert isinstance(second, PullRequest)
    assert second.call is not None
    assert second.call.call_id == "call-2"
    assert loop.budget.limits.max_turns == 3

    result = loop.resume(ToolResult(second.call.call_id))
    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.GOAL_MET
    assert result.turns == 3
    assert result.tool_calls == 2
    assert result.completed_steps == 3


def test_tool_budget_pauses_mid_step_before_second_call() -> None:
    loop = HarnessLoop(
        clock=FakeClock(),
        limits=BudgetLimits(max_turns=6, max_tool_calls=1, max_wall_clock_s=30),
    )

    first = loop.run("tool budget")
    assert isinstance(first, PullRequest)
    assert first.call is not None
    pause = loop.resume(ToolResult(first.call.call_id))

    assert isinstance(pause, PullRequest)
    assert pause.kind is PullKind.PAUSE
    assert pause.pause is not None
    assert pause.pause.limit is BudgetLimit.TOOL_CALLS
    assert pause.pause.resume_state is State.EXECUTING
    assert pause.budget.tool_calls == 1
    assert pause.budget.turns == 2
    assert pause.pause.message == "tool call budget reached"

    step_starts = [
        event.data["index"]
        for event in loop.events.events
        if event.type is EventType.STEP_START
    ]
    assert step_starts == [0, 1]
    tool_calls = [
        event.data["index"]
        for event in loop.events.events
        if event.type is EventType.TOOL_CALL
    ]
    assert tool_calls == [0]

    with pytest.raises(BudgetNotExtended):
        loop.resume(ResumeDecision())

    second = loop.resume(
        ResumeDecision(extension=BudgetExtension(tool_calls=1))
    )
    assert isinstance(second, PullRequest)
    assert second.call is not None
    assert second.call.call_id == "call-2"
    assert [
        event.data["index"]
        for event in loop.events.events
        if event.type is EventType.STEP_START
    ] == [0, 1]
    assert [
        event.data["index"]
        for event in loop.events.events
        if event.type is EventType.TOOL_CALL
    ] == [0, 1]

    result = loop.resume(ToolResult(second.call.call_id))
    assert result.outcome is Outcome.GOAL_MET
    assert result.tool_calls == 2


def test_wall_clock_pause_and_extension() -> None:
    clock = FakeClock()
    loop = HarnessLoop(
        clock=clock,
        limits=BudgetLimits(max_turns=6, max_tool_calls=6, max_wall_clock_s=1.0),
    )

    first = loop.run("wall clock")
    assert isinstance(first, PullRequest)
    assert first.call is not None
    clock.advance(2.0)
    pause = loop.resume(ToolResult(first.call.call_id))

    assert isinstance(pause, PullRequest)
    assert pause.pause is not None
    assert pause.pause.reason is PauseReason.BUDGET_EXCEEDED
    assert pause.pause.limit is BudgetLimit.WALL_CLOCK
    assert pause.pause.resume_state is State.REFLECTING
    assert pause.pause.message == "wall-clock budget reached"
    assert pause.budget.elapsed_s == pytest.approx(2.0)
    warning = next(
        event
        for event in loop.events.events
        if event.type is EventType.BUDGET_WARN
    )
    assert warning.data["limit"] == "wall_clock"
    assert warning.data["current"] == pytest.approx(2.0)
    assert warning.data["maximum"] == pytest.approx(1.0)
    assert loop.events.events[-1].type is EventType.SESSION_PAUSE
    assert loop.events.events.index(warning) < len(loop.events.events) - 1

    tool_result_before_pause = [
        event.type
        for event in loop.events.events
        if event.type is EventType.TOOL_RESULT
    ]
    assert tool_result_before_pause == [EventType.TOOL_RESULT]

    second = loop.resume(
        ResumeDecision(extension=BudgetExtension(wall_clock_s=10.0))
    )
    assert isinstance(second, PullRequest)
    assert second.call is not None
    assert second.call.call_id == "call-2"
    result = loop.resume(ToolResult(second.call.call_id))

    assert result.outcome is Outcome.GOAL_MET
    assert result.elapsed_s == pytest.approx(2.0)
    assert loop.budget is not None
    assert loop.budget.limits.max_wall_clock_s == pytest.approx(11.0)


def test_close_at_budget_pause_finishes_closed_and_once() -> None:
    loop = HarnessLoop(
        clock=FakeClock(),
        limits=BudgetLimits(max_turns=1, max_tool_calls=4, max_wall_clock_s=30),
    )
    first = loop.run("close on pause")
    assert isinstance(first, PullRequest)
    assert first.call is not None
    pause = loop.resume(ToolResult(first.call.call_id))
    assert isinstance(pause, PullRequest)

    result = loop.close()
    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.CLOSED
    assert loop.state is State.DONE
    assert loop.transitions[-1].from_state is State.IDLE
    assert loop.transitions[-1].to_state is State.DONE
    assert loop.transitions[-1].trigger == "close"
    assert (
        sum(
            1
            for event in loop.events.events
            if event.type is EventType.SESSION_COMPLETE
        )
        == 1
    )
    assert loop.events.events[-1].type is EventType.SESSION_COMPLETE
    assert loop.result == result

    with pytest.raises(IllegalTransition):
        loop.resume(ResumeDecision())
    with pytest.raises(IllegalTransition):
        loop.close()


def test_resume_after_completion_is_illegal() -> None:
    loop = HarnessLoop(clock=FakeClock())
    result = drain(loop, loop.run("finish"))
    assert result.outcome is Outcome.GOAL_MET
    with pytest.raises(IllegalTransition):
        loop.resume(ResumeDecision())
    with pytest.raises(IllegalTransition):
        loop.close()

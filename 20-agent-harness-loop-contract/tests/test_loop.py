from __future__ import annotations

import pytest

from agent_harness import (
    EventType,
    HarnessLoop,
    HookSignal,
    HookTopic,
    Outcome,
    ProtocolError,
    PullKind,
    PullRequest,
    ResumeDecision,
    SessionResult,
    State,
    StepStatus,
    ToolResult,
)
from conftest import FakeClock, drain


EXPECTED_EVENTS = [
    EventType.SESSION_START,
    EventType.PLAN_DRAFT,
    EventType.PLAN_COMMIT,
    EventType.STEP_START,
    EventType.TOOL_CALL,
    EventType.TOOL_RESULT,
    EventType.STEP_END,
    EventType.STEP_START,
    EventType.TOOL_CALL,
    EventType.TOOL_RESULT,
    EventType.STEP_END,
    EventType.STEP_START,
    EventType.STEP_END,
    EventType.SESSION_COMPLETE,
]


EXPECTED_TRANSITIONS = [
    (State.IDLE, State.PLANNING, "run"),
    (State.PLANNING, State.EXECUTING, "plan_committed"),
    (State.EXECUTING, State.AWAITING_TOOL, "tool_requested"),
    (State.AWAITING_TOOL, State.REFLECTING, "tool_result"),
    (State.REFLECTING, State.EXECUTING, "next_step"),
    (State.EXECUTING, State.AWAITING_TOOL, "tool_requested"),
    (State.AWAITING_TOOL, State.REFLECTING, "tool_result"),
    (State.REFLECTING, State.EXECUTING, "next_step"),
    (State.EXECUTING, State.REFLECTING, "step_finished"),
    (State.REFLECTING, State.DONE, "goal_met"),
]


def test_happy_path_pins_events_pulls_transitions_and_result() -> None:
    clock = FakeClock()
    loop = HarnessLoop(clock=clock)

    first = loop.run("fix the failing test")
    assert isinstance(first, PullRequest)
    assert first.kind is PullKind.TOOL
    assert first.state is State.AWAITING_TOOL
    assert first.pause is None
    assert first.call is not None
    assert first.call.call_id == "call-1"
    assert first.call.tool == "lookup_context"
    assert first.call.arguments == {"goal": "fix the failing test"}
    assert first.event_offset == len(loop.events.events) == 5
    assert first.budget.turns == 1
    assert first.budget.tool_calls == 1

    second = loop.resume(ToolResult(first.call.call_id, data={"matches": 2}))
    assert isinstance(second, PullRequest)
    assert second.call is not None
    assert second.call.call_id == "call-2"
    assert second.call.tool == "validate_change"
    assert second.budget.turns == 2
    assert second.budget.tool_calls == 2

    result = loop.resume(ToolResult(second.call.call_id, data={"valid": True}))
    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.GOAL_MET
    assert result.turns == 3
    assert result.tool_calls == 2
    assert result.completed_steps == 3
    assert result.failed_steps == 0
    assert result.skipped_steps == 0
    assert result.event_offset == len(loop.events.events) == 14
    assert loop.state is State.DONE
    assert loop.result == result

    assert [event.type for event in loop.events.events] == EXPECTED_EVENTS
    assert [
        (edge.from_state, edge.to_state, edge.trigger) for edge in loop.transitions
    ] == EXPECTED_TRANSITIONS
    assert all(event.session_id == "session-1" for event in loop.events.events)
    assert all(event.at == 0.0 for event in loop.events.events)

    starts = [
        event
        for event in loop.events.events
        if event.type is EventType.STEP_START
    ]
    assert [event.data["step_id"] for event in starts] == [
        "scan",
        "validate",
        "report",
    ]
    assert [event.data["requires_tool"] for event in starts] == [True, True, False]


def test_same_script_replays_same_events_and_transitions() -> None:
    def scripted_loop() -> HarnessLoop:
        loop = HarnessLoop(clock=FakeClock())
        outcome = loop.run("same goal")
        while isinstance(outcome, PullRequest):
            assert outcome.call is not None
            outcome = loop.resume(
                ToolResult(outcome.call.call_id, data={"tool": outcome.call.tool})
            )
        assert isinstance(outcome, SessionResult)
        return loop

    left = scripted_loop()
    right = scripted_loop()

    assert left.transitions == right.transitions
    assert [event.as_dict() for event in left.events.events] == [
        event.as_dict() for event in right.events.events
    ]


def test_tool_error_marks_step_failed_and_session_incomplete() -> None:
    loop = HarnessLoop(clock=FakeClock())
    first = loop.run("fail one tool")
    assert isinstance(first, PullRequest)
    assert first.call is not None

    second = loop.resume(
        ToolResult(first.call.call_id, ok=False, error="fixture rejected")
    )
    assert isinstance(second, PullRequest)
    assert second.call is not None
    result = loop.resume(ToolResult(second.call.call_id))

    assert result.outcome is Outcome.INCOMPLETE
    assert result.completed_steps == 2
    assert result.failed_steps == 1
    assert result.skipped_steps == 0
    statuses = [
        event.data["status"]
        for event in loop.events.events
        if event.type is EventType.STEP_END
    ]
    assert statuses == [
        StepStatus.FAILED.value,
        StepStatus.SUCCESS.value,
        StepStatus.SUCCESS.value,
    ]
    errors = [
        event.data
        for event in loop.events.events
        if event.type is EventType.TOOL_ERROR
    ]
    assert errors == [
        {
            "index": 0,
            "call_id": "call-1",
            "error": "fixture rejected",
            "source": "tool",
        }
    ]


def test_malformed_tool_result_leaves_session_resumable() -> None:
    loop = HarnessLoop(clock=FakeClock())
    first = loop.run("malformed result")
    assert isinstance(first, PullRequest)
    assert first.call is not None

    with pytest.raises(ProtocolError):
        loop.resume(ToolResult("call-999", data={"bad": True}))
    with pytest.raises(ProtocolError):
        loop.resume(ResumeDecision())
    assert loop.state is State.AWAITING_TOOL
    assert loop.last_pull == first

    second = loop.resume(ToolResult(first.call.call_id))
    assert isinstance(second, PullRequest)
    assert second.call is not None
    result = loop.resume(ToolResult(second.call.call_id))

    assert result.outcome is Outcome.GOAL_MET
    assert sum(
        1 for event in loop.events.events if event.type is EventType.TOOL_RESULT
    ) == 2


def test_skip_sentinel_consumes_turn_and_runs_no_tool() -> None:
    loop = HarnessLoop(clock=FakeClock())
    loop.hooks.subscribe(
        HookTopic.BEFORE_STEP,
        "skip-first",
        lambda context: (
            HookSignal.SKIP_STEP if context.payload["index"] == 0 else None
        ),
    )

    pull = loop.run("skip the scan")
    assert isinstance(pull, PullRequest)
    assert pull.call is not None
    assert pull.call.call_id == "call-2"

    result = drain(loop, pull)
    assert result.outcome is Outcome.INCOMPLETE
    assert result.turns == 3
    assert result.tool_calls == 1
    assert result.completed_steps == 2
    assert result.skipped_steps == 1
    assert result.failed_steps == 0

    first_end = next(
        event
        for event in loop.events.events
        if event.type is EventType.STEP_END
    )
    assert first_end.data == {
        "index": 0,
        "step_id": "scan",
        "status": "skipped",
    }
    tool_calls = [
        event for event in loop.events.events if event.type is EventType.TOOL_CALL
    ]
    assert [event.data["index"] for event in tool_calls] == [1]

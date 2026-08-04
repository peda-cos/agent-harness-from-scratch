from __future__ import annotations

from dataclasses import replace

import pytest

from agent_harness import (
    EventType,
    HarnessLoop,
    HookContext,
    HookFailure,
    HookRegistry,
    HookSignal,
    HookTopic,
    Outcome,
    PauseReason,
    PullKind,
    PullRequest,
    ResumeDecision,
    SessionResult,
    State,
    StepStatus,
    ToolCall,
)
from conftest import FakeClock, complete_normal, drain


def _recorder(calls: list[str], name: str):
    def hook(context: HookContext) -> None:
        calls.append(name)

    return hook


def test_all_hook_topics_fire_in_registration_order() -> None:
    calls: list[str] = []
    loop = HarnessLoop(clock=FakeClock())
    for topic in HookTopic:
        loop.hooks.subscribe(topic, topic.value, _recorder(calls, topic.value))

    complete_normal(loop)

    assert calls == [
        "before_plan",
        "after_plan",
        "before_step",
        "before_tool_call",
        "after_tool_call",
        "after_step",
        "before_step",
        "before_tool_call",
        "after_tool_call",
        "after_step",
        "before_step",
        "after_step",
        "on_complete",
    ]


def test_after_plan_mutation_is_visible_and_committed() -> None:
    seen: list[str] = []
    loop = HarnessLoop(clock=FakeClock())

    def rename(context: HookContext) -> None:
        seen.append(context.payload["plan"][0].title)
        updated = list(context.payload["plan"])
        updated[0] = replace(updated[0], title="Hooked scan")
        context.payload["plan"] = updated

    def observe(context: HookContext) -> None:
        seen.append(context.payload["plan"][0].title)

    loop.hooks.subscribe(HookTopic.AFTER_PLAN, "first", rename)
    loop.hooks.subscribe(HookTopic.AFTER_PLAN, "second", observe)
    complete_normal(loop)

    assert seen == ["Scan the goal context", "Hooked scan"]
    commit = next(
        event
        for event in loop.events.events
        if event.type is EventType.PLAN_COMMIT
    )
    assert commit.data["plan"][0]["title"] == "Hooked scan"


def test_registry_rejects_duplicate_empty_and_non_callable_hooks() -> None:
    registry = HookRegistry()
    registry.subscribe(HookTopic.BEFORE_STEP, "policy", lambda context: None)

    with pytest.raises(ValueError):
        registry.subscribe(HookTopic.BEFORE_STEP, "policy", lambda context: None)
    with pytest.raises(ValueError):
        registry.subscribe(HookTopic.AFTER_STEP, "  ", lambda context: None)
    with pytest.raises(ValueError):
        registry.subscribe(HookTopic.AFTER_STEP, "not-callable", object())
    assert registry.subscribers(HookTopic.BEFORE_STEP) == ("policy",)


def test_hook_exception_stops_topic_and_aborts_planning_session() -> None:
    calls: list[str] = []
    errors: list[HookFailure] = []
    loop = HarnessLoop(clock=FakeClock())

    def loose_policy(context: HookContext) -> None:
        calls.append("loose")
        raise RuntimeError("policy backend unavailable")

    def later(context: HookContext) -> None:
        calls.append("later")

    loop.hooks.subscribe(HookTopic.BEFORE_PLAN, "policy", loose_policy)
    loop.hooks.subscribe(HookTopic.BEFORE_PLAN, "later", later)
    loop.hooks.subscribe(
        HookTopic.ON_ERROR,
        "collect",
        lambda context: errors.append(context.payload["failure"]),
    )

    result = loop.run("planning must abort")

    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.ABORTED
    assert calls == ["loose"]
    assert len(errors) == 1
    assert errors[0].topic is HookTopic.BEFORE_PLAN
    assert errors[0].hook == "policy"
    assert "policy backend unavailable" in errors[0].message
    assert [event.type for event in loop.events.events] == [
        EventType.SESSION_START,
        EventType.SESSION_COMPLETE,
    ]


def test_before_tool_call_exception_aborts_turn_but_session_continues() -> None:
    errors: list[HookFailure] = []
    loop = HarnessLoop(clock=FakeClock())

    def deny_first(context: HookContext) -> None:
        if context.payload["index"] == 0:
            raise RuntimeError("blocked by policy")

    loop.hooks.subscribe(HookTopic.BEFORE_TOOL_CALL, "deny", deny_first)
    loop.hooks.subscribe(
        HookTopic.ON_ERROR,
        "collect",
        lambda context: errors.append(context.payload["failure"]),
    )

    pull = loop.run("policy blocks the first tool")
    assert isinstance(pull, PullRequest)
    assert pull.call is not None
    assert pull.call.call_id == "call-2"

    result = drain(loop, pull)
    assert result.outcome is Outcome.INCOMPLETE
    assert result.completed_steps == 2
    assert result.failed_steps == 1
    assert len(errors) == 1

    tool_errors = [
        event.data
        for event in loop.events.events
        if event.type is EventType.TOOL_ERROR
    ]
    assert tool_errors[0]["source"] == "hook"
    assert tool_errors[0]["hook"] == "deny"
    assert tool_errors[0]["topic"] == "before_tool_call"
    tool_calls = [
        event.data["index"]
        for event in loop.events.events
        if event.type is EventType.TOOL_CALL
    ]
    assert tool_calls == [1]
    first_end = next(
        event.data
        for event in loop.events.events
        if event.type is EventType.STEP_END and event.data["index"] == 0
    )
    assert first_end["status"] == StepStatus.FAILED.value


def test_after_plan_exception_aborts_without_plan_commit() -> None:
    loop = HarnessLoop(clock=FakeClock())

    def reject(context: HookContext) -> None:
        raise ValueError("bad plan")

    loop.hooks.subscribe(HookTopic.AFTER_PLAN, "reject", reject)
    result = loop.run("cannot commit")

    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.ABORTED
    assert [event.type for event in loop.events.events] == [
        EventType.SESSION_START,
        EventType.PLAN_DRAFT,
        EventType.SESSION_COMPLETE,
    ]


def test_after_step_exception_marks_step_failed() -> None:
    loop = HarnessLoop(clock=FakeClock())

    def fail_once(context: HookContext) -> None:
        if context.payload["index"] == 0:
            raise RuntimeError("telemetry failed")

    loop.hooks.subscribe(HookTopic.AFTER_STEP, "telemetry", fail_once)
    result = complete_normal(loop)

    assert result.outcome is Outcome.INCOMPLETE
    assert result.failed_steps == 1
    assert result.completed_steps == 2


def test_safe_on_complete_error_is_recorded_not_fatal() -> None:
    loop = HarnessLoop(clock=FakeClock())
    on_error_calls: list[HookFailure] = []

    def explode(context: HookContext) -> None:
        raise RuntimeError("completion sink down")

    loop.hooks.subscribe(HookTopic.ON_COMPLETE, "sink", explode)
    loop.hooks.subscribe(
        HookTopic.ON_ERROR,
        "collect",
        lambda context: on_error_calls.append(context.payload["failure"]),
    )

    result = complete_normal(loop)

    assert result.outcome is Outcome.GOAL_MET
    assert on_error_calls == []
    assert len(loop.hooks.failures) == 1
    assert loop.hooks.failures[0].topic is HookTopic.ON_COMPLETE
    assert (
        sum(
            1
            for event in loop.events.events
            if event.type is EventType.SESSION_COMPLETE
        )
        == 1
    )


def test_hook_review_pause_resumes_without_refiring_hook() -> None:
    loop = HarnessLoop(clock=FakeClock())
    calls: list[int] = []

    def review_once(context: HookContext) -> HookSignal | None:
        calls.append(context.payload["index"])
        if len(calls) == 1:
            return HookSignal.PAUSE
        return None

    loop.hooks.subscribe(HookTopic.BEFORE_STEP, "human-review", review_once)

    pause = loop.run("review before execution")
    assert isinstance(pause, PullRequest)
    assert pause.kind is PullKind.PAUSE
    assert pause.state is State.IDLE
    assert pause.pause is not None
    assert pause.pause.reason is PauseReason.HOOK_REVIEW
    assert pause.pause.resume_state is State.EXECUTING
    assert pause.pause.message == "before_step requested review"
    assert loop.state is State.IDLE
    assert loop.events.events[-1].type is EventType.SESSION_PAUSE

    tool_pull = loop.resume(ResumeDecision())
    assert isinstance(tool_pull, PullRequest)
    assert tool_pull.call is not None
    assert calls == [0]
    result = drain(loop, tool_pull)
    assert result.outcome is Outcome.GOAL_MET
    assert calls == [0, 1, 2]


def test_before_tool_call_mutation_reaches_pull_and_event() -> None:
    loop = HarnessLoop(clock=FakeClock())

    def replace_call(context: HookContext) -> None:
        original = context.payload["call"]
        context.payload["call"] = ToolCall(
            call_id=original.call_id,
            tool="guarded_lookup",
            arguments={"goal": original.arguments["goal"]},
        )

    loop.hooks.subscribe(HookTopic.BEFORE_TOOL_CALL, "rewrite", replace_call)
    first = loop.run("rewrite the call")
    assert isinstance(first, PullRequest)
    assert first.call is not None
    assert first.call.tool == "guarded_lookup"
    assert first.call.arguments == {"goal": "rewrite the call"}
    call_event = next(
        event for event in loop.events.events if event.type is EventType.TOOL_CALL
    )
    assert call_event.data["tool"] == "guarded_lookup"
    drain(loop, first)

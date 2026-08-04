from __future__ import annotations

from collections.abc import Sequence

import pytest

from agent_harness import (
    DeterministicPlanner,
    EventType,
    HarnessLoop,
    HookTopic,
    IllegalTransition,
    Outcome,
    PlanStep,
    PullRequest,
    ResumeDecision,
    SessionResult,
    State,
    StepStatus,
)
from conftest import FakeClock, complete_normal, drain


class EmptyPlanner:
    def plan(self, goal: str) -> Sequence[PlanStep]:
        return ()


class RecordingPlanner:
    def __init__(self) -> None:
        self.goals: list[str] = []

    def plan(self, goal: str) -> Sequence[PlanStep]:
        self.goals.append(goal)
        return DeterministicPlanner().plan(goal)


def test_public_contract_members_are_exact() -> None:
    assert {state.value for state in State} == {
        "IDLE",
        "PLANNING",
        "EXECUTING",
        "AWAITING_TOOL",
        "REFLECTING",
        "DONE",
    }
    assert {topic.value for topic in HookTopic} == {
        "before_plan",
        "after_plan",
        "before_tool_call",
        "after_tool_call",
        "before_step",
        "after_step",
        "on_error",
        "on_pause",
        "on_budget_exceeded",
        "on_complete",
    }
    assert {event.value for event in EventType} == {
        "session.start",
        "plan.draft",
        "plan.commit",
        "step.start",
        "step.end",
        "tool.call",
        "tool.result",
        "tool.error",
        "budget.warn",
        "session.pause",
        "session.complete",
    }
    assert {status.value for status in StepStatus} == {
        "success",
        "failed",
        "skipped",
    }


def test_fresh_loop_guards_and_run_once() -> None:
    loop = HarnessLoop(clock=FakeClock())

    with pytest.raises(IllegalTransition):
        loop.resume(ResumeDecision())
    with pytest.raises(ValueError):
        loop.run("   ")

    pull = loop.run("ship the contract")
    assert isinstance(pull, PullRequest)
    assert loop.state is State.AWAITING_TOOL

    with pytest.raises(IllegalTransition):
        loop.run("a second goal")
    with pytest.raises(IllegalTransition):
        loop.close()


def test_empty_plan_completes_with_no_plan() -> None:
    completed: list[Outcome] = []
    loop = HarnessLoop(planner=EmptyPlanner(), clock=FakeClock())
    loop.hooks.subscribe(
        HookTopic.ON_COMPLETE,
        "record",
        lambda context: completed.append(context.payload["outcome"]),
    )

    result = loop.run("goal with no plan")

    assert isinstance(result, SessionResult)
    assert result.outcome is Outcome.NO_PLAN
    assert result.turns == 0
    assert result.tool_calls == 0
    assert loop.state is State.DONE
    assert [event.type for event in loop.events.events] == [
        EventType.SESSION_START,
        EventType.PLAN_DRAFT,
        EventType.SESSION_COMPLETE,
    ]
    assert completed == [Outcome.NO_PLAN]
    assert [(edge.from_state, edge.to_state, edge.trigger) for edge in loop.transitions] == [
        (State.IDLE, State.PLANNING, "run"),
        (State.PLANNING, State.DONE, "no_plan"),
    ]


def test_planner_receives_mutated_goal_exactly_once() -> None:
    planner = RecordingPlanner()
    loop = HarnessLoop(planner=planner, clock=FakeClock())

    pull = loop.run("original goal")
    assert planner.goals == ["original goal"]
    drain(loop, pull)
    assert planner.goals == ["original goal"]


def test_normal_completion_is_goal_met() -> None:
    loop = HarnessLoop(clock=FakeClock())
    result = complete_normal(loop)

    assert result.outcome is Outcome.GOAL_MET
    assert result.turns == 3
    assert result.tool_calls == 2
    assert result.completed_steps == 3
    assert result.failed_steps == 0
    assert result.skipped_steps == 0
    assert result.event_offset == len(loop.events.events)
    assert loop.state is State.DONE

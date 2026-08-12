from __future__ import annotations

from collections.abc import Callable

import pytest

from plan_execute import (
    PlanExecuteAgent,
    Planner,
    SessionResult,
    Step,
    Toolbox,
    ToolError,
    deterministic_planner,
)

TOOL_NAMES = (
    "release.inspect",
    "release.draft_notes",
    "release.validate",
    "release.publish",
    "notes.read_cached",
    "release.queue_for_human",
    "notify.escalate",
)


def make_tools(
    failures: dict[str, str] | None = None,
    calls: list[str] | None = None,
) -> Toolbox:
    failure_map = failures or {}
    call_log = calls if calls is not None else []
    toolbox = Toolbox()

    def handler_for(name: str) -> Callable[..., object]:
        def handler(**kwargs: object) -> object:
            call_log.append(name)
            if name in failure_map:
                raise ToolError(failure_map[name])
            return {"tool": name, "args": kwargs}

        return handler

    for name in TOOL_NAMES:
        toolbox.register(name, handler_for(name), required=("repo",))
    return toolbox


def run_agent(
    failures: dict[str, str] | None = None,
    *,
    max_steps: int = 12,
    max_replans: int = 5,
    planner: Planner = deterministic_planner,
) -> tuple[SessionResult, list[str]]:
    calls: list[str] = []
    agent = PlanExecuteAgent(
        planner,
        make_tools(failures, calls),
        max_steps=max_steps,
        max_replans=max_replans,
    )
    return agent.run("release 1.0"), calls


def test_linear_success_emits_expected_outcomes_and_completes() -> None:
    result, calls = run_agent()

    assert result.status == "completed"
    assert result.reason == "goal_met"
    assert [step.id for step in result.history] == [1, 2, 3, 4]
    assert calls == list(TOOL_NAMES[:4])
    assert result.revisions == []

    plan_event = result.events[0]
    assert plan_event["event"] == "plan.created"
    assert [step["expected_outcome"] for step in plan_event["steps"]] == [
        "repository metadata is available",
        "draft release notes exist",
        "release notes pass validation",
        "the stable release is published",
    ]
    starts = [
        event for event in result.events if event["event"] == "step.started"
    ]
    assert len(starts) == 4
    assert all(event["expected_outcome"] for event in starts)
    assert result.events[-1]["event"] == "session.completed"
    assert result.events[-1]["status"] == "completed"
    assert result.events[-1]["steps_used"] == 4


def test_mid_plan_failure_replans_once_from_cursor() -> None:
    seen_errors: list[str | None] = []
    seen_histories: list[list[Step]] = []

    def recording_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        seen_errors.append(last_error)
        seen_histories.append(list(history))
        return deterministic_planner(goal, history, last_error)

    result, calls = run_agent(
        {"release.draft_notes": "rate_limited: quota"},
        planner=recording_planner,
    )

    assert result.status == "completed"
    assert result.reason == "goal_met"
    assert calls == [
        "release.inspect",
        "release.draft_notes",
        "notes.read_cached",
        "release.validate",
        "release.publish",
    ]
    assert [step.tool_name for step in result.history] == calls
    assert len(result.revisions) == 1
    assert result.revisions[0].to_dict() == {
        "removed": [4],
        "added": [1],
        "revised": [2, 3],
    }

    diff_event = next(
        event for event in result.events if event["event"] == "plan.diff"
    )
    assert diff_event["revision"] == 2
    assert diff_event["removed"] == [4]
    assert diff_event["added"] == [1]
    assert diff_event["revised"] == [2, 3]
    assert diff_event["trigger_error"] == "rate_limited: quota"

    assert seen_errors == [None, "rate_limited: quota"]
    assert [step.tool_name for step in seen_histories[1]] == [
        "release.inspect",
        "release.draft_notes",
    ]
    assert seen_histories[1][0].result == {
        "tool": "release.inspect",
        "args": {"repo": "release 1.0"},
    }
    assert seen_histories[1][1].error == "rate_limited: quota"


def test_second_failure_replans_with_prior_error() -> None:
    seen_errors: list[str | None] = []

    def recording_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        seen_errors.append(last_error)
        return deterministic_planner(goal, history, last_error)

    result, calls = run_agent(
        {
            "release.draft_notes": "rate_limited: quota",
            "release.validate": "approval_required: sign-off missing",
        },
        planner=recording_planner,
    )

    assert result.status == "completed"
    assert result.reason == "goal_met"
    assert calls == [
        "release.inspect",
        "release.draft_notes",
        "notes.read_cached",
        "release.validate",
        "release.queue_for_human",
        "notify.escalate",
    ]
    assert len(result.history) == 6
    assert len(result.revisions) == 2
    assert result.revisions[1].to_dict() == {
        "removed": [3],
        "added": [1],
        "revised": [2],
    }
    assert seen_errors == [
        None,
        "rate_limited: quota",
        "approval_required: sign-off missing",
    ]
    diff_events = [
        event for event in result.events if event["event"] == "plan.diff"
    ]
    assert [event["revision"] for event in diff_events] == [2, 3]


def test_replan_exhaustion_stops_at_ceiling() -> None:
    seen_errors: list[str | None] = []

    def recording_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        seen_errors.append(last_error)
        return deterministic_planner(goal, history, last_error)

    result, calls = run_agent(
        {
            "release.draft_notes": "rate_limited: quota",
            "notes.read_cached": "rate_limited: cache unavailable",
        },
        max_replans=2,
        planner=recording_planner,
    )

    assert result.status == "failed"
    assert result.reason == "replan_budget"
    assert len(seen_errors) == 3
    assert calls == [
        "release.inspect",
        "release.draft_notes",
        "notes.read_cached",
        "notes.read_cached",
    ]
    assert len(result.history) == 4
    assert len(result.revisions) == 2
    assert result.revisions[1].to_dict() == {
        "removed": [],
        "added": [],
        "revised": [],
    }
    assert result.events[-1]["event"] == "session.failed"
    assert result.events[-1]["reason"] == "replan_budget"
    assert result.events[-1]["replans_used"] == 2


def test_step_budget_counts_failed_attempts() -> None:
    result, calls = run_agent(
        {"release.draft_notes": "rate_limited: quota"},
        max_steps=3,
    )

    assert result.status == "failed"
    assert result.reason == "step_budget"
    assert calls == [
        "release.inspect",
        "release.draft_notes",
        "notes.read_cached",
    ]
    assert len(result.history) == 3
    assert len(result.revisions) == 1
    assert result.events[-1]["event"] == "session.failed"
    assert result.events[-1]["reason"] == "step_budget"
    assert result.events[-1]["steps_used"] == 3


def test_zero_step_budget_refuses_first_step() -> None:
    result, calls = run_agent(max_steps=0)

    assert result.status == "failed"
    assert result.reason == "step_budget"
    assert calls == []
    assert result.history == []
    assert result.revisions == []
    assert [event["event"] for event in result.events] == [
        "plan.created",
        "session.failed",
    ]
    assert result.events[-1]["steps_used"] == 0


def test_initial_empty_plan_returns_no_plan() -> None:
    calls: list[str] = []

    def empty_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        return []

    result = PlanExecuteAgent(empty_planner, make_tools(calls=calls)).run(
        "release 1.0"
    )

    assert result.status == "failed"
    assert result.reason == "no_plan"
    assert result.history == []
    assert result.revisions == []
    assert calls == []
    assert [event["event"] for event in result.events] == ["session.failed"]


def test_empty_replan_returns_no_plan_and_records_removal() -> None:
    result, calls = run_agent(
        {"release.draft_notes": "broken_manifest: missing version"}
    )

    assert result.status == "failed"
    assert result.reason == "no_plan"
    assert calls == ["release.inspect", "release.draft_notes"]
    assert len(result.history) == 2
    assert len(result.revisions) == 1
    assert result.revisions[0].to_dict() == {
        "removed": [2, 3, 4],
        "added": [],
        "revised": [],
    }
    diff_events = [
        event for event in result.events if event["event"] == "plan.diff"
    ]
    assert len(diff_events) == 1
    assert diff_events[0]["removed"] == [2, 3, 4]
    assert not any(
        event["event"] == "plan.created" and event["revision"] == 2
        for event in result.events
    )
    assert result.events[-1]["event"] == "session.failed"


def test_zero_replans_skips_planner_after_first_failure() -> None:
    seen_errors: list[str | None] = []

    def recording_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        seen_errors.append(last_error)
        return deterministic_planner(goal, history, last_error)

    result, calls = run_agent(
        {"release.draft_notes": "rate_limited: quota"},
        max_replans=0,
        planner=recording_planner,
    )

    assert result.status == "failed"
    assert result.reason == "replan_budget"
    assert seen_errors == [None]
    assert calls == ["release.inspect", "release.draft_notes"]
    assert len(result.history) == 2
    assert result.revisions == []
    assert not any(event["event"] == "plan.diff" for event in result.events)


def test_revised_step_uses_new_args() -> None:
    plans = [
        [
            Step(1, "demo.hit", {"value": 1}, "the first attempt works"),
            Step(2, "demo.ok", {}, "the second step works"),
        ],
        [
            Step(1, "demo.hit", {"value": 2}, "the retry works"),
            Step(2, "demo.ok", {}, "the second step works"),
        ],
    ]
    planner_calls: list[str | None] = []
    seen_values: list[object] = []

    def scripted_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        plan = plans[len(planner_calls)]
        planner_calls.append(last_error)
        return plan

    toolbox = Toolbox()

    def hit(**kwargs: object) -> object:
        seen_values.append(kwargs["value"])
        if kwargs["value"] == 1:
            raise ToolError("retry_with_new_args")
        return {"ok": True}

    def ok(**kwargs: object) -> object:
        return {"ok": True}

    toolbox.register("demo.hit", hit)
    toolbox.register("demo.ok", ok)

    result = PlanExecuteAgent(scripted_planner, toolbox).run("goal")

    assert result.status == "completed"
    assert len(result.history) == 3
    assert [step.tool_name for step in result.history] == [
        "demo.hit",
        "demo.hit",
        "demo.ok",
    ]
    assert result.revisions[0].to_dict() == {
        "removed": [],
        "added": [],
        "revised": [1],
    }
    assert seen_values == [1, 2]
    assert planner_calls == [None, "retry_with_new_args"]


def test_invalid_options_and_planner_output_raise() -> None:
    toolbox = make_tools()

    for options in (
        {"max_steps": -1},
        {"max_steps": True},
        {"max_replans": -1},
        {"max_replans": False},
    ):
        with pytest.raises(ValueError):
            PlanExecuteAgent(
                deterministic_planner,
                toolbox,
                **options,
            )

    calls: list[str] = []

    def bad_planner(
        goal: str,
        history: list[Step],
        last_error: str | None,
    ) -> list[Step]:
        return [
            Step(2, "release.inspect", {"repo": goal}, "first"),
            Step(1, "release.publish", {"repo": goal}, "second"),
        ]

    agent = PlanExecuteAgent(bad_planner, make_tools(calls=calls))
    with pytest.raises(ValueError, match="strictly increasing"):
        agent.run("release 1.0")
    assert calls == []

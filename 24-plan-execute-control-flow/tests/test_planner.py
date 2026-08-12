from __future__ import annotations

import pytest

from plan_execute import (
    APPROVAL_REQUIRED,
    RATE_LIMITED,
    Step,
    deterministic_planner,
    diff_plans,
    validate_plan,
)


def make_step(
    step_id: int,
    tool_name: str = "demo.tool",
    args: dict[str, object] | None = None,
) -> Step:
    return Step(step_id, tool_name, args or {}, "the step succeeds")


def test_first_plan_has_four_typed_steps() -> None:
    goal = "release 1.0"
    plan = deterministic_planner(goal, [], None)

    assert [step.id for step in plan] == [1, 2, 3, 4]
    assert [step.tool_name for step in plan] == [
        "release.inspect",
        "release.draft_notes",
        "release.validate",
        "release.publish",
    ]
    assert all(step.args["repo"] == goal for step in plan)
    assert all(step.expected_outcome for step in plan)
    assert all(step.result is None and step.error is None for step in plan)


def test_known_errors_route_and_unknown_error_returns_empty() -> None:
    rate_limited = deterministic_planner(
        "release 1.0", [], f"{RATE_LIMITED}: quota exhausted"
    )
    approval = deterministic_planner(
        "release 1.0", [], f"{APPROVAL_REQUIRED}: sign-off missing"
    )

    assert [step.tool_name for step in rate_limited] == [
        "notes.read_cached",
        "release.validate",
        "release.publish",
    ]
    assert [step.tool_name for step in approval] == [
        "release.queue_for_human",
        "notify.escalate",
    ]
    assert deterministic_planner("release 1.0", [], "something_else") == []


def test_validate_plan_rejects_non_monotonic_ids() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_plan([make_step(1), make_step(1)])
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_plan([make_step(2), make_step(1)])


def test_diff_plans_reports_removed_added_revised() -> None:
    old = [
        make_step(1, "A"),
        make_step(2, "B"),
        make_step(3, "C"),
        make_step(4, "D"),
    ]
    new = [
        make_step(1, "A"),
        make_step(2, "B2", {"changed": True}),
        make_step(3, "C"),
        make_step(5, "E"),
    ]

    diff = diff_plans(old, new)

    assert diff.removed == [4]
    assert diff.added == [5]
    assert diff.revised == [2]

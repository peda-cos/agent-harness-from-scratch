"""Offline, self-verifying demo for plan-execute control flow."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from plan_execute import (  # noqa: E402
    DEFAULT_MAX_REPLANS,
    DEFAULT_MAX_STEPS,
    PlanExecuteAgent,
    SessionResult,
    Step,
    ToolError,
    Toolbox,
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


def _emit(scenario: str, **fields: object) -> None:
    print(
        json.dumps(
            {"scenario": scenario, **fields},
            separators=(",", ":"),
        )
    )


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _handler(
    name: str, failures: dict[str, str]
) -> Callable[..., object]:
    def handler(**kwargs: object) -> object:
        if name in failures:
            raise ToolError(failures[name])
        return {"tool": name, "args": kwargs}

    return handler


def _toolbox(failures: dict[str, str] | None = None) -> Toolbox:
    failure_map = failures or {}
    toolbox = Toolbox()
    for name in TOOL_NAMES:
        toolbox.register(
            name,
            _handler(name, failure_map),
            required=("repo",),
        )
    return toolbox


def _empty_planner(
    goal: str,
    history: list[Step],
    last_error: str | None,
) -> list[Step]:
    return []


def _run(
    failures: dict[str, str] | None = None,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_replans: int = DEFAULT_MAX_REPLANS,
) -> SessionResult:
    agent = PlanExecuteAgent(
        deterministic_planner,
        _toolbox(failures),
        max_steps=max_steps,
        max_replans=max_replans,
    )
    return agent.run("release 1.0")


def run_demo() -> None:
    linear = _run()
    _expect(
        linear.status == "completed" and linear.reason == "goal_met",
        "linear plan should complete",
    )
    _expect(
        len(linear.history) == 4 and len(linear.revisions) == 0,
        "linear plan should execute four steps without revisions",
    )
    _emit(
        "linear_success",
        status=linear.status,
        reason=linear.reason,
        steps=len(linear.history),
        replans=len(linear.revisions),
    )

    replan_once = _run(
        {"release.draft_notes": "rate_limited: provider quota"}
    )
    _expect(
        replan_once.status == "completed"
        and replan_once.reason == "goal_met",
        "one replan should complete",
    )
    _expect(
        len(replan_once.history) == 5
        and len(replan_once.revisions) == 1,
        "one replan should execute five steps",
    )
    diff_event = next(
        event
        for event in replan_once.events
        if event["event"] == "plan.diff"
    )
    _expect(
        diff_event["removed"] == [4]
        and diff_event["added"] == [1]
        and diff_event["revised"] == [2, 3],
        "replan diff should describe the route-around plan",
    )
    _emit(
        "replan_once",
        status=replan_once.status,
        reason=replan_once.reason,
        steps=len(replan_once.history),
        replans=len(replan_once.revisions),
    )

    replan_twice = _run(
        {
            "release.draft_notes": "rate_limited: provider quota",
            "release.validate": (
                "approval_required: release manager sign-off missing"
            ),
        }
    )
    _expect(
        replan_twice.status == "completed"
        and len(replan_twice.history) == 6
        and len(replan_twice.revisions) == 2,
        "two replans should complete with six steps",
    )
    _emit(
        "replan_twice",
        status=replan_twice.status,
        reason=replan_twice.reason,
        steps=len(replan_twice.history),
        replans=len(replan_twice.revisions),
    )

    exhausted = _run(
        {
            "release.draft_notes": "rate_limited: provider quota",
            "notes.read_cached": "rate_limited: cache unavailable",
        },
        max_replans=2,
    )
    _expect(
        exhausted.status == "failed"
        and exhausted.reason == "replan_budget"
        and len(exhausted.history) == 4
        and len(exhausted.revisions) == 2,
        "replan ceiling should fail after two revisions",
    )
    _emit(
        "replan_exhaustion",
        status=exhausted.status,
        reason=exhausted.reason,
        steps=len(exhausted.history),
        replans=len(exhausted.revisions),
    )

    budget = _run(
        {"release.draft_notes": "rate_limited: provider quota"},
        max_steps=3,
    )
    _expect(
        budget.status == "failed"
        and budget.reason == "step_budget"
        and len(budget.history) == 3,
        "step budget should stop the fourth execution",
    )
    _emit(
        "step_budget",
        status=budget.status,
        reason=budget.reason,
        steps=len(budget.history),
        replans=len(budget.revisions),
    )

    no_plan = PlanExecuteAgent(_empty_planner, _toolbox()).run(
        "release 1.0"
    )
    _expect(
        no_plan.status == "failed"
        and no_plan.reason == "no_plan"
        and len(no_plan.history) == 0,
        "an empty initial plan should fail without executing",
    )
    _emit(
        "no_plan",
        status=no_plan.status,
        reason=no_plan.reason,
        steps=len(no_plan.history),
        replans=len(no_plan.revisions),
    )

    _expect(
        diff_event["revision"] == 2,
        "first replan should emit revision 2",
    )
    _emit(
        "plan_diff",
        revision=diff_event["revision"],
        removed=diff_event["removed"],
        added=diff_event["added"],
        revised=diff_event["revised"],
    )

    print("demo verified: 7 scenarios")


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Sequential plan executor with cursor replanning and hard budgets."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    Event,
    PlanDiff,
    SessionReason,
    SessionResult,
    SessionStatus,
    Step,
    diff_plans,
    validate_plan,
)
from .planner import Planner
from .tools import Dispatcher

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_REPLANS = 5


def _validate_limit(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


class PlanExecuteAgent:
    """Run plans sequentially and replan from the failed cursor within budgets."""

    def __init__(
        self,
        planner: Planner,
        dispatcher: Dispatcher,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_replans: int = DEFAULT_MAX_REPLANS,
    ) -> None:
        _validate_limit("max_steps", max_steps)
        _validate_limit("max_replans", max_replans)
        self._planner = planner
        self._dispatcher = dispatcher
        self.max_steps = max_steps
        self.max_replans = max_replans

    @staticmethod
    def _session_event(
        event: str,
        status: SessionStatus,
        reason: SessionReason,
        steps_used: int,
        replans_used: int,
    ) -> Event:
        return {
            "event": event,
            "status": status,
            "reason": reason,
            "steps_used": steps_used,
            "replans_used": replans_used,
        }

    def run(self, goal: str) -> SessionResult:
        history: list[Step] = []
        revisions: list[PlanDiff] = []
        events: list[Event] = []
        steps_used = 0
        replans_used = 0
        revision = 1

        plan = list(self._planner(goal, [], None))
        validate_plan(plan)

        while True:
            if not plan:
                events.append(
                    self._session_event(
                        "session.failed",
                        "failed",
                        "no_plan",
                        steps_used,
                        replans_used,
                    )
                )
                return SessionResult(
                    status="failed",
                    reason="no_plan",
                    history=history,
                    revisions=revisions,
                    events=events,
                )

            events.append(
                {
                    "event": "plan.created",
                    "revision": revision,
                    "count": len(plan),
                    "steps": [step.to_dict() for step in plan],
                }
            )

            failed_index: int | None = None
            last_error: str | None = None

            for index, step in enumerate(plan):
                if steps_used >= self.max_steps:
                    events.append(
                        self._session_event(
                            "session.failed",
                            "failed",
                            "step_budget",
                            steps_used,
                            replans_used,
                        )
                    )
                    return SessionResult(
                        status="failed",
                        reason="step_budget",
                        history=history,
                        revisions=revisions,
                        events=events,
                    )

                started: Event = {
                    "event": "step.started",
                    "revision": revision,
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "args": dict(step.args),
                    "expected_outcome": step.expected_outcome,
                }
                events.append(started)
                steps_used += 1

                try:
                    value = self._dispatcher.dispatch(
                        step.tool_name, dict(step.args)
                    )
                except Exception as exc:
                    message = str(exc).strip() or type(exc).__name__
                    executed = replace(step, result=None, error=message)
                    history.append(executed)
                    events.append(
                        {
                            **started,
                            "event": "step.failed",
                            "error": message,
                        }
                    )
                    failed_index = index
                    last_error = message
                    break

                executed = replace(step, result=value, error=None)
                history.append(executed)
                events.append(
                    {
                        **started,
                        "event": "step.succeeded",
                        "result": value,
                    }
                )

            if failed_index is None:
                events.append(
                    self._session_event(
                        "session.completed",
                        "completed",
                        "goal_met",
                        steps_used,
                        replans_used,
                    )
                )
                return SessionResult(
                    status="completed",
                    reason="goal_met",
                    history=history,
                    revisions=revisions,
                    events=events,
                )

            if replans_used >= self.max_replans:
                events.append(
                    self._session_event(
                        "session.failed",
                        "failed",
                        "replan_budget",
                        steps_used,
                        replans_used,
                    )
                )
                return SessionResult(
                    status="failed",
                    reason="replan_budget",
                    history=history,
                    revisions=revisions,
                    events=events,
                )

            if last_error is None:
                raise RuntimeError("internal failure without an error message")

            old_remaining = plan[failed_index:]
            new_plan = list(
                self._planner(goal, list(history), last_error)
            )
            validate_plan(new_plan)
            replans_used += 1
            revision += 1
            diff = diff_plans(old_remaining, new_plan)
            revisions.append(diff)
            events.append(
                {
                    "event": "plan.diff",
                    "revision": revision,
                    "removed": list(diff.removed),
                    "added": list(diff.added),
                    "revised": list(diff.revised),
                    "trigger_error": last_error,
                }
            )
            plan = new_plan


__all__ = ["DEFAULT_MAX_REPLANS", "DEFAULT_MAX_STEPS", "PlanExecuteAgent"]

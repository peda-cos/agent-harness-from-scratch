"""Offline demo for the harness loop contract.

Run with ``.venv/bin/python main.py``. No model, tool process, network, or
credentials are involved: tool pulls are answered by deterministic fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_harness import (  # noqa: E402
    BudgetExtension,
    BudgetLimits,
    HarnessLoop,
    Outcome,
    PullKind,
    PullRequest,
    ResumeDecision,
    SessionResult,
    ToolCall,
    ToolResult,
)


class FixedClock:
    """Reproducible event timestamps for the demo."""

    def __init__(self, step: float = 0.25) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        return self._now

    def advance(self) -> None:
        self._now += self._step


def _call_for(pull: PullRequest) -> ToolCall:
    if pull.call is None:
        raise RuntimeError("tool pull without a call")
    return pull.call


def _answer(pull: PullRequest) -> ToolResult:
    call = _call_for(pull)
    fixtures: dict[str, dict[str, Any]] = {
        "lookup_context": {"matches": ["README.md", "pyproject.toml"]},
        "validate_change": {"valid": True, "checks": 4},
    }
    return ToolResult(call_id=call.call_id, data=fixtures[call.tool])


def _print_events(loop: HarnessLoop) -> None:
    for event in loop.events.events:
        data = json.dumps(event.as_dict()["data"], sort_keys=True)
        print(f"  {event.offset:02d} {event.type.value:<16} {data}")


def _print_result(result: SessionResult) -> None:
    print(
        "  result "
        f"outcome={result.outcome.value} turns={result.turns} "
        f"tool_calls={result.tool_calls} elapsed_s={result.elapsed_s:.2f} "
        f"completed={result.completed_steps} failed={result.failed_steps} "
        f"skipped={result.skipped_steps}"
    )


def run_normal_demo() -> SessionResult:
    print("\n[normal session]")
    clock = FixedClock()
    loop = HarnessLoop(clock=clock)
    outcome = loop.run("validate the loop contract")

    while isinstance(outcome, PullRequest):
        if outcome.kind is not PullKind.TOOL:
            raise RuntimeError("normal session unexpectedly paused")
        call = _call_for(outcome)
        print(f"  pull   tool {call.tool} id={call.call_id}")
        outcome = loop.resume(_answer(outcome))
        clock.advance()

    _print_events(loop)
    _print_result(outcome)
    if outcome.outcome is not Outcome.GOAL_MET:
        raise RuntimeError(f"normal demo did not meet its goal: {outcome.outcome}")
    if outcome.turns != 3 or outcome.tool_calls != 2:
        raise RuntimeError("normal demo counters changed")
    return outcome


def run_budget_demo() -> SessionResult:
    print("\n[budget session: max_turns=2, extend by one]")
    clock = FixedClock()
    loop = HarnessLoop(
        clock=clock,
        limits=BudgetLimits(max_turns=2, max_tool_calls=4, max_wall_clock_s=30),
    )
    outcome = loop.run("show the budget yield")
    pauses = 0

    while isinstance(outcome, PullRequest):
        if outcome.kind is PullKind.TOOL:
            call = _call_for(outcome)
            print(f"  pull   tool {call.tool} id={call.call_id}")
            outcome = loop.resume(_answer(outcome))
            clock.advance()
            continue
        if outcome.pause is None:
            raise RuntimeError("pause pull without pause details")
        pauses += 1
        print(
            "  pause  "
            f"reason={outcome.pause.reason.value} "
            f"limit={outcome.pause.limit.value if outcome.pause.limit else None} "
            f"message={outcome.pause.message}"
        )
        outcome = loop.resume(
            ResumeDecision(extension=BudgetExtension(turns=1, wall_clock_s=30.0))
        )
        clock.advance()

    _print_events(loop)
    _print_result(outcome)
    if pauses != 1:
        raise RuntimeError(f"expected one budget pause, saw {pauses}")
    if outcome.outcome is not Outcome.GOAL_MET:
        raise RuntimeError(f"budget demo did not meet its goal: {outcome.outcome}")
    return outcome


def main() -> int:
    run_normal_demo()
    run_budget_demo()
    print("\nok: offline harness contract demo completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

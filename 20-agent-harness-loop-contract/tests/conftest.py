from __future__ import annotations

from typing import Any

import pytest

from agent_harness import (
    HarnessLoop,
    PullKind,
    PullRequest,
    SessionResult,
    ToolResult,
)


class FakeClock:
    """Deterministic callable clock; the loop never touches real time in tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def answer(
    pull: PullRequest,
    data: dict[str, Any] | None = None,
) -> ToolResult:
    assert pull.call is not None
    return ToolResult(call_id=pull.call.call_id, data=data or {"ok": True})


def complete_normal(
    loop: HarnessLoop,
    goal: str = "fix the failing test",
) -> SessionResult:
    return drain(loop, loop.run(goal))


def drain(loop: HarnessLoop, outcome: PullRequest | SessionResult) -> SessionResult:
    while isinstance(outcome, PullRequest):
        if outcome.kind is not PullKind.TOOL:
            raise AssertionError(f"unexpected pause in normal session: {outcome.pause}")
        outcome = loop.resume(answer(outcome))
    return outcome


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()

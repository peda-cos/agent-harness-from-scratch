"""Local control-flow throughput measurement; no network or model calls."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from plan_execute import (  # noqa: E402
    PlanExecuteAgent,
    Toolbox,
    deterministic_planner,
)

ROUNDS = 5
RUNS = 1_000
TOOL_NAMES = (
    "release.inspect",
    "release.draft_notes",
    "release.validate",
    "release.publish",
)


def _noop(**kwargs: object) -> object:
    return None


def _toolbox() -> Toolbox:
    toolbox = Toolbox()
    for name in TOOL_NAMES:
        toolbox.register(name, _noop, required=("repo",))
    return toolbox


def _measure() -> tuple[float, float]:
    agent = PlanExecuteAgent(deterministic_planner, _toolbox())
    agent.run("warmup")

    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(RUNS):
            agent.run("bench")
        elapsed = time.perf_counter() - started
        samples.append(elapsed / RUNS * 1_000_000.0)

    median_us = statistics.median(samples)
    return median_us, 1_000_000.0 / median_us


def main() -> int:
    median_us, rate = _measure()
    print(
        f"four-step plan : {median_us:.2f} us/run  {rate:,.0f} runs/s"
    )
    print(
        json.dumps(
            {
                "benchmark": "plan_execute_us_per_run",
                "value": median_us,
            },
            separators=(",", ":"),
        )
    )
    print(
        json.dumps(
            {
                "benchmark": "plan_execute_runs_per_s",
                "value": rate,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

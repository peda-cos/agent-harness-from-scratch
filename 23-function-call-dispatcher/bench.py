"""Local dispatch-throughput measurement; no network or model calls."""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from function_dispatcher import Call, Dispatcher, ToolRegistry  # noqa: E402

ROUNDS = 5
SEQUENTIAL_CALLS = 2_000
FANOUT_CALLS = 40
FANOUT_BATCHES = 100
CACHE_HITS = 10_000


async def _noop(**kwargs: object) -> object:
    return None


async def _no_sleep(delay: float) -> None:
    return None


async def _measure() -> dict[str, float]:
    registry = ToolRegistry()
    registry.register("bench.noop", {"type": "object"}, _noop)
    dispatcher = Dispatcher(registry, sleep=_no_sleep, jitter=lambda: 0.0)

    await dispatcher.dispatch("bench.noop")  # warm the loop and registry

    sequential_samples: list[float] = []
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(SEQUENTIAL_CALLS):
            await dispatcher.dispatch("bench.noop")
        elapsed = time.perf_counter() - start
        sequential_samples.append(elapsed / SEQUENTIAL_CALLS * 1_000_000.0)
    sequential_us = statistics.median(sequential_samples)

    fanout = [Call("bench.noop") for _ in range(FANOUT_CALLS)]
    await dispatcher.gather_bounded(fanout)
    fanout_samples: list[float] = []
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(FANOUT_BATCHES):
            await dispatcher.gather_bounded(fanout)
        elapsed = time.perf_counter() - start
        fanout_samples.append(
            FANOUT_CALLS * FANOUT_BATCHES / elapsed
        )
    fanout_per_second = statistics.median(fanout_samples)

    await dispatcher.dispatch("bench.noop", idempotency_key="bench-cache-key")
    cache_samples: list[float] = []
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(CACHE_HITS):
            await dispatcher.dispatch(
                "bench.noop", idempotency_key="bench-cache-key"
            )
        elapsed = time.perf_counter() - start
        cache_samples.append(elapsed / CACHE_HITS * 1_000_000.0)
    cache_us = statistics.median(cache_samples)

    return {
        "sequential_us_per_op": sequential_us,
        "sequential_ops_per_s": 1_000_000.0 / sequential_us,
        "fanout_calls_per_s": fanout_per_second,
        "cache_us_per_op": cache_us,
        "cache_ops_per_s": 1_000_000.0 / cache_us,
    }


def main() -> int:
    results = asyncio.run(_measure())
    print(
        f"sequential dispatch   : {results['sequential_us_per_op']:8.2f} us/op"
        f"  {results['sequential_ops_per_s']:12,.0f} ops/s"
    )
    print(
        f"40-call fan-out       : {results['fanout_calls_per_s']:12,.0f} calls/s"
    )
    print(
        f"idempotency cache hit : {results['cache_us_per_op']:8.2f} us/op"
        f"  {results['cache_ops_per_s']:12,.0f} ops/s"
    )
    for key, value in results.items():
        print(json.dumps({"benchmark": key, "value": value}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

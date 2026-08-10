"""Offline, self-verifying demo for the function-call dispatcher."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from function_dispatcher import (  # noqa: E402
    Call,
    Dispatcher,
    DispatchError,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    Ok,
    ToolCallBudget,
    ToolRegistry,
    TransientError,
)

FORECAST_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["city"],
    "properties": {"city": {"type": "string"}},
}

LEDGER_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["entry"],
    "properties": {"entry": {"type": "integer"}},
}


def _emit(scenario: str, **fields: object) -> None:
    print(json.dumps({"scenario": scenario, **fields}, separators=(",", ":")))


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def run_demo() -> None:
    registry = ToolRegistry()
    delays: list[float] = []
    weather_calls = 0
    charge_starts = 0
    charge_cancelled = False
    inventory_calls = 0
    ledger_calls = 0
    echo_active = 0
    echo_peak = 0
    ledger_started = asyncio.Event()
    ledger_release = asyncio.Event()

    async def forecast(**kwargs: object) -> object:
        nonlocal weather_calls
        weather_calls += 1
        return kwargs

    async def charge(**kwargs: object) -> object:
        nonlocal charge_starts, charge_cancelled
        charge_starts += 1
        try:
            await asyncio.sleep(0.05)
        finally:
            charge_cancelled = True
        return {"charged": True}

    async def reserve(**kwargs: object) -> object:
        nonlocal inventory_calls
        inventory_calls += 1
        if inventory_calls < 3:
            raise TransientError("inventory lock")
        return {"reserved": True}

    async def append_ledger(**kwargs: object) -> object:
        nonlocal ledger_calls
        ledger_calls += 1
        ledger_started.set()
        await ledger_release.wait()
        return {"entry": kwargs["entry"], "durable": True}

    async def echo(**kwargs: object) -> object:
        nonlocal echo_active, echo_peak
        echo_active += 1
        echo_peak = max(echo_peak, echo_active)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        echo_active -= 1
        return kwargs["index"]

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    registry.register(
        "weather.forecast", FORECAST_SCHEMA, forecast, idempotent=False
    )
    registry.register(
        "orders.charge",
        {"type": "object"},
        charge,
        idempotent=False,
        timeout_ms=2,
    )
    registry.register(
        "inventory.reserve",
        {"type": "object"},
        reserve,
        idempotent=True,
    )
    registry.register(
        "ledger.append",
        LEDGER_SCHEMA,
        append_ledger,
        idempotent=True,
    )
    registry.register("demo.echo", {"type": "object"}, echo)

    dispatcher = Dispatcher(registry, jitter=lambda: 0.0, sleep=record_sleep)

    # 1. Schema rejection happens before any handler call.
    schema_result = await dispatcher.dispatch("weather.forecast", {"city": 3})
    _expect(isinstance(schema_result, DispatchError), "schema error expected")
    assert isinstance(schema_result, DispatchError)
    _expect(schema_result.kind == "schema", "schema kind")
    _expect(schema_result.attempts == 0, "schema attempts")
    _expect(
        schema_result.jsonrpc_code == JSONRPC_INVALID_PARAMS,
        "schema JSON-RPC code",
    )
    _expect(weather_calls == 0, "schema must not call the handler")
    _emit(
        "schema_error",
        kind=schema_result.kind,
        attempts=schema_result.attempts,
        code=schema_result.jsonrpc_code,
    )

    # 2. A non-idempotent timeout is cancelled and never retried.
    timeout_result = await dispatcher.dispatch("orders.charge")
    _expect(isinstance(timeout_result, DispatchError), "timeout error expected")
    assert isinstance(timeout_result, DispatchError)
    _expect(timeout_result.kind == "timeout", "timeout kind")
    _expect(timeout_result.attempts == 1, "timeout attempts")
    _expect(
        timeout_result.jsonrpc_code == JSONRPC_INTERNAL_ERROR,
        "timeout JSON-RPC code",
    )
    _expect(charge_starts == 1, "timeout must start the handler once")
    _expect(charge_cancelled, "timeout must cancel the handler")
    _emit(
        "timeout",
        kind=timeout_result.kind,
        attempts=timeout_result.attempts,
        cancelled=charge_cancelled,
    )

    # 3. A transient failure retries an idempotent tool with the spec schedule.
    retry_result = await dispatcher.dispatch("inventory.reserve")
    _expect(isinstance(retry_result, Ok), "retry should eventually succeed")
    _expect(inventory_calls == 3, "retry handler count")
    _expect(delays == [0.1, 0.4], "retry backoff schedule")
    _emit("retry", kind="ok", calls=inventory_calls, delays=delays)

    # 4. Two concurrent calls with one key collapse onto one handler invocation.
    key = "step-4:ledger.append"
    first = asyncio.create_task(
        dispatcher.dispatch("ledger.append", {"entry": 1}, idempotency_key=key)
    )
    await ledger_started.wait()
    second = asyncio.create_task(
        dispatcher.dispatch("ledger.append", {"entry": 1}, idempotency_key=key)
    )
    await asyncio.sleep(0)
    ledger_release.set()
    ledger_results = await asyncio.gather(first, second)
    _expect(ledger_calls == 1, "dedupe handler count")
    _expect(
        all(isinstance(result, Ok) for result in ledger_results),
        "dedupe results",
    )
    _emit("dedupe", kind="ok", calls=ledger_calls, waiters=2)

    # 5. The completed key is served from the TTL cache.
    cached = await dispatcher.dispatch(
        "ledger.append", {"entry": 1}, idempotency_key=key
    )
    _expect(isinstance(cached, Ok), "cache hit result")
    _expect(ledger_calls == 1, "cache hit must not call the handler again")
    _emit("cache_hit", kind="ok", calls=ledger_calls)

    # 6. Forty calls are scheduled through the default concurrency limit of eight.
    fanout = [
        Call("demo.echo", {"index": index}) for index in range(40)
    ]
    fanout_results = await dispatcher.gather_bounded(fanout)
    _expect(echo_peak == 8, "fan-out peak")
    _expect(
        all(isinstance(result, Ok) for result in fanout_results),
        "fan-out results",
    )
    _expect(
        [result.value for result in fanout_results if isinstance(result, Ok)]
        == list(range(40)),
        "fan-out input order",
    )
    _emit("fanout", ok=len(fanout_results), peak=echo_peak)

    # 7. An empty budget fails before the handler is invoked.
    budget_result = await dispatcher.dispatch(
        "demo.echo", {"index": 99}, budget=ToolCallBudget(0)
    )
    _expect(isinstance(budget_result, DispatchError), "budget error expected")
    assert isinstance(budget_result, DispatchError)
    _expect(budget_result.kind == "budget_exceeded", "budget kind")
    _expect(budget_result.attempts == 0, "budget attempts")
    _emit(
        "budget",
        kind=budget_result.kind,
        attempts=budget_result.attempts,
    )

    print("demo verified: 7 scenarios, peak fan-out 8")


def main() -> int:
    asyncio.run(run_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

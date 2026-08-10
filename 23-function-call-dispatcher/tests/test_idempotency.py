from __future__ import annotations

import asyncio

from function_dispatcher import DispatchError, Dispatcher, Ok, ToolRegistry


def make_dispatcher(
    handler: object,
    *,
    max_cache_entries: int = 1024,
    clock: object = None,
) -> Dispatcher:
    registry = ToolRegistry()
    registry.register(
        "demo.tool",
        {"type": "object"},
        handler,  # type: ignore[arg-type]
        timeout_ms=500,
    )
    if clock is None:
        return Dispatcher(registry, max_cache_entries=max_cache_entries)
    return Dispatcher(
        registry,
        max_cache_entries=max_cache_entries,
        clock=clock,  # type: ignore[arg-type]
    )


def test_concurrent_same_key_runs_handler_once() -> None:
    async def scenario() -> tuple[int, list[object]]:
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"id": 42}

        dispatcher = make_dispatcher(handler)
        first = asyncio.create_task(
            dispatcher.dispatch(
                "demo.tool", {"order": 7}, idempotency_key="step-7:charge"
            )
        )
        await started.wait()
        waiters = [
            asyncio.create_task(
                dispatcher.dispatch(
                    "demo.tool", {"order": 7}, idempotency_key="step-7:charge"
                )
            )
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, *waiters)
        return calls, list(results)

    calls, results = asyncio.run(scenario())

    assert calls == 1
    assert all(isinstance(result, Ok) for result in results)
    assert [result.value for result in results] == [{"id": 42}] * 5


def test_completed_result_cached_until_ttl() -> None:
    async def scenario() -> tuple[int, list[object]]:
        now = [1_000.0]
        calls = 0

        async def handler(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return calls

        dispatcher = make_dispatcher(handler, clock=lambda: now[0])
        first = await dispatcher.dispatch("demo.tool", idempotency_key="k")
        second = await dispatcher.dispatch("demo.tool", idempotency_key="k")
        now[0] += 61.0
        third = await dispatcher.dispatch("demo.tool", idempotency_key="k")
        return calls, [first, second, third]

    calls, results = asyncio.run(scenario())

    assert calls == 2
    assert [result.value for result in results] == [1, 1, 2]


def test_failed_keyed_call_is_not_cached() -> None:
    async def scenario() -> tuple[int, list[object]]:
        calls = 0

        async def handler(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise ValueError("handler exploded")

        dispatcher = make_dispatcher(handler)
        first = await dispatcher.dispatch("demo.tool", idempotency_key="k")
        second = await dispatcher.dispatch("demo.tool", idempotency_key="k")
        return calls, [first, second]

    calls, results = asyncio.run(scenario())

    assert calls == 2
    assert all(isinstance(result, DispatchError) for result in results)
    assert all(result.kind == "internal" for result in results)


def test_cache_is_bounded_to_max_entries() -> None:
    async def scenario() -> tuple[int, list[object]]:
        calls = 0

        async def handler(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return calls

        dispatcher = make_dispatcher(handler, max_cache_entries=2)
        await dispatcher.dispatch("demo.tool", idempotency_key="k0")
        await dispatcher.dispatch("demo.tool", idempotency_key="k1")
        await dispatcher.dispatch("demo.tool", idempotency_key="k2")
        after_fill = calls

        recent = [
            await dispatcher.dispatch("demo.tool", idempotency_key="k1"),
            await dispatcher.dispatch("demo.tool", idempotency_key="k2"),
        ]
        after_hits = calls
        evicted = await dispatcher.dispatch("demo.tool", idempotency_key="k0")
        return after_fill, [after_hits, calls, *recent, evicted]

    after_fill, results = asyncio.run(scenario())

    assert after_fill == 3
    assert results[0] == 3
    assert results[1] == 4
    assert [result.value for result in results[2:4]] == [2, 3]
    assert results[4].value == 4

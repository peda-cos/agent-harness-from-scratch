from __future__ import annotations

import asyncio

from function_dispatcher import Call, DispatchError, Dispatcher, Ok, ToolRegistry


def test_gather_bounded_caps_peak_concurrency() -> None:
    async def scenario() -> tuple[int, list[object]]:
        active = 0
        peak = 0

        async def handler(**kwargs: object) -> object:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            active -= 1
            return kwargs["index"]

        registry = ToolRegistry()
        registry.register(
            "demo.echo",
            {
                "type": "object",
                "required": ["index"],
                "properties": {"index": {"type": "integer"}},
            },
            handler,
        )
        dispatcher = Dispatcher(registry, max_concurrency=3)
        calls = [Call("demo.echo", {"index": index}) for index in range(12)]

        results = await dispatcher.gather_bounded(calls)
        return peak, list(results)

    peak, results = asyncio.run(scenario())

    assert peak == 3
    assert all(isinstance(result, Ok) for result in results)
    assert [result.value for result in results] == list(range(12))


def test_forty_calls_use_default_limit_of_eight() -> None:
    async def scenario() -> tuple[int, list[object]]:
        active = 0
        peak = 0

        async def handler(**kwargs: object) -> object:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            active -= 1
            return kwargs["index"]

        registry = ToolRegistry()
        registry.register(
            "demo.echo",
            {
                "type": "object",
                "required": ["index"],
                "properties": {"index": {"type": "integer"}},
            },
            handler,
        )
        dispatcher = Dispatcher(registry)
        calls = [Call("demo.echo", {"index": index}) for index in range(40)]

        results = await dispatcher.gather_bounded(calls)
        return peak, list(results)

    peak, results = asyncio.run(scenario())

    assert peak == 8
    assert all(isinstance(result, Ok) for result in results)
    assert [result.value for result in results] == list(range(40))


def test_gather_bounded_preserves_mixed_outcome_positions() -> None:
    async def scenario() -> list[object]:
        async def echo(**kwargs: object) -> object:
            return kwargs

        async def slow(**kwargs: object) -> object:
            await asyncio.sleep(0.05)
            return "late"

        registry = ToolRegistry()
        registry.register(
            "demo.strict",
            {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
            },
            echo,
        )
        registry.register("demo.echo", {"type": "object"}, echo)
        registry.register("demo.slow", {"type": "object"}, slow, timeout_ms=5)
        dispatcher = Dispatcher(registry)

        results = await dispatcher.gather_bounded(
            [
                Call("demo.strict", {"value": 1}),
                Call("demo.echo", {"text": "ok"}),
                Call("demo.slow"),
            ]
        )
        return list(results)

    results = asyncio.run(scenario())

    assert isinstance(results[0], DispatchError)
    assert results[0].kind == "schema"
    assert isinstance(results[1], Ok)
    assert results[1].value == {"text": "ok"}
    assert isinstance(results[2], DispatchError)
    assert results[2].kind == "timeout"

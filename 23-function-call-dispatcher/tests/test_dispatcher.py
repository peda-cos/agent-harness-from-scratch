from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

import pytest

from function_dispatcher import (
    DispatchError,
    Dispatcher,
    Ok,
    ToolCallBudget,
    ToolRegistry,
    TransientError,
)

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    async def wrapper() -> T:
        return await coro

    return asyncio.run(wrapper())


def make_dispatcher(
    handler: object,
    *,
    schema: dict[str, object] | None = None,
    idempotent: bool = False,
    timeout_ms: int = 500,
    **dispatcher_options: object,
) -> Dispatcher:
    registry = ToolRegistry()
    registry.register(
        "demo.tool",
        schema or {"type": "object"},
        handler,  # type: ignore[arg-type]
        idempotent=idempotent,
        timeout_ms=timeout_ms,
    )
    return Dispatcher(registry, **dispatcher_options)  # type: ignore[arg-type]


def test_success_returns_ok_and_forwards_arguments() -> None:
    seen: list[dict[str, object]] = []

    async def handler(**kwargs: object) -> object:
        seen.append(kwargs)
        return {"seen": kwargs}

    dispatcher = make_dispatcher(
        handler,
        schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    )

    result = run(dispatcher.dispatch("demo.tool", {"text": "hi"}))

    assert isinstance(result, Ok)
    assert result.value == {"seen": {"text": "hi"}}
    assert seen == [{"text": "hi"}]


def test_schema_error_skips_handler_and_events() -> None:
    calls = 0
    events: list[dict[str, object]] = []

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return kwargs

    dispatcher = make_dispatcher(
        handler,
        schema={
            "type": "object",
            "required": ["city"],
            "properties": {"city": {"type": "string"}},
        },
        on_event=events.append,
    )

    result = run(dispatcher.dispatch("demo.tool", {"city": 3}))

    assert isinstance(result, DispatchError)
    assert result.kind == "schema"
    assert result.attempts == 0
    assert result.jsonrpc_code == -32602
    assert calls == 0
    assert events == []
    assert "/city: expected string, got integer" in result.message


def test_unknown_tool_returns_not_found() -> None:
    dispatcher = Dispatcher(ToolRegistry())

    result = run(dispatcher.dispatch("missing.tool"))

    assert isinstance(result, DispatchError)
    assert result.kind == "not_found"
    assert result.attempts == 0
    assert result.jsonrpc_code == -32601


def test_default_timeout_cancels_non_idempotent_handler() -> None:
    starts = 0
    cancelled = False

    async def handler(**kwargs: object) -> object:
        nonlocal starts, cancelled
        starts += 1
        try:
            await asyncio.sleep(0.05)
        finally:
            cancelled = True
        return "late"

    dispatcher = make_dispatcher(handler, timeout_ms=5)

    result = run(dispatcher.dispatch("demo.tool"))

    assert isinstance(result, DispatchError)
    assert result.kind == "timeout"
    assert result.attempts == 1
    assert result.jsonrpc_code == -32603
    assert starts == 1
    assert cancelled is True


def test_per_call_timeout_override_is_used() -> None:
    async def handler(**kwargs: object) -> object:
        await asyncio.sleep(0.005)
        return "made it"

    dispatcher = make_dispatcher(handler, timeout_ms=1)

    result = run(dispatcher.dispatch("demo.tool", timeout_ms=1_000))

    assert isinstance(result, Ok)
    assert result.value == "made it"


def test_idempotent_timeout_retries_with_spec_backoff() -> None:
    starts = 0
    delays: list[float] = []
    events: list[dict[str, object]] = []

    async def handler(**kwargs: object) -> object:
        nonlocal starts
        starts += 1
        await asyncio.sleep(0.05)
        return "late"

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    dispatcher = make_dispatcher(
        handler,
        idempotent=True,
        timeout_ms=2,
        sleep=fake_sleep,
        jitter=lambda: 0.0,
        on_event=events.append,
    )

    result = run(dispatcher.dispatch("demo.tool"))

    assert isinstance(result, DispatchError)
    assert result.kind == "timeout"
    assert result.attempts == 3
    assert result.jsonrpc_code == -32603
    assert starts == 3
    assert delays == [0.1, 0.4]
    assert [event["event"] for event in events] == [
        "dispatch.attempt",
        "dispatch.retry",
        "dispatch.attempt",
        "dispatch.retry",
        "dispatch.attempt",
    ]
    assert [event["delay_s"] for event in events if event["event"] == "dispatch.retry"] == [
        0.1,
        0.4,
    ]


def test_transient_error_retries_even_when_not_idempotent() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientError("upstream reset")
        return {"ok": True}

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    dispatcher = make_dispatcher(
        handler,
        idempotent=False,
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )

    result = run(dispatcher.dispatch("demo.tool"))

    assert isinstance(result, Ok)
    assert result.value == {"ok": True}
    assert calls == 2
    assert delays == [0.1]


def test_transient_exhaustion_returns_envelope() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TransientError("busy")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    dispatcher = make_dispatcher(
        handler,
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )

    result = run(dispatcher.dispatch("demo.tool"))

    assert isinstance(result, DispatchError)
    assert result.kind == "transient"
    assert result.attempts == 3
    assert result.jsonrpc_code == -32603
    assert calls == 3
    assert delays == [0.1, 0.4]
    assert "failed transiently: busy" in result.message


def test_internal_error_is_not_retried() -> None:
    calls = 0
    events: list[dict[str, object]] = []

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    dispatcher = make_dispatcher(handler, on_event=events.append)

    result = run(dispatcher.dispatch("demo.tool"))

    assert isinstance(result, DispatchError)
    assert result.kind == "internal"
    assert result.attempts == 1
    assert result.jsonrpc_code == -32603
    assert calls == 1
    assert "ValueError" in result.message
    assert "boom" in result.message
    assert [event["event"] for event in events] == ["dispatch.attempt"]


def test_cancelled_error_propagates() -> None:
    async def handler(**kwargs: object) -> object:
        raise asyncio.CancelledError

    dispatcher = make_dispatcher(handler)

    with pytest.raises(asyncio.CancelledError):
        run(dispatcher.dispatch("demo.tool"))


def test_budget_exhaustion_before_first_attempt() -> None:
    calls = 0

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return "no"

    dispatcher = make_dispatcher(handler)
    budget = ToolCallBudget(0)

    result = run(dispatcher.dispatch("demo.tool", budget=budget))

    assert isinstance(result, DispatchError)
    assert result.kind == "budget_exceeded"
    assert result.attempts == 0
    assert result.jsonrpc_code == -32603
    assert calls == 0
    assert budget.used == 0


def test_budget_exhaustion_mid_retry() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TransientError("busy")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    dispatcher = make_dispatcher(
        handler,
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )
    budget = ToolCallBudget(2)

    result = run(dispatcher.dispatch("demo.tool", budget=budget))

    assert isinstance(result, DispatchError)
    assert result.kind == "budget_exceeded"
    assert result.attempts == 2
    assert result.jsonrpc_code == -32603
    assert calls == 2
    assert budget.used == 2
    assert delays == [0.1, 0.4]


def test_invalid_configuration_and_options_raise() -> None:
    async def handler(**kwargs: object) -> object:
        return None

    dispatcher = make_dispatcher(handler)

    for options in (
        {"max_concurrency": 0},
        {"max_attempts": 0},
        {"base_delay": -0.1},
        {"cache_ttl": -1.0},
        {"max_cache_entries": 0},
    ):
        with pytest.raises(ValueError):
            make_dispatcher(handler, **options)

    with pytest.raises(ValueError):
        run(dispatcher.dispatch("demo.tool", timeout_ms=0))
    with pytest.raises(ValueError):
        run(dispatcher.dispatch("demo.tool", idempotency_key=""))


def test_dispatch_error_to_jsonrpc() -> None:
    error = DispatchError("not_found", "unknown tool 'missing'", 0, -32601)

    assert error.to_jsonrpc("id-1") == {
        "jsonrpc": "2.0",
        "id": "id-1",
        "error": {"code": -32601, "message": "unknown tool 'missing'"},
    }
    with pytest.raises(ValueError):
        DispatchError("timeout", "bad code", 1, -32601)
    with pytest.raises(ValueError):
        DispatchError("timeout", "bad attempts", -1, -32603)

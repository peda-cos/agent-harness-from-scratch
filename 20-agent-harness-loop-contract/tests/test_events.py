from __future__ import annotations

import json

import pytest

from agent_harness import EventStream, EventType
from conftest import FakeClock


def test_append_replay_offsets_and_clock() -> None:
    clock = FakeClock()
    stream = EventStream("trace-1", clock)

    first = stream.append(
        EventType.PLAN_DRAFT,
        {"nested": {"x": 1}, "items": [1, 2]},
    )
    clock.advance(1.5)
    second = stream.append(EventType.PLAN_COMMIT, {"plan": []})

    assert first.offset == 0
    assert second.offset == 1
    assert first.session_id == "trace-1"
    assert first.at == 0.0
    assert second.at == 1.5
    assert stream.events == (first, second)
    assert stream.replay() == (first, second)
    assert stream.replay(1) == (second,)
    assert stream.replay(2) == ()

    with pytest.raises(ValueError):
        stream.replay(-1)
    with pytest.raises(ValueError):
        stream.replay(3)


def test_event_payloads_are_deeply_immutable_and_thawed() -> None:
    stream = EventStream("trace", FakeClock())
    event = stream.append(
        EventType.TOOL_RESULT,
        {"data": {"nested": {"value": 3}}, "items": [1, {"two": 2}]},
    )

    with pytest.raises(TypeError):
        event.data["data"]["nested"] = {"value": 4}  # type: ignore[index]
    assert isinstance(event.data["items"], tuple)

    serialised = event.as_dict()
    json.dumps(serialised)
    serialised["data"]["data"]["nested"]["value"] = 99
    assert event.data["data"]["nested"]["value"] == 3


def test_live_subscription_and_unsubscribe() -> None:
    stream = EventStream("trace", FakeClock())
    seen: list[int] = []
    unsubscribe = stream.subscribe(lambda event: seen.append(event.offset))

    stream.append(EventType.SESSION_START, {})
    unsubscribe()
    unsubscribe()
    stream.append(EventType.SESSION_COMPLETE, {})

    assert seen == [0]
    assert len(stream.events) == 2
    assert stream.failures == ()


def test_observer_exception_is_isolated_and_recorded() -> None:
    stream = EventStream("trace", FakeClock())
    received: list[str] = []

    def failing(event) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("tracer unavailable")

    def healthy(event) -> None:  # type: ignore[no-untyped-def]
        received.append(event.type.value)

    stream.subscribe(failing)
    stream.subscribe(healthy)
    stream.append(EventType.SESSION_START, {})

    assert received == ["session.start"]
    assert len(stream.failures) == 1
    assert stream.failures[0].offset == 0
    assert isinstance(stream.failures[0].error, RuntimeError)
    assert len(stream.events) == 1


def test_subscription_requires_callable() -> None:
    stream = EventStream("trace", FakeClock())
    with pytest.raises(ValueError):
        stream.subscribe(object())  # type: ignore[arg-type]

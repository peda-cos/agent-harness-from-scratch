"""Append-only typed event stream for downstream UIs and tracers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from .contract import EventType


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen: dict[object, object] = {}
        for key, item in value.items():
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Event:
    """One immutable observation. ``offset`` is zero-based."""

    offset: int
    type: EventType
    session_id: str
    at: float
    data: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-serialisable representation."""
        return {
            "offset": self.offset,
            "type": self.type.value,
            "session_id": self.session_id,
            "at": self.at,
            "data": _thaw(self.data),
        }


@dataclass(frozen=True, slots=True)
class SubscriberFailure:
    """An observer raised; the stream records it and keeps going."""

    offset: int
    error: BaseException


class EventStream:
    """In-memory append-only stream with replay and live subscriptions."""

    def __init__(
        self,
        session_id: str,
        clock: Callable[[], float],
    ) -> None:
        self._session_id = session_id
        self._clock = clock
        self._events: list[Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        self._failures: list[SubscriberFailure] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def failures(self) -> tuple[SubscriberFailure, ...]:
        return tuple(self._failures)

    def append(self, event_type: EventType, data: Mapping[str, Any]) -> Event:
        """Append one event, notify live subscribers, and return it."""
        frozen = cast(Mapping[str, Any], _freeze(dict(data)))
        event = Event(
            offset=len(self._events),
            type=event_type,
            session_id=self._session_id,
            at=self._clock(),
            data=frozen,
        )
        self._events.append(event)
        for callback in tuple(self._subscribers):
            try:
                callback(event)
            except Exception as exc:  # observers must never drive the loop
                self._failures.append(SubscriberFailure(event.offset, exc))
        return event

    def replay(self, offset: int = 0) -> tuple[Event, ...]:
        """Return events from ``offset`` onward without consuming them."""
        if offset < 0 or offset > len(self._events):
            raise ValueError(
                f"replay offset must be between 0 and {len(self._events)}, got {offset}"
            )
        return tuple(self._events[offset:])

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to live events; the returned function unsubscribes."""
        if not callable(callback):
            raise ValueError("event subscriber must be callable")
        self._subscribers.append(callback)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if not active:
                return
            active = False
            self._subscribers.remove(callback)

        return unsubscribe

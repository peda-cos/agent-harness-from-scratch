"""Ordered hook registry with typed failures and control signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from .contract import HookSignal, HookTopic, State

Hook = Callable[["HookContext"], HookSignal | None]
_AnyHook = Callable[["HookContext"], object]


@dataclass(slots=True)
class HookContext:
    """Mutable payload passed through subscribers in registration order."""

    topic: HookTopic
    session_id: str
    state: State
    payload: dict[str, Any] = field(default_factory=dict)
    skip_next_step: bool = False
    pause_requested: bool = False

    def request_skip(self) -> None:
        self.skip_next_step = True

    def request_pause(self) -> None:
        self.pause_requested = True


@dataclass(frozen=True, slots=True)
class HookFailure:
    """A subscriber raised or returned an unsupported value."""

    topic: HookTopic
    hook: str
    message: str


@dataclass(frozen=True, slots=True)
class HookOutcome:
    """What one topic fire observed after all subscribers ran."""

    payload: dict[str, Any]
    skip_next_step: bool
    pause_requested: bool
    failure: HookFailure | None


@dataclass(frozen=True, slots=True)
class _Subscriber:
    name: str
    callback: _AnyHook


class HookRegistry:
    """Per-topic ordered subscriber list; the loop owns abort semantics."""

    def __init__(self) -> None:
        self._subscribers: dict[HookTopic, list[_Subscriber]] = {
            topic: [] for topic in HookTopic
        }
        self._failures: list[HookFailure] = []

    @property
    def failures(self) -> tuple[HookFailure, ...]:
        return tuple(self._failures)

    def subscribe(self, topic: HookTopic, name: str, callback: object) -> None:
        if not name or not name.strip():
            raise ValueError("hook name must not be empty")
        if not callable(callback):
            raise ValueError("hook callback must be callable")
        handler = cast(_AnyHook, callback)
        subscribers = self._subscribers[topic]
        if any(subscriber.name == name for subscriber in subscribers):
            raise ValueError(f"hook {name!r} already registered for {topic.value}")
        subscribers.append(_Subscriber(name=name, callback=handler))

    def subscribers(self, topic: HookTopic) -> tuple[str, ...]:
        return tuple(subscriber.name for subscriber in self._subscribers[topic])

    def fire(
        self,
        topic: HookTopic,
        *,
        session_id: str,
        state: State,
        payload: dict[str, Any],
    ) -> HookOutcome:
        context = HookContext(
            topic=topic,
            session_id=session_id,
            state=state,
            payload=payload,
        )
        # Snapshot so subscribing during a fire affects only later fires.
        for subscriber in tuple(self._subscribers[topic]):
            try:
                signal = subscriber.callback(context)
            except Exception as exc:  # operator hooks may raise; the loop decides
                failure = HookFailure(
                    topic=topic,
                    hook=subscriber.name,
                    message=f"{type(exc).__name__}: {exc}",
                )
                self._failures.append(failure)
                return HookOutcome(
                    payload=context.payload,
                    skip_next_step=context.skip_next_step,
                    pause_requested=context.pause_requested,
                    failure=failure,
                )
            if signal is HookSignal.SKIP_STEP:
                context.request_skip()
            elif signal is HookSignal.PAUSE:
                context.request_pause()
            elif signal is not None:
                failure = HookFailure(
                    topic=topic,
                    hook=subscriber.name,
                    message=f"TypeError: unsupported hook return {signal!r}",
                )
                self._failures.append(failure)
                return HookOutcome(
                    payload=context.payload,
                    skip_next_step=context.skip_next_step,
                    pause_requested=context.pause_requested,
                    failure=failure,
                )
        return HookOutcome(
            payload=context.payload,
            skip_next_step=context.skip_next_step,
            pause_requested=context.pause_requested,
            failure=None,
        )

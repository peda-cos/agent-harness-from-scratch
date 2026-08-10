"""The dispatch seam: validation, timeouts, retries, and error mapping."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .budget import Budget
from .errors import JSONRPC_CODES, DispatchError, ErrorKind, TransientError
from .registry import Registry, ToolNotFoundError, ToolRecord

FailureKind = Literal["timeout", "transient"]


async def _default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _default_jitter() -> float:
    return random.random()


@dataclass(frozen=True, slots=True)
class Ok:
    """A successful handler result."""

    value: object


DispatchOutcome: TypeAlias = Ok | DispatchError


@dataclass(frozen=True, slots=True)
class _Failure:
    kind: FailureKind
    message: str


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _non_negative_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be a non-negative number")
    return float(value)


def _arguments(args: Mapping[str, object] | None) -> dict[str, object]:
    if args is None:
        return {}
    if not isinstance(args, Mapping):
        raise TypeError("args must be a mapping")
    return dict(args)


def _resolve_timeout(timeout_ms: int | None) -> int | None:
    if timeout_ms is None:
        return None
    return _positive_int(timeout_ms, "timeout_ms")


def _check_key(key: str | None) -> str | None:
    if key is None:
        return None
    if not isinstance(key, str) or not key:
        raise ValueError("idempotency_key must be a non-empty string")
    return key


def internal_message(name: str, exc: Exception) -> str:
    detail = str(exc).strip()
    message = f"tool '{name}' raised {type(exc).__name__}"
    if detail:
        message += f": {detail[:200]}"
    return message


class Dispatcher:
    """Run tool handlers behind one typed, bounded dispatch contract."""

    def __init__(
        self,
        registry: Registry,
        *,
        max_concurrency: int = 8,
        max_attempts: int = 3,
        base_delay: float = 0.1,
        cache_ttl: float = 60.0,
        max_cache_entries: int = 1024,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
        jitter: Callable[[], float] = _default_jitter,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._registry = registry
        self._max_concurrency = _positive_int(max_concurrency, "max_concurrency")
        self._max_attempts = _positive_int(max_attempts, "max_attempts")
        self._base_delay = _non_negative_float(base_delay, "base_delay")
        self._cache_ttl = _non_negative_float(cache_ttl, "cache_ttl")
        self._max_cache_entries = _positive_int(
            max_cache_entries, "max_cache_entries"
        )
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock
        self._on_event = on_event
        self._semaphore = asyncio.Semaphore(self._max_concurrency)

    async def dispatch(
        self,
        name: str,
        args: Mapping[str, object] | None = None,
        *,
        idempotency_key: str | None = None,
        timeout_ms: int | None = None,
        budget: Budget | None = None,
    ) -> DispatchOutcome:
        params = _arguments(args)
        override = _resolve_timeout(timeout_ms)
        key = _check_key(idempotency_key)

        try:
            record = self._registry.get(name)
        except ToolNotFoundError:
            return self._error("not_found", f"unknown tool '{name}'", 0)

        issues = self._registry.validate(name, params)
        if issues:
            detail = "; ".join(str(issue) for issue in issues)
            return self._error(
                "schema", f"invalid arguments for tool '{name}': {detail}", 0
            )

        effective_timeout = record.timeout_ms if override is None else override
        if key is not None:
            return await self._run_with_retries(
                record, params, effective_timeout, budget, key
            )
        return await self._run_with_retries(
            record, params, effective_timeout, budget, None
        )

    async def _run_with_retries(
        self,
        record: ToolRecord,
        args: Mapping[str, object],
        timeout_ms: int,
        budget: Budget | None,
        idempotency_key: str | None,
    ) -> DispatchOutcome:
        attempts = 0
        while True:
            if budget is not None and not budget.try_consume():
                return self._error(
                    "budget_exceeded",
                    f"tool call budget exhausted before invoking '{record.name}'",
                    attempts,
                )

            attempts += 1
            self._emit(
                {
                    "event": "dispatch.attempt",
                    "tool": record.name,
                    "attempt": attempts,
                    "max_attempts": self._max_attempts,
                    "timeout_ms": timeout_ms,
                    "idempotency_key": idempotency_key,
                }
            )

            try:
                async with self._semaphore:
                    result = await asyncio.wait_for(
                        record.handler(**args), timeout_ms / 1000.0
                    )
                return Ok(result)
            except TimeoutError:
                failure = _Failure(
                    "timeout",
                    f"tool '{record.name}' timed out after {timeout_ms} ms",
                )
            except TransientError as exc:
                detail = str(exc).strip()[:200]
                message = f"tool '{record.name}' failed transiently"
                if detail:
                    message += f": {detail}"
                failure = _Failure("transient", message)
            except Exception as exc:
                return self._error(
                    "internal", internal_message(record.name, exc), attempts
                )

            if failure.kind == "timeout" and not record.idempotent:
                return self._error("timeout", failure.message, attempts)
            if attempts >= self._max_attempts:
                return self._error(failure.kind, failure.message, attempts)

            delay = (
                self._base_delay
                * (4 ** (attempts - 1))
                * (1 + 0.5 * self._jitter())
            )
            self._emit(
                {
                    "event": "dispatch.retry",
                    "tool": record.name,
                    "attempt": attempts,
                    "next_attempt": attempts + 1,
                    "delay_s": delay,
                    "kind": failure.kind,
                }
            )
            await self._sleep(delay)

    def _emit(self, event: dict[str, object]) -> None:
        if self._on_event is not None:
            self._on_event(event)

    @staticmethod
    def _error(kind: ErrorKind, message: str, attempts: int) -> DispatchError:
        return DispatchError(kind, message, attempts, JSONRPC_CODES[kind])


__all__ = [
    "Dispatcher",
    "DispatchOutcome",
    "Ok",
    "internal_message",
]

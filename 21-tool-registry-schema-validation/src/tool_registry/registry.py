"""Typed tool registry with guarded registration and pure validation."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from .schema import ValidationError, check_schema, validate_instance

DEFAULT_TIMEOUT_MS: Final = 30_000
_NAME_PATTERN: Final = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*\Z")
_NAME_MESSAGE = (
    "expected lowercase alphanumeric/underscore segments separated by dots"
)


@dataclass(frozen=True)
class ToolRecord:
    """A registered tool: schema is data, handler is opaque code."""

    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., Any]
    idempotent: bool = False
    timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclass(frozen=True)
class Ok:
    """Marker returned when every assertion passed."""

    def to_dict(self) -> dict[str, bool]:
        return {"ok": True}


class ToolAlreadyRegisteredError(ValueError):
    """A duplicate name was registered without ``override=True``."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"tool {name!r} is already registered; "
            "pass override=True to replace it"
        )


class ToolNotFoundError(KeyError):
    """A lookup named a tool that is not registered."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(name)

    def __str__(self) -> str:
        return f"tool {self.name!r} is not registered"


def _check_name(name: object) -> str:
    if not isinstance(name, str) or _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(f"invalid tool name {name!r}: {_NAME_MESSAGE}")
    return name


def _check_description(description: object) -> str:
    if not isinstance(description, str):
        raise TypeError("description must be a string")
    if "\n" in description or "\r" in description:
        raise ValueError("description must be a single line")
    return description


def _check_handler(handler: object) -> Callable[..., Any]:
    if not callable(handler):
        raise TypeError("handler must be callable")
    return cast(Callable[..., Any], handler)


def _check_idempotent(idempotent: object) -> bool:
    if not isinstance(idempotent, bool):
        raise TypeError("idempotent must be a bool")
    return idempotent


def _check_timeout(timeout_ms: object) -> int:
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise TypeError("timeout_ms must be an integer")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    return timeout_ms


def _check_override(override: object) -> bool:
    if not isinstance(override, bool):
        raise TypeError("override must be a bool")
    return override


class ToolRegistry:
    """In-memory tool catalog in registration order."""

    def __init__(self) -> None:
        self._records: dict[str, ToolRecord] = {}

    def register(
        self,
        name: str,
        schema: Mapping[str, Any],
        handler: Callable[..., Any],
        *,
        description: str = "",
        idempotent: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        override: bool = False,
    ) -> ToolRecord:
        valid_name = _check_name(name)
        if valid_name in self._records and not override:
            raise ToolAlreadyRegisteredError(valid_name)

        check_schema(schema)

        valid_description = _check_description(description)
        valid_handler = _check_handler(handler)
        valid_idempotent = _check_idempotent(idempotent)
        valid_timeout = _check_timeout(timeout_ms)
        _check_override(override)

        record = ToolRecord(
            name=valid_name,
            description=valid_description,
            schema=copy.deepcopy(dict(schema)),
            handler=valid_handler,
            idempotent=valid_idempotent,
            timeout_ms=valid_timeout,
        )
        self._records[valid_name] = record
        return record

    def get(self, name: str) -> ToolRecord:
        try:
            return self._records[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def validate(self, name: str, args: object) -> Ok | list[ValidationError]:
        record = self.get(name)
        errors = validate_instance(record.schema, args)
        if errors:
            return errors
        return Ok()

    def names(self) -> list[str]:
        return list(self._records)


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "Ok",
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolRecord",
    "ToolRegistry",
]

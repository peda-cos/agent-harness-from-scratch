"""A compact tool registry: records, names, and a closed schema subset."""

from __future__ import annotations

import copy
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias, cast

DEFAULT_TIMEOUT_MS = 30_000

Handler: TypeAlias = Callable[..., Awaitable[object]]

_NAME_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_JSON_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"}
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One failed assertion at one argument location."""

    path: str
    keyword: str
    message: str

    def __str__(self) -> str:
        if not self.path:
            return self.message
        return f"{self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class ToolRecord:
    """The immutable catalog entry the dispatcher consumes."""

    name: str
    schema: Mapping[str, object]
    handler: Handler
    description: str
    idempotent: bool
    timeout_ms: int


class ToolNotFoundError(KeyError):
    """Raised when a registry lookup misses."""


class Registry(Protocol):
    """The dispatcher-visible half of a tool registry."""

    def get(self, name: str) -> ToolRecord:
        """Return a record or raise ToolNotFoundError."""
        ...

    def validate(
        self, name: str, args: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        """Return every schema violation for the named tool."""
        ...


def _escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(token: str) -> str:
    return f"/{_escape_token(token)}"


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _matches_type(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return False


def _check_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _check_schema(schema: Mapping[str, object]) -> dict[str, object]:
    root = dict(schema)
    unknown = set(root) - {"type", "required", "properties"}
    if unknown:
        raise ValueError(f"unsupported schema keyword: {sorted(unknown)[0]}")
    if root.get("type") != "object":
        raise ValueError('schema type must be "object"')

    required_raw = root.get("required", [])
    if not isinstance(required_raw, Sequence) or isinstance(
        required_raw, (str, bytes)
    ):
        raise ValueError("required must be a sequence of names")
    required: list[str] = []
    seen: set[str] = set()
    for item in required_raw:
        if not isinstance(item, str):
            raise ValueError("required names must be strings")
        if item in seen:
            raise ValueError(f"duplicate required name: {item}")
        seen.add(item)
        required.append(item)

    properties_raw = root.get("properties", {})
    if not isinstance(properties_raw, Mapping):
        raise ValueError("properties must be a mapping")
    properties: dict[str, object] = {}
    for prop_name, prop_schema in properties_raw.items():
        if not isinstance(prop_name, str):
            raise ValueError("property names must be strings")
        if not isinstance(prop_schema, Mapping):
            raise ValueError(f"schema for property {prop_name!r} must be a mapping")
        unknown_prop = set(prop_schema) - {"type"}
        if unknown_prop:
            raise ValueError(
                f"unsupported schema keyword for property {prop_name!r}: "
                f"{sorted(unknown_prop)[0]}"
            )
        type_name = prop_schema.get("type")
        if not isinstance(type_name, str) or type_name not in _JSON_TYPES:
            raise ValueError(
                f"property {prop_name!r} has unsupported type {type_name!r}"
            )
        properties[prop_name] = {"type": type_name}

    normalized: dict[str, object] = {
        "type": "object",
        "required": required,
        "properties": properties,
    }
    return copy.deepcopy(normalized)


def _is_async_callable(handler: Callable[..., object]) -> bool:
    if inspect.iscoroutinefunction(handler):
        return True
    return inspect.iscoroutinefunction(getattr(handler, "__call__", None))


class ToolRegistry:
    """In-memory insertion-ordered registry with a closed schema subset."""

    def __init__(self) -> None:
        self._records: dict[str, ToolRecord] = {}

    def register(
        self,
        name: str,
        schema: Mapping[str, object],
        handler: Handler,
        *,
        description: str = "",
        idempotent: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        override: bool = False,
    ) -> ToolRecord:
        if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
            raise ValueError(f"invalid tool name: {name!r}")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        if type(idempotent) is not bool:
            raise ValueError("idempotent must be a bool")
        if type(override) is not bool:
            raise ValueError("override must be a bool")
        checked_timeout = _check_positive_int(timeout_ms, "timeout_ms")
        if not callable(handler):
            raise ValueError("handler must be callable")
        if not _is_async_callable(handler):
            raise ValueError("handler must be an async callable")
        if name in self._records and not override:
            raise ValueError(f"tool already registered: {name}")

        record = ToolRecord(
            name=name,
            schema=_check_schema(schema),
            handler=handler,
            description=description,
            idempotent=idempotent,
            timeout_ms=checked_timeout,
        )
        self._records[name] = record
        return record

    def get(self, name: str) -> ToolRecord:
        try:
            return self._records[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def validate(
        self, name: str, args: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        record = self.get(name)
        return _validate_object(record.schema, args)

    def names(self) -> tuple[str, ...]:
        return tuple(self._records)


def _validate_object(
    schema: Mapping[str, object], args: Mapping[str, object]
) -> tuple[ValidationIssue, ...]:
    required = cast(list[str], schema.get("required", []))
    properties = cast(dict[str, dict[str, str]], schema.get("properties", {}))
    issues: list[ValidationIssue] = []

    for name in required:
        if name not in args:
            issues.append(
                ValidationIssue(
                    _pointer(name), "required", "required property is missing"
                )
            )

    for name, prop_schema in properties.items():
        if name not in args:
            continue
        expected = prop_schema["type"]
        value = args[name]
        if not _matches_type(value, expected):
            issues.append(
                ValidationIssue(
                    _pointer(name),
                    "type",
                    f"expected {expected}, got {_json_type(value)}",
                )
            )

    return tuple(issues)


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "Handler",
    "Registry",
    "ToolNotFoundError",
    "ToolRecord",
    "ToolRegistry",
    "ValidationIssue",
]

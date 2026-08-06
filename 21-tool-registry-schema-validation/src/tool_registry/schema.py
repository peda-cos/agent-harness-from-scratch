"""Pure JSON Schema 2020-12 subset validation.

The supported assertions are ``type``, ``properties``, ``required``,
``enum``, ``minLength``, ``maxLength``, ``pattern``, and ``items``.
This module never calls a handler, reads the clock, or mutates its inputs.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

JSON_TYPES: Final = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)


@dataclass(frozen=True)
class ValidationError:
    """One failed assertion at one instance location."""

    path: str
    keyword: str
    message: str
    expected: object = None
    actual: object = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "keyword": self.keyword,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
        }


def _escape_token(token: str) -> str:
    """Escape one RFC 6901 reference token."""
    return token.replace("~", "~0").replace("/", "~1")


def _child(path: str, token: str | int) -> str:
    return f"{path}/{_escape_token(str(token))}"


def _preview(value: object, limit: int = 80) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 3]}..."


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return _is_number(value)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return (
            isinstance(value, float)
            and math.isfinite(value)
            and value.is_integer()
        )
    return False


def _type_label(expected: object) -> str:
    if isinstance(expected, str):
        return expected
    if isinstance(expected, list):
        names = [item for item in expected if isinstance(item, str)]
        return " or ".join(names)
    return str(expected)


def _json_equal(left: object, right: object) -> bool:
    """JSON data-model equality: booleans are never numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if left is None or right is None:
        return left is None and right is None
    if _is_number(left) and _is_number(right):
        return bool(left == right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return False


def _string_errors(
    instance: str, schema: Mapping[str, Any], path: str
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if "minLength" in schema:
        minimum = cast(int, schema["minLength"])
        if len(instance) < minimum:
            errors.append(
                ValidationError(
                    path=path,
                    keyword="minLength",
                    message=f"string length {len(instance)} is below minLength {minimum}",
                    expected=minimum,
                    actual=len(instance),
                )
            )
    if "maxLength" in schema:
        maximum = cast(int, schema["maxLength"])
        if len(instance) > maximum:
            errors.append(
                ValidationError(
                    path=path,
                    keyword="maxLength",
                    message=f"string length {len(instance)} exceeds maxLength {maximum}",
                    expected=maximum,
                    actual=len(instance),
                )
            )
    if "pattern" in schema:
        pattern = cast(str, schema["pattern"])
        if re.search(pattern, instance) is None:
            errors.append(
                ValidationError(
                    path=path,
                    keyword="pattern",
                    message=f"string does not match pattern {pattern!r}",
                    expected=pattern,
                    actual=_preview(instance),
                )
            )
    return errors


def _object_errors(
    instance: dict[Any, Any], schema: Mapping[str, Any], path: str
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in instance:
                errors.append(
                    ValidationError(
                        path=_child(path, name),
                        keyword="required",
                        message=f"required property {name!r} is missing",
                        expected=name,
                        actual="missing",
                    )
                )
    properties = schema.get("properties", {})
    if isinstance(properties, Mapping):
        for name, subschema in properties.items():
            if (
                isinstance(name, str)
                and name in instance
                and isinstance(subschema, Mapping)
            ):
                _walk(
                    cast(Mapping[str, Any], subschema),
                    instance[name],
                    _child(path, name),
                    errors,
                )
    return errors


def _array_errors(
    instance: list[Any], schema: Mapping[str, Any], path: str
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    items = schema.get("items")
    if isinstance(items, Mapping):
        items_schema = cast(Mapping[str, Any], items)
        for index, item in enumerate(instance):
            _walk(items_schema, item, _child(path, index), errors)
    return errors


def _walk(
    schema: Mapping[str, Any],
    instance: object,
    path: str,
    errors: list[ValidationError],
) -> None:
    if "enum" in schema:
        allowed = schema["enum"]
        if isinstance(allowed, list) and not any(
            _json_equal(instance, member) for member in allowed
        ):
            errors.append(
                ValidationError(
                    path=path,
                    keyword="enum",
                    message="value is not one of the allowed enum values",
                    expected=allowed,
                    actual=_preview(instance),
                )
            )

    if "type" in schema:
        expected = schema["type"]
        if isinstance(expected, str):
            types: tuple[str, ...] = (expected,)
        elif isinstance(expected, list):
            types = tuple(item for item in expected if isinstance(item, str))
        else:
            types = ()
        if types and not any(_matches_type(instance, item) for item in types):
            errors.append(
                ValidationError(
                    path=path,
                    keyword="type",
                    message=(
                        f"expected type {_type_label(expected)}, "
                        f"got {_json_type(instance)}"
                    ),
                    expected=expected,
                    actual=_json_type(instance),
                )
            )

    if isinstance(instance, str):
        errors.extend(_string_errors(instance, schema, path))
    elif isinstance(instance, dict):
        errors.extend(_object_errors(instance, schema, path))
    elif isinstance(instance, list):
        errors.extend(_array_errors(instance, schema, path))


def validate_instance(
    schema: Mapping[str, Any], instance: object
) -> list[ValidationError]:
    """Validate a JSON-like instance and return every failure.

    The schema is expected to have passed :func:`check_schema` (the registry
    guarantees this). Validation is pure: it does not mutate the schema, the
    instance, or global state.
    """
    errors: list[ValidationError] = []
    _walk(schema, instance, "", errors)
    return errors

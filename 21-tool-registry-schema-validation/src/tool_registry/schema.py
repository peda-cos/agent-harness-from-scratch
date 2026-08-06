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
ASSERTION_KEYWORDS: Final = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "minLength",
        "maxLength",
        "pattern",
        "items",
    }
)
ANNOTATION_KEYWORDS: Final = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

_TYPE_MESSAGE = "type must be a valid type name or non-empty list of unique type names"
_PROPERTIES_MESSAGE = "properties must be an object of schemas"
_REQUIRED_MESSAGE = "required must be a list of unique strings"
_ENUM_MESSAGE = "enum must be a non-empty list of unique primitive values"
_PATTERN_MESSAGE = "pattern must be a string that compiles with Python re"
_ITEMS_MESSAGE = "items must be a schema object"


class SchemaError(ValueError):
    """A schema is malformed or outside the supported subset."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path or '/'}: {message}")


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


def _is_primitive(value: object) -> bool:
    return value is None or isinstance(value, (bool, str)) or _is_number(value)


def _check_type(value: object, path: str) -> None:
    if isinstance(value, str):
        if value not in JSON_TYPES:
            raise SchemaError(path, _TYPE_MESSAGE)
        return
    if not isinstance(value, list) or not value:
        raise SchemaError(path, _TYPE_MESSAGE)
    seen: set[str] = set()
    for index, item in enumerate(value):
        if (
            not isinstance(item, str)
            or item not in JSON_TYPES
            or item in seen
        ):
            raise SchemaError(_child(path, index), _TYPE_MESSAGE)
        seen.add(item)


def _check_properties(value: object, path: str) -> None:
    if not isinstance(value, Mapping):
        raise SchemaError(path, _PROPERTIES_MESSAGE)
    for name, subschema in value.items():
        if not isinstance(name, str):
            raise SchemaError(path, "properties keys must be strings")
        _check_schema(subschema, _child(path, name))


def _check_required(value: object, path: str) -> None:
    if not isinstance(value, list):
        raise SchemaError(path, _REQUIRED_MESSAGE)
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or item in seen:
            raise SchemaError(_child(path, index), _REQUIRED_MESSAGE)
        seen.add(item)


def _check_enum(value: object, path: str) -> None:
    if not isinstance(value, list) or not value:
        raise SchemaError(path, _ENUM_MESSAGE)
    seen: list[object] = []
    for index, item in enumerate(value):
        if not _is_primitive(item) or any(
            _json_equal(item, previous) for previous in seen
        ):
            raise SchemaError(_child(path, index), _ENUM_MESSAGE)
        seen.append(item)


def _check_length(value: object, path: str, keyword: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaError(path, f"{keyword} must be a non-negative integer")
    return value


def _check_pattern(value: object, path: str) -> None:
    if not isinstance(value, str):
        raise SchemaError(path, _PATTERN_MESSAGE)
    try:
        re.compile(value)
    except re.error:
        raise SchemaError(path, _PATTERN_MESSAGE) from None


def _check_keyword(keyword: str, value: object, path: str) -> None:
    keyword_path = _child(path, keyword)
    if keyword == "type":
        _check_type(value, keyword_path)
    elif keyword == "properties":
        _check_properties(value, keyword_path)
    elif keyword == "required":
        _check_required(value, keyword_path)
    elif keyword == "enum":
        _check_enum(value, keyword_path)
    elif keyword in {"minLength", "maxLength"}:
        _check_length(value, keyword_path, keyword)
    elif keyword == "pattern":
        _check_pattern(value, keyword_path)
    elif keyword == "items":
        if not isinstance(value, Mapping):
            raise SchemaError(keyword_path, _ITEMS_MESSAGE)
        _check_schema(value, keyword_path)


def _check_schema(schema: object, path: str) -> None:
    if not isinstance(schema, Mapping):
        raise SchemaError(path, "schema must be an object")
    for key in schema:
        if not isinstance(key, str):
            raise SchemaError(path, "schema keys must be strings")
    for key, value in schema.items():
        if key in ANNOTATION_KEYWORDS:
            continue
        if key not in ASSERTION_KEYWORDS:
            raise SchemaError(_child(path, key), f"unsupported keyword {key!r}")
        _check_keyword(key, value, path)

    if "minLength" in schema and "maxLength" in schema:
        minimum = cast(int, schema["minLength"])
        maximum = cast(int, schema["maxLength"])
        if minimum > maximum:
            raise SchemaError(
                _child(path, "minLength"),
                "minLength must not exceed maxLength",
            )


def check_schema(schema: object, path: str = "") -> None:
    """Reject a schema that is malformed or outside the supported subset.

    ``path`` is a JSON Pointer into the schema, used to prefix nested errors.
    """
    _check_schema(schema, path)


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


__all__ = ["SchemaError", "ValidationError", "check_schema", "validate_instance"]

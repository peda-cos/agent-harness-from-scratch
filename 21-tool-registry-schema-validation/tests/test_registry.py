from __future__ import annotations

import inspect
from typing import Any

import pytest

from tool_registry import (
    DEFAULT_TIMEOUT_MS,
    Ok,
    SchemaError,
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
    ToolRecord,
    ToolRegistry,
)


def _handler(*args: object, **kwargs: object) -> str:
    return "ok"


def _valid_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }


def test_register_returns_record_with_defaults() -> None:
    registry = ToolRegistry()
    record = registry.register("math.add", _valid_schema(), _handler)
    assert isinstance(record, ToolRecord)
    assert record.name == "math.add"
    assert record.description == ""
    assert record.schema == _valid_schema()
    assert record.handler is _handler
    assert record.idempotent is False
    assert record.timeout_ms == DEFAULT_TIMEOUT_MS
    assert registry.get("math.add") is record


def test_register_stores_custom_fields() -> None:
    registry = ToolRegistry()
    record = registry.register(
        "math.add",
        _valid_schema(),
        _handler,
        description="Add two numbers",
        idempotent=True,
        timeout_ms=250,
    )
    assert record.description == "Add two numbers"
    assert record.idempotent is True
    assert record.timeout_ms == 250


def test_names_preserve_registration_order_and_return_a_copy() -> None:
    registry = ToolRegistry()
    registry.register("a.first", _valid_schema(), _handler)
    registry.register("b.second", _valid_schema(), _handler)
    names = registry.names()
    assert names == ["a.first", "b.second"]
    names.append("c.third")
    assert registry.names() == ["a.first", "b.second"]


def test_duplicate_registration_is_rejected_and_original_survives() -> None:
    registry = ToolRegistry()
    original = registry.register("math.add", _valid_schema(), _handler)
    with pytest.raises(ToolAlreadyRegisteredError) as caught:
        registry.register("math.add", {"type": "string"}, _handler)
    assert str(caught.value) == (
        "tool 'math.add' is already registered; "
        "pass override=True to replace it"
    )
    assert registry.get("math.add") is original


def test_override_replaces_record_and_preserves_position() -> None:
    registry = ToolRegistry()
    registry.register("a.first", _valid_schema(), _handler)
    registry.register("b.second", _valid_schema(), _handler)
    registry.register("c.third", _valid_schema(), _handler)

    def replacement(*args: object, **kwargs: object) -> str:
        return "replacement"

    new_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    record = registry.register(
        "b.second",
        new_schema,
        replacement,
        description="Replacement",
        override=True,
    )
    assert registry.names() == ["a.first", "b.second", "c.third"]
    assert registry.get("b.second") is record
    assert record.handler is replacement
    assert record.schema == new_schema
    assert registry.validate("b.second", {"name": "Ada"}) == Ok()


def test_override_of_absent_name_registers_normally() -> None:
    registry = ToolRegistry()
    record = registry.register("new.tool", _valid_schema(), _handler, override=True)
    assert registry.names() == ["new.tool"]
    assert registry.get("new.tool") is record


def test_failed_override_of_invalid_schema_leaves_original() -> None:
    registry = ToolRegistry()
    original = registry.register("math.add", _valid_schema(), _handler)
    with pytest.raises(SchemaError):
        registry.register(
            "math.add", {"type": "not-a-type"}, _handler, override=True
        )
    assert registry.get("math.add") is original


def test_failed_override_of_invalid_timeout_leaves_original() -> None:
    registry = ToolRegistry()
    original = registry.register("math.add", _valid_schema(), _handler)
    with pytest.raises(ValueError, match="timeout_ms must be positive"):
        registry.register(
            "math.add", _valid_schema(), _handler, timeout_ms=0, override=True
        )
    assert registry.get("math.add") is original


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Upper",
        "with-hyphen",
        "with space",
        "a..b",
        ".a",
        "a.",
        "a/b",
        "a.b-c",
    ],
)
def test_invalid_names_are_rejected(name: str) -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError) as caught:
        registry.register(name, _valid_schema(), _handler)
    assert str(caught.value) == (
        f"invalid tool name {name!r}: expected lowercase alphanumeric/"
        "underscore segments separated by dots"
    )
    assert registry.names() == []


@pytest.mark.parametrize(
    "name",
    ["a", "a_b", "snake_case.segment.case", "a1.b2", "1a.2b", "_"],
)
def test_valid_names_are_accepted(name: str) -> None:
    registry = ToolRegistry()
    registry.register(name, _valid_schema(), _handler)
    assert registry.names() == [name]


def test_unknown_get_raises_key_error_with_readable_message() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError) as caught:
        registry.get("missing.tool")
    assert isinstance(caught.value, KeyError)
    assert str(caught.value) == "tool 'missing.tool' is not registered"


def test_validate_success_returns_ok() -> None:
    registry = ToolRegistry()
    registry.register("math.add", _valid_schema(), _handler)
    outcome = registry.validate("math.add", {"id": 1})
    assert isinstance(outcome, Ok)
    assert outcome.to_dict() == {"ok": True}


def test_validate_failure_returns_errors_without_calling_handler() -> None:
    calls: list[object] = []

    def handler(*args: object, **kwargs: object) -> None:
        calls.append(args)

    registry = ToolRegistry()
    registry.register("math.add", _valid_schema(), handler)
    outcome = registry.validate("math.add", {"id": "not an integer"})
    assert isinstance(outcome, list)
    assert [(error.path, error.keyword) for error in outcome] == [
        ("/id", "type")
    ]
    assert calls == []


def test_validate_with_non_object_args_reports_root_type_error() -> None:
    registry = ToolRegistry()
    registry.register("math.add", _valid_schema(), _handler)
    outcome = registry.validate("math.add", [])
    assert isinstance(outcome, list)
    assert [(error.path, error.keyword) for error in outcome] == [("", "type")]


def test_async_handler_is_stored_opaque() -> None:
    async def async_handler(value: int) -> int:
        return value

    registry = ToolRegistry()
    record = registry.register("async.echo", {"type": "object"}, async_handler)
    assert inspect.iscoroutinefunction(record.handler)
    assert registry.validate("async.echo", {}) == Ok()


def test_schema_is_snapshotted_at_registration() -> None:
    registry = ToolRegistry()
    schema = _valid_schema()
    registry.register("math.add", schema, _handler)
    schema["type"] = "array"
    schema["required"].append("extra")
    schema["properties"]["id"]["type"] = "string"
    assert registry.validate("math.add", {"id": 1}) == Ok()
    outcome = registry.validate("math.add", {})
    assert isinstance(outcome, list)
    assert [(error.path, error.keyword) for error in outcome] == [
        ("/id", "required")
    ]


def test_invalid_schema_leaves_registry_empty() -> None:
    registry = ToolRegistry()
    with pytest.raises(SchemaError):
        registry.register("math.add", {"type": "nope"}, _handler)
    assert registry.names() == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout_ms": 0}, "timeout_ms must be positive"),
        ({"timeout_ms": -1}, "timeout_ms must be positive"),
    ],
)
def test_non_positive_timeout_is_a_value_error(
    kwargs: dict[str, object], message: str
) -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match=message):
        registry.register("math.add", _valid_schema(), _handler, **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout_ms": True}, "timeout_ms must be an integer"),
        ({"timeout_ms": "5"}, "timeout_ms must be an integer"),
        ({"idempotent": 1}, "idempotent must be a bool"),
        ({"override": 1}, "override must be a bool"),
        ({"description": 3}, "description must be a string"),
    ],
)
def test_wrong_typed_options_are_type_errors(
    kwargs: dict[str, object], message: str
) -> None:
    registry = ToolRegistry()
    with pytest.raises(TypeError, match=message):
        registry.register("math.add", _valid_schema(), _handler, **kwargs)


@pytest.mark.parametrize("description", ["line one\nline two", "line one\rline two"])
def test_multiline_description_is_rejected(description: str) -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="description must be a single line"):
        registry.register(
            "math.add", _valid_schema(), _handler, description=description
        )


def test_non_callable_handler_is_rejected() -> None:
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="handler must be callable"):
        registry.register("math.add", _valid_schema(), 3)  # type: ignore[arg-type]
    assert registry.names() == []

from __future__ import annotations

import math

import pytest

from tool_registry import SchemaError, check_schema


def _error(schema: object) -> SchemaError:
    with pytest.raises(SchemaError) as caught:
        check_schema(schema)
    return caught.value


def test_valid_schema_is_accepted() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"enum": ["fast", "slow"]},
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 8},
            },
        },
        "required": ["mode"],
    }
    assert check_schema(schema) is None


def test_empty_schema_and_type_union_are_accepted() -> None:
    assert check_schema({}) is None
    assert check_schema({"type": ["string", "null"]}) is None


def test_annotations_are_accepted_and_ignored() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Example",
        "description": "A schema",
        "$comment": "comment",
        "default": None,
        "examples": [{}],
        "deprecated": False,
        "readOnly": True,
        "writeOnly": False,
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
    }
    assert check_schema(schema) is None


def test_root_must_be_an_object() -> None:
    error = _error([])
    assert error.path == ""
    assert error.message == "schema must be an object"
    assert str(error) == "/: schema must be an object"
    assert isinstance(error, ValueError)


def test_unknown_keyword_is_rejected() -> None:
    error = _error({"type": "string", "format": "email"})
    assert error.path == "/format"
    assert error.message == "unsupported keyword 'format'"


def test_nested_unknown_keyword_uses_schema_pointer() -> None:
    error = _error(
        {
            "type": "object",
            "properties": {"email": {"type": "string", "format": "email"}},
        }
    )
    assert error.path == "/properties/email/format"
    assert error.message == "unsupported keyword 'format'"


def test_non_string_schema_key_is_rejected() -> None:
    error = _error({1: "value"})
    assert error.path == ""
    assert error.message == "schema keys must be strings"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "strin"},
        {"type": []},
        {"type": ["string", "string"]},
        {"type": ["string", "email"]},
        {"type": [1]},
    ],
)
def test_invalid_type_is_rejected(schema: dict[str, object]) -> None:
    error = _error(schema)
    assert error.message == (
        "type must be a valid type name or non-empty list of unique type names"
    )


def test_invalid_type_list_element_path_points_at_element() -> None:
    error = _error({"type": ["string", "email"]})
    assert error.path == "/type/1"


def test_invalid_properties_are_rejected() -> None:
    error = _error({"properties": []})
    assert error.path == "/properties"
    assert error.message == "properties must be an object of schemas"


def test_non_string_property_key_is_rejected() -> None:
    error = _error({"properties": {1: {"type": "string"}}})
    assert error.path == "/properties"
    assert error.message == "properties keys must be strings"


def test_property_value_must_be_a_schema_object() -> None:
    error = _error({"properties": {"x": True}})
    assert error.path == "/properties/x"
    assert error.message == "schema must be an object"


@pytest.mark.parametrize(
    ("required", "path"),
    [
        ("x", "/required"),
        ([1], "/required/0"),
        (["x", "x"], "/required/1"),
    ],
)
def test_invalid_required_is_rejected(required: object, path: str) -> None:
    error = _error({"required": required})
    assert error.path == path
    assert error.message == "required must be a list of unique strings"


@pytest.mark.parametrize(
    ("enum", "path"),
    [
        ([], "/enum"),
        ([[]], "/enum/0"),
        ([{"a": 1}], "/enum/0"),
        ([1, 1.0], "/enum/1"),
        ([float("nan")], "/enum/0"),
    ],
)
def test_invalid_enum_is_rejected(enum: object, path: str) -> None:
    error = _error({"enum": enum})
    assert error.path == path
    assert error.message == "enum must be a non-empty list of unique primitive values"


def test_nan_is_not_primitive() -> None:
    assert math.isnan(float("nan"))
    error = _error({"enum": [float("nan")]})
    assert error.path == "/enum/0"


@pytest.mark.parametrize(
    ("schema", "path"),
    [
        ({"minLength": -1}, "/minLength"),
        ({"minLength": True}, "/minLength"),
        ({"maxLength": "3"}, "/maxLength"),
        ({"minLength": 5, "maxLength": 2}, "/minLength"),
    ],
)
def test_invalid_lengths_are_rejected(
    schema: dict[str, object], path: str
) -> None:
    error = _error(schema)
    assert error.path == path
    if path == "/minLength" and "maxLength" in schema:
        assert error.message == "minLength must not exceed maxLength"
    else:
        assert "must be a non-negative integer" in error.message


@pytest.mark.parametrize("pattern", ["[", 3, True])
def test_invalid_pattern_is_rejected(pattern: object) -> None:
    error = _error({"pattern": pattern})
    assert error.path == "/pattern"
    assert error.message == "pattern must be a string that compiles with Python re"


@pytest.mark.parametrize("items", [True, [], "schema", 3])
def test_invalid_items_is_rejected(items: object) -> None:
    error = _error({"items": items})
    assert error.path == "/items"
    assert error.message == "items must be a schema object"


def test_nested_schema_path_can_be_prefixed() -> None:
    with pytest.raises(SchemaError) as caught:
        check_schema({"type": "bad"}, path="/properties/city")
    assert caught.value.path == "/properties/city/type"


def test_schema_checking_does_not_mutate_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }
    before = repr(schema)
    check_schema(schema)
    assert repr(schema) == before

from __future__ import annotations

import copy

from tool_registry import check_schema, validate_instance


def test_pointer_escaping_under_nested_array_and_property() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a/b": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"m~n": {"type": "integer"}},
                },
            }
        },
    }
    errors = validate_instance(schema, {"a/b": [{"m~n": "not an integer"}]})
    assert [error.path for error in errors] == ["/a~1b/0/m~0n"]


def test_empty_property_name_uses_single_slash_pointer() -> None:
    schema = {"type": "object", "properties": {"": {"type": "integer"}}}
    errors = validate_instance(schema, {"": "not an integer"})
    assert [error.path for error in errors] == ["/"]


def test_nested_required_error_points_at_missing_array_item_property() -> None:
    schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            }
        },
    }
    errors = validate_instance(schema, {"tags": [{"id": 1}, {}]})
    assert [(error.path, error.keyword) for error in errors] == [
        ("/tags/1/id", "required")
    ]


def test_repeated_validation_returns_independent_but_equal_lists() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    instance = {"id": "bad"}
    first = validate_instance(schema, instance)
    second = validate_instance(schema, instance)
    assert first is not second
    assert [error.to_dict() for error in first] == [
        error.to_dict() for error in second
    ]
    first.clear()
    assert len(second) == 1


def test_schema_and_instance_survive_checking_and_validation() -> None:
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tags"],
    }
    instance = {"tags": ["ok", 3]}
    schema_before = copy.deepcopy(schema)
    instance_before = copy.deepcopy(instance)
    check_schema(schema)
    validate_instance(schema, instance)
    assert schema == schema_before
    assert instance == instance_before


def test_empty_array_and_empty_object_are_valid() -> None:
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    assert validate_instance(schema, {"tags": []}) == []

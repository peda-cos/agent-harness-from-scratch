from __future__ import annotations

import copy
import json

import pytest

from tool_registry import ValidationError, validate_instance


def _bad_nested_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name"],
            }
        },
        "required": ["user"],
    }


def test_valid_nested_object_returns_no_errors() -> None:
    instance = {"user": {"name": "Ada", "age": 36}}
    assert validate_instance(_bad_nested_schema(), instance) == []


def test_type_error_points_at_nested_property() -> None:
    errors = validate_instance(_bad_nested_schema(), {"user": {"name": 42}})
    assert len(errors) == 1
    error = errors[0]
    assert error.path == "/user/name"
    assert error.keyword == "type"
    assert error.expected == "string"
    assert error.actual == "integer"
    assert error.message == "expected type string, got integer"


def test_required_missing_targets_the_missing_property() -> None:
    errors = validate_instance(_bad_nested_schema(), {"user": {}})
    assert [(error.path, error.keyword) for error in errors] == [
        ("/user/name", "required")
    ]
    assert errors[0].expected == "name"
    assert errors[0].actual == "missing"


def test_required_missing_at_root_targets_property() -> None:
    schema = {"type": "object", "required": ["id"]}
    errors = validate_instance(schema, {})
    assert [(error.path, error.keyword) for error in errors] == [("/id", "required")]


def test_every_keyword_accepts_a_valid_instance() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"enum": ["fast", "slow"]},
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 5,
                    "pattern": "^[a-z]+$",
                },
            },
        },
        "required": ["mode", "tags"],
    }
    instance = {"mode": "fast", "tags": ["ab", "cde"]}
    assert validate_instance(schema, instance) == []


def test_every_keyword_reports_a_failure() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"enum": ["fast", "slow"]},
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 5,
                    "pattern": "^[a-z]+$",
                },
            },
        },
        "required": ["mode", "tags"],
    }
    errors = validate_instance(schema, {"mode": "warp", "tags": ["A"]})
    assert [(error.path, error.keyword) for error in errors] == [
        ("/mode", "enum"),
        ("/tags/0", "minLength"),
        ("/tags/0", "pattern"),
    ]


@pytest.mark.parametrize(
    ("value", "expected_keywords"),
    [
        ("ab", []),
        ("abc", []),
        ("a", ["minLength"]),
        ("abcd", ["maxLength"]),
    ],
)
def test_string_lengths_are_inclusive(
    value: str, expected_keywords: list[str]
) -> None:
    schema = {"type": "string", "minLength": 2, "maxLength": 3}
    errors = validate_instance(schema, value)
    assert [error.keyword for error in errors] == expected_keywords


def test_pattern_is_unanchored_and_uses_search() -> None:
    assert validate_instance({"type": "string", "pattern": "b"}, "abc") == []
    errors = validate_instance({"type": "string", "pattern": "^b$"}, "abc")
    assert [error.keyword for error in errors] == ["pattern"]


def test_integer_accepts_integral_float_but_not_fraction_or_bool() -> None:
    schema = {"type": "integer"}
    assert validate_instance(schema, 3) == []
    assert validate_instance(schema, 3.0) == []
    fraction = validate_instance(schema, 3.5)
    boolean = validate_instance(schema, True)
    assert [error.keyword for error in fraction] == ["type"]
    assert fraction[0].actual == "number"
    assert [error.keyword for error in boolean] == ["type"]
    assert boolean[0].actual == "boolean"


def test_number_rejects_strings_and_booleans() -> None:
    schema = {"type": "number"}
    assert validate_instance(schema, 3) == []
    assert validate_instance(schema, 3.5) == []
    assert [error.actual for error in validate_instance(schema, "3")] == ["string"]
    assert [error.actual for error in validate_instance(schema, True)] == ["boolean"]


def test_enum_uses_json_equality() -> None:
    schema = {"enum": [1, "1", None, False]}
    assert validate_instance(schema, 1.0) == []
    assert validate_instance(schema, "1") == []
    assert validate_instance(schema, None) == []
    assert validate_instance(schema, False) == []
    for invalid in (True, 0, "true"):
        errors = validate_instance(schema, invalid)
        assert len(errors) == 1
        assert errors[0].keyword == "enum"
        assert errors[0].expected == [1, "1", None, False]


def test_enum_works_without_type_keyword() -> None:
    schema = {"enum": ["a", "b"]}
    assert validate_instance(schema, "a") == []
    errors = validate_instance(schema, "c")
    assert errors[0].path == ""
    assert errors[0].keyword == "enum"


def test_array_items_report_element_index() -> None:
    schema = {"type": "array", "items": {"type": "integer"}}
    errors = validate_instance(schema, [1, "x", 2.5])
    assert [error.path for error in errors] == ["/1", "/2"]
    assert [error.keyword for error in errors] == ["type", "type"]


def test_pointer_escaping_for_property_names() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a/b": {"type": "integer"},
            "m~n": {"type": "string"},
        },
    }
    errors = validate_instance(schema, {"a/b": "x", "m~n": 1})
    assert [error.path for error in errors] == ["/a~1b", "/m~0n"]


def test_union_type_accepts_either_member() -> None:
    schema = {"type": ["string", "null"]}
    assert validate_instance(schema, "x") == []
    assert validate_instance(schema, None) == []
    errors = validate_instance(schema, 1)
    assert len(errors) == 1
    assert errors[0].message == "expected type string or null, got integer"
    assert errors[0].expected == ["string", "null"]


def test_keywords_apply_only_to_matching_instance_types() -> None:
    schema = {"properties": {"x": {"type": "string"}}, "minLength": 5}
    assert validate_instance(schema, 3) == []
    assert validate_instance(schema, [1]) == []


def test_root_type_error_has_empty_pointer() -> None:
    errors = validate_instance({"type": "string"}, 1)
    assert len(errors) == 1
    assert errors[0].path == ""


def test_boolean_and_null_union() -> None:
    schema = {"type": ["boolean", "null"]}
    assert validate_instance(schema, True) == []
    assert validate_instance(schema, None) == []
    errors = validate_instance(schema, 0)
    assert [error.actual for error in errors] == ["integer"]


def test_extra_properties_are_allowed() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    assert validate_instance(schema, {"id": 1, "extra": "anything"}) == []


def test_all_errors_are_aggregated_in_stable_order() -> None:
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "minLength": 2},
            "days": {"type": "integer"},
            "units": {"enum": ["metric", "imperial"]},
        },
        "required": ["city", "days"],
    }
    errors = validate_instance(
        schema, {"city": "x", "days": "three", "units": "kelvin"}
    )
    assert [(error.path, error.keyword) for error in errors] == [
        ("/city", "minLength"),
        ("/days", "type"),
        ("/units", "enum"),
    ]


def test_required_errors_precede_nested_property_errors() -> None:
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
        },
        "required": ["id", "name"],
    }
    errors = validate_instance(schema, {"name": 3})
    assert [(error.path, error.keyword) for error in errors] == [
        ("/id", "required"),
        ("/name", "type"),
    ]


def test_validator_does_not_mutate_schema_or_instance() -> None:
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["tags"],
    }
    instance = {"tags": ["a", 2]}
    schema_before = copy.deepcopy(schema)
    instance_before = copy.deepcopy(instance)
    validate_instance(schema, instance)
    assert schema == schema_before
    assert instance == instance_before


def test_repeated_validation_is_deterministic_and_independent() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    instance = {"id": "nope"}
    first = validate_instance(schema, instance)
    second = validate_instance(schema, instance)
    assert [error.to_dict() for error in first] == [
        error.to_dict() for error in second
    ]
    first.clear()
    assert len(validate_instance(schema, instance)) == 1


def test_error_to_dict_is_json_serializable() -> None:
    errors = validate_instance(
        {"type": "object", "properties": {"id": {"enum": [1, 2]}}},
        {"id": "x"},
    )
    payload = [error.to_dict() for error in errors]
    assert set(payload[0]) == {"path", "keyword", "message", "expected", "actual"}
    encoded = json.dumps(payload)
    assert '"/id"' in encoded
    assert '"enum"' in encoded


def test_validation_error_equality_and_repr() -> None:
    error = ValidationError(
        path="/id",
        keyword="type",
        message="expected type integer, got string",
        expected="integer",
        actual="string",
    )
    assert error == ValidationError(
        path="/id",
        keyword="type",
        message="expected type integer, got string",
        expected="integer",
        actual="string",
    )
    assert "ValidationError" in repr(error)

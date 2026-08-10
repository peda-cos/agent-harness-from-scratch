from __future__ import annotations

import pytest

from function_dispatcher import (
    DEFAULT_TIMEOUT_MS,
    ToolNotFoundError,
    ToolRegistry,
)


async def echo(**kwargs: object) -> object:
    return kwargs


def test_register_get_and_names() -> None:
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "required": ["city"],
        "properties": {"city": {"type": "string"}},
    }
    first = registry.register(
        "weather.forecast",
        schema,
        echo,
        description="synthetic forecast",
        idempotent=True,
        timeout_ms=1_234,
    )
    second = registry.register("math.add", {"type": "object"}, echo)

    assert first.name == "weather.forecast"
    assert first.description == "synthetic forecast"
    assert first.idempotent is True
    assert first.timeout_ms == 1_234
    assert first.handler is echo
    assert registry.get("weather.forecast") is first
    assert second.timeout_ms == DEFAULT_TIMEOUT_MS
    assert registry.names() == ("weather.forecast", "math.add")
    with pytest.raises(ToolNotFoundError):
        registry.get("missing")


def test_validation_aggregates_paths_and_allows_extra_properties() -> None:
    registry = ToolRegistry()
    registry.register(
        "weather.forecast",
        {
            "type": "object",
            "required": ["city", "days"],
            "properties": {
                "city": {"type": "string"},
                "days": {"type": "integer"},
                "ratio": {"type": "number"},
                "active": {"type": "boolean"},
            },
        },
        echo,
    )
    registry.register(
        "odd.pointer",
        {
            "type": "object",
            "required": ["a/b~c"],
            "properties": {},
        },
        echo,
    )

    issues = registry.validate("weather.forecast", {"city": 3, "extra": True})

    assert [issue.path for issue in issues] == ["/days", "/city"]
    assert [issue.keyword for issue in issues] == ["required", "type"]
    assert str(issues[0]) == "/days: required property is missing"
    assert str(issues[1]) == "/city: expected string, got integer"
    assert registry.validate("weather.forecast", {"city": "Oslo", "days": 2}) == ()
    assert registry.validate("weather.forecast", {"city": "Oslo", "days": True})[0].path == "/days"
    assert registry.validate("odd.pointer", {})[0].path == "/a~1b~0c"


def test_duplicate_override_snapshot_and_invalid_registration() -> None:
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "required": ["city"],
        "properties": {"city": {"type": "string"}},
    }
    original = registry.register("weather.forecast", schema, echo)
    schema["properties"]["city"]["type"] = "integer"  # type: ignore[index]

    assert registry.validate("weather.forecast", {"city": "Oslo"}) == ()
    assert registry.validate("weather.forecast", {"city": 1})[0].path == "/city"
    assert registry.get("weather.forecast") is original

    with pytest.raises(ValueError):
        registry.register("weather.forecast", schema, echo)

    replacement = registry.register(
        "weather.forecast", {"type": "object"}, echo, override=True
    )
    assert registry.get("weather.forecast") is replacement
    assert registry.names() == ("weather.forecast",)

    with pytest.raises(ValueError):
        registry.register("bad", {"type": "object", "extra": 1}, echo)
    with pytest.raises(ValueError):
        registry.register("bad", {"type": "array"}, echo)
    with pytest.raises(ValueError):

        def sync_handler() -> object:
            return None

        registry.register("bad", {"type": "object"}, sync_handler)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        registry.register("bad", {"type": "object"}, echo, timeout_ms=0)
    with pytest.raises(ValueError):
        registry.register("bad", {"type": "object"}, echo, idempotent=1)  # type: ignore[arg-type]

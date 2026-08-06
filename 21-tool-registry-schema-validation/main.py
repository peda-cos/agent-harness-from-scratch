"""Offline demo for the typed tool registry.

Run with ``.venv/bin/python main.py``. The weather handler is a deterministic
fixture, not a real forecast; no network, credentials, or model are involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tool_registry import (  # noqa: E402
    Ok,
    SchemaError,
    ToolAlreadyRegisteredError,
    ToolRegistry,
)

FORECAST_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "minLength": 2},
        "days": {"type": "integer"},
        "units": {"enum": ["metric", "imperial"]},
    },
    "required": ["city", "days"],
}

ADD_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
    },
    "required": ["a", "b"],
}

WORD_COUNT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"text": {"type": "string", "minLength": 1}},
    "required": ["text"],
}


def get_forecast(city: str, days: int, units: str = "metric") -> dict[str, object]:
    """Return a synthetic forecast so the demo is fully deterministic."""
    base = 21 if units == "metric" else 70
    return {
        "city": city,
        "units": units,
        "forecast": [
            {"day": index + 1, "temperature": base + index}
            for index in range(days)
        ],
    }


def add(a: float, b: float) -> float:
    return a + b


def word_count(text: str) -> int:
    return len(text.split())


def main() -> int:
    registry = ToolRegistry()
    registry.register(
        "weather.get_forecast",
        FORECAST_SCHEMA,
        get_forecast,
        description="Deterministic local forecast fixture",
        idempotent=True,
        timeout_ms=5_000,
    )
    registry.register(
        "math.add",
        ADD_SCHEMA,
        add,
        description="Add two numbers",
        idempotent=True,
    )
    registry.register(
        "text.word_count",
        WORD_COUNT_SCHEMA,
        word_count,
        description="Count whitespace-separated words",
        idempotent=True,
    )

    print("registered tools:", registry.names())

    print("\ninvalid forecast call:")
    bad = registry.validate(
        "weather.get_forecast",
        {"city": "x", "days": "three", "units": "kelvin"},
    )
    if not isinstance(bad, list):
        raise RuntimeError("expected validation errors for the bad call")
    print(json.dumps([error.to_dict() for error in bad], indent=2))

    corrected = registry.validate(
        "weather.get_forecast",
        {"city": "Lisbon", "days": 2, "units": "metric"},
    )
    if not isinstance(corrected, Ok):
        raise RuntimeError("expected the corrected call to pass")
    print("\ncorrected call:", repr(corrected))
    print(
        "forecast:",
        json.dumps(get_forecast("Lisbon", 2, "metric"), sort_keys=True),
    )

    print()
    try:
        registry.register("math.add", ADD_SCHEMA, add)
    except ToolAlreadyRegisteredError as error:
        print("duplicate rejected:", error)

    try:
        registry.register("bad.tool", {"type": "strin"}, add)
    except SchemaError as error:
        print("invalid schema rejected:", error)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

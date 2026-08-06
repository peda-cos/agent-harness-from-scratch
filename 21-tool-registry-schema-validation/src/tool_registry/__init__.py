"""Typed tool registry with a pure JSON Schema subset validator."""

from __future__ import annotations

from .registry import (
    DEFAULT_TIMEOUT_MS,
    Ok,
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
    ToolRecord,
    ToolRegistry,
)
from .schema import SchemaError, ValidationError, check_schema, validate_instance

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "Ok",
    "SchemaError",
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolRecord",
    "ToolRegistry",
    "ValidationError",
    "check_schema",
    "validate_instance",
]

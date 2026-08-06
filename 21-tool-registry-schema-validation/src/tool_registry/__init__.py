"""Typed tool registry with a pure JSON Schema subset validator."""

from __future__ import annotations

from .schema import SchemaError, ValidationError, check_schema, validate_instance

__version__ = "0.1.0"

__all__ = [
    "SchemaError",
    "ValidationError",
    "check_schema",
    "validate_instance",
]

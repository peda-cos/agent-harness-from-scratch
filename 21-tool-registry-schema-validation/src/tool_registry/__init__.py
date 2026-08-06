"""Typed tool registry with a pure JSON Schema subset validator."""

from __future__ import annotations

from .schema import ValidationError, validate_instance

__version__ = "0.1.0"

__all__ = [
    "ValidationError",
    "validate_instance",
]

"""Typed registry, error envelope, and budget primitives."""

from __future__ import annotations

from .budget import Budget, ToolCallBudget
from .errors import (
    JSONRPC_CODES,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    DispatchError,
    ErrorKind,
    TransientError,
)
from .registry import (
    DEFAULT_TIMEOUT_MS,
    Registry,
    ToolNotFoundError,
    ToolRecord,
    ToolRegistry,
    ValidationIssue,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "JSONRPC_CODES",
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_METHOD_NOT_FOUND",
    "Budget",
    "DispatchError",
    "ErrorKind",
    "Registry",
    "ToolCallBudget",
    "ToolNotFoundError",
    "ToolRecord",
    "ToolRegistry",
    "TransientError",
    "ValidationIssue",
    "__version__",
]

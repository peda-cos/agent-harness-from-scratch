"""A development-time subprocess guardrail with a denylist and a path jail."""

from __future__ import annotations

from .models import (
    DEFAULT_DENYLIST,
    DEFAULT_INTERPRETER_BLOCK,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    DENIED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    TRUNCATION_MARKER,
    SandboxConfig,
    SandboxResult,
    truncate_bytes,
)
from .sandbox import SHELL_METACHARS, Sandbox

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_DENYLIST",
    "DEFAULT_INTERPRETER_BLOCK",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "DENIED_EXIT_CODE",
    "SHELL_METACHARS",
    "TIMEOUT_EXIT_CODE",
    "TRUNCATION_MARKER",
    "Sandbox",
    "SandboxConfig",
    "SandboxResult",
    "__version__",
    "truncate_bytes",
]

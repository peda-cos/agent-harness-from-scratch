"""The dispatcher seam and a compact local tool fixture."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias

ToolHandler: TypeAlias = Callable[..., object]


class ToolError(RuntimeError):
    """A tool-level failure that the executor hands to the planner."""


class Dispatcher(Protocol):
    """The replaceable execution seam. Real adapters own I/O and retries."""

    def dispatch(self, tool_name: str, args: dict[str, object]) -> object:
        """Execute one tool call or raise an exception."""
        ...


class Toolbox:
    """A small synchronous registry for deterministic local tools."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolHandler, tuple[str, ...]]] = {}

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        required: Sequence[str] = (),
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("tool name must be a non-empty string")
        if not callable(handler):
            raise ValueError("tool handler must be callable")
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered")
        required_keys = tuple(required)
        for key in required_keys:
            if not isinstance(key, str) or not key:
                raise ValueError("required keys must be non-empty strings")
        self._tools[name] = (handler, required_keys)

    def dispatch(self, tool_name: str, args: dict[str, object]) -> object:
        entry = self._tools.get(tool_name)
        if entry is None:
            raise ToolError(f"unknown tool '{tool_name}'")
        handler, required = entry
        call_args = dict(args)
        missing = [key for key in required if key not in call_args]
        if missing:
            raise ToolError(
                f"tool '{tool_name}' missing required args: {', '.join(missing)}"
            )
        return handler(**call_args)


__all__ = ["Dispatcher", "ToolHandler", "ToolError", "Toolbox"]

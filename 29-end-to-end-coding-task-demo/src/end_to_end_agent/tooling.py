"""The tool contract, path policy, and deterministic project listing."""

from __future__ import annotations

import os
from pathlib import Path

from .models import ToolCall

ALLOWED_TOOLS = frozenset({"read_file", "run_tests", "write_file"})
_EXPECTED_KEYS: dict[str, frozenset[str]] = {
    "read_file": frozenset({"path"}),
    "run_tests": frozenset(),
    "write_file": frozenset({"path", "content"}),
}
_SKIP_DIRECTORIES = frozenset({"__pycache__", "expected"})


class PathViolation(ValueError):
    """A tool path violates the sandbox's path policy."""


def validate_arguments(call: ToolCall) -> dict[str, str]:
    """Validate and normalize the arguments for one known tool call."""

    expected = _EXPECTED_KEYS.get(call.name)
    if expected is None:
        raise ValueError(f"unknown tool: {call.name!r}")
    if set(call.arguments) != set(expected):
        raise ValueError(
            f"tool {call.name!r} expects arguments {sorted(expected)}; "
            f"got {sorted(call.arguments)}"
        )

    normalized: dict[str, str] = {}
    for key in sorted(expected):
        value = call.arguments[key]
        if not isinstance(value, str):
            raise ValueError(f"argument {key!r} must be a string")
        if "\x00" in value:
            raise ValueError(f"argument {key!r} contains a NUL byte")
        normalized[key] = value
    if "path" in normalized and not normalized["path"].strip():
        raise ValueError("path must be a non-empty string")
    return normalized


def resolve_inside(root: str | Path, relative: str, *, allow_root: bool) -> Path:
    """Resolve a relative path and require it to stay inside ``root``."""

    if not relative or "\x00" in relative:
        raise PathViolation("path must be non-empty and cannot contain NUL")
    if relative.startswith("~") or "\\" in relative:
        raise PathViolation(f"path {relative!r} uses a forbidden prefix or separator")

    candidate = Path(relative)
    if candidate.is_absolute():
        raise PathViolation(f"absolute paths are not allowed: {relative!r}")
    if ".." in candidate.parts:
        raise PathViolation(f"parent traversal is not allowed: {relative!r}")
    if "expected" in candidate.parts:
        raise PathViolation(f"the expected tree is not readable by the agent: {relative!r}")

    root_path = Path(root).resolve()
    resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise PathViolation(f"path escapes project root: {relative!r}") from exc
    if not allow_root and resolved == root_path:
        raise PathViolation("the project root is not a writable file path")
    return resolved


def validate_read_path(root: str | Path, relative: str) -> Path:
    """Resolve a read target; only ``.`` may refer to the project root."""

    return resolve_inside(root, relative, allow_root=relative == ".")


def validate_write_path(root: str | Path, relative: str) -> Path:
    """Resolve a write target and restrict writes to ``src/``."""

    resolved = resolve_inside(root, relative, allow_root=False)
    parts = Path(relative).parts
    if not parts or parts[0] != "src":
        raise PathViolation("writes are restricted to the src/ directory")
    return resolved


def tree_listing(root: str | Path) -> str:
    """Return a stable, sorted listing of files in the scratch repository."""

    root_path = Path(root).resolve()
    entries: list[str] = []
    for directory, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in _SKIP_DIRECTORIES
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = Path(directory) / filename
            entries.append(path.relative_to(root_path).as_posix())
    if not entries:
        return "(empty)\n"
    return "\n".join(sorted(entries)) + "\n"


__all__ = [
    "ALLOWED_TOOLS",
    "PathViolation",
    "resolve_inside",
    "tree_listing",
    "validate_arguments",
    "validate_read_path",
    "validate_write_path",
]

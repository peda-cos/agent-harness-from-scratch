"""Deterministic verifier registry and built-in verifier functions."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from .models import FixtureError, FixtureTask, safe_child

Verifier = Callable[[FixtureTask, Path, Mapping[str, object]], bool]

_DENIED_EXECUTABLES = frozenset(
    {
        "bash",
        "chmod",
        "chown",
        "dash",
        "dd",
        "doas",
        "kill",
        "mkfs",
        "mkfs.ext4",
        "mount",
        "pkill",
        "poweroff",
        "reboot",
        "rm",
        "rmdir",
        "sh",
        "shutdown",
        "su",
        "sudo",
        "truncate",
        "umount",
        "zsh",
    }
)
_DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 60.0


class VerifierRegistry:
    """Map fixture-declared verifier names to deterministic callables."""

    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register(self, name: str, verifier: Verifier) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("verifier name must be a non-empty string")
        if not callable(verifier):
            raise ValueError("verifier must be callable")
        normalized = name.strip()
        if normalized in self._verifiers:
            raise ValueError(f"verifier already registered: {normalized}")
        self._verifiers[normalized] = verifier

    def resolve(self, name: str) -> Verifier:
        try:
            return self._verifiers[name]
        except KeyError:
            available = ", ".join(self.names()) or "(none)"
            raise FixtureError(
                f"unknown verifier {name!r}; registered verifiers: {available}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))


def _reject_unknown(params: Mapping[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise FixtureError(f"unknown verifier params: {unknown}")


def _string_param(params: Mapping[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise FixtureError(f"verifier param {key!r} must be a non-empty string")
    return value


def _command_param(params: Mapping[str, object]) -> list[str]:
    value = params.get("command")
    if not isinstance(value, list) or not value:
        raise FixtureError("verifier param 'command' must be a non-empty list")
    command: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise FixtureError("verifier command entries must be non-empty strings")
        command.append(item)
    return command


def _timeout_param(params: Mapping[str, object]) -> float:
    value = params.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FixtureError("verifier param 'timeout_seconds' must be a number")
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout <= 0.0
        or timeout > _MAX_TIMEOUT_SECONDS
    ):
        raise FixtureError(
            f"'timeout_seconds' must be in (0, {_MAX_TIMEOUT_SECONDS}]"
        )
    return timeout


def file_equals(
    task: FixtureTask, scratch: Path, params: Mapping[str, object]
) -> bool:
    """Pass when the named scratch file exactly equals the expected artifact."""

    _reject_unknown(params, {"path", "expected"})
    relative = _string_param(params, "path")
    expected_relative = _string_param(params, "expected")
    output_path = safe_child(scratch, relative)
    expected_path = safe_child(task.root, expected_relative)

    try:
        expected = expected_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FixtureError(f"cannot read expected file {expected_path}: {exc}") from exc

    try:
        actual = output_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return actual == expected


def regex_match(
    task: FixtureTask, scratch: Path, params: Mapping[str, object]
) -> bool:
    """Pass when the named scratch file contains a regex match."""

    _reject_unknown(params, {"path", "pattern"})
    relative = _string_param(params, "path")
    raw_pattern = _string_param(params, "pattern")
    try:
        pattern = re.compile(raw_pattern)
    except re.error as exc:
        raise FixtureError(f"invalid regex {raw_pattern!r}: {exc}") from exc

    output_path = safe_child(scratch, relative)
    try:
        text = output_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return bool(pattern.search(text))


def shell_exit_zero(
    task: FixtureTask, scratch: Path, params: Mapping[str, object]
) -> bool:
    """Pass when an argv command completes with exit code zero."""

    _reject_unknown(params, {"command", "timeout_seconds"})
    command = _command_param(params)
    timeout = _timeout_param(params)
    argv = [sys.executable if item == "{python}" else item for item in command]

    executable = Path(argv[0]).name.lower()
    if executable in _DENIED_EXECUTABLES:
        raise FixtureError(f"verifier command is denylisted: {argv[0]!r}")

    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(scratch),
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        completed = subprocess.run(
            argv,
            cwd=scratch,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot run verifier command {argv!r}: {exc}") from exc
    return completed.returncode == 0


def builtin_registry() -> VerifierRegistry:
    """Return a registry containing only the three built-in verifiers."""

    registry = VerifierRegistry()
    registry.register("file_equals", file_equals)
    registry.register("regex_match", regex_match)
    registry.register("shell_exit_zero", shell_exit_zero)
    return registry

"""Structured configuration, results, and output truncation for the sandbox runner."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DENIED_EXIT_CODE = -100
TIMEOUT_EXIT_CODE = -101
TRUNCATION_MARKER = b"\n...[truncated]...\n"
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0

DEFAULT_DENYLIST = frozenset(
    {
        "busybox",
        "chgrp",
        "chmod",
        "chown",
        "chroot",
        "crontab",
        "dd",
        "doas",
        "fdisk",
        "groupadd",
        "groupdel",
        "halt",
        "insmod",
        "kill",
        "killall",
        "mkfs",
        "mkfs.btrfs",
        "mkfs.ext4",
        "mkfs.fat",
        "mkfs.vfat",
        "mkfs.xfs",
        "mke2fs",
        "mkswap",
        "modprobe",
        "mount",
        "nsenter",
        "parted",
        "passwd",
        "pkill",
        "poweroff",
        "reboot",
        "rm",
        "rmdir",
        "rmmod",
        "runuser",
        "service",
        "shred",
        "shutdown",
        "su",
        "sudo",
        "swapoff",
        "swapon",
        "sysctl",
        "systemctl",
        "umount",
        "unshare",
        "useradd",
        "userdel",
        "usermod",
        "visudo",
        "wipefs",
    }
)

DEFAULT_INTERPRETER_BLOCK = frozenset(
    {
        "ash",
        "bash",
        "bun",
        "busybox",
        "cmd",
        "csh",
        "dash",
        "deno",
        "fish",
        "ksh",
        "lua",
        "luajit",
        "mksh",
        "node",
        "nodejs",
        "osascript",
        "perl",
        "perl6",
        "php",
        "powershell",
        "pwsh",
        "python",
        "python3",
        "raku",
        "ruby",
        "sh",
        "tclsh",
        "tcsh",
        "wish",
        "zsh",
    }
)


def truncate_bytes(data: bytes, limit: int) -> tuple[bytes, bool]:
    """Clip bytes to a hard ceiling, preserving the beginning and the end."""

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if len(data) <= limit:
        return data, False
    if limit == 0:
        return b"", True
    if limit <= len(TRUNCATION_MARKER):
        return data[:limit], True

    body = limit - len(TRUNCATION_MARKER)
    head = (body + 1) // 2
    tail = body - head
    clipped = data[:head] + TRUNCATION_MARKER
    if tail:
        clipped += data[-tail:]
    return clipped, True


def _freeze_names(field: str, values: object) -> frozenset[str]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise ValueError(f"{field} must be an iterable of command names")
    names: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field} entries must be strings")
        name = value.strip().lower()
        if not name:
            raise ValueError(f"{field} entries must be non-empty")
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class SandboxConfig:
    """Immutable policy and resource limits for a sandbox instance."""

    project_root: Path
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    denylist: frozenset[str] = DEFAULT_DENYLIST
    interpreter_block: frozenset[str] = DEFAULT_INTERPRETER_BLOCK

    def __post_init__(self) -> None:
        try:
            raw_root = os.fspath(self.project_root)
        except TypeError as exc:
            raise ValueError("project_root must be a path") from exc
        if not isinstance(raw_root, str):
            raise ValueError("project_root must be a string path")
        root = os.path.realpath(raw_root)
        if not os.path.isdir(root):
            raise ValueError(
                f"project_root must be an existing directory: {raw_root}"
            )
        object.__setattr__(self, "project_root", Path(root))

        if isinstance(self.max_output_bytes, bool) or not isinstance(
            self.max_output_bytes, int
        ):
            raise ValueError("max_output_bytes must be an integer")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")

        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be a number")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        object.__setattr__(
            self, "denylist", _freeze_names("denylist", self.denylist)
        )
        object.__setattr__(
            self,
            "interpreter_block",
            _freeze_names("interpreter_block", self.interpreter_block),
        )


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """The structured outcome of one sandboxed invocation."""

    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
    timed_out: bool
    denied: bool
    reason: str | None
    duration_ms: float

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        for name in ("truncated", "timed_out", "denied"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise ValueError("stdout and stderr must be strings")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason
        ):
            raise ValueError("reason must be a non-empty string or None")
        if isinstance(self.duration_ms, bool) or not isinstance(
            self.duration_ms, (int, float)
        ):
            raise ValueError("duration_ms must be a number")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        object.__setattr__(self, "duration_ms", float(self.duration_ms))

        if self.denied:
            if self.exit_code != DENIED_EXIT_CODE:
                raise ValueError("a denied result must use DENIED_EXIT_CODE")
            if not self.reason:
                raise ValueError("a denied result requires a reason")
            if self.timed_out:
                raise ValueError("a result cannot be denied and timed out")
            if self.truncated:
                raise ValueError("a denied result cannot be truncated")
        if self.timed_out:
            if self.exit_code != TIMEOUT_EXIT_CODE:
                raise ValueError("a timed-out result must use TIMEOUT_EXIT_CODE")
            if not self.reason:
                raise ValueError("a timed-out result requires a reason")
            if self.denied:
                raise ValueError("a result cannot be timed out and denied")

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "truncated": self.truncated,
            "timed_out": self.timed_out,
            "denied": self.denied,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


__all__ = [
    "DEFAULT_DENYLIST",
    "DEFAULT_INTERPRETER_BLOCK",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "DENIED_EXIT_CODE",
    "TIMEOUT_EXIT_CODE",
    "TRUNCATION_MARKER",
    "SandboxConfig",
    "SandboxResult",
    "truncate_bytes",
]

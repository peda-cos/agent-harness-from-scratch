"""Minimal tool sandbox: argument schema, path jail, fixed test command."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .models import RunLimits, ToolCall
from .tooling import (
    PathViolation,
    tree_listing,
    validate_arguments,
    validate_read_path,
    validate_write_path,
)

DENIED_EXIT_CODE = -100
TIMEOUT_EXIT_CODE = -101
TRUNCATION_MARKER = "\n...[truncated]...\n"
_POST_KILL_DRAIN_SECONDS = 1.0


def _cap_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARKER):
        return text[:limit]
    body = limit - len(TRUNCATION_MARKER)
    head = (body + 1) // 2
    tail = body - head
    clipped = text[:head] + TRUNCATION_MARKER
    if tail:
        clipped += text[-tail:]
    return clipped


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """The structured outcome of one sandboxed tool execution."""

    exit_code: int
    output: str
    denied: bool
    timed_out: bool
    reason: str | None
    duration_ms: float

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        if not isinstance(self.output, str):
            raise ValueError("output must be a string")
        if not isinstance(self.denied, bool) or not isinstance(self.timed_out, bool):
            raise ValueError("denied and timed_out must be booleans")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason
        ):
            raise ValueError("reason must be a non-empty string or None")
        if isinstance(self.duration_ms, bool) or not isinstance(
            self.duration_ms, (int, float)
        ):
            raise ValueError("duration_ms must be a number")
        duration = float(self.duration_ms)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("duration_ms must be finite and non-negative")
        object.__setattr__(self, "duration_ms", duration)
        if self.denied and self.timed_out:
            raise ValueError("a result cannot be denied and timed out")
        if (self.denied or self.timed_out) and not self.reason:
            raise ValueError("a refused result requires a reason")

    @property
    def ok(self) -> bool:
        return not self.denied and not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "output": self.output,
            "denied": self.denied,
            "timed_out": self.timed_out,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


class Sandbox:
    """Execute a fixed set of file and test tools inside one scratch root."""

    def __init__(self, root: str | Path, limits: RunLimits) -> None:
        if not isinstance(limits, RunLimits):
            raise TypeError("limits must be a RunLimits")
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise ValueError(f"sandbox root must be an existing directory: {root}")
        self.root = root_path
        self.limits = limits

    def execute(self, call: ToolCall) -> SandboxResult:
        started = time.perf_counter()
        try:
            arguments = validate_arguments(call)
        except ValueError as exc:
            return self._denied(str(exc), started)

        if call.name == "read_file":
            return self._read(arguments["path"], started)
        if call.name == "write_file":
            return self._write(arguments["path"], arguments["content"], started)
        if call.name == "run_tests":
            return self._run_tests(started)
        return self._denied(f"unknown tool: {call.name!r}", started)

    def _read(self, relative: str, started: float) -> SandboxResult:
        try:
            path = validate_read_path(self.root, relative)
        except PathViolation as exc:
            return self._denied(str(exc), started)
        try:
            if path.is_dir():
                return self._ok(tree_listing(self.root), started)
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return self._error(f"read_file failed: {exc}", started)
        return self._ok(_cap_text(text, self.limits.max_output_bytes), started)

    def _write(self, relative: str, content: str, started: float) -> SandboxResult:
        try:
            path = validate_write_path(self.root, relative)
        except PathViolation as exc:
            return self._denied(str(exc), started)
        try:
            path.write_text(content, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return self._error(f"write_file failed: {exc}", started)
        size = len(content.encode("utf-8"))
        return self._ok(f"wrote {size} bytes to {relative}", started)

    def _run_tests(self, started: float) -> SandboxResult:
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ]
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "TEMP": str(self.root),
            "TMP": str(self.root),
            "PYTHONPATH": "",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            return self._error(f"could not start tests: {exc}", started, code=127)

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = process.communicate(
                timeout=self.limits.test_timeout_seconds
            )
        except subprocess.TimeoutExpired:
            self._kill_process_group(process)
            stdout_bytes, stderr_bytes = self._drain(process)
            timed_out = True

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if timed_out:
            output = (
                f"timed out after {self.limits.test_timeout_seconds:g}s\n"
                f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
            )
            return SandboxResult(
                exit_code=TIMEOUT_EXIT_CODE,
                output=_cap_text(output, self.limits.max_output_bytes),
                denied=False,
                timed_out=True,
                reason=(
                    f"wall-clock timeout after "
                    f"{self.limits.test_timeout_seconds:g}s"
                ),
                duration_ms=self._elapsed_ms(started),
            )

        exit_code = process.returncode if process.returncode is not None else 1
        output = (
            f"exit_code={exit_code}\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        )
        return SandboxResult(
            exit_code=exit_code,
            output=_cap_text(output, self.limits.max_output_bytes),
            denied=False,
            timed_out=False,
            reason=None,
            duration_ms=self._elapsed_ms(started),
        )

    def _kill_process_group(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "posix":
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
            except (AttributeError, OSError):
                pass
        try:
            process.kill()
        except OSError:
            pass

    def _drain(
        self, process: subprocess.Popen[bytes]
    ) -> tuple[bytes, bytes]:
        try:
            stdout_bytes, stderr_bytes = process.communicate(
                timeout=_POST_KILL_DRAIN_SECONDS
            )
        except subprocess.TimeoutExpired:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            try:
                process.wait(timeout=_POST_KILL_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            return b"", b""
        return stdout_bytes or b"", stderr_bytes or b""

    def _ok(self, output: str, started: float) -> SandboxResult:
        return SandboxResult(
            exit_code=0,
            output=output,
            denied=False,
            timed_out=False,
            reason=None,
            duration_ms=self._elapsed_ms(started),
        )

    def _denied(self, reason: str, started: float) -> SandboxResult:
        return SandboxResult(
            exit_code=DENIED_EXIT_CODE,
            output="",
            denied=True,
            timed_out=False,
            reason=reason,
            duration_ms=self._elapsed_ms(started),
        )

    def _error(
        self, reason: str, started: float, *, code: int = 1
    ) -> SandboxResult:
        return SandboxResult(
            exit_code=code,
            output=reason,
            denied=False,
            timed_out=False,
            reason=None,
            duration_ms=self._elapsed_ms(started),
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((time.perf_counter() - started) * 1000.0, 3)


__all__ = [
    "DENIED_EXIT_CODE",
    "TIMEOUT_EXIT_CODE",
    "Sandbox",
    "SandboxResult",
]

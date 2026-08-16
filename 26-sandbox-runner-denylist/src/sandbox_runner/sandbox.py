"""A development-time subprocess guardrail with a denylist and a path jail."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .models import (
    DENIED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    SandboxConfig,
    SandboxResult,
    truncate_bytes,
)

SHELL_METACHARS = (";", "|", "&", ">", "<", "`", "$(", "\n", "\r")
INLINE_CODE_FLAGS = frozenset({"-", "--eval", "--exec", "--print", "--run"})
INLINE_CODE_PREFIXES = ("-c", "-e", "-m", "-p", "-r")
SHELL_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "\n"})
SHELL_PREFIX_KEYWORDS = frozenset(
    {
        "!",
        "case",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "fi",
        "for",
        "function",
        "if",
        "in",
        "then",
        "time",
        "until",
        "while",
    }
)
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset(
    {"command", "env", "exec", "nice", "nohup", "setsid", "stdbuf", "timeout"}
)
_ENV_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})
_NICE_VALUE_OPTIONS = frozenset({"-n", "--adjustment"})
_TIMEOUT_VALUE_OPTIONS = frozenset(
    {"-s", "--signal", "-k", "--kill-after"}
)
_STDBUF_VALUE_OPTIONS = frozenset(
    {"-i", "-o", "-e", "--input", "--output", "--error"}
)
_EXEC_VALUE_OPTIONS = frozenset({"-a"})
_ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LANGUAGE",
    "TZ",
    "TERM",
    "COLUMNS",
    "LINES",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
)
_POST_KILL_DRAIN_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _Invocation:
    """The normalized and inspected shape of one call."""

    tokens: tuple[str, ...]
    command_positions: tuple[int, ...]
    path_args: tuple[str, ...]
    command: tuple[str, ...] | str
    shell: bool


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _command_name(value: str) -> str:
    name = os.path.basename(value).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _is_interpreter(name: str, block: frozenset[str]) -> bool:
    if name in block:
        return True
    for entry in block:
        if not name.startswith(entry):
            continue
        suffix = name[len(entry) :]
        parts = [part for part in suffix.strip(".").split(".") if part]
        if parts and all(part.isdigit() for part in parts):
            return True
    return False


def _is_inline_flag(token: str) -> bool:
    return token in INLINE_CODE_FLAGS or token.startswith(INLINE_CODE_PREFIXES)


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _command_positions(tokens: Sequence[str]) -> tuple[int, ...]:
    positions: list[int] = []
    index = 0
    length = len(tokens)
    while index < length:
        if index == 0 or tokens[index - 1] in SHELL_SEPARATORS:
            cursor = index
            while cursor < length and _ASSIGNMENT.match(tokens[cursor]):
                cursor += 1
            while cursor < length and tokens[cursor] in SHELL_PREFIX_KEYWORDS:
                cursor += 1
            if cursor < length:
                positions.append(cursor)
            index = cursor + 1
        else:
            index += 1
    return tuple(positions)


def _skip_options(
    tokens: Sequence[str], cursor: int, value_options: frozenset[str]
) -> int:
    """Advance past wrapper options, consuming values only when required."""

    while cursor < len(tokens):
        token = tokens[cursor]
        if token == "--":
            return cursor + 1
        if not token.startswith("-") or token == "-":
            return cursor
        cursor += 1
        if "=" not in token and token in value_options:
            cursor += 1
    return cursor


def _wrapper_target(
    tokens: Sequence[str], index: int
) -> tuple[int | None, str | None]:
    """Return the command a known wrapper executes, or a refusal reason."""

    name = _command_name(tokens[index])
    if name not in _WRAPPERS:
        return None, None
    cursor = index + 1

    if name == "env":
        while cursor < len(tokens):
            token = tokens[cursor]
            if token == "--":
                cursor += 1
                break
            if token.startswith("-") and token != "-":
                combined = not token.startswith("--") and "S" in token[1:]
                if token == "-S" or token.startswith("--split-string") or combined:
                    return (
                        None,
                        f"wrapper 'env' option {token!r} cannot be inspected",
                    )
                option, separator, _ = token.partition("=")
                cursor += 1
                if not separator and option in _ENV_VALUE_OPTIONS:
                    cursor += 1
                continue
            if _ASSIGNMENT.match(token):
                cursor += 1
                continue
            break
        return (cursor if cursor < len(tokens) else None), None

    if name == "command":
        while cursor < len(tokens):
            token = tokens[cursor]
            if token in {"-v", "-V"}:
                return None, None
            if not token.startswith("-") or token == "-":
                break
            cursor += 1
        return (cursor if cursor < len(tokens) else None), None

    if name == "timeout":
        cursor = _skip_options(tokens, cursor, _TIMEOUT_VALUE_OPTIONS)
        cursor += 1
        return (cursor if cursor < len(tokens) else None), None

    value_options = {
        "nice": _NICE_VALUE_OPTIONS,
        "stdbuf": _STDBUF_VALUE_OPTIONS,
        "exec": _EXEC_VALUE_OPTIONS,
    }.get(name, frozenset())
    cursor = _skip_options(tokens, cursor, value_options)
    return (cursor if cursor < len(tokens) else None), None


def _expanded_command_positions(
    tokens: Sequence[str], positions: Sequence[int]
) -> tuple[tuple[int, ...], str | None]:
    """Unwrap commands that execute another command, preserving order."""

    expanded: list[int] = []
    pending = list(positions)
    seen: set[int] = set()
    while pending:
        position = pending.pop(0)
        if position in seen or position >= len(tokens):
            continue
        seen.add(position)
        expanded.append(position)
        target, reason = _wrapper_target(tokens, position)
        if reason is not None:
            return tuple(expanded), reason
        if target is not None:
            pending.append(target)
    return tuple(expanded), None


class Sandbox:
    """Run one call after four pure refusal axes have passed."""

    def __init__(self, config: SandboxConfig) -> None:
        if not isinstance(config, SandboxConfig):
            raise TypeError("config must be a SandboxConfig")
        self.config = config

    def run(
        self,
        argv: Sequence[str] | str,
        *,
        shell: bool = False,
        cwd: str | os.PathLike[str] | None = None,
    ) -> SandboxResult:
        started = time.perf_counter()
        if not _is_bool(shell):
            return self._deny("shell must be a boolean", started)

        invocation, reason = self._normalize(argv, shell)
        if invocation is None:
            return self._deny(reason or "invalid invocation", started)

        reason = self._check_executable_denylist(
            invocation.tokens,
            invocation.command_positions,
            shell=invocation.shell,
        )
        if reason is not None:
            return self._deny(reason, started)

        reason = self._check_argv_interpreter(
            invocation.tokens,
            invocation.command_positions,
            shell=invocation.shell,
        )
        if reason is not None:
            return self._deny(reason, started)

        if not invocation.shell:
            reason = self._check_shell_metachars(invocation.tokens)
            if reason is not None:
                return self._deny(reason, started)

        resolved_cwd, reason = self._resolve_cwd(cwd)
        if resolved_cwd is None:
            return self._deny(reason or "invalid cwd", started)

        reason = self._check_path_jail(invocation.path_args, resolved_cwd)
        if reason is not None:
            return self._deny(reason, started)

        return self._spawn(invocation, resolved_cwd, started)

    def _normalize(
        self, argv: object, shell: bool
    ) -> tuple[_Invocation | None, str | None]:
        if shell:
            return self._normalize_shell(argv)
        return self._normalize_exec(argv)

    def _normalize_exec(
        self, argv: object
    ) -> tuple[_Invocation | None, str | None]:
        if isinstance(argv, str):
            return None, "argv must be a sequence of strings"
        if isinstance(argv, (bytes, bytearray)) or not isinstance(argv, Sequence):
            return None, "argv must be a sequence of strings"
        if len(argv) == 0:
            return None, "argv cannot be empty"
        tokens: list[str] = []
        for entry in argv:
            if not isinstance(entry, str):
                return None, "argv entries must be strings"
            if "\x00" in entry:
                return None, "argument contains a NUL byte"
            tokens.append(entry)
        if not tokens[0]:
            return None, "executable name cannot be empty"
        return (
            _Invocation(
                tokens=tuple(tokens),
                command_positions=(0,),
                path_args=tuple(tokens[1:]),
                command=tuple(tokens),
                shell=False,
            ),
            None,
        )

    def _normalize_shell(
        self, argv: object
    ) -> tuple[_Invocation | None, str | None]:
        if isinstance(argv, str):
            if not argv:
                return None, "shell command is empty"
            if "\x00" in argv:
                return None, "argument contains a NUL byte"
            command = argv
        else:
            if isinstance(argv, (bytes, bytearray)) or not isinstance(
                argv, Sequence
            ):
                return None, "argv must be a sequence of strings"
            if len(argv) == 0:
                return None, "argv cannot be empty"
            entries: list[str] = []
            for entry in argv:
                if not isinstance(entry, str):
                    return None, "argv entries must be strings"
                if "\x00" in entry:
                    return None, "argument contains a NUL byte"
                entries.append(entry)
            if len(entries) == 1:
                command = entries[0]
                if not command:
                    return None, "shell command is empty"
            else:
                command = shlex.join(entries)

        try:
            tokens = _shell_tokens(command)
        except ValueError as exc:
            return None, f"shell command is not parseable: {exc}"
        if not tokens:
            return None, "shell command is empty"

        positions = _command_positions(tokens)
        position_set = set(positions)
        path_args = tuple(
            token
            for index, token in enumerate(tokens)
            if index not in position_set
        )
        return (
            _Invocation(
                tokens=tuple(tokens),
                command_positions=positions,
                path_args=path_args,
                command=command,
                shell=True,
            ),
            None,
        )

    def _check_executable_denylist(
        self,
        tokens: Sequence[str],
        command_positions: Sequence[int],
        *,
        shell: bool,
    ) -> str | None:
        positions, wrapper_reason = _expanded_command_positions(
            tokens, command_positions
        )
        if wrapper_reason is not None:
            if shell:
                wrapper_reason += " in shell command"
            return wrapper_reason
        for position in positions:
            if position >= len(tokens):
                continue
            name = _command_name(tokens[position])
            if name in self.config.denylist:
                reason = f"executable '{name}' is denylisted"
                if shell:
                    reason += " in shell command"
                return reason
        return None

    def _check_argv_interpreter(
        self,
        tokens: Sequence[str],
        command_positions: Sequence[int],
        *,
        shell: bool,
    ) -> str | None:
        positions, wrapper_reason = _expanded_command_positions(
            tokens, command_positions
        )
        if wrapper_reason is not None:
            if shell:
                wrapper_reason += " in shell command"
            return wrapper_reason
        for position in positions:
            if position >= len(tokens):
                continue
            name = _command_name(tokens[position])
            if not _is_interpreter(name, self.config.interpreter_block):
                continue
            index = position + 1
            while index < len(tokens):
                token = tokens[index]
                if shell and token in SHELL_SEPARATORS:
                    break
                if _is_inline_flag(token):
                    return (
                        f"interpreter '{name}' with inline-code flag "
                        f"'{token}' is denied"
                    )
                index += 1
        return None

    def _check_shell_metachars(self, tokens: Sequence[str]) -> str | None:
        for token in tokens:
            for meta in SHELL_METACHARS:
                if meta in token:
                    return f"shell metacharacter in argument: {token!r}"
        return None

    def _resolve_cwd(
        self, cwd: Any
    ) -> tuple[str | None, str | None]:
        if cwd is None:
            return str(self.config.project_root), None
        try:
            raw = os.fspath(cwd)
        except TypeError:
            return None, "cwd must be a string or path-like object"
        if not isinstance(raw, str):
            return None, "cwd must be a string or path-like object"
        if not raw:
            return None, "cwd cannot be empty"
        expanded = self._expand_home(raw)
        if expanded is None:
            return None, f"home-relative path {raw!r} is not allowed"
        candidate = expanded
        if not os.path.isabs(candidate):
            candidate = os.path.join(str(self.config.project_root), candidate)
        resolved = os.path.realpath(candidate)
        if not self._is_inside(resolved):
            return None, f"cwd {raw!r} escapes project root: {resolved}"
        if not os.path.isdir(resolved):
            return None, f"cwd {raw!r} is not an existing directory"
        return resolved, None

    def _check_path_jail(
        self, path_args: Sequence[str], cwd: str
    ) -> str | None:
        for arg in path_args:
            for value in self._path_values(arg):
                if not self._looks_like_path(value, cwd):
                    continue
                expanded = self._expand_home(value)
                if expanded is None:
                    return f"home-relative path {value!r} is not allowed"
                candidate = expanded
                if not os.path.isabs(candidate):
                    candidate = os.path.join(cwd, candidate)
                resolved = os.path.realpath(candidate)
                if not self._is_inside(resolved):
                    return f"path {value!r} escapes project root: {resolved}"
        return None

    def _path_values(self, arg: str) -> tuple[str, ...]:
        values = [arg]
        if "=" in arg:
            values.append(arg.split("=", 1)[1])
        if arg.startswith("-"):
            if "/" in arg:
                index = arg.find("/")
                prefix = arg[:index]
                # Attached ../ and ./ paths are handled by the relative
                # candidate below; treating their slash as absolute is wrong.
                if index > 0 and not prefix.endswith((".", "..")):
                    values.append(arg[index:])
            if ".." in arg:
                index = arg.find("..")
                if index > 0:
                    values.append(arg[index:])
        return tuple(values)

    def _looks_like_path(self, value: str, cwd: str) -> bool:
        if not value:
            return False
        if value.startswith("~"):
            return True
        if value in (".", ".."):
            return True
        if value.startswith(("./", "../", ".\\", "..\\")):
            return True
        if os.path.isabs(value):
            return True
        if "/" in value:
            return True
        if os.sep != "/" and os.sep in value:
            return True
        if os.altsep is not None and os.altsep in value:
            return True
        return os.path.lexists(os.path.join(cwd, value))

    def _expand_home(self, value: str) -> str | None:
        root = str(self.config.project_root)
        if value == "~":
            return root
        if value.startswith("~/"):
            return root + value[1:]
        if os.sep != "/" and value.startswith("~\\"):
            return root + value[1:]
        if value.startswith("~"):
            return None
        return value

    def _is_inside(self, path: str) -> bool:
        root = str(self.config.project_root)
        try:
            common = os.path.commonpath([root, path])
        except ValueError:
            return False
        return os.path.normcase(common) == os.path.normcase(root)

    def _child_env(self, cwd: str) -> dict[str, str]:
        env = {
            key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ
        }
        env.setdefault("PATH", os.defpath)
        root = str(self.config.project_root)
        env["HOME"] = root
        env["TMPDIR"] = root
        env["TMP"] = root
        env["TEMP"] = root
        env["PWD"] = cwd
        return env

    def _spawn(
        self, invocation: _Invocation, cwd: str, started: float
    ) -> SandboxResult:
        executable = invocation.tokens[0]
        try:
            proc = subprocess.Popen(
                invocation.command,
                shell=invocation.shell,
                cwd=cwd,
                env=self._child_env(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
        except (FileNotFoundError, NotADirectoryError):
            return self._spawn_failure(
                127,
                f"sandbox: executable not found: {executable}",
                started,
            )
        except PermissionError:
            return self._spawn_failure(
                126,
                f"sandbox: permission denied: {executable}",
                started,
            )
        except OSError as exc:
            return self._spawn_failure(
                1,
                f"sandbox: could not execute {executable}: {exc}",
                started,
            )

        timed_out = False
        reason: str | None = None
        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=self.config.timeout_seconds
            )
            exit_code = proc.returncode if proc.returncode is not None else 1
        except subprocess.TimeoutExpired:
            self._kill_process_group(proc)
            stdout_bytes, stderr_bytes = self._drain_after_timeout(proc)
            exit_code = TIMEOUT_EXIT_CODE
            timed_out = True
            reason = (
                f"wall-clock timeout after "
                f"{self.config.timeout_seconds:g}s"
            )

        return self._result_from_output(
            exit_code,
            stdout_bytes or b"",
            stderr_bytes or b"",
            timed_out,
            reason,
            started,
        )

    def _kill_process_group(self, proc: subprocess.Popen[bytes]) -> None:
        if os.name == "posix":
            try:
                if os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGKILL)
            except (AttributeError, OSError):
                pass
        try:
            proc.kill()
        except OSError:
            pass

    def _drain_after_timeout(
        self, proc: subprocess.Popen[bytes]
    ) -> tuple[bytes, bytes]:
        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=_POST_KILL_DRAIN_SECONDS
            )
            return stdout_bytes or b"", stderr_bytes or b""
        except subprocess.TimeoutExpired:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            try:
                proc.wait(timeout=_POST_KILL_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            return b"", b""

    def _result_from_output(
        self,
        exit_code: int,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        timed_out: bool,
        reason: str | None,
        started: float,
    ) -> SandboxResult:
        stdout_chunk, stdout_truncated = truncate_bytes(
            stdout_bytes, self.config.max_output_bytes
        )
        stderr_chunk, stderr_truncated = truncate_bytes(
            stderr_bytes, self.config.max_output_bytes
        )
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout_chunk.decode("utf-8", errors="replace"),
            stderr=stderr_chunk.decode("utf-8", errors="replace"),
            truncated=stdout_truncated or stderr_truncated,
            timed_out=timed_out,
            denied=False,
            reason=reason,
            duration_ms=self._elapsed_ms(started),
        )

    def _spawn_failure(
        self, exit_code: int, stderr: str, started: float
    ) -> SandboxResult:
        return SandboxResult(
            exit_code=exit_code,
            stdout="",
            stderr=stderr,
            truncated=False,
            timed_out=False,
            denied=False,
            reason=None,
            duration_ms=self._elapsed_ms(started),
        )

    def _deny(self, reason: str, started: float) -> SandboxResult:
        return SandboxResult(
            exit_code=DENIED_EXIT_CODE,
            stdout="",
            stderr="",
            truncated=False,
            timed_out=False,
            denied=True,
            reason=reason,
            duration_ms=self._elapsed_ms(started),
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((time.perf_counter() - started) * 1000.0, 3)


__all__ = ["SHELL_METACHARS", "Sandbox"]

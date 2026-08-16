from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import Sandbox, SandboxConfig


METACHARS = [";", "|", "&", ">", "<", "`", "$(", "\n", "\r"]


@pytest.mark.parametrize("meta", METACHARS)
def test_metachars_are_refused_without_shell(
    meta: str,
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", "print('hi')\n")
    sandbox = Sandbox(SandboxConfig(project_root=project_root))
    result = sandbox.run([sys.executable, "hello.py", f"value{meta}tail"])

    assert result.denied is True
    assert result.reason is not None
    assert "shell metacharacter" in result.reason


def test_shell_mode_runs_raw_shell_syntax(project_root: Path) -> None:
    sandbox = Sandbox(SandboxConfig(project_root=project_root))
    result = sandbox.run("echo one; echo two", shell=True)

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "one\ntwo\n"


def test_multi_token_shell_sequence_is_quoted(project_root: Path) -> None:
    sandbox = Sandbox(SandboxConfig(project_root=project_root))
    result = sandbox.run(["echo", "a;b"], shell=True)

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "a;b\n"


def test_one_element_shell_sequence_is_a_raw_command(
    project_root: Path,
) -> None:
    sandbox = Sandbox(SandboxConfig(project_root=project_root))
    result = sandbox.run(["echo hello"], shell=True)

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "hello\n"


def test_shell_mode_still_checks_denylist_and_interpreter(
    sandbox: Sandbox,
) -> None:
    denied_name = sandbox.run(["rm", "-rf", "."], shell=True)
    assert denied_name.denied is True
    assert denied_name.reason == (
        "executable 'rm' is denylisted in shell command"
    )

    denied_interpreter = sandbox.run(
        ["bash", "-c", "echo hi"], shell=True
    )
    assert denied_interpreter.denied is True
    assert denied_interpreter.reason is not None
    assert "inline-code" in denied_interpreter.reason


def test_shell_mode_denies_compound_denylisted_commands(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("echo hi; rm -rf .", shell=True)

    assert result.denied is True
    assert result.reason == "executable 'rm' is denylisted in shell command"


def test_shell_mode_unwraps_commands_that_execute_commands(
    sandbox: Sandbox,
) -> None:
    wrapped = sandbox.run("echo hi; env rm -rf .", shell=True)
    assert wrapped.denied is True
    assert wrapped.reason == (
        "executable 'rm' is denylisted in shell command"
    )

    interpreter = sandbox.run("env python3 -c 'print(1)'", shell=True)
    assert interpreter.denied is True
    assert interpreter.reason is not None
    assert "inline-code" in interpreter.reason

    builtin = sandbox.run("command rm -rf .", shell=True)
    assert builtin.denied is True
    assert builtin.reason == (
        "executable 'rm' is denylisted in shell command"
    )


def test_shell_mode_skips_leading_assignments(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("MODE=clean rm -rf .", shell=True)

    assert result.denied is True
    assert result.reason == "executable 'rm' is denylisted in shell command"


def test_unparseable_shell_command_is_refused(sandbox: Sandbox) -> None:
    result = sandbox.run("echo 'unterminated", shell=True)

    assert result.denied is True
    assert result.reason is not None
    assert "not parseable" in result.reason


def test_bare_string_without_shell_is_refused(sandbox: Sandbox) -> None:
    result = sandbox.run("echo hi")

    assert result.denied is True
    assert result.reason == "argv must be a sequence of strings"

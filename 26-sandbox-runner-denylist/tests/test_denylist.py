from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import (
    DENIED_EXIT_CODE,
    Sandbox,
    SandboxConfig,
    SandboxResult,
)


@pytest.mark.parametrize(
    "name",
    [
        "rm",
        "sudo",
        "mkfs",
        "mkfs.ext4",
        "dd",
        "chmod",
        "shutdown",
        "busybox",
    ],
)
def test_denylisted_names_are_refused(name: str, sandbox: Sandbox) -> None:
    result = sandbox.run([name, "--help"])

    assert result.denied is True
    assert result.exit_code == DENIED_EXIT_CODE
    assert result.reason == f"executable '{name}' is denylisted"
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("path", ["/bin/rm", "/usr/bin/rm", "rm.exe"])
def test_denylist_aliases_collapse_to_basenames(
    path: str, sandbox: Sandbox
) -> None:
    result = sandbox.run([path, "-rf", "."])

    assert result.denied is True
    assert result.reason == "executable 'rm' is denylisted"


def test_custom_denylist_can_block_an_otherwise_allowed_tool(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", "print('ok')\n")
    python_name = Path(sys.executable).name.lower().removesuffix(".exe")
    custom = Sandbox(
        SandboxConfig(project_root=project_root, denylist={python_name})
    )
    denied = custom.run([sys.executable, "hello.py"])

    assert denied.denied is True
    assert denied.reason == f"executable '{python_name}' is denylisted"

    allowed = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, "hello.py"]
    )
    assert allowed.exit_code == 0
    assert allowed.stdout == "ok\n"


def test_empty_and_malformed_calls_are_refused(sandbox: Sandbox) -> None:
    empty = sandbox.run([])
    assert empty.denied is True
    assert empty.reason == "argv cannot be empty"

    empty_name = sandbox.run([""])
    assert empty_name.denied is True
    assert empty_name.reason == "executable name cannot be empty"

    bare_string = sandbox.run("echo hi")
    assert bare_string.denied is True
    assert bare_string.reason == "argv must be a sequence of strings"

    non_string = sandbox.run([sys.executable, 1])  # type: ignore[list-item]
    assert non_string.denied is True
    assert non_string.reason == "argv entries must be strings"

    nul = sandbox.run(["echo", "a\x00b"])
    assert nul.denied is True
    assert nul.reason == "argument contains a NUL byte"

    bad_shell = sandbox.run(["echo", "hi"], shell=1)  # type: ignore[arg-type]
    assert bad_shell.denied is True
    assert bad_shell.reason == "shell must be a boolean"


def test_denial_results_have_no_process_output(sandbox: Sandbox) -> None:
    result: SandboxResult = sandbox.run(["rm", "-rf", "."])

    assert result.truncated is False
    assert result.timed_out is False
    assert result.duration_ms >= 0


@pytest.mark.parametrize(
    "argv",
    [
        ["env", "rm", "-rf", "."],
        ["env", "-i", "rm", "-rf", "."],
        ["env", "MODE=clean", "rm", "-rf", "."],
        ["env", "-u", "HOME", "rm", "-rf", "."],
        ["env", "/bin/rm", "-rf", "."],
        ["nohup", "rm", "-rf", "."],
        ["nice", "-n", "5", "rm", "-rf", "."],
        ["timeout", "1", "rm", "-rf", "."],
        ["stdbuf", "-o", "L", "rm", "-rf", "."],
        ["setsid", "rm", "-rf", "."],
        ["command", "rm", "-rf", "."],
        ["exec", "rm", "-rf", "."],
    ],
)
def test_common_wrappers_do_not_hide_denylisted_commands(
    argv: list[str], sandbox: Sandbox
) -> None:
    result = sandbox.run(argv)

    assert result.denied is True
    assert result.reason == "executable 'rm' is denylisted"


def test_env_split_string_is_refused_as_uninspectable(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run(["env", "-S", "rm -rf ."])

    assert result.denied is True
    assert result.reason is not None
    assert "cannot be inspected" in result.reason


def test_env_without_a_command_is_harmless(sandbox: Sandbox) -> None:
    result = sandbox.run(["env"])

    assert result.denied is False
    assert result.exit_code == 0


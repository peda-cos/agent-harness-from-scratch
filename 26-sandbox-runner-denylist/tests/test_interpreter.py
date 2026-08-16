from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import Sandbox, SandboxConfig


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-c", "print(1)"],
        [sys.executable, "-cprint(1)"],
        [sys.executable, "--eval", "print(1)"],
        [sys.executable, "-m", "http.server"],
        [sys.executable, "-"],
        ["bash", "-c", "echo hi"],
        ["sh", "-c", "echo hi"],
        ["node", "-e", "console.log(1)"],
        ["node", "-p", "1"],
        ["perl", "-e", "print 1"],
        ["php", "-r", "echo 1;"],
        ["ruby", "-e", "puts 1"],
        ["lua", "-e", "print(1)"],
    ],
)
def test_inline_code_flags_are_refused(
    argv: list[str], sandbox: Sandbox
) -> None:
    result = sandbox.run(argv)

    assert result.denied is True
    assert result.reason is not None
    assert "inline-code" in result.reason
    assert "'-" in result.reason


def test_script_invocation_is_allowed(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", "print('script ok')\n")
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, "hello.py"]
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "script ok\n"


def test_empty_interpreter_block_disables_the_shape_check(
    project_root: Path,
) -> None:
    sandbox = Sandbox(
        SandboxConfig(
            project_root=project_root,
            interpreter_block=frozenset(),
        )
    )
    result = sandbox.run([sys.executable, "-c", "print('inline ok')"])

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "inline ok\n"


def test_denylist_is_checked_before_the_interpreter_axis(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run(["busybox", "sh", "-c", "echo hi"])

    assert result.denied is True
    assert result.reason == "executable 'busybox' is denylisted"


@pytest.mark.parametrize(
    "wrapper",
    [
        ["env"],
        ["env", "-i"],
        ["env", "-u", "HOME"],
        ["env", "MODE=clean"],
        ["nohup"],
        ["nice", "-n", "5"],
        ["timeout", "5"],
        ["stdbuf", "-o", "L"],
        ["setsid"],
    ],
)
def test_inline_code_is_refused_through_wrappers(
    wrapper: list[str], sandbox: Sandbox
) -> None:
    result = sandbox.run([*wrapper, sys.executable, "-c", "print(1)"])

    assert result.denied is True
    assert result.reason is not None
    assert "inline-code" in result.reason


def test_env_wrapper_still_runs_a_clean_command(
    project_root: Path,
) -> None:
    env = shutil.which("env")
    if env is None:
        pytest.skip("env is unavailable")
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [env, "MODE=clean", "echo", "wrapped ok"]
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "wrapped ok\n"


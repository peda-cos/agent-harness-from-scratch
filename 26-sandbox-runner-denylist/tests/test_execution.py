from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import (
    TRUNCATION_MARKER,
    Sandbox,
    SandboxConfig,
)

HELLO_SCRIPT = "print('ok')\n"
FAIL_SCRIPT = (
    "import sys\n"
    "print('failure detail', file=sys.stderr)\n"
    "raise SystemExit(3)\n"
)
SHOW_CWD_SCRIPT = "import os\nprint(os.getcwd())\n"
ENV_SCRIPT = (
    "import os\n"
    "print(os.environ.get('SANDBOX_TEST_SECRET', 'missing'))\n"
    "print(os.environ.get('HOME', 'missing'))\n"
    "print(os.environ.get('PYTHONPATH', 'missing'))\n"
)
BIG_STDOUT_SCRIPT = 'import sys\nsys.stdout.write("0123456789abcdef" * 512)\n'
BIG_STDERR_SCRIPT = 'import sys\nsys.stderr.write("E" * 4096)\n'
EXACT_SCRIPT = 'import sys\nsys.stdout.write("x" * 64)\n'


def test_success_and_nonzero_exit_are_captured(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", HELLO_SCRIPT)
    write_script("fail.py", FAIL_SCRIPT)
    sandbox = Sandbox(SandboxConfig(project_root=project_root))

    hello = sandbox.run([sys.executable, "hello.py"])
    assert hello.exit_code == 0
    assert hello.stdout == "ok\n"
    assert hello.stderr == ""
    assert hello.denied is False
    assert hello.timed_out is False
    assert hello.duration_ms >= 0

    failed = sandbox.run([sys.executable, "fail.py"])
    assert failed.exit_code == 3
    assert failed.stdout == ""
    assert failed.stderr == "failure detail\n"
    assert failed.denied is False


def test_missing_executable_is_a_structured_127(sandbox: Sandbox) -> None:
    result = sandbox.run(["definitely-not-a-real-executable-xyz"])

    assert result.exit_code == 127
    assert result.denied is False
    assert result.timed_out is False
    assert result.truncated is False
    assert "executable not found" in result.stderr


def test_permission_denied_is_a_structured_126(
    project_root: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX execute permissions are required")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can bypass the execute-bit check")

    script = project_root / "noexec.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o644)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [str(script)]
    )

    assert result.exit_code == 126
    assert result.denied is False
    assert "permission denied" in result.stderr


def test_default_cwd_is_project_root(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("showcwd.py", SHOW_CWD_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, "showcwd.py"]
    )

    assert result.exit_code == 0
    assert result.stdout == os.path.realpath(project_root) + "\n"


def test_environment_is_scrubbed(
    project_root: Path,
    write_script: Callable[[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TEST_SECRET", "not-a-real-secret")
    monkeypatch.setenv("PYTHONPATH", "/tmp/should-not-be-inherited")
    write_script("env.py", ENV_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, "env.py"]
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "missing",
        os.path.realpath(project_root),
        "missing",
    ]


def test_cwd_override_is_respected(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("showcwd.py", SHOW_CWD_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, str(project_root / "showcwd.py")],
        cwd="sub",
    )

    assert result.exit_code == 0
    assert result.stdout == os.path.realpath(project_root / "sub") + "\n"


def test_stdout_truncation_sets_flag_and_marker(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("bigout.py", BIG_STDOUT_SCRIPT)
    result = Sandbox(
        SandboxConfig(project_root=project_root, max_output_bytes=64)
    ).run([sys.executable, "bigout.py"])

    assert result.truncated is True
    assert result.exit_code == 0
    assert len(result.stdout.encode("utf-8")) <= 64
    assert TRUNCATION_MARKER.decode("utf-8") in result.stdout
    assert result.stdout.startswith("0123456789")
    assert result.stdout.endswith("cdef")


def test_stderr_truncation_sets_flag_and_marker(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("bigerr.py", BIG_STDERR_SCRIPT)
    result = Sandbox(
        SandboxConfig(project_root=project_root, max_output_bytes=64)
    ).run([sys.executable, "bigerr.py"])

    assert result.truncated is True
    assert result.stdout == ""
    assert len(result.stderr.encode("utf-8")) <= 64
    assert TRUNCATION_MARKER.decode("utf-8") in result.stderr


def test_exact_limit_is_not_truncated(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("exact.py", EXACT_SCRIPT)
    result = Sandbox(
        SandboxConfig(project_root=project_root, max_output_bytes=64)
    ).run([sys.executable, "exact.py"])

    assert result.truncated is False
    assert result.stdout == "x" * 64


def test_result_dict_is_json_serializable(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", HELLO_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, "hello.py"]
    )
    encoded = json.dumps(result.to_dict())
    decoded = json.loads(encoded)

    assert decoded["exit_code"] == 0
    assert decoded["stdout"] == "ok\n"
    assert set(decoded) == {
        "exit_code",
        "stdout",
        "stderr",
        "truncated",
        "timed_out",
        "denied",
        "reason",
        "duration_ms",
    }


def test_bad_cwd_inputs_are_refused(sandbox: Sandbox) -> None:
    bad_type = sandbox.run([sys.executable, "hello.py"], cwd=1)  # type: ignore[arg-type]
    assert bad_type.denied is True
    assert bad_type.reason == "cwd must be a string or path-like object"

    empty = sandbox.run([sys.executable, "hello.py"], cwd="")
    assert empty.denied is True
    assert empty.reason == "cwd cannot be empty"


def test_sandbox_requires_a_config() -> None:
    with pytest.raises(TypeError):
        Sandbox(object())  # type: ignore[arg-type]

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import Sandbox, SandboxConfig


READ_SCRIPT = (
    "import sys\n"
    "with open(sys.argv[1], encoding='utf-8') as handle:\n"
    "    print(handle.read(), end='')\n"
)
SHOW_CWD_SCRIPT = "import os\nprint(os.getcwd())\n"


def _read_args(target: str) -> list[str]:
    return [sys.executable, "read.py", target]


def test_relative_inside_is_allowed(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("hello.txt")
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "hello from the sandbox\n"


def test_absolute_inside_is_allowed(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args(str(project_root / "hello.txt"))
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "hello from the sandbox\n"


def test_parent_escape_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("../outside.txt")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_absolute_outside_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("/etc/passwd")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_sibling_prefix_is_refused(
    project_root: Path,
    tmp_path: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret\n", encoding="utf-8")

    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("../root-evil/secret.txt")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_nested_escape_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("sub/../../outside")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_symlink_escape_is_refused(
    project_root: Path,
    tmp_path: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (project_root / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("escape.txt")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_symlink_inside_is_allowed(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    try:
        (project_root / "alias.txt").symlink_to(project_root / "hello.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("alias.txt")
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "hello from the sandbox\n"


def test_long_flag_value_escape_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("--file=/etc/passwd")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_attached_short_flag_escape_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("-I/etc")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_attached_dotdot_escape_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("-f../outside")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "escapes project root" in result.reason


def test_relative_subdirectory_is_not_a_false_positive(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("sub/nested.txt")
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "nested\n"


def test_missing_inside_path_is_not_denied(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("missing.txt")
    )

    assert result.denied is False
    assert result.exit_code != 0


def test_cwd_outside_is_refused(
    project_root: Path,
    tmp_path: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("hello.txt"), cwd=tmp_path
    )

    assert result.denied is True
    assert result.reason is not None
    assert "cwd" in result.reason
    assert "escapes project root" in result.reason


def test_cwd_missing_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("hello.txt"), cwd=project_root / "missing"
    )

    assert result.denied is True
    assert result.reason is not None
    assert "is not an existing directory" in result.reason


def test_cwd_inside_changes_relative_resolution(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("showcwd.py", SHOW_CWD_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, str(project_root / "showcwd.py")],
        cwd="sub",
    )

    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == os.path.realpath(project_root / "sub") + "\n"


def test_home_cwd_maps_to_project_root(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("showcwd.py", SHOW_CWD_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        [sys.executable, str(project_root / "showcwd.py")],
        cwd="~",
    )

    assert result.denied is False
    assert result.stdout == str(project_root) + "\n"


def test_other_user_home_is_refused(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("read.py", READ_SCRIPT)
    result = Sandbox(SandboxConfig(project_root=project_root)).run(
        _read_args("~root/secret.txt")
    )

    assert result.denied is True
    assert result.reason is not None
    assert "home-relative path" in result.reason

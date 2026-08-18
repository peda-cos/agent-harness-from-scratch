from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness import FixtureError, FixtureTask, VerifierRegistry, builtin_registry
from eval_harness.verifiers import file_equals, regex_match, shell_exit_zero


def _make_task(
    tmp_path: Path, verifier: str, params: dict[str, object]
) -> tuple[FixtureTask, Path]:
    root = tmp_path / "task"
    setup = root / "buggy"
    setup.mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    task = FixtureTask(
        id="unit-task",
        goal="exercise a verifier",
        root=root,
        setup_dir=setup,
        verifier=verifier,
        params=params,
    )
    return task, scratch


def test_file_equals_requires_exact_text(tmp_path: Path) -> None:
    task, scratch = _make_task(
        tmp_path,
        "file_equals",
        {"path": "answer.txt", "expected": "expected/answer.txt"},
    )
    expected = task.root / "expected" / "answer.txt"
    expected.parent.mkdir()
    expected.write_text("ok\n", encoding="utf-8")
    output = scratch / "answer.txt"

    output.write_text("ok\n", encoding="utf-8")
    assert file_equals(task, scratch, task.params) is True

    output.write_text("ok", encoding="utf-8")
    assert file_equals(task, scratch, task.params) is False

    output.unlink()
    assert file_equals(task, scratch, task.params) is False


def test_file_equals_rejects_missing_expected_and_escapes(
    tmp_path: Path,
) -> None:
    task, scratch = _make_task(
        tmp_path,
        "file_equals",
        {"path": "answer.txt", "expected": "expected/missing.txt"},
    )
    (scratch / "answer.txt").write_text("ok\n", encoding="utf-8")
    with pytest.raises(FixtureError, match="cannot read expected"):
        file_equals(task, scratch, task.params)

    escape_task, escape_scratch = _make_task(
        tmp_path / "escape",
        "file_equals",
        {"path": "../outside.txt", "expected": "expected/answer.txt"},
    )
    with pytest.raises(FixtureError, match="inside|escapes"):
        file_equals(escape_task, escape_scratch, escape_task.params)


def test_file_equals_rejects_unknown_params(tmp_path: Path) -> None:
    task, scratch = _make_task(tmp_path, "file_equals", {"path": "a"})
    with pytest.raises(FixtureError, match="unknown verifier params"):
        file_equals(task, scratch, {"path": "a", "unexpected": True})


def test_regex_match_searches_file_contents(tmp_path: Path) -> None:
    task, scratch = _make_task(
        tmp_path,
        "regex_match",
        {"path": "validation.py", "pattern": r"(?m)^    return port$"},
    )
    output = scratch / "validation.py"
    output.write_text("def parse_port():\n    return port\n", encoding="utf-8")

    assert regex_match(task, scratch, task.params) is True
    assert (
        regex_match(
            task,
            scratch,
            {"path": "validation.py", "pattern": r"(?m)^    return 0$"},
        )
        is False
    )
    output.unlink()
    assert regex_match(task, scratch, task.params) is False


def test_regex_match_rejects_invalid_pattern(tmp_path: Path) -> None:
    task, scratch = _make_task(
        tmp_path, "regex_match", {"path": "validation.py", "pattern": "["}
    )
    (scratch / "validation.py").write_text("anything\n", encoding="utf-8")

    with pytest.raises(FixtureError, match="invalid regex"):
        regex_match(task, scratch, task.params)


def test_shell_exit_zero_runs_argv_and_checks_exit(tmp_path: Path) -> None:
    task, scratch = _make_task(
        tmp_path, "shell_exit_zero", {"command": ["{python}", "ok.py"]}
    )
    (scratch / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    assert shell_exit_zero(task, scratch, task.params) is True

    (scratch / "ok.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    assert shell_exit_zero(task, scratch, task.params) is False


def test_shell_exit_zero_times_out(tmp_path: Path) -> None:
    task, scratch = _make_task(
        tmp_path,
        "shell_exit_zero",
        {"command": ["{python}", "slow.py"], "timeout_seconds": 0.1},
    )
    (scratch / "slow.py").write_text(
        "import time\ntime.sleep(5)\n", encoding="utf-8"
    )
    assert shell_exit_zero(task, scratch, task.params) is False


def test_shell_exit_zero_rejects_unsafe_or_invalid_commands(
    tmp_path: Path,
) -> None:
    task, scratch = _make_task(tmp_path, "shell_exit_zero", {"command": ["rm", "-rf", "."]})
    with pytest.raises(FixtureError, match="denylisted"):
        shell_exit_zero(task, scratch, task.params)

    with pytest.raises(FixtureError, match="cannot run verifier command"):
        shell_exit_zero(
            task,
            scratch,
            {"command": ["definitely-not-a-real-command-xyz"]},
        )

    with pytest.raises(FixtureError, match="must be a non-empty list"):
        shell_exit_zero(
            task, scratch, {"command": "{python} -c 'print(1)'"}
        )

    with pytest.raises(FixtureError, match="unknown verifier params"):
        shell_exit_zero(task, scratch, {"command": ["{python}", "ok.py"], "extra": 1})


def test_registry_registration_and_resolution() -> None:
    registry = builtin_registry()
    assert registry.names() == ("file_equals", "regex_match", "shell_exit_zero")

    def custom(task: FixtureTask, scratch: Path, params: dict[str, object]) -> bool:
        return True

    registry.register("custom", custom)  # type: ignore[arg-type]
    assert registry.resolve("custom") is custom
    assert registry.names() == (
        "custom",
        "file_equals",
        "regex_match",
        "shell_exit_zero",
    )

    with pytest.raises(ValueError, match="already registered"):
        registry.register("custom", custom)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("", custom)  # type: ignore[arg-type]
    with pytest.raises(FixtureError, match="unknown verifier"):
        registry.resolve("missing")


def test_custom_registry_can_start_empty() -> None:
    registry = VerifierRegistry()
    assert registry.names() == ()
    with pytest.raises(FixtureError, match="\\(none\\)"):
        registry.resolve("file_equals")

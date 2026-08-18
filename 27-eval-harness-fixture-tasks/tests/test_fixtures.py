from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from eval_harness import FixtureError, load_tasks

from conftest import TASKS_DIR

TASK_IDS = {
    "empty-function-body",
    "error-message-typo",
    "factorial-missing-return",
    "fizzbuzz-off-by-one",
    "linked-list-off-by-one",
}


def _copy_tasks(tmp_path: Path, task_id: str) -> Path:
    root = tmp_path / "tasks"
    shutil.copytree(TASKS_DIR / task_id, root / task_id)
    return root


def _rewrite_task_json(task_dir: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    task_file = task_dir / "task.json"
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    mutate(payload)
    task_file.write_text(json.dumps(payload), encoding="utf-8")


def test_loads_five_tasks_in_sorted_order() -> None:
    tasks = load_tasks(TASKS_DIR)

    assert [task.id for task in tasks] == sorted(TASK_IDS)
    assert {task.id for task in tasks} == TASK_IDS
    assert Counter(task.verifier for task in tasks) == Counter(
        {"file_equals": 2, "regex_match": 1, "shell_exit_zero": 2}
    )
    for task in tasks:
        assert task.goal.strip()
        assert task.setup_dir.is_dir()
        assert task.root.name == task.id
        assert isinstance(task.params, dict)


def test_id_must_match_directory(tmp_path: Path) -> None:
    root = _copy_tasks(tmp_path, "factorial-missing-return")
    task_dir = root / "factorial-missing-return"
    _rewrite_task_json(task_dir, lambda payload: payload.update(id="wrong-id"))

    with pytest.raises(FixtureError, match="must equal directory"):
        load_tasks(root)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(extra=True),
        lambda payload: payload.pop("goal"),
        lambda payload: payload.pop("setup"),
        lambda payload: payload.update(id="Bad_ID"),
    ],
)
def test_task_json_schema_is_strict(tmp_path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    root = _copy_tasks(tmp_path, "factorial-missing-return")
    task_dir = root / "factorial-missing-return"
    _rewrite_task_json(task_dir, mutate)

    with pytest.raises(FixtureError):
        load_tasks(root)


def test_missing_or_malformed_task_json(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-json"
    (missing_root / "task-one").mkdir(parents=True)
    with pytest.raises(FixtureError, match="missing task.json"):
        load_tasks(missing_root)

    malformed_root = tmp_path / "malformed"
    task_dir = malformed_root / "factorial-missing-return"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(FixtureError, match="cannot read"):
        load_tasks(malformed_root)


@pytest.mark.parametrize("copy_value", ["missing", "../outside"])
def test_setup_must_be_an_existing_child_directory(
    tmp_path: Path, copy_value: str
) -> None:
    root = _copy_tasks(tmp_path, "factorial-missing-return")
    task_dir = root / "factorial-missing-return"

    def mutate(payload: dict[str, object]) -> None:
        setup = payload["setup"]
        assert isinstance(setup, dict)
        setup["copy"] = copy_value

    _rewrite_task_json(task_dir, mutate)

    with pytest.raises(FixtureError):
        load_tasks(root)


@pytest.mark.parametrize("make_root", [False, True])
def test_tasks_root_must_exist_and_contain_a_task(
    tmp_path: Path, make_root: bool
) -> None:
    root = tmp_path / "tasks"
    if make_root:
        root.mkdir()

    with pytest.raises(FixtureError):
        load_tasks(root)


def test_loader_does_not_resolve_verifier_names(tmp_path: Path) -> None:
    root = _copy_tasks(tmp_path, "factorial-missing-return")
    task_dir = root / "factorial-missing-return"

    def mutate(payload: dict[str, object]) -> None:
        verifier = payload["verifier"]
        assert isinstance(verifier, dict)
        verifier["name"] = "custom_registered_later"

    _rewrite_task_json(task_dir, mutate)

    tasks = load_tasks(root)
    assert tasks[0].verifier == "custom_registered_later"

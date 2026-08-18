"""Load fixture tasks from a directory of JSON declarations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import FixtureError, FixtureTask, safe_child

_TASK_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_REQUIRED_KEYS = frozenset({"id", "goal", "setup", "verifier"})


def _as_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FixtureError(f"{label} must be a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise FixtureError(f"{label} keys must be strings")
        result[key] = item
    return result


def _as_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{label} must be a non-empty string")
    return value


def load_tasks(tasks_dir: str | Path) -> tuple[FixtureTask, ...]:
    """Load, validate, and deterministically order every fixture task."""

    root = Path(tasks_dir)
    if not root.is_dir():
        raise FixtureError(f"tasks directory does not exist: {root}")

    task_dirs = sorted(
        (entry for entry in root.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name,
    )
    if not task_dirs:
        raise FixtureError(f"no fixture tasks found in {root}")

    tasks: list[FixtureTask] = []
    seen: set[str] = set()
    for task_dir in task_dirs:
        task_file = task_dir / "task.json"
        if not task_file.is_file():
            raise FixtureError(f"missing task.json: {task_file}")
        try:
            raw = json.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FixtureError(f"cannot read {task_file}: {exc}") from exc

        payload = _as_mapping(raw, str(task_file))
        unknown = sorted(set(payload) - _REQUIRED_KEYS)
        missing = sorted(_REQUIRED_KEYS - set(payload))
        if unknown:
            raise FixtureError(f"{task_file}: unknown keys {unknown}")
        if missing:
            raise FixtureError(f"{task_file}: missing keys {missing}")

        task_id = _as_string(payload.get("id"), f"{task_file}: id")
        if not _TASK_ID.fullmatch(task_id):
            raise FixtureError(f"{task_file}: invalid id {task_id!r}")
        if task_id != task_dir.name:
            raise FixtureError(
                f"{task_file}: id {task_id!r} must equal directory {task_dir.name!r}"
            )
        if task_id in seen:
            raise FixtureError(f"duplicate task id: {task_id}")
        seen.add(task_id)

        goal = _as_string(payload.get("goal"), f"{task_file}: goal")

        setup = _as_mapping(payload.get("setup"), f"{task_file}: setup")
        if set(setup) != {"copy"}:
            raise FixtureError(f"{task_file}: setup must contain exactly 'copy'")
        copy = _as_string(setup.get("copy"), f"{task_file}: setup.copy")
        setup_dir = safe_child(task_dir, copy)
        if not setup_dir.is_dir():
            raise FixtureError(f"{task_file}: setup.copy is not a directory: {copy}")

        verifier = _as_mapping(payload.get("verifier"), f"{task_file}: verifier")
        if set(verifier) != {"name", "params"}:
            raise FixtureError(
                f"{task_file}: verifier must contain exactly 'name' and 'params'"
            )
        verifier_name = _as_string(verifier.get("name"), f"{task_file}: verifier.name")
        params = _as_mapping(verifier.get("params"), f"{task_file}: verifier.params")

        tasks.append(
            FixtureTask(
                id=task_id,
                goal=goal,
                root=task_dir.resolve(),
                setup_dir=setup_dir,
                verifier=verifier_name,
                params=params,
            )
        )

    return tuple(sorted(tasks, key=lambda task: task.id))

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from eval_harness import (
    EvalHarness,
    FixtureError,
    SampleResult,
    VerifierRegistry,
    apply_known_fixes,
)

from conftest import TASKS_DIR


def _no_fix(task, scratch):  # type: ignore[no-untyped-def]
    return SampleResult(True, 1.0, 0.0, ())


def _raising(task, scratch):  # type: ignore[no-untyped-def]
    raise RuntimeError("candidate exploded")


def _wrong_type(task, scratch):  # type: ignore[no-untyped-def]
    return None


def test_reference_candidate_passes_every_bundled_task(
    reference_candidate,
) -> None:  # type: ignore[no-untyped-def]
    report = EvalHarness(TASKS_DIR, reference_candidate, k=3).run()

    assert report.total_samples == 15
    assert report.pass_at_1 == 1.0
    assert report.pass_at_k == 1.0
    assert report.self_reported_pass_rate == 1.0
    assert report.total_candidate_errors == 0
    assert [task.task_id for task in report.tasks] == sorted(
        task.task_id for task in report.tasks
    )
    assert all(task.passes == 3 for task in report.tasks)
    assert all(task.self_reported_passes == 3 for task in report.tasks)
    assert all(task.candidate_errors == 0 for task in report.tasks)


def test_no_fix_candidate_gets_zero_and_cannot_hide_behind_self_report() -> None:
    report = EvalHarness(TASKS_DIR, _no_fix, k=1).run()

    assert report.total_samples == 5
    assert report.pass_at_1 == 0.0
    assert report.pass_at_k == 0.0
    assert report.self_reported_pass_rate == 1.0
    assert all(task.passes == 0 for task in report.tasks)
    assert all(task.self_reported_passes == 1 for task in report.tasks)


@pytest.mark.parametrize("candidate", [_raising, _wrong_type])
def test_candidate_failures_are_recorded_and_do_not_abort(candidate) -> None:  # type: ignore[no-untyped-def]
    report = EvalHarness(TASKS_DIR, candidate, k=2).run()

    assert report.pass_at_1 == 0.0
    assert report.pass_at_k == 0.0
    assert report.total_samples == 10
    assert report.total_candidate_errors == 10
    assert report.self_reported_pass_rate == 0.0
    assert all(task.candidate_errors == 2 for task in report.tasks)


def test_report_is_json_serializable_with_stable_contract() -> None:
    report = EvalHarness(TASKS_DIR, _no_fix, k=1).run()
    payload = report.to_dict()

    assert set(payload) == {
        "k",
        "total_tasks",
        "total_samples",
        "pass_at_1",
        "pass_at_k",
        "self_reported_pass_rate",
        "mean_latency_ms",
        "p95_latency_ms",
        "mean_cost_units",
        "total_cost_units",
        "total_candidate_errors",
        "tasks",
    }
    assert payload["total_tasks"] == 5
    tasks = payload["tasks"]
    assert isinstance(tasks, list)
    assert set(tasks[0]) == {
        "task_id",
        "k",
        "passes",
        "pass_rate",
        "pass_at_k",
        "self_reported_passes",
        "candidate_errors",
        "mean_latency_ms",
        "mean_cost_units",
    }
    assert json.loads(json.dumps(payload)) == payload


def test_fresh_scratch_per_sample_and_custom_registry(
    tmp_path: Path,
) -> None:
    tasks_root = tmp_path / "tasks"
    task_dir = tasks_root / "factorial-missing-return"
    shutil.copytree(TASKS_DIR / "factorial-missing-return", task_dir)
    task_file = task_dir / "task.json"
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    payload["verifier"] = {"name": "always_true", "params": {}}
    task_file.write_text(json.dumps(payload), encoding="utf-8")

    registry = VerifierRegistry()

    def always_true(task, scratch, params):  # type: ignore[no-untyped-def]
        return True

    registry.register("always_true", always_true)  # type: ignore[arg-type]
    seen: list[Path] = []

    def candidate(task, scratch):  # type: ignore[no-untyped-def]
        marker = Path(scratch) / "marker.txt"
        assert not marker.exists()
        marker.write_text("seen", encoding="utf-8")
        seen.append(Path(scratch))
        return SampleResult(True, 1.0, 0.0, ())

    report = EvalHarness(tasks_root, candidate, k=3, registry=registry).run()

    assert report.pass_at_1 == 1.0
    assert len(seen) == 3
    assert len(set(seen)) == 3
    assert all(not path.exists() for path in seen)


def test_unknown_verifier_fails_before_candidate_calls(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    task_dir = tasks_root / "factorial-missing-return"
    shutil.copytree(TASKS_DIR / "factorial-missing-return", task_dir)
    task_file = task_dir / "task.json"
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    payload["verifier"] = {"name": "not_registered", "params": {}}
    task_file.write_text(json.dumps(payload), encoding="utf-8")
    calls: list[str] = []

    def candidate(task, scratch):  # type: ignore[no-untyped-def]
        calls.append(task.id)
        return SampleResult(True, 1.0, 0.0, ())

    with pytest.raises(FixtureError, match="unknown verifier"):
        EvalHarness(tasks_root, candidate, k=1).run()
    assert calls == []


def test_verifier_errors_abort_without_scoring_a_candidate_failure(
    tmp_path: Path,
) -> None:
    tasks_root = tmp_path / "tasks"
    task_dir = tasks_root / "factorial-missing-return"
    shutil.copytree(TASKS_DIR / "factorial-missing-return", task_dir)
    task_file = task_dir / "task.json"
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    payload["verifier"] = {"name": "broken", "params": {}}
    task_file.write_text(json.dumps(payload), encoding="utf-8")

    registry = VerifierRegistry()

    def broken(task, scratch, params):  # type: ignore[no-untyped-def]
        raise FixtureError("fixture is broken")

    registry.register("broken", broken)  # type: ignore[arg-type]

    with pytest.raises(FixtureError, match="fixture is broken"):
        EvalHarness(
            tasks_root, _no_fix, k=1, registry=registry
        ).run()


def test_k_and_candidate_validation() -> None:
    for invalid in (0, -1, True, 1.5, "1"):
        with pytest.raises(ValueError):
            EvalHarness(TASKS_DIR, apply_known_fixes, k=invalid)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        EvalHarness(TASKS_DIR, object())  # type: ignore[arg-type]


def test_empty_tasks_directory_raises(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    with pytest.raises(FixtureError, match="no fixture tasks"):
        EvalHarness(tmp_path / "tasks", apply_known_fixes).run()

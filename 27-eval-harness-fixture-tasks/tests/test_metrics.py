from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eval_harness import (
    EvalHarness,
    SampleResult,
    apply_known_fixes,
    pass_at_k,
    percentile,
)

from conftest import TASKS_DIR


def test_pass_at_k_known_values() -> None:
    assert pass_at_k(0.0, 5) == 0.0
    assert pass_at_k(1.0, 5) == 1.0
    assert pass_at_k(0.5, 1) == 0.5
    assert pass_at_k(0.5, 2) == 0.75
    assert pass_at_k(0.5, 4) == 0.9375


@pytest.mark.parametrize("rate", [-0.1, 1.1, float("nan"), float("inf")])
def test_pass_at_k_rejects_invalid_rate(rate: float) -> None:
    with pytest.raises(ValueError):
        pass_at_k(rate, 3)


@pytest.mark.parametrize("k", [0, -1, True, 1.5, "1"])
def test_pass_at_k_rejects_invalid_k(k: object) -> None:
    with pytest.raises(ValueError):
        pass_at_k(0.5, k)  # type: ignore[arg-type]


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([10.0], 95) == 10.0
    assert percentile([4, 1, 3, 2], 50) == 2.0
    assert percentile([1, 2, 3, 4], 95) == 4.0
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10.0
    assert percentile(list(range(1, 21)), 95) == 19.0


def test_percentile_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        percentile([], 95)
    for percent in (0, -1, 101, float("nan")):
        with pytest.raises(ValueError):
            percentile([1.0], percent)
    with pytest.raises(ValueError):
        percentile([1.0, float("inf")], 95)


def test_sample_result_validates_and_normalizes() -> None:
    result = SampleResult(True, 2, 1, ("fizzbuzz.py",))
    assert result.latency_ms == 2.0
    assert isinstance(result.latency_ms, float)
    assert result.cost_units == 1.0
    assert result.edits == ("fizzbuzz.py",)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"success_self_reported": 1, "latency_ms": 1.0, "cost_units": 0.0},
        {"success_self_reported": True, "latency_ms": -1.0, "cost_units": 0.0},
        {"success_self_reported": True, "latency_ms": 1.0, "cost_units": -1.0},
        {"success_self_reported": True, "latency_ms": float("nan"), "cost_units": 0.0},
        {"success_self_reported": True, "latency_ms": float("inf"), "cost_units": 0.0},
        {"success_self_reported": True, "latency_ms": 1.0, "cost_units": float("nan")},
        {"success_self_reported": True, "latency_ms": True, "cost_units": 0.0},
        {"success_self_reported": True, "latency_ms": 1.0, "cost_units": 0.0, "edits": ["a"]},
        {"success_self_reported": True, "latency_ms": 1.0, "cost_units": 0.0, "edits": ("",)},
    ],
)
def test_sample_result_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SampleResult(**kwargs)  # type: ignore[arg-type]


def test_macro_aggregation_and_self_report_gap(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    shutil.copytree(
        TASKS_DIR / "factorial-missing-return",
        tasks_root / "factorial-missing-return",
    )
    shutil.copytree(
        TASKS_DIR / "error-message-typo",
        tasks_root / "error-message-typo",
    )

    def candidate(task, scratch):  # type: ignore[no-untyped-def]
        if task.id == "factorial-missing-return":
            fixed = apply_known_fixes(task, scratch)
            return SampleResult(True, 10.0, 0.5, fixed.edits)
        return SampleResult(True, 20.0, 1.0, ())

    report = EvalHarness(tasks_root, candidate, k=2).run()

    assert report.pass_at_1 == 0.5
    assert report.pass_at_k == 0.5  # macro mean, not 1 - (1 - 0.5) ** 2
    assert report.self_reported_pass_rate == 1.0
    assert report.mean_latency_ms == 15.0
    assert report.p95_latency_ms == 20.0
    assert report.mean_cost_units == 0.75
    assert report.total_cost_units == 3.0
    assert report.total_samples == 4


def test_self_report_does_not_score_a_task(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    shutil.copytree(
        TASKS_DIR / "factorial-missing-return",
        tasks_root / "factorial-missing-return",
    )

    def liar(task, scratch):  # type: ignore[no-untyped-def]
        return SampleResult(True, 1.0, 0.0, ())

    report = EvalHarness(tasks_root, liar, k=1).run()

    assert report.pass_at_1 == 0.0
    assert report.self_reported_pass_rate == 1.0
    assert report.tasks[0].self_reported_passes == 1
    assert report.tasks[0].passes == 0

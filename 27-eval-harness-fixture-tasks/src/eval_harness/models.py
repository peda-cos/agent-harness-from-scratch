"""Data contracts, metric math, and path safety for the eval harness."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class FixtureError(RuntimeError):
    """A fixture, verifier, or candidate contract is malformed."""


@dataclass(frozen=True, slots=True)
class FixtureTask:
    """A loadable task: goal, scratch setup, and a named deterministic verifier."""

    id: str
    goal: str
    root: Path
    setup_dir: Path
    verifier: str
    params: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SampleResult:
    """One candidate attempt. The self-report never determines the score."""

    success_self_reported: bool
    latency_ms: float
    cost_units: float
    edits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self_report: object = self.success_self_reported
        if not isinstance(self_report, bool):
            raise ValueError("success_self_reported must be a bool")

        for name in ("latency_ms", "cost_units"):
            raw: object = getattr(self, name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{name} must be a finite non-negative number")
            value = float(raw)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
            object.__setattr__(self, name, value)

        edits: object = self.edits
        if not isinstance(edits, tuple):
            raise ValueError("edits must be a tuple of non-empty strings")
        for edit in edits:
            if not isinstance(edit, str) or not edit:
                raise ValueError("edits must be a tuple of non-empty strings")


@dataclass(frozen=True, slots=True)
class TaskReport:
    """Aggregate score and measurements for one fixture task."""

    task_id: str
    k: int
    passes: int
    pass_rate: float
    pass_at_k: float
    self_reported_passes: int
    candidate_errors: int
    mean_latency_ms: float
    mean_cost_units: float

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "k": self.k,
            "passes": self.passes,
            "pass_rate": self.pass_rate,
            "pass_at_k": self.pass_at_k,
            "self_reported_passes": self.self_reported_passes,
            "candidate_errors": self.candidate_errors,
            "mean_latency_ms": self.mean_latency_ms,
            "mean_cost_units": self.mean_cost_units,
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """A complete, JSON-serializable evaluation run."""

    tasks: tuple[TaskReport, ...]
    k: int
    pass_at_1: float
    pass_at_k: float
    mean_latency_ms: float
    p95_latency_ms: float
    mean_cost_units: float
    total_cost_units: float
    total_samples: int
    self_reported_pass_rate: float
    total_candidate_errors: int

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    def to_dict(self) -> dict[str, object]:
        return {
            "k": self.k,
            "total_tasks": self.total_tasks,
            "total_samples": self.total_samples,
            "pass_at_1": self.pass_at_1,
            "pass_at_k": self.pass_at_k,
            "self_reported_pass_rate": self.self_reported_pass_rate,
            "mean_latency_ms": self.mean_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "mean_cost_units": self.mean_cost_units,
            "total_cost_units": self.total_cost_units,
            "total_candidate_errors": self.total_candidate_errors,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def pass_at_k(pass_rate: float, k: int) -> float:
    """Plug-in pass@k estimate: 1 - (1 - p) ** k."""

    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be an integer >= 1")
    rate = float(pass_rate)
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise ValueError("pass_rate must be a finite number between 0 and 1")
    return 1.0 - (1.0 - rate) ** k


def percentile(values: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile; no interpolation between samples."""

    if not values:
        raise ValueError("percentile requires at least one value")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise ValueError("percent must be a number in (0, 100]")
    fraction = float(percent)
    if not math.isfinite(fraction) or not 0.0 < fraction <= 100.0:
        raise ValueError("percent must be a number in (0, 100]")

    ordered: list[float] = []
    for raw in values:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("percentile values must be finite numbers")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("percentile values must be finite numbers")
        ordered.append(value)

    ordered.sort()
    rank = math.ceil(fraction / 100.0 * len(ordered))
    return ordered[max(rank - 1, 0)]


def safe_child(root: Path, relative: str) -> Path:
    """Resolve a fixture-relative path, rejecting absolute and escaping paths."""

    if not isinstance(relative, str) or not relative:
        raise FixtureError("path must be a non-empty relative string")
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise FixtureError(f"path must stay inside {root}: {relative!r}")

    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise FixtureError(f"path escapes {root}: {relative!r}") from exc
    return resolved

"""Sample execution and aggregate reporting for fixture-task evaluations."""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .fixtures import load_tasks
from .models import (
    EvalReport,
    FixtureTask,
    SampleResult,
    TaskReport,
    pass_at_k,
    percentile,
)
from .verifiers import VerifierRegistry, builtin_registry

Candidate = Callable[[FixtureTask, str], SampleResult]


class EvalHarness:
    """Run every fixture task k times against a candidate and verify each sample."""

    def __init__(
        self,
        tasks_dir: str | Path,
        candidate: Candidate,
        *,
        k: int = 1,
        registry: VerifierRegistry | None = None,
    ) -> None:
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError("k must be an integer >= 1")
        if not callable(candidate):
            raise TypeError("candidate must be callable")
        self._tasks_dir = Path(tasks_dir)
        self._candidate = candidate
        self._k = k
        self._registry = registry if registry is not None else builtin_registry()

    def run(self) -> EvalReport:
        tasks = load_tasks(self._tasks_dir)
        resolved = [
            (task, self._registry.resolve(task.verifier)) for task in tasks
        ]

        task_reports: list[TaskReport] = []
        all_latencies: list[float] = []
        all_costs: list[float] = []
        total_self_reported = 0
        total_errors = 0

        for task, verifier in resolved:
            passes = 0
            self_reported_passes = 0
            candidate_errors = 0
            task_latencies: list[float] = []
            task_costs: list[float] = []

            for _ in range(self._k):
                scratch = Path(
                    tempfile.mkdtemp(prefix=f"eval-{task.id}-")
                )
                try:
                    shutil.copytree(
                        task.setup_dir, scratch, dirs_exist_ok=True
                    )
                    started = time.perf_counter()
                    passed = False
                    latency = 0.0
                    cost = 0.0
                    try:
                        result: object = self._candidate(task, str(scratch))
                    except Exception:
                        candidate_errors += 1
                        latency = (time.perf_counter() - started) * 1000.0
                    else:
                        if not isinstance(result, SampleResult):
                            candidate_errors += 1
                            latency = (
                                time.perf_counter() - started
                            ) * 1000.0
                        else:
                            latency = result.latency_ms
                            cost = result.cost_units
                            if result.success_self_reported:
                                self_reported_passes += 1
                            passed = verifier(task, scratch, task.params)

                    if passed:
                        passes += 1
                    task_latencies.append(latency)
                    task_costs.append(cost)
                finally:
                    shutil.rmtree(scratch, ignore_errors=True)

            pass_rate = passes / self._k
            task_reports.append(
                TaskReport(
                    task_id=task.id,
                    k=self._k,
                    passes=passes,
                    pass_rate=pass_rate,
                    pass_at_k=pass_at_k(pass_rate, self._k),
                    self_reported_passes=self_reported_passes,
                    candidate_errors=candidate_errors,
                    mean_latency_ms=sum(task_latencies) / self._k,
                    mean_cost_units=sum(task_costs) / self._k,
                )
            )
            all_latencies.extend(task_latencies)
            all_costs.extend(task_costs)
            total_self_reported += self_reported_passes
            total_errors += candidate_errors

        total_samples = self._k * len(task_reports)
        return EvalReport(
            tasks=tuple(task_reports),
            k=self._k,
            pass_at_1=sum(report.pass_rate for report in task_reports)
            / len(task_reports),
            pass_at_k=sum(report.pass_at_k for report in task_reports)
            / len(task_reports),
            mean_latency_ms=sum(all_latencies) / total_samples,
            p95_latency_ms=percentile(all_latencies, 95.0),
            mean_cost_units=sum(all_costs) / total_samples,
            total_cost_units=sum(all_costs),
            total_samples=total_samples,
            self_reported_pass_rate=total_self_reported / total_samples,
            total_candidate_errors=total_errors,
        )

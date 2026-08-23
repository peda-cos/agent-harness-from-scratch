"""Outcome-based scoring for one end-to-end agent run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .metrics import prometheus_exposition

if TYPE_CHECKING:
    from .harness import AgentRunReport


@dataclass(frozen=True, slots=True)
class EvalCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class EvalReport:
    passed: bool
    checks: tuple[EvalCheck, ...]
    pass_at_1: float
    step_count: int
    observation_tokens: int
    gate_denials: int

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "pass_at_1": self.pass_at_1,
            "step_count": self.step_count,
            "observation_tokens": self.observation_tokens,
            "gate_denials": self.gate_denials,
            "checks": [check.to_dict() for check in self.checks],
        }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


class EvalHarness:
    """Score the scratch outcome independently of the policy's self-report."""

    def __init__(self, fixture_root: str | Path) -> None:
        root = Path(fixture_root).resolve()
        self.fixture_root = root
        self.expected_path = root / "expected" / "src" / "fizz.py"
        self.tests_path = root / "tests" / "test_fizz.py"
        if not self.expected_path.is_file():
            raise ValueError(f"missing expected artifact: {self.expected_path}")
        if not self.tests_path.is_file():
            raise ValueError(f"missing bundled tests: {self.tests_path}")

    def evaluate(
        self,
        report: AgentRunReport,
        repo_root: str | Path,
    ) -> EvalReport:
        scratch = Path(repo_root)
        expected_source = self.expected_path.read_text(encoding="utf-8")
        bundled_tests = self.tests_path.read_text(encoding="utf-8")
        actual_source = _read_text(scratch / "src" / "fizz.py")
        actual_tests = _read_text(scratch / "tests" / "test_fizz.py")

        test_steps = [step for step in report.steps if step.tool == "run_tests"]
        tests_pass = bool(test_steps) and test_steps[-1].exit_code == 0
        source_ok = actual_source == expected_source
        tests_ok = actual_tests == bundled_tests
        step_ok = report.step_count < report.limits.max_steps
        observation_ok = (
            report.observation_tokens <= report.limits.observation_tokens
        )
        denials_ok = report.gate_denials == 0

        tool_spans = [
            span
            for span in report.spans
            if span.name.startswith("execute_tool ")
        ]
        span_ids = {span.span_id for span in tool_spans}
        trace_ok = (
            len(tool_spans) == report.step_count
            and all(step.span_id in span_ids for step in report.steps)
            and all(span.trace_id == report.trace_id for span in tool_spans)
        )

        exposition = prometheus_exposition(report.metrics)
        required_metric_lines = (
            'tools_called_total{tool="read_file"} 2',
            "# TYPE tool_latency_ms histogram",
            'tool_latency_ms_count{tool="read_file"} 2',
            'tool_latency_ms_bucket{tool="read_file",le="+Inf"} 2',
        )
        metrics_ok = all(line in exposition for line in required_metric_lines)

        checks = (
            EvalCheck(
                "agent_success",
                report.policy_success,
                f"final state {report.final_state}",
            ),
            EvalCheck(
                "tests_pass",
                tests_pass,
                (
                    f"last run_tests exit={test_steps[-1].exit_code}"
                    if test_steps
                    else "no run_tests step"
                ),
            ),
            EvalCheck(
                "expected_source",
                source_ok,
                "scratch src/fizz.py matches expected/src/fizz.py",
            ),
            EvalCheck(
                "tests_untouched",
                tests_ok,
                "scratch tests match the bundled tests",
            ),
            EvalCheck(
                "step_budget",
                step_ok,
                f"{report.step_count} steps < {report.limits.max_steps}",
            ),
            EvalCheck(
                "observation_budget",
                observation_ok,
                (
                    f"{report.observation_tokens} <= "
                    f"{report.limits.observation_tokens} tokens"
                ),
            ),
            EvalCheck(
                "zero_gate_denials",
                denials_ok,
                f"{report.gate_denials} gate denials",
            ),
            EvalCheck(
                "trace_coverage",
                trace_ok,
                f"{len(tool_spans)} tool spans for {report.step_count} steps",
            ),
            EvalCheck(
                "metrics_exposition",
                metrics_ok,
                "required tool counter and histogram series present",
            ),
        )
        passed = all(check.passed for check in checks)
        return EvalReport(
            passed=passed,
            checks=checks,
            pass_at_1=1.0 if passed else 0.0,
            step_count=report.step_count,
            observation_tokens=report.observation_tokens,
            gate_denials=report.gate_denials,
        )


__all__ = ["EvalCheck", "EvalHarness", "EvalReport"]

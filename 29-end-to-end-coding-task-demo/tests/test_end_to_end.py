from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from end_to_end_agent import (
    AgentRun,
    EvalHarness,
    Repo,
    RunLimits,
    prometheus_exposition,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = MODULE_ROOT / "fixture_repo"
MAIN_PATH = MODULE_ROOT / "main.py"


def test_full_run_solves_the_fixture_in_five_steps(tmp_path: Path) -> None:
    original_buggy = (FIXTURE_ROOT / "src" / "fizz.py").read_text(
        encoding="utf-8"
    )
    expected = (FIXTURE_ROOT / "expected" / "src" / "fizz.py").read_text(
        encoding="utf-8"
    )
    bundled_tests = (FIXTURE_ROOT / "tests" / "test_fizz.py").read_text(
        encoding="utf-8"
    )

    with Repo(FIXTURE_ROOT) as repo:
        scratch = repo.root
        report = AgentRun(
            repo,
            trace_path=tmp_path / "traces.jsonl",
        ).run()

        assert report.success
        assert report.policy_success
        assert report.failure_reason is None
        assert report.step_count == 5
        assert [step.state for step in report.steps] == [
            "SURVEY",
            "RUN_TESTS",
            "INSPECT",
            "FIX",
            "VERIFY",
        ]
        assert [step.tool for step in report.steps] == [
            "read_file",
            "run_tests",
            "read_file",
            "write_file",
            "run_tests",
        ]
        assert [step.status for step in report.steps] == [
            "ok",
            "tests_failed",
            "ok",
            "ok",
            "ok",
        ]
        assert report.gate_denials == 0
        assert 0 < report.observation_tokens <= report.limits.observation_tokens
        assert report.observation_tokens == sum(
            step.observation_tokens for step in report.steps
        )
        assert (scratch / "src" / "fizz.py").read_text(
            encoding="utf-8"
        ) == expected
        assert (scratch / "tests" / "test_fizz.py").read_text(
            encoding="utf-8"
        ) == bundled_tests
        assert not (scratch / "expected").exists()

        evaluation = report.eval_report
        assert evaluation is not None
        assert evaluation.passed
        assert evaluation.pass_at_1 == 1.0
        assert all(check.passed for check in evaluation.checks)

    assert not scratch.exists()
    assert (FIXTURE_ROOT / "src" / "fizz.py").read_text(
        encoding="utf-8"
    ) == original_buggy


def test_trace_has_one_span_per_step_plus_the_root(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    with Repo(FIXTURE_ROOT) as repo:
        report = AgentRun(repo, trace_path=trace_path).run()

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == report.step_count + 1
    spans = [json.loads(line) for line in lines]
    root = next(
        span for span in spans if span["name"] == "invoke_agent coding-agent"
    )
    tools = [
        span for span in spans if str(span["name"]).startswith("execute_tool ")
    ]
    assert len(tools) == report.step_count
    assert root["parent_span_id"] is None
    assert root["status"] == "OK"
    assert root["attributes"]["harness.success"] is True
    assert root["attributes"]["harness.steps"] == report.step_count
    assert root["attributes"]["harness.gate_denials"] == 0
    assert all(span["trace_id"] == report.trace_id for span in spans)
    assert all(
        span["parent_span_id"] == report.root_span_id for span in tools
    )
    assert {step.span_id for step in report.steps} == {
        span["span_id"] for span in tools
    }
    assert all(span["status"] == "OK" for span in tools)
    assert report.eval_report is not None
    assert report.eval_report.passed


def test_prometheus_exposition_has_required_series(tmp_path: Path) -> None:
    with Repo(FIXTURE_ROOT) as repo:
        report = AgentRun(
            repo,
            trace_path=tmp_path / "traces.jsonl",
        ).run()

    exposition = prometheus_exposition(report.metrics)
    assert 'tools_called_total{tool="read_file"} 2' in exposition
    assert 'tools_called_total{tool="run_tests"} 2' in exposition
    assert 'tools_called_total{tool="write_file"} 1' in exposition
    assert "# TYPE tool_latency_ms histogram" in exposition
    assert 'tool_latency_ms_count{tool="read_file"} 2' in exposition
    assert 'tool_latency_ms_sum{tool="read_file"}' in exposition

    bucket_lines = [
        line
        for line in exposition.splitlines()
        if line.startswith('tool_latency_ms_bucket{tool="read_file",')
    ]
    counts = [int(line.rsplit(" ", 1)[1]) for line in bucket_lines]
    assert len(counts) == 12
    assert counts == sorted(counts)
    assert counts[-1] == 2
    assert 'tool_latency_ms_bucket{tool="read_file",le="+Inf"} 2' in exposition


def test_step_budget_denial_halts_the_agent(tmp_path: Path) -> None:
    limits = RunLimits(max_steps=2)
    with Repo(FIXTURE_ROOT) as repo:
        report = AgentRun(
            repo,
            limits=limits,
            trace_path=tmp_path / "traces.jsonl",
        ).run()

    assert not report.success
    assert not report.policy_success
    assert report.final_state == "INSPECT"
    assert report.step_count == 3
    assert report.gate_denials == 1
    last = report.steps[-1]
    assert last.status == "gate_denied"
    assert last.decision.gate_name == "run_budget"
    assert last.decision.reason is not None
    assert "step budget" in last.decision.reason
    assert report.observation_tokens <= limits.observation_tokens
    assert report.metrics.counters["tools_called_total"].value(
        {"tool": "read_file"}
    ) == 1
    assert report.metrics.counters["tools_called_total"].value(
        {"tool": "run_tests"}
    ) == 1
    assert report.metrics.counters["tools_called_total"].value(
        {"tool": "write_file"}
    ) == 0
    assert report.metrics.counters["gate_denials_total"].value(
        {"gate": "run_budget"}
    ) == 1
    tool_spans = [
        span for span in report.spans if span.name.startswith("execute_tool ")
    ]
    assert len(tool_spans) == 3
    assert tool_spans[-1].status == "ERROR"
    root = next(span for span in report.spans if span.span_id == report.root_span_id)
    assert root.status == "ERROR"


def test_observation_budget_denial_halts_before_overrun(
    tmp_path: Path,
) -> None:
    limits = RunLimits(observation_tokens=1)
    with Repo(FIXTURE_ROOT) as repo:
        report = AgentRun(
            repo,
            limits=limits,
            trace_path=tmp_path / "traces.jsonl",
        ).run()

    assert not report.success
    assert report.final_state == "RUN_TESTS"
    assert report.step_count == 2
    assert report.observation_tokens == 1
    assert report.gate_denials == 1
    assert report.steps[0].truncated
    assert report.steps[0].observation_tokens == 1
    assert report.steps[1].status == "gate_denied"
    assert report.steps[1].decision.gate_name == "run_budget"
    assert report.metrics.counters["tools_called_total"].value(
        {"tool": "read_file"}
    ) == 1
    assert report.metrics.counters["tools_called_total"].value(
        {"tool": "run_tests"}
    ) == 0


def test_demo_is_idempotent_from_a_foreign_cwd(tmp_path: Path) -> None:
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(MAIN_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        output = completed.stdout
        assert "Coding agent demo: fix the fizzbuzz off-by-one" in output
        assert "Result: SUCCESS in 5 steps; 6 spans; 0 gate denials" in output
        assert "Eval report: PASS (9/9 checks)" in output
        assert 'tools_called_total{tool="read_file"} 2' in output
        assert "# TYPE tool_latency_ms histogram" in output
        assert "temporary copy removed after demo" in output
        assert not (tmp_path / "traces.jsonl").exists()


def test_eval_harness_detects_tampered_scratch_source(
    tmp_path: Path,
) -> None:
    with Repo(FIXTURE_ROOT) as repo:
        report = AgentRun(
            repo,
            trace_path=tmp_path / "traces.jsonl",
        ).run()
        assert report.eval_report is not None
        assert report.eval_report.passed

        (repo.root / "src" / "fizz.py").write_text(
            "def broken() -> None:\n    return None\n",
            encoding="utf-8",
        )
        evaluation = EvalHarness(repo.fixture_root).evaluate(
            report,
            repo.root,
        )

    source_check = next(
        check for check in evaluation.checks if check.name == "expected_source"
    )
    assert not source_check.passed
    assert not evaluation.passed

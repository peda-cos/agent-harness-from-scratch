"""Run the deterministic coding agent end to end and print its artifacts."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from end_to_end_agent import (  # noqa: E402
    AGENT_NAME,
    ALLOWED_TOOLS,
    DEFAULT_LATENCY_BUCKETS,
    DEFAULT_LIMITS,
    ERROR_TYPE,
    GEN_AI_AGENT_NAME,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    AgentRun,
    AgentRunReport,
    ChainResult,
    CodingAgentPolicy,
    Counter,
    DENIED_EXIT_CODE,
    EvalCheck,
    EvalHarness,
    EvalReport,
    GateChain,
    GateContext,
    GateDecision,
    GenAISpan,
    Histogram,
    MetricsRegistry,
    Observation,
    ObservationLedger,
    PathGate,
    PathViolation,
    Policy,
    Repo,
    RunBudgetGate,
    RunLimits,
    Sandbox,
    SandboxResult,
    SpanBuilder,
    StepDispatcher,
    StepRecord,
    TIMEOUT_EXIT_CODE,
    ToolCall,
    VerificationGate,
    WhitelistGate,
    clip_text,
    estimate_tokens,
    new_span_id,
    new_trace_id,
    prometheus_exposition,
    tree_listing,
    validate_arguments,
)

__all__ = [
    "AGENT_NAME",
    "ALLOWED_TOOLS",
    "AgentRun",
    "AgentRunReport",
    "ChainResult",
    "CodingAgentPolicy",
    "Counter",
    "DEFAULT_LATENCY_BUCKETS",
    "DEFAULT_LIMITS",
    "DENIED_EXIT_CODE",
    "ERROR_TYPE",
    "EvalCheck",
    "EvalHarness",
    "EvalReport",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_TYPE",
    "GateChain",
    "GateContext",
    "GateDecision",
    "GenAISpan",
    "Histogram",
    "MetricsRegistry",
    "Observation",
    "ObservationLedger",
    "PathGate",
    "PathViolation",
    "Policy",
    "Repo",
    "RunBudgetGate",
    "RunLimits",
    "Sandbox",
    "SandboxResult",
    "SpanBuilder",
    "StepDispatcher",
    "StepRecord",
    "TIMEOUT_EXIT_CODE",
    "ToolCall",
    "VerificationGate",
    "WhitelistGate",
    "clip_text",
    "estimate_tokens",
    "new_span_id",
    "new_trace_id",
    "prometheus_exposition",
    "run_demo",
    "tree_listing",
    "validate_arguments",
]


def _print_checks(report: AgentRunReport) -> None:
    evaluation = report.eval_report
    if evaluation is None:
        raise AssertionError("run report has no eval report")
    passed = sum(1 for check in evaluation.checks if check.passed)
    verdict = "PASS" if evaluation.passed else "FAIL"
    print(f"\nEval report: {verdict} ({passed}/{len(evaluation.checks)} checks)")
    for check in evaluation.checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"- {check.name}: {marker} ({check.detail})")


def _assert_demo_invariants(
    report: AgentRunReport,
    exposition: str,
    trace_lines: int,
) -> None:
    if report.step_count >= report.limits.max_steps:
        raise AssertionError(
            f"agent used {report.step_count} steps; "
            f"expected fewer than {report.limits.max_steps}"
        )
    if report.observation_tokens > report.limits.observation_tokens:
        raise AssertionError("observation token budget was exceeded")
    if report.gate_denials != 0:
        raise AssertionError(
            f"legal run triggered {report.gate_denials} gate denials"
        )

    tool_spans = [
        span for span in report.spans if span.name.startswith("execute_tool ")
    ]
    span_ids = {span.span_id for span in tool_spans}
    if len(tool_spans) != report.step_count:
        raise AssertionError("tool span count does not match step count")
    if any(step.span_id not in span_ids for step in report.steps):
        raise AssertionError("a step has no corresponding span")
    if trace_lines != report.step_count + 1:
        raise AssertionError(
            f"trace has {trace_lines} lines; expected "
            f"{report.step_count} step spans plus one root span"
        )

    required = (
        'tools_called_total{tool="read_file"} 2',
        "# TYPE tool_latency_ms histogram",
        'tool_latency_ms_count{tool="read_file"} 2',
        'tool_latency_ms_bucket{tool="read_file",le="+Inf"} 2',
    )
    for line in required:
        if line not in exposition:
            raise AssertionError(f"missing expected metrics line: {line}")

    if not report.success:
        raise AssertionError(f"agent run failed: {report.failure_reason}")
    evaluation = report.eval_report
    if evaluation is None or not evaluation.passed:
        raise AssertionError("eval harness rejected the run")


def run_demo() -> int:
    with tempfile.TemporaryDirectory(prefix="coding-agent-demo-") as tmp:
        trace_path = Path(tmp) / "traces.jsonl"
        with Repo() as repo:
            report = AgentRun(repo, trace_path=trace_path).run()
            evaluation = report.eval_report
            if evaluation is None:
                raise AssertionError("run report has no eval report")
            exposition = prometheus_exposition(report.metrics)
            trace_lines = len(
                trace_path.read_text(encoding="utf-8").splitlines()
            )
            _assert_demo_invariants(report, exposition, trace_lines)

            print("Coding agent demo: fix the fizzbuzz off-by-one")
            print(
                f"Policy: {report.final_state}; "
                f"{report.step_count} steps; "
                f"{report.observation_tokens}/"
                f"{report.limits.observation_tokens} observation tokens"
            )
            for step in report.steps:
                target = f" {step.target}" if step.target else ""
                print(
                    f"[step {step.step}] {step.state:<10} "
                    f"{step.tool}{target} -> {step.status} "
                    f"({step.observation_tokens} tokens, "
                    f"{step.duration_ms:.2f} ms, "
                    f"span {step.span_id[:8]})"
                )
            print(
                f"\nResult: SUCCESS in {report.step_count} steps; "
                f"{len(report.spans)} spans; "
                f"{report.gate_denials} gate denials; "
                f"eval pass_at_1={evaluation.pass_at_1:.1f}"
            )
            print(
                f"Trace: {trace_lines} JSONL lines "
                f"({report.trace_path}); temporary copy removed after demo"
            )
            _print_checks(report)

            print("\nPrometheus exposition:")
            print(exposition, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())

"""Repo preparation, the single dispatch seam, and the agent run loop."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal

from .evaluation import EvalHarness, EvalReport
from .gates import GateChain, GateContext, PathGate, RunBudgetGate, WhitelistGate
from .ledger import ObservationLedger, clip_text, estimate_tokens
from .metrics import Counter, Histogram, MetricsRegistry
from .models import GateDecision, RunLimits, ToolCall
from .policy import CodingAgentPolicy, Policy
from .sandbox import Sandbox
from .span import (
    AGENT_NAME,
    ERROR_TYPE,
    GEN_AI_AGENT_NAME,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GenAISpan,
    SpanBuilder,
)
from .tooling import ALLOWED_TOOLS

DEFAULT_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixture_repo"
StepStatus = Literal[
    "ok",
    "tests_failed",
    "gate_denied",
    "sandbox_denied",
    "timeout",
    "error",
]


class Repo:
    """Copy only the agent-visible buggy fixture into a disposable scratch dir."""

    def __init__(self, fixture_root: str | Path = DEFAULT_FIXTURE_ROOT) -> None:
        root = Path(fixture_root).resolve()
        for name in ("src", "tests", "expected"):
            if not (root / name).is_dir():
                raise ValueError(f"fixture is missing {name}/: {root}")
        self.fixture_root = root
        self._tmp = tempfile.TemporaryDirectory(prefix="coding-agent-repo-")
        self.root = Path(self._tmp.name)
        shutil.copytree(root / "src", self.root / "src")
        shutil.copytree(root / "tests", self.root / "tests")

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def __enter__(self) -> Repo:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()


@dataclass(frozen=True, slots=True)
class StepRecord:
    step: int
    state: str
    tool: str
    target: str
    decision: GateDecision
    evaluations: tuple[GateDecision, ...]
    status: StepStatus
    observation: str | None
    observation_tokens: int
    truncated: bool
    exit_code: int | None
    duration_ms: float
    span_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "state": self.state,
            "tool": self.tool,
            "target": self.target,
            "decision": self.decision.to_dict(),
            "evaluations": [
                evaluation.to_dict() for evaluation in self.evaluations
            ],
            "status": self.status,
            "observation_tokens": self.observation_tokens,
            "truncated": self.truncated,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "span_id": self.span_id,
        }


class StepDispatcher:
    """The only path from policy calls to gates, sandbox, ledger, and metrics."""

    def __init__(
        self,
        chain: GateChain,
        sandbox: Sandbox,
        ledger: ObservationLedger,
        spans: SpanBuilder,
        metrics: MetricsRegistry,
        limits: RunLimits,
    ) -> None:
        if not isinstance(limits, RunLimits):
            raise TypeError("limits must be a RunLimits")
        self._chain = chain
        self._sandbox = sandbox
        self._ledger = ledger
        self._spans = spans
        self._limits = limits
        self._tools: Counter = metrics.counter(
            "tools_called_total",
            "Total tool invocations after the gate chain allowed the call.",
        )
        self._latency: Histogram = metrics.histogram(
            "tool_latency_ms",
            "Tool execution latency in milliseconds.",
        )
        self._denials: Counter = metrics.counter(
            "gate_denials_total",
            "Tool calls denied by the gate chain.",
        )

    def dispatch(self, call: ToolCall, state: str) -> StepRecord:
        attributes: dict[str, object] = {
            GEN_AI_OPERATION_NAME: "execute_tool",
            GEN_AI_AGENT_NAME: AGENT_NAME,
            GEN_AI_TOOL_NAME: call.name,
            GEN_AI_TOOL_TYPE: "function",
            GEN_AI_TOOL_CALL_ID: f"call_{uuid.uuid4().hex[:16]}",
            "agent.step": call.step,
            "agent.state": state,
        }
        target = ""
        path_argument = call.arguments.get("path")
        if isinstance(path_argument, str):
            target = path_argument

        with self._spans.span(
            f"execute_tool {call.name}",
            attributes,
        ) as span:
            result = self._chain.review(
                call,
                GateContext(self._ledger, self._limits),
            )
            span.attributes["harness.gate.decision"] = (
                "allow" if result.allowed else "deny"
            )
            if not result.allowed:
                self._denials.inc({"gate": result.decision.gate_name})
                span.status = "ERROR"
                span.status_message = result.decision.reason or "gate denied"
                span.attributes[ERROR_TYPE] = "gate_denied"
                span.attributes["harness.gate.denied_by"] = (
                    result.decision.gate_name
                )
                span.attributes["harness.gate.reason"] = (
                    result.decision.reason or ""
                )
                return StepRecord(
                    step=call.step,
                    state=state,
                    tool=call.name,
                    target=target,
                    decision=result.decision,
                    evaluations=result.evaluations,
                    status="gate_denied",
                    observation=None,
                    observation_tokens=0,
                    truncated=False,
                    exit_code=None,
                    duration_ms=0.0,
                    span_id=span.span_id,
                )

            self._tools.inc({"tool": call.name})
            sandbox_result = self._sandbox.execute(call)
            self._latency.observe(
                sandbox_result.duration_ms,
                {"tool": call.name},
            )

            observation: str | None = None
            observation_tokens = 0
            truncated = False
            if not sandbox_result.denied and not sandbox_result.timed_out:
                remaining = max(
                    self._limits.observation_tokens - self._ledger.cumulative(),
                    0,
                )
                observation, truncated = clip_text(
                    sandbox_result.output,
                    remaining,
                )
                observation_tokens = estimate_tokens(observation)
                self._ledger.record(
                    call.step,
                    call.name,
                    observation_tokens,
                    truncated=truncated,
                )

            if sandbox_result.denied:
                status: StepStatus = "sandbox_denied"
            elif sandbox_result.timed_out:
                status = "timeout"
            elif call.name == "run_tests" and sandbox_result.exit_code != 0:
                status = "tests_failed"
            elif sandbox_result.exit_code != 0:
                status = "error"
            else:
                status = "ok"

            span.attributes["harness.observation.tokens"] = observation_tokens
            span.attributes["harness.observation.truncated"] = truncated
            if sandbox_result.exit_code is not None:
                span.attributes["harness.tool.exit_code"] = (
                    sandbox_result.exit_code
                )

            if status in {"sandbox_denied", "timeout", "error"}:
                span.status = "ERROR"
                span.status_message = (
                    sandbox_result.reason or sandbox_result.output or status
                )
                span.attributes[ERROR_TYPE] = status

            return StepRecord(
                step=call.step,
                state=state,
                tool=call.name,
                target=target,
                decision=result.decision,
                evaluations=result.evaluations,
                status=status,
                observation=observation,
                observation_tokens=observation_tokens,
                truncated=truncated,
                exit_code=sandbox_result.exit_code,
                duration_ms=sandbox_result.duration_ms,
                span_id=span.span_id,
            )


@dataclass(slots=True)
class AgentRunReport:
    success: bool
    policy_success: bool
    final_state: str
    failure_reason: str | None
    steps: tuple[StepRecord, ...]
    gate_denials: int
    observation_tokens: int
    limits: RunLimits
    metrics: MetricsRegistry
    spans: tuple[GenAISpan, ...]
    root_span_id: str
    trace_id: str
    trace_path: Path | None
    eval_report: EvalReport | None

    @property
    def step_count(self) -> int:
        return len(self.steps)


class AgentRun:
    """Drive a policy through the harness and score the resulting workspace."""

    def __init__(
        self,
        repo: Repo,
        *,
        policy: Policy | None = None,
        limits: RunLimits | None = None,
        trace_path: str | Path | None = None,
    ) -> None:
        if not isinstance(repo, Repo):
            raise TypeError("repo must be a Repo")
        if policy is not None and not hasattr(policy, "next_call"):
            raise TypeError("policy must implement the Policy protocol")
        self._repo = repo
        self._policy: Policy = policy if policy is not None else CodingAgentPolicy()
        self._limits = limits if limits is not None else RunLimits()
        self._trace_path = Path(trace_path) if trace_path is not None else None

    def run(self) -> AgentRunReport:
        limits = self._limits
        policy = self._policy
        metrics = MetricsRegistry()
        spans = SpanBuilder(self._trace_path)
        ledger = ObservationLedger()
        chain = GateChain(
            (
                WhitelistGate(ALLOWED_TOOLS),
                PathGate(str(self._repo.root)),
                RunBudgetGate(limits),
            )
        )
        sandbox = Sandbox(self._repo.root, limits)
        dispatcher = StepDispatcher(
            chain,
            sandbox,
            ledger,
            spans,
            metrics,
            limits,
        )

        steps: list[StepRecord] = []
        failure_reason: str | None = None
        root_attributes: dict[str, object] = {
            GEN_AI_OPERATION_NAME: "invoke_agent",
            GEN_AI_AGENT_NAME: AGENT_NAME,
            "agent.task": "fix-fizzbuzz-off-by-one",
        }

        with spans.span(
            f"invoke_agent {AGENT_NAME}",
            root_attributes,
        ) as root:
            while not policy.done:
                step_number = len(steps) + 1
                call = policy.next_call(step_number)
                if call is None:
                    failure_reason = (
                        f"policy produced no call in state {policy.state}"
                    )
                    break
                if call.step != step_number:
                    raise ValueError(
                        f"policy returned step {call.step}; expected {step_number}"
                    )
                record = dispatcher.dispatch(call, policy.state)
                steps.append(record)
                if record.status == "gate_denied":
                    failure_reason = (
                        record.decision.reason or "gate denied a tool call"
                    )
                    break
                if record.status not in {"ok", "tests_failed"}:
                    failure_reason = (
                        f"{record.tool} failed with status {record.status}"
                    )
                    break
                policy.observe(
                    call,
                    record.observation or "",
                    record.exit_code,
                )

            policy_success = (
                policy.state == "SUCCESS" and failure_reason is None
            )
            if not policy_success and failure_reason is None:
                failure_reason = (
                    policy.failure_reason
                    or f"policy halted in state {policy.state}"
                )

            report = AgentRunReport(
                success=False,
                policy_success=policy_success,
                final_state=policy.state,
                failure_reason=failure_reason,
                steps=tuple(steps),
                gate_denials=sum(
                    1 for step in steps if step.status == "gate_denied"
                ),
                observation_tokens=ledger.cumulative(),
                limits=limits,
                metrics=metrics,
                spans=tuple(spans.spans),
                root_span_id=root.span_id,
                trace_id=spans.trace_id,
                trace_path=spans.export_path,
                eval_report=None,
            )
            evaluation = EvalHarness(self._repo.fixture_root).evaluate(
                report,
                self._repo.root,
            )
            report.eval_report = evaluation
            report.success = policy_success and evaluation.passed

            root.attributes.update(
                {
                    "harness.steps": report.step_count,
                    "harness.gate_denials": report.gate_denials,
                    "harness.observation_tokens": report.observation_tokens,
                    "harness.success": report.success,
                }
            )
            if report.success:
                root.status = "OK"
            else:
                root.status = "ERROR"
                root.status_message = failure_reason or "evaluation failed"
                root.attributes[ERROR_TYPE] = "agent_failure"

        report.spans = tuple(spans.spans)
        return report


__all__ = [
    "AgentRun",
    "AgentRunReport",
    "DEFAULT_FIXTURE_ROOT",
    "Repo",
    "StepDispatcher",
    "StepRecord",
    "StepStatus",
]

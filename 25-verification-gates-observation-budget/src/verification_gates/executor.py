"""Budget-aware dispatch: gate, run, clip, record, and count refusals."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .chain import GateChain
from .gates import BudgetGate, GateContext
from .ledger import ObservationLedger
from .models import GateDecision, Observation, ToolCall
from .tokens import clip_text, estimate_tokens

Dispatcher: TypeAlias = Callable[[ToolCall], object]
ExecutionStatus: TypeAlias = Literal["allowed", "truncated", "denied"]


@dataclass(frozen=True, slots=True)
class Execution:
    """One tool-call attempt and its structured, content-free outcome."""

    call: ToolCall
    decision: GateDecision
    evaluations: tuple[GateDecision, ...]
    output: str | None
    observation: Observation | None
    original_tokens: int
    refusal_count: int
    closed: bool

    @property
    def status(self) -> ExecutionStatus:
        if not self.decision.allow:
            return "denied"
        if self.observation is not None and self.observation.truncated:
            return "truncated"
        return "allowed"

    @property
    def refusal_message(self) -> str | None:
        if self.decision.allow:
            return None
        return (
            f"tool call '{self.call.name}' denied by "
            f"{self.decision.gate_name}: {self.decision.reason}"
        )

    def to_dict(self) -> dict[str, object]:
        omitted = self.original_tokens
        if self.observation is not None:
            omitted -= self.observation.tokens
        return {
            "tool": self.call.name,
            "turn": self.call.turn,
            "status": self.status,
            "decision": self.decision.to_dict(),
            "evaluations": [
                decision.to_dict() for decision in self.evaluations
            ],
            "observation": (
                self.observation.to_dict()
                if self.observation is not None
                else None
            ),
            "original_tokens": self.original_tokens,
            "omitted_tokens": omitted,
            "refusal_count": self.refusal_count,
            "closed": self.closed,
        }


class GatedToolExecutor:
    """The harness seam: evaluate the chain, dispatch on allow, record on success."""

    def __init__(
        self,
        chain: GateChain,
        dispatcher: Dispatcher,
        *,
        refusal_limit: int = 3,
    ) -> None:
        if isinstance(refusal_limit, bool) or not isinstance(refusal_limit, int):
            raise ValueError("refusal_limit must be an integer")
        if refusal_limit < 1:
            raise ValueError("refusal_limit must be at least 1")
        if not callable(dispatcher):
            raise TypeError("dispatcher must be callable")

        budget_gates = [
            gate for gate in chain.gates if isinstance(gate, BudgetGate)
        ]
        if len(budget_gates) != 1:
            raise ValueError(
                "executor requires exactly one BudgetGate in the chain"
            )

        self._chain = chain
        self._dispatcher = dispatcher
        self._budget_gate = budget_gates[0]
        self._refusal_limit = refusal_limit
        self._ledger = ObservationLedger()
        self._refusal_count = 0
        self._original_tokens = 0

    @property
    def ledger(self) -> ObservationLedger:
        return self._ledger

    @property
    def max_observation_tokens(self) -> int:
        return self._budget_gate.limit

    @property
    def refusal_count(self) -> int:
        return self._refusal_count

    @property
    def closed(self) -> bool:
        return self._refusal_count >= self._refusal_limit

    @property
    def original_tokens(self) -> int:
        return self._original_tokens

    @property
    def omitted_tokens(self) -> int:
        return self._original_tokens - self._ledger.cumulative()

    def execute(self, call: ToolCall) -> Execution:
        if self.closed:
            decision = GateDecision.deny(
                "harness",
                (
                    f"refusal limit reached: {self._refusal_count}/"
                    f"{self._refusal_limit}"
                ),
            )
            return Execution(
                call=call,
                decision=decision,
                evaluations=(),
                output=None,
                observation=None,
                original_tokens=0,
                refusal_count=self._refusal_count,
                closed=True,
            )

        last_turn = self._ledger.last_turn
        if last_turn is not None and call.turn < last_turn:
            raise ValueError(
                "call turn cannot precede the latest observation"
            )

        result = self._chain.review(call, GateContext(self._ledger))
        if not result.decision.allow:
            self._refusal_count += 1
            return Execution(
                call=call,
                decision=result.decision,
                evaluations=result.evaluations,
                output=None,
                observation=None,
                original_tokens=0,
                refusal_count=self._refusal_count,
                closed=self.closed,
            )

        raw = self._dispatcher(call)
        text = _render_output(raw)
        original = estimate_tokens(text)
        remaining = self._budget_gate.limit - self._ledger.cumulative()
        clipped, truncated = clip_text(text, remaining)
        observed = estimate_tokens(clipped)
        observation = self._ledger.record(
            call.turn,
            call.name,
            observed,
            truncated=truncated,
        )
        self._original_tokens += original

        return Execution(
            call=call,
            decision=result.decision,
            evaluations=result.evaluations,
            output=clipped,
            observation=observation,
            original_tokens=original,
            refusal_count=self._refusal_count,
            closed=self.closed,
        )


def _render_output(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "tool results must be strings or JSON-serializable values"
        ) from exc


__all__ = ["Dispatcher", "Execution", "GatedToolExecutor"]

"""Ordered gate evaluation with first-deny short-circuit semantics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .gates import GateContext, VerificationGate
from .models import GateDecision, ToolCall


@dataclass(frozen=True, slots=True)
class ChainResult:
    """The terminal decision plus every gate decision observed on the way."""

    decision: GateDecision
    evaluations: tuple[GateDecision, ...]

    @property
    def allowed(self) -> bool:
        return self.decision.allow


class GateChain:
    """An ordered list of gates; the first deny wins."""

    def __init__(self, gates: Iterable[VerificationGate]) -> None:
        self._gates = tuple(gates)
        if not self._gates:
            raise ValueError("gate chain requires at least one gate")
        seen: set[str] = set()
        for gate in self._gates:
            name = gate.name
            if not isinstance(name, str) or not name:
                raise ValueError("gate name must be a non-empty string")
            if name in seen:
                raise ValueError(f"duplicate gate name: {name}")
            seen.add(name)

    @property
    def gates(self) -> tuple[VerificationGate, ...]:
        return self._gates

    def review(self, call: ToolCall, ctx: GateContext) -> ChainResult:
        evaluations: list[GateDecision] = []
        for gate in self._gates:
            decision = gate.evaluate(call, ctx)
            if not isinstance(decision, GateDecision):
                raise TypeError("gate must return a GateDecision")
            if decision.gate_name != gate.name:
                raise ValueError(
                    f"gate '{gate.name}' returned a decision labeled "
                    f"'{decision.gate_name}'"
                )
            evaluations.append(decision)
            if not decision.allow:
                return ChainResult(decision, tuple(evaluations))
        return ChainResult(
            decision=GateDecision.permit("chain"),
            evaluations=tuple(evaluations),
        )

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        return self.review(call, ctx).decision


__all__ = ["ChainResult", "GateChain"]

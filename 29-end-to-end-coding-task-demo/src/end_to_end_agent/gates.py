"""Ordered verification gates for agent tool calls."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .ledger import ObservationLedger
from .models import GateDecision, RunLimits, ToolCall
from .tooling import (
    PathViolation,
    validate_arguments,
    validate_read_path,
    validate_write_path,
)


@dataclass(frozen=True, slots=True)
class GateContext:
    """Read-only run state available to every gate."""

    ledger: ObservationLedger
    limits: RunLimits


class VerificationGate(Protocol):
    """One deterministic check over a tool call and the run state."""

    name: str

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision: ...


@dataclass(frozen=True, slots=True)
class ChainResult:
    """The terminal decision plus every decision observed on the way."""

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
            if not isinstance(gate.name, str) or not gate.name:
                raise ValueError("gate name must be a non-empty string")
            if gate.name in seen:
                raise ValueError(f"duplicate gate name: {gate.name}")
            seen.add(gate.name)

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
                    f"gate {gate.name!r} returned decision labeled "
                    f"{decision.gate_name!r}"
                )
            evaluations.append(decision)
            if not decision.allow:
                return ChainResult(decision, tuple(evaluations))
        return ChainResult(GateDecision.permit("chain"), tuple(evaluations))

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        return self.review(call, ctx).decision


class WhitelistGate:
    """Deny any tool name outside an explicit set."""

    name = "whitelist"

    def __init__(self, allowed: Iterable[str]) -> None:
        if isinstance(allowed, str):
            raise ValueError("allowed must be an iterable of tool names, not a string")
        tools = frozenset(allowed)
        if not tools:
            raise ValueError("whitelist requires at least one tool name")
        for tool in tools:
            if not isinstance(tool, str) or not tool:
                raise ValueError("allowed tool names must be non-empty strings")
        self.allowed = tools

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del ctx
        if call.name not in self.allowed:
            return GateDecision.deny(
                self.name,
                f"tool {call.name!r} is not in the allowlist",
            )
        return GateDecision.permit(self.name)


class PathGate:
    """Deny malformed arguments and paths outside the agent's write policy."""

    name = "path"

    def __init__(self, root: str) -> None:
        if not isinstance(root, str) or not root:
            raise ValueError("root must be a non-empty path string")
        self.root = root

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del ctx
        if call.name not in {"read_file", "write_file"}:
            return GateDecision.permit(self.name)
        try:
            arguments = validate_arguments(call)
        except ValueError as exc:
            return GateDecision.deny(self.name, f"path arguments invalid: {exc}")
        try:
            if call.name == "read_file":
                validate_read_path(self.root, arguments["path"])
            else:
                validate_write_path(self.root, arguments["path"])
        except PathViolation as exc:
            return GateDecision.deny(self.name, f"path denied: {exc}")
        return GateDecision.permit(self.name)


class RunBudgetGate:
    """Deny work beyond the global step or observation-token budget."""

    name = "run_budget"

    def __init__(self, limits: RunLimits) -> None:
        if not isinstance(limits, RunLimits):
            raise TypeError("limits must be a RunLimits")
        self.limits = limits

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        if call.step > self.limits.max_steps:
            return GateDecision.deny(
                self.name,
                (
                    f"step budget exhausted: step {call.step} > "
                    f"max {self.limits.max_steps}"
                ),
            )
        used = ctx.ledger.cumulative()
        if used >= self.limits.observation_tokens:
            return GateDecision.deny(
                self.name,
                (
                    f"observation budget exhausted: {used}/"
                    f"{self.limits.observation_tokens} tokens"
                ),
            )
        return GateDecision.permit(self.name)


__all__ = [
    "ChainResult",
    "GateChain",
    "GateContext",
    "PathGate",
    "RunBudgetGate",
    "VerificationGate",
    "WhitelistGate",
]

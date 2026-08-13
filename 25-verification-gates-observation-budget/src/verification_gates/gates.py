"""Deterministic gates that decide whether a tool call may fire."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .ledger import ObservationLedger
from .models import GateDecision, ToolCall


@dataclass(frozen=True, slots=True)
class GateContext:
    """The read-only session state available to every gate."""

    ledger: ObservationLedger


class VerificationGate(Protocol):
    """One deterministic policy check over a call and the session ledger."""

    name: str

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision: ...


class WhitelistGate:
    """Deny any tool name outside an explicit set."""

    name: str = "whitelist"

    def __init__(self, allowed: Iterable[str]) -> None:
        if isinstance(allowed, str):
            raise ValueError("allowed must be an iterable of tool names, not a string")
        tools = frozenset(allowed)
        for tool in tools:
            if not isinstance(tool, str) or not tool:
                raise ValueError("allowed tool names must be non-empty strings")
        self.allowed = tools

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del ctx
        if call.name not in self.allowed:
            return GateDecision.deny(
                self.name,
                f"tool '{call.name}' is not in the allowlist",
            )
        return GateDecision.permit(self.name)


@dataclass(frozen=True, slots=True)
class RegexRule:
    """One blocked argument pattern and its stable reason."""

    pattern: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ValueError("pattern must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string")


class RegexGate:
    """Deny a call when its serialized arguments match a blocked pattern."""

    name: str = "regex"

    def __init__(self, rules: Iterable[RegexRule]) -> None:
        self.rules: tuple[RegexRule, ...] = tuple(rules)
        if not self.rules:
            raise ValueError("regex gate requires at least one rule")
        compiled: list[re.Pattern[str]] = []
        for rule in self.rules:
            if not isinstance(rule, RegexRule):
                raise ValueError("regex rules must be RegexRule instances")
            try:
                compiled.append(re.compile(rule.pattern))
            except re.error as exc:
                raise ValueError(f"invalid regex pattern: {rule.pattern}") from exc
        self._patterns: tuple[re.Pattern[str], ...] = tuple(compiled)

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del ctx
        payload = json.dumps(
            dict(call.arguments),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        for rule, pattern in zip(self.rules, self._patterns, strict=True):
            if pattern.search(payload):
                return GateDecision.deny(
                    self.name,
                    f"argument matches blocked pattern: {rule.reason}",
                )
        return GateDecision.permit(self.name)


class RecencyGate:
    """Deny a call grounded in an observation outside the visible window."""

    name: str = "recency"

    def __init__(self, window: int) -> None:
        _require_int("window", window, minimum=1)
        self.window = window

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del ctx
        observed = call.observed_turn
        if observed is None:
            return GateDecision.permit(self.name)
        oldest = call.turn - self.window + 1
        if observed < oldest:
            return GateDecision.deny(
                self.name,
                (
                    f"observation from turn {observed} is outside the "
                    f"{self.window}-turn window at turn {call.turn}"
                ),
            )
        return GateDecision.permit(self.name)


class BudgetGate:
    """Deny every call once the cumulative observation ceiling is reached."""

    name: str = "budget"

    def __init__(self, limit: int) -> None:
        _require_int("limit", limit, minimum=0)
        self.limit = limit

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del call
        used = ctx.ledger.cumulative()
        if used >= self.limit:
            return GateDecision.deny(
                self.name,
                f"observation budget exhausted: {used}/{self.limit} tokens",
            )
        return GateDecision.permit(self.name)


class PerToolBudgetGate:
    """Deny a call once its tool's cumulative observation share is spent."""

    name: str = "tool_budget"

    def __init__(self, limits: Mapping[str, int]) -> None:
        if not isinstance(limits, Mapping):
            raise ValueError("limits must be a mapping of tool names to token limits")
        copied: dict[str, int] = {}
        for tool, limit in limits.items():
            if not isinstance(tool, str) or not tool:
                raise ValueError("limit keys must be non-empty tool names")
            _require_int(f"limit for '{tool}'", limit, minimum=0)
            copied[tool] = limit
        if not copied:
            raise ValueError("per-tool budget gate requires at least one limit")
        self.limits = copied

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        limit = self.limits.get(call.name)
        if limit is None:
            return GateDecision.permit(self.name)
        used = ctx.ledger.per_tool(call.name)
        if used >= limit:
            return GateDecision.deny(
                self.name,
                (
                    f"per-tool observation budget exhausted for '{call.name}': "
                    f"{used}/{limit} tokens"
                ),
            )
        return GateDecision.permit(self.name)


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


__all__ = [
    "BudgetGate",
    "GateContext",
    "PerToolBudgetGate",
    "RecencyGate",
    "RegexGate",
    "RegexRule",
    "VerificationGate",
    "WhitelistGate",
]

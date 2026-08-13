"""Deterministic verification gates and an observation budget for tool calls."""

from __future__ import annotations

from .gates import (
    BudgetGate,
    GateContext,
    PerToolBudgetGate,
    RecencyGate,
    RegexGate,
    RegexRule,
    VerificationGate,
    WhitelistGate,
)
from .ledger import ObservationLedger
from .models import GateDecision, Observation, ToolCall
from .tokens import TRUNCATION_MARKER, clip_text, estimate_tokens

__version__ = "0.1.0"

__all__ = [
    "BudgetGate",
    "GateContext",
    "GateDecision",
    "Observation",
    "ObservationLedger",
    "PerToolBudgetGate",
    "RecencyGate",
    "RegexGate",
    "RegexRule",
    "TRUNCATION_MARKER",
    "ToolCall",
    "VerificationGate",
    "WhitelistGate",
    "clip_text",
    "estimate_tokens",
    "__version__",
]

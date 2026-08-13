from __future__ import annotations

import pytest

from verification_gates.gates import (
    BudgetGate,
    GateContext,
    PerToolBudgetGate,
    RecencyGate,
    RegexGate,
    RegexRule,
    WhitelistGate,
)
from verification_gates.ledger import ObservationLedger
from verification_gates.models import ToolCall


def make_ctx() -> GateContext:
    return GateContext(ObservationLedger())


def test_whitelist_allows_and_denies_with_stable_reason() -> None:
    gate = WhitelistGate({"read_file", "grep"})
    ctx = make_ctx()

    allowed = gate.evaluate(ToolCall(1, "read_file", {}), ctx)
    denied = gate.evaluate(ToolCall(1, "shell", {}), ctx)

    assert allowed.allow is True
    assert allowed.reason is None
    assert allowed.gate_name == "whitelist"
    assert denied.allow is False
    assert denied.gate_name == "whitelist"
    assert denied.reason == "tool 'shell' is not in the allowlist"


def test_whitelist_empty_set_fails_closed_and_rejects_bad_config() -> None:
    empty = WhitelistGate([])
    assert empty.evaluate(ToolCall(1, "read_file", {}), make_ctx()).allow is False

    with pytest.raises(ValueError):
        WhitelistGate("read_file")
    with pytest.raises(ValueError):
        WhitelistGate([""])
    with pytest.raises(ValueError):
        WhitelistGate([1])  # type: ignore[list-item]


def test_regex_blocks_nested_arguments_and_allows_clean_calls() -> None:
    gate = RegexGate(
        [
            RegexRule(r"\brm\s+-rf\b", "destructive shell command"),
            RegexRule(r"://(?:10\.|127\.|192\.168\.)", "private network address"),
        ]
    )
    ctx = make_ctx()

    shell = gate.evaluate(
        ToolCall(1, "shell", {"command": "rm -rf /tmp/demo"}),
        ctx,
    )
    nested_http = gate.evaluate(
        ToolCall(1, "http_get", {"request": {"url": "http://10.0.0.7/admin"}}),
        ctx,
    )
    clean = gate.evaluate(ToolCall(1, "read_file", {"path": "src/app.py"}), ctx)

    assert shell.allow is False
    assert shell.reason == (
        "argument matches blocked pattern: destructive shell command"
    )
    assert nested_http.allow is False
    assert nested_http.reason == (
        "argument matches blocked pattern: private network address"
    )
    assert clean.allow is True
    assert clean.reason is None


def test_regex_rules_and_patterns_are_validated_at_construction() -> None:
    with pytest.raises(ValueError):
        RegexGate([])
    with pytest.raises(ValueError):
        RegexGate([RegexRule("(", "bad pattern")])
    with pytest.raises(ValueError):
        RegexRule("", "reason")
    with pytest.raises(ValueError):
        RegexRule("x", "")


def test_recency_allows_inside_the_window_and_denies_at_the_boundary() -> None:
    gate = RecencyGate(2)
    ctx = make_ctx()

    assert gate.evaluate(
        ToolCall(1, "read_file", {}, observed_turn=0), ctx
    ).allow is True
    assert gate.evaluate(
        ToolCall(3, "read_file", {}, observed_turn=2), ctx
    ).allow is True
    assert gate.evaluate(ToolCall(3, "read_file", {}), ctx).allow is True

    denied = gate.evaluate(
        ToolCall(3, "read_file", {}, observed_turn=1), ctx
    )
    assert denied.allow is False
    assert denied.gate_name == "recency"
    assert denied.reason == (
        "observation from turn 1 is outside the 2-turn window at turn 3"
    )


def test_recency_window_one_and_invalid_windows() -> None:
    gate = RecencyGate(1)
    ctx = make_ctx()

    assert gate.evaluate(
        ToolCall(2, "read_file", {}, observed_turn=1), ctx
    ).allow is False
    assert gate.evaluate(
        ToolCall(2, "read_file", {}, observed_turn=2), ctx
    ).allow is True

    with pytest.raises(ValueError):
        RecencyGate(0)
    with pytest.raises(ValueError):
        RecencyGate(True)


def test_budget_allows_below_and_denies_at_or_above_the_ceiling() -> None:
    ledger = ObservationLedger()
    gate = BudgetGate(100)
    ctx = GateContext(ledger)

    assert gate.evaluate(ToolCall(1, "read_file", {}), ctx).allow is True
    ledger.record(1, "read_file", 99)
    assert gate.evaluate(ToolCall(1, "read_file", {}), ctx).allow is True
    ledger.record(1, "grep", 1)

    at_limit = gate.evaluate(ToolCall(2, "read_file", {}), ctx)
    assert at_limit.allow is False
    assert at_limit.reason == "observation budget exhausted: 100/100 tokens"

    ledger.record(2, "grep", 10)
    above_limit = gate.evaluate(ToolCall(3, "read_file", {}), ctx)
    assert above_limit.reason == "observation budget exhausted: 110/100 tokens"

    zero = BudgetGate(0)
    assert zero.evaluate(ToolCall(1, "read_file", {}), make_ctx()).allow is False
    with pytest.raises(ValueError):
        BudgetGate(-1)
    with pytest.raises(ValueError):
        BudgetGate(True)


def test_per_tool_budget_isolated_and_denies_at_its_limit() -> None:
    ledger = ObservationLedger()
    ledger.record(1, "read_file", 90)
    ledger.record(1, "grep", 500)
    gate = PerToolBudgetGate({"read_file": 100})
    ctx = GateContext(ledger)

    assert gate.evaluate(ToolCall(1, "read_file", {}), ctx).allow is True
    assert gate.evaluate(ToolCall(1, "grep", {}), ctx).allow is True

    ledger.record(2, "read_file", 10)
    denied = gate.evaluate(ToolCall(2, "read_file", {}), ctx)
    assert denied.allow is False
    assert denied.reason == (
        "per-tool observation budget exhausted for 'read_file': 100/100 tokens"
    )

    zero = PerToolBudgetGate({"grep": 0})
    assert zero.evaluate(ToolCall(1, "grep", {}), ctx).allow is False


def test_per_tool_budget_rejects_bad_configurations() -> None:
    with pytest.raises(ValueError):
        PerToolBudgetGate({})
    with pytest.raises(ValueError):
        PerToolBudgetGate({"": 1})
    with pytest.raises(ValueError):
        PerToolBudgetGate({"read_file": -1})
    with pytest.raises(ValueError):
        PerToolBudgetGate({"read_file": True})
    with pytest.raises(ValueError):
        PerToolBudgetGate([])  # type: ignore[arg-type]

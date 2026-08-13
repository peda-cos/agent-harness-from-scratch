from __future__ import annotations

import pytest

from verification_gates.chain import ChainResult, GateChain
from verification_gates.gates import GateContext
from verification_gates.ledger import ObservationLedger
from verification_gates.models import GateDecision, ToolCall


class SpyGate:
    def __init__(self, name: str, decision: GateDecision) -> None:
        self.name = name
        self._decision = decision
        self.calls = 0

    def evaluate(self, call: ToolCall, ctx: GateContext) -> GateDecision:
        del call, ctx
        self.calls += 1
        return self._decision


class BadGate:
    def __init__(self, name: str) -> None:
        self.name = name

    def evaluate(self, call: ToolCall, ctx: GateContext) -> object:
        del call, ctx
        return object()


def make_ctx() -> GateContext:
    return GateContext(ObservationLedger())


def test_all_pass_returns_chain_allow_and_evaluation_trail() -> None:
    first = SpyGate("first", GateDecision.permit("first"))
    second = SpyGate("second", GateDecision.permit("second"))
    chain = GateChain([first, second])

    result = chain.review(ToolCall(1, "read_file", {}), make_ctx())

    assert isinstance(result, ChainResult)
    assert result.allowed is True
    assert result.decision.allow is True
    assert result.decision.gate_name == "chain"
    assert result.decision.reason is None
    assert [decision.gate_name for decision in result.evaluations] == [
        "first",
        "second",
    ]
    assert [first.calls, second.calls] == [1, 1]


def test_first_deny_short_circuits_and_later_gates_are_not_called() -> None:
    first = SpyGate("first", GateDecision.permit("first"))
    denied = GateDecision.deny("second", "blocked by policy")
    second = SpyGate("second", denied)
    third = SpyGate("third", GateDecision.permit("third"))
    chain = GateChain([first, second, third])

    result = chain.review(ToolCall(1, "read_file", {}), make_ctx())

    assert result.allowed is False
    assert result.decision is denied
    assert result.evaluations == (
        GateDecision.permit("first"),
        denied,
    )
    assert [first.calls, second.calls, third.calls] == [1, 1, 0]


def test_evaluate_returns_the_terminal_decision_only() -> None:
    chain = GateChain(
        [
            SpyGate("first", GateDecision.permit("first")),
            SpyGate("second", GateDecision.deny("second", "no")),
        ]
    )
    decision = chain.evaluate(ToolCall(1, "read_file", {}), make_ctx())

    assert decision.allow is False
    assert decision.gate_name == "second"
    assert decision.reason == "no"


def test_constructor_rejects_empty_duplicate_and_bad_names() -> None:
    with pytest.raises(ValueError, match="at least one gate"):
        GateChain([])

    duplicate = [
        SpyGate("same", GateDecision.permit("same")),
        SpyGate("same", GateDecision.permit("same")),
    ]
    with pytest.raises(ValueError, match="duplicate gate name: same"):
        GateChain(duplicate)

    with pytest.raises(ValueError, match="non-empty"):
        GateChain([SpyGate("", GateDecision.permit(""))])


def test_review_rejects_mislabeled_or_foreign_decisions() -> None:
    mislabeled = GateChain(
        [SpyGate("first", GateDecision.permit("somewhere_else"))]
    )
    with pytest.raises(ValueError, match="returned a decision labeled"):
        mislabeled.review(ToolCall(1, "read_file", {}), make_ctx())

    foreign = GateChain([BadGate("bad")])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="GateDecision"):
        foreign.review(ToolCall(1, "read_file", {}), make_ctx())

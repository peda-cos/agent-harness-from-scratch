from __future__ import annotations

import pytest

from verification_gates import (
    BudgetGate,
    GateChain,
    PerToolBudgetGate,
    RecencyGate,
    RegexGate,
    RegexRule,
    VerificationGate,
    WhitelistGate,
)
from verification_gates.executor import Dispatcher, GatedToolExecutor
from verification_gates.models import ToolCall
from verification_gates.tokens import estimate_tokens


def make_chain(
    *,
    allowed: tuple[str, ...] = ("read_file",),
    limit: int = 200,
    window: int = 10,
    per_tool: dict[str, int] | None = None,
) -> GateChain:
    gates: list[VerificationGate] = [
        WhitelistGate(allowed),
        RegexGate([RegexRule("__never_matches__", "never")]),
        RecencyGate(window),
        BudgetGate(limit),
    ]
    if per_tool is not None:
        gates.append(PerToolBudgetGate(per_tool))
    return GateChain(gates)


def make_dispatcher(
    outputs: dict[str, object],
    calls: list[ToolCall],
) -> Dispatcher:
    def dispatch(call: ToolCall) -> object:
        calls.append(call)
        return outputs[call.name]

    return dispatch


def make_executor(
    outputs: dict[str, object],
    calls: list[ToolCall],
    *,
    allowed: tuple[str, ...] = ("read_file",),
    limit: int = 200,
    window: int = 10,
    refusal_limit: int = 3,
    per_tool: dict[str, int] | None = None,
) -> GatedToolExecutor:
    return GatedToolExecutor(
        make_chain(
            allowed=allowed,
            limit=limit,
            window=window,
            per_tool=per_tool,
        ),
        make_dispatcher(outputs, calls),
        refusal_limit=refusal_limit,
    )


def test_allowed_call_records_observation_and_output() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "x" * 400}, calls)
    call = ToolCall(1, "read_file", {"path": "src/app.py"}, observed_turn=0)

    execution = executor.execute(call)

    assert execution.status == "allowed"
    assert execution.decision.allow is True
    assert execution.decision.gate_name == "chain"
    assert execution.output == "x" * 400
    assert execution.observation is not None
    assert execution.observation.tokens == 100
    assert execution.observation.truncated is False
    assert execution.original_tokens == 100
    assert execution.refusal_count == 0
    assert execution.closed is False
    assert calls == [call]
    assert executor.ledger.cumulative() == 100
    assert executor.ledger.per_tool("read_file") == 100
    assert executor.original_tokens == 100
    assert executor.omitted_tokens == 0


def test_denied_call_does_not_dispatch_or_write_ledger() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"shell": "should not run"}, calls)
    call = ToolCall(1, "shell", {"command": "echo hi"})

    execution = executor.execute(call)

    assert execution.status == "denied"
    assert execution.decision.gate_name == "whitelist"
    assert execution.decision.reason == "tool 'shell' is not in the allowlist"
    assert execution.refusal_message == (
        "tool call 'shell' denied by whitelist: "
        "tool 'shell' is not in the allowlist"
    )
    assert execution.output is None
    assert execution.observation is None
    assert execution.original_tokens == 0
    assert execution.refusal_count == 1
    assert calls == []
    assert len(executor.ledger) == 0
    assert executor.closed is False


def test_output_is_clipped_to_exact_remaining_budget() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "x" * 400}, calls, limit=30)

    first = executor.execute(ToolCall(1, "read_file", {}))
    second = executor.execute(ToolCall(2, "read_file", {}))

    assert first.status == "truncated"
    assert first.observation is not None
    assert first.observation.tokens == 30
    assert first.observation.truncated is True
    assert first.original_tokens == 100
    assert executor.omitted_tokens == 70
    assert estimate_tokens(first.output or "") == 30
    assert second.status == "denied"
    assert second.decision.gate_name == "budget"
    assert second.refusal_count == 1
    assert calls == [ToolCall(1, "read_file", {})]
    assert executor.ledger.cumulative() == 30


def test_exact_budget_output_is_not_truncated() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "x" * 120}, calls, limit=30)

    first = executor.execute(ToolCall(1, "read_file", {}))
    second = executor.execute(ToolCall(2, "read_file", {}))

    assert first.status == "allowed"
    assert first.observation is not None
    assert first.observation.tokens == 30
    assert first.observation.truncated is False
    assert executor.omitted_tokens == 0
    assert second.decision.gate_name == "budget"
    assert calls == [ToolCall(1, "read_file", {})]


def test_zero_budget_denies_before_dispatch() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "content"}, calls, limit=0)

    execution = executor.execute(ToolCall(1, "read_file", {}))

    assert execution.decision.gate_name == "budget"
    assert execution.decision.reason == (
        "observation budget exhausted: 0/0 tokens"
    )
    assert calls == []
    assert len(executor.ledger) == 0


def test_recency_denial_through_executor() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "x" * 40}, calls, window=2)

    first = executor.execute(ToolCall(1, "read_file", {}))
    stale = executor.execute(
        ToolCall(3, "read_file", {}, observed_turn=1)
    )

    assert first.status == "allowed"
    assert stale.status == "denied"
    assert stale.decision.gate_name == "recency"
    assert stale.decision.reason == (
        "observation from turn 1 is outside the 2-turn window at turn 3"
    )
    assert stale.refusal_count == 1
    assert calls == [ToolCall(1, "read_file", {})]
    assert executor.ledger.cumulative() == 10


def test_refusal_limit_closes_and_blocks_without_dispatch() -> None:
    calls: list[ToolCall] = []
    executor = make_executor(
        {"read_file": "content"},
        calls,
        refusal_limit=2,
    )

    first = executor.execute(ToolCall(1, "shell", {}))
    second = executor.execute(ToolCall(2, "shell", {}))
    third = executor.execute(ToolCall(3, "read_file", {}))

    assert first.closed is False
    assert second.closed is True
    assert third.status == "denied"
    assert third.decision.gate_name == "harness"
    assert third.decision.reason == "refusal limit reached: 2/2"
    assert third.evaluations == ()
    assert third.refusal_count == 2
    assert third.closed is True
    assert calls == []
    assert len(executor.ledger) == 0


def test_non_monotonic_call_is_rejected_before_dispatch() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "content"}, calls)

    executor.execute(ToolCall(2, "read_file", {}))
    with pytest.raises(ValueError, match="precede"):
        executor.execute(ToolCall(1, "read_file", {}))

    assert calls == [ToolCall(2, "read_file", {})]
    assert len(executor.ledger) == 1
    assert executor.refusal_count == 0


def test_dict_result_serialization_is_sorted_and_counted() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": {"b": 2, "a": 1}}, calls)

    execution = executor.execute(ToolCall(1, "read_file", {}))

    assert execution.output == '{"a":1,"b":2}'
    assert execution.observation is not None
    assert execution.observation.tokens == estimate_tokens(
        '{"a":1,"b":2}'
    )
    assert execution.status == "allowed"


def test_unsupported_result_type_raises_after_dispatch_without_row() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": object()}, calls)

    with pytest.raises(TypeError, match="JSON-serializable"):
        executor.execute(ToolCall(1, "read_file", {}))

    assert len(calls) == 1
    assert len(executor.ledger) == 0
    assert executor.original_tokens == 0
    assert executor.refusal_count == 0


def test_dispatcher_exception_propagates_without_row() -> None:
    calls: list[ToolCall] = []

    def dispatch(call: ToolCall) -> object:
        calls.append(call)
        raise RuntimeError("tool crashed")

    executor = GatedToolExecutor(make_chain(), dispatch)

    with pytest.raises(RuntimeError, match="tool crashed"):
        executor.execute(ToolCall(1, "read_file", {}))

    assert len(calls) == 1
    assert len(executor.ledger) == 0
    assert executor.refusal_count == 0


class SecondBudgetGate(BudgetGate):
    name = "budget_two"


def test_executor_requires_exactly_one_budget_gate() -> None:
    calls: list[ToolCall] = []
    dispatcher = make_dispatcher({"read_file": "content"}, calls)

    without_budget = GateChain([WhitelistGate({"read_file"})])
    with pytest.raises(ValueError, match="exactly one BudgetGate"):
        GatedToolExecutor(without_budget, dispatcher)

    two_budgets = GateChain(
        [
            WhitelistGate({"read_file"}),
            BudgetGate(100),
            SecondBudgetGate(100),
        ]
    )
    with pytest.raises(ValueError, match="exactly one BudgetGate"):
        GatedToolExecutor(two_budgets, dispatcher)


def test_execution_to_dict_is_content_free() -> None:
    calls: list[ToolCall] = []
    executor = make_executor({"read_file": "x" * 40}, calls)
    call = ToolCall(1, "read_file", {"path": "secret.txt"})

    payload = executor.execute(call).to_dict()

    assert payload["tool"] == "read_file"
    assert payload["turn"] == 1
    assert payload["status"] == "allowed"
    assert payload["observation"] == {
        "turn": 1,
        "tool": "read_file",
        "tokens": 10,
        "truncated": False,
    }
    assert "arguments" not in payload
    assert "output" not in payload
    assert call.to_dict()["arguments"] == {"path": "secret.txt"}


def test_three_turn_loop_clips_then_refuses_at_the_ceiling() -> None:
    calls: list[ToolCall] = []
    executor = make_executor(
        {"read_file": "r" * 400, "grep": "g" * 400},
        calls,
        allowed=("read_file", "grep"),
        limit=150,
    )

    first = executor.execute(ToolCall(1, "read_file", {}))
    second = executor.execute(ToolCall(2, "grep", {}))
    third = executor.execute(ToolCall(3, "read_file", {}))

    assert first.status == "allowed"
    assert second.status == "truncated"
    assert second.observation is not None
    assert second.observation.tokens == 50
    assert third.status == "denied"
    assert third.decision.gate_name == "budget"
    assert executor.ledger.cumulative() == 150
    assert executor.original_tokens == 200
    assert executor.omitted_tokens == 50
    assert executor.refusal_count == 1
    assert calls == [
        ToolCall(1, "read_file", {}),
        ToolCall(2, "grep", {}),
    ]


def test_invalid_refusal_limit_is_rejected() -> None:
    calls: list[ToolCall] = []
    dispatcher = make_dispatcher({"read_file": "content"}, calls)
    chain = make_chain()

    with pytest.raises(ValueError):
        GatedToolExecutor(chain, dispatcher, refusal_limit=0)
    with pytest.raises(ValueError):
        GatedToolExecutor(chain, dispatcher, refusal_limit=True)
    with pytest.raises(TypeError):
        GatedToolExecutor(chain, None)  # type: ignore[arg-type]

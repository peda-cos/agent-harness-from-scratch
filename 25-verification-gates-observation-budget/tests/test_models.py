from __future__ import annotations

import pytest

from verification_gates.models import GateDecision, Observation, ToolCall


def test_tool_call_copies_arguments_and_serializes() -> None:
    arguments: dict[str, object] = {
        "path": "src/app.py",
        "nested": {"b": 2, "a": 1},
    }
    call = ToolCall(
        turn=2,
        name="read_file",
        arguments=arguments,
        observed_turn=1,
    )
    arguments["path"] = "changed.py"

    assert call.arguments["path"] == "src/app.py"
    assert call.to_dict() == {
        "turn": 2,
        "name": "read_file",
        "arguments": {"path": "src/app.py", "nested": {"b": 2, "a": 1}},
        "observed_turn": 1,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"turn": 0, "name": "read_file", "arguments": {}},
        {"turn": True, "name": "read_file", "arguments": {}},
        {"turn": 1, "name": "", "arguments": {}},
        {"turn": 1, "name": "read_file", "arguments": []},
        {"turn": 1, "name": "read_file", "arguments": {1: "x"}},
        {"turn": 1, "name": "read_file", "arguments": {"x": object()}},
        {"turn": 1, "name": "read_file", "arguments": {"x": float("nan")}},
        {
            "turn": 1,
            "name": "read_file",
            "arguments": {},
            "observed_turn": -1,
        },
        {
            "turn": 2,
            "name": "read_file",
            "arguments": {},
            "observed_turn": 3,
        },
        {
            "turn": 2,
            "name": "read_file",
            "arguments": {},
            "observed_turn": True,
        },
    ],
)
def test_tool_call_rejects_bad_shapes(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ToolCall(**kwargs)  # type: ignore[arg-type]


def test_gate_decision_invariants_and_serialization() -> None:
    permitted = GateDecision.permit("whitelist")
    denied = GateDecision.deny("regex", "argument matches blocked pattern: rm -rf")

    assert permitted.allow is True
    assert permitted.reason is None
    assert permitted.gate_name == "whitelist"
    assert permitted.to_dict() == {
        "allow": True,
        "reason": None,
        "gate_name": "whitelist",
    }
    assert denied.to_dict() == {
        "allow": False,
        "reason": "argument matches blocked pattern: rm -rf",
        "gate_name": "regex",
    }

    with pytest.raises(ValueError):
        GateDecision(allow=True, reason="why", gate_name="chain")
    with pytest.raises(ValueError):
        GateDecision(allow=False, reason=None, gate_name="chain")
    with pytest.raises(ValueError):
        GateDecision(allow=False, reason="", gate_name="chain")
    with pytest.raises(ValueError):
        GateDecision(allow=False, reason="why", gate_name="")
    with pytest.raises(ValueError):
        GateDecision(allow=1, reason=None, gate_name="chain")  # type: ignore[arg-type]


def test_observation_validation_and_serialization() -> None:
    observation = Observation(turn=3, tool="grep", tokens=42, truncated=True)
    assert observation.to_dict() == {
        "turn": 3,
        "tool": "grep",
        "tokens": 42,
        "truncated": True,
    }

    for kwargs in (
        {"turn": 0, "tool": "grep", "tokens": 1},
        {"turn": 1, "tool": "", "tokens": 1},
        {"turn": 1, "tool": "grep", "tokens": -1},
        {"turn": 1, "tool": "grep", "tokens": True},
        {"turn": 1, "tool": "grep", "tokens": 1, "truncated": "yes"},
    ):
        with pytest.raises(ValueError):
            Observation(**kwargs)  # type: ignore[arg-type]

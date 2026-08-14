from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from verification_gates import (  # noqa: E402
    BudgetGate,
    ChainResult,
    GateChain,
    GateContext,
    GatedToolExecutor,
    Observation,
    ObservationLedger,
    PerToolBudgetGate,
    RecencyGate,
    RegexGate,
    RegexRule,
    ToolCall,
    VerificationGate,
    WhitelistGate,
    estimate_tokens,
)
from verification_gates.executor import Execution  # noqa: E402

APP_PY = "# app.py\n" + "".join(
    f"value_{index:02d} = {index}\n" for index in range(40)
)
GREP_OUTPUT = "".join(
    f"src/app.py:{index}: match value_{index:02d}\n" for index in range(20)
)
README_TEXT = "# Verification Gates\n\nA short fixture for the denied third call.\n"

TOOL_HANDLERS: dict[str, Callable[..., object]] = {}


def _read_file(path: str) -> str:
    if path == "src/app.py":
        return APP_PY
    return README_TEXT


def _grep(pattern: str, path: str) -> str:
    del pattern, path
    return GREP_OUTPUT


def _shell(command: str) -> str:
    return f"simulated shell: {command}"


def _http_get(url: str) -> str:
    return f"simulated http: {url}"


def _delete_repo() -> str:
    return "simulated deletion"


TOOL_HANDLERS.update(
    {
        "read_file": _read_file,
        "grep": _grep,
        "shell": _shell,
        "http_get": _http_get,
        "delete_repo": _delete_repo,
    }
)


class Checker:
    def __init__(self) -> None:
        self.count = 0

    def expect(self, condition: bool, message: str) -> None:
        self.count += 1
        if not condition:
            raise AssertionError(message)


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, sort_keys=True, separators=(",", ":")))


def _observation(execution: Execution) -> Observation:
    if execution.observation is None:
        raise AssertionError("expected an allowed execution with an observation")
    return execution.observation


def _build_chain() -> GateChain:
    gates: list[VerificationGate] = [
        WhitelistGate({"read_file", "grep", "shell", "http_get"}),
        RegexGate(
            [
                RegexRule(r"\brm\s+-rf\b", "destructive shell command"),
                RegexRule(
                    r"://(?:10\.|127\.|192\.168\.)",
                    "private network address",
                ),
            ]
        ),
        RecencyGate(2),
        BudgetGate(200),
        PerToolBudgetGate({"read_file": 180, "grep": 180}),
    ]
    return GateChain(gates)


def _probe(
    name: str,
    chain: GateChain,
    call: ToolCall,
    ledger: ObservationLedger | None = None,
) -> ChainResult:
    active_ledger = ledger if ledger is not None else ObservationLedger()
    result = chain.review(call, GateContext(active_ledger))
    _emit(
        {
            "event": "probe",
            "name": name,
            "tool": call.name,
            "gate": result.decision.gate_name,
            "allow": result.decision.allow,
            "reason": result.decision.reason,
            "evaluations": [
                decision.gate_name for decision in result.evaluations
            ],
        }
    )
    return result


def run_demo() -> None:
    checker = Checker()
    chain = _build_chain()
    dispatched: list[str] = []

    def dispatch(call: ToolCall) -> object:
        dispatched.append(call.name)
        handler = TOOL_HANDLERS[call.name]
        return handler(**dict(call.arguments))

    executor = GatedToolExecutor(chain, dispatch, refusal_limit=1)
    calls = [
        ToolCall(1, "read_file", {"path": "src/app.py"}, observed_turn=0),
        ToolCall(
            2,
            "grep",
            {"pattern": "value_", "path": "src/app.py"},
            observed_turn=1,
        ),
        ToolCall(3, "read_file", {"path": "README.md"}, observed_turn=2),
    ]

    executions: list[Execution] = []
    for call in calls:
        _emit(
            {
                "event": "call",
                "turn": call.turn,
                "tool": call.name,
                "arguments": dict(call.arguments),
                "observed_turn": call.observed_turn,
            }
        )
        execution = executor.execute(call)
        executions.append(execution)
        for decision in execution.evaluations:
            _emit(
                {
                    "event": "gate",
                    "turn": call.turn,
                    "gate": decision.gate_name,
                    "allow": decision.allow,
                    "reason": decision.reason,
                }
            )
        if execution.observation is not None:
            _emit(
                {
                    "event": "observation",
                    **execution.observation.to_dict(),
                    "cumulative": executor.ledger.cumulative(),
                }
            )
        else:
            _emit(
                {
                    "event": "denied",
                    "turn": call.turn,
                    "tool": call.name,
                    "gate": execution.decision.gate_name,
                    "reason": execution.decision.reason,
                    "message": execution.refusal_message,
                    "refusals": execution.refusal_count,
                    "closed": execution.closed,
                }
            )

    checker.expect(len(executions) == 3, "demo must attempt exactly three turns")
    first, second, third = executions
    first_observation = _observation(first)
    second_observation = _observation(second)

    checker.expect(first.status == "allowed", "turn 1 must be allowed")
    checker.expect(
        first_observation.truncated is False,
        "turn 1 must fit without truncation",
    )
    checker.expect(
        first_observation.tokens == estimate_tokens(APP_PY),
        "turn 1 must record the full app fixture",
    )
    checker.expect(second.status == "truncated", "turn 2 must be clipped")
    checker.expect(
        second_observation.truncated is True,
        "turn 2 observation must carry the truncated flag",
    )
    checker.expect(
        first_observation.tokens + second_observation.tokens == 200,
        "the two allowed observations must exactly fill the budget",
    )
    checker.expect(
        second_observation.tokens == 200 - first_observation.tokens,
        "turn 2 must receive only the remaining budget",
    )
    checker.expect(third.status == "denied", "turn 3 must be denied")
    checker.expect(
        third.decision.gate_name == "budget",
        "turn 3 must be denied by the global budget gate",
    )
    checker.expect(third.observation is None, "a denied call has no observation")
    checker.expect(
        dispatched == ["read_file", "grep"],
        "denied calls must not reach the dispatcher",
    )
    checker.expect(
        executor.ledger.cumulative() == 200,
        "the ledger must stop exactly at the ceiling",
    )
    checker.expect(executor.refusal_count == 1, "the refusal must be counted")
    checker.expect(executor.closed is True, "one refusal must close this demo")
    checker.expect(
        executor.original_tokens
        == estimate_tokens(APP_PY) + estimate_tokens(GREP_OUTPUT),
        "original token accounting must include the omitted text",
    )
    checker.expect(
        executor.omitted_tokens == executor.original_tokens - 200,
        "omitted tokens must be the unread remainder",
    )
    checker.expect(
        [decision.gate_name for decision in third.evaluations]
        == ["whitelist", "regex", "recency", "budget"],
        "the budget deny must short-circuit before the per-tool gate",
    )

    visible = executor.ledger.visible(3, 2)
    masked = executor.ledger.masked(3, 2)
    _emit(
        {
            "event": "context",
            "turn": 3,
            "window": 2,
            "visible": [row.to_dict() for row in visible],
            "masked": [row.to_dict() for row in masked],
            "visible_tokens": sum(row.tokens for row in visible),
            "masked_tokens": sum(row.tokens for row in masked),
        }
    )
    checker.expect(visible == (second_observation,), "only turn 2 remains visible")
    checker.expect(masked == (first_observation,), "turn 1 is masked")

    whitelist_probe = _probe(
        "whitelist",
        chain,
        ToolCall(4, "delete_repo", {}),
    )
    checker.expect(
        whitelist_probe.decision.gate_name == "whitelist"
        and whitelist_probe.decision.allow is False,
        "an unknown tool must be denied by the whitelist",
    )

    shell_probe = _probe(
        "regex_shell",
        chain,
        ToolCall(4, "shell", {"command": "rm -rf /tmp/demo"}),
    )
    checker.expect(
        shell_probe.decision.gate_name == "regex"
        and shell_probe.decision.allow is False,
        "a destructive shell argument must be denied by the regex gate",
    )

    ip_probe = _probe(
        "regex_private_ip",
        chain,
        ToolCall(4, "http_get", {"url": "http://10.0.0.7/admin"}),
    )
    checker.expect(
        ip_probe.decision.gate_name == "regex"
        and ip_probe.decision.allow is False,
        "a private-network URL must be denied by the regex gate",
    )

    recency_ledger = ObservationLedger()
    recency_ledger.record(1, "read_file", 10)
    recency_probe = _probe(
        "recency",
        chain,
        ToolCall(
            6,
            "read_file",
            {"path": "src/app.py"},
            observed_turn=1,
        ),
        recency_ledger,
    )
    checker.expect(
        recency_probe.decision.gate_name == "recency"
        and recency_probe.decision.allow is False,
        "a masked grounding observation must be denied",
    )

    per_tool_ledger = ObservationLedger()
    per_tool_ledger.record(1, "read_file", 180)
    per_tool_probe = _probe(
        "per_tool_budget",
        chain,
        ToolCall(2, "read_file", {"path": "src/app.py"}),
        per_tool_ledger,
    )
    checker.expect(
        per_tool_probe.decision.gate_name == "tool_budget"
        and per_tool_probe.decision.allow is False,
        "the per-tool ceiling must deny its own tool",
    )

    refused_dispatches: list[str] = []

    def deny_dispatch(call: ToolCall) -> object:
        refused_dispatches.append(call.name)
        return "must not dispatch"

    deny_chain = GateChain(
        [WhitelistGate({"read_file"}), BudgetGate(200)]
    )
    deny_executor = GatedToolExecutor(
        deny_chain,
        deny_dispatch,
        refusal_limit=2,
    )
    first_deny = deny_executor.execute(ToolCall(1, "delete_repo", {}))
    second_deny = deny_executor.execute(ToolCall(2, "delete_repo", {}))
    third_deny = deny_executor.execute(ToolCall(3, "read_file", {}))
    _emit(
        {
            "event": "probe",
            "name": "refusal_limit",
            "first_gate": first_deny.decision.gate_name,
            "first_closed": first_deny.closed,
            "second_closed": second_deny.closed,
            "third_gate": third_deny.decision.gate_name,
            "third_evaluations": len(third_deny.evaluations),
            "refusals": third_deny.refusal_count,
            "dispatched": len(refused_dispatches),
        }
    )
    checker.expect(
        first_deny.closed is False and second_deny.closed is True,
        "the refusal threshold must close on the second denial",
    )
    checker.expect(
        third_deny.decision.gate_name == "harness"
        and third_deny.evaluations == (),
        "a closed loop must reject calls before gate evaluation",
    )
    checker.expect(third_deny.refusal_count == 2, "closure must freeze the count")
    checker.expect(refused_dispatches == [], "closed calls must never dispatch")

    _emit(
        {
            "event": "summary",
            "turns": len(executions),
            "allowed": 2,
            "denied": 1,
            "refusals": executor.refusal_count,
            "observed_tokens": executor.ledger.cumulative(),
            "original_tokens": executor.original_tokens,
            "omitted_tokens": executor.omitted_tokens,
            "closed": executor.closed,
        }
    )
    print(f"demo verified: {checker.count} checks")


def main() -> int:
    run_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

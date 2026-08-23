from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from end_to_end_agent import (
    ALLOWED_TOOLS,
    DEFAULT_LATENCY_BUCKETS,
    DENIED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    ChainResult,
    Counter,
    GateChain,
    GateContext,
    MetricsRegistry,
    ObservationLedger,
    PathGate,
    Repo,
    RunBudgetGate,
    RunLimits,
    Sandbox,
    SpanBuilder,
    StepDispatcher,
    ToolCall,
    WhitelistGate,
    clip_text,
    estimate_tokens,
    prometheus_exposition,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixture_repo"


def test_gate_chain_short_circuits_on_first_deny(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    chain = GateChain(
        (
            WhitelistGate(ALLOWED_TOOLS),
            PathGate(str(tmp_path)),
            RunBudgetGate(RunLimits()),
        )
    )
    ctx = GateContext(ObservationLedger(), RunLimits())

    denied = chain.review(ToolCall(1, "shell", {}), ctx)
    assert isinstance(denied, ChainResult)
    assert not denied.allowed
    assert denied.decision.gate_name == "whitelist"
    assert len(denied.evaluations) == 1

    allowed = chain.review(ToolCall(2, "read_file", {"path": "."}), ctx)
    assert allowed.allowed
    assert [item.gate_name for item in allowed.evaluations] == [
        "whitelist",
        "path",
        "run_budget",
    ]
    assert all(item.allow for item in allowed.evaluations)


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", {"path": "."}, True),
        ("read_file", {"path": "src/fizz.py"}, True),
        ("write_file", {"path": "src/fizz.py", "content": "x"}, True),
        ("read_file", {"path": "expected/src/fizz.py"}, False),
        ("read_file", {"path": "../escape.py"}, False),
        ("read_file", {"path": "/etc/passwd"}, False),
        ("write_file", {"path": "tests/test_fizz.py", "content": "x"}, False),
        ("read_file", {"bad": "arguments"}, False),
    ],
)
def test_path_gate_denies_escapes_expected_and_test_writes(
    tmp_path: Path,
    name: str,
    arguments: dict[str, str],
    expected: bool,
) -> None:
    (tmp_path / "src").mkdir()
    gate = PathGate(str(tmp_path))
    decision = gate.evaluate(
        ToolCall(1, name, arguments),
        GateContext(ObservationLedger(), RunLimits()),
    )
    assert decision.allow is expected
    if not expected:
        assert decision.reason is not None


def test_sandbox_path_jail_blocks_escape_and_test_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "escape.py"
    sandbox = Sandbox(root, RunLimits())

    escaped = sandbox.execute(
        ToolCall(1, "write_file", {"path": "../escape.py", "content": "owned"})
    )
    assert escaped.denied
    assert escaped.exit_code == DENIED_EXIT_CODE
    assert not outside.exists()

    absolute = sandbox.execute(
        ToolCall(
            2,
            "write_file",
            {"path": str(outside), "content": "owned"},
        )
    )
    assert absolute.denied
    assert not outside.exists()

    test_write = sandbox.execute(
        ToolCall(
            3,
            "write_file",
            {"path": "tests/test_fizz.py", "content": "changed"},
        )
    )
    assert test_write.denied
    assert not (root / "tests" / "test_fizz.py").exists()


def test_sandbox_rejects_unknown_tools_and_extra_arguments(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    sandbox = Sandbox(tmp_path, RunLimits())

    unknown = sandbox.execute(ToolCall(1, "shell", {}))
    assert unknown.denied
    assert unknown.exit_code == DENIED_EXIT_CODE

    extra = sandbox.execute(
        ToolCall(2, "run_tests", {"command": ["python", "-c", "print(1)"]})
    )
    assert extra.denied
    assert extra.exit_code == DENIED_EXIT_CODE


def test_sandbox_runs_real_fixture_tests() -> None:
    with Repo(FIXTURE_ROOT) as repo:
        result = Sandbox(repo.root, RunLimits()).execute(
            ToolCall(1, "run_tests", {})
        )
    assert not result.denied
    assert not result.timed_out
    assert not result.ok
    assert result.exit_code == 1
    assert "fizzbuzz(15) must end with 'FizzBuzz'" in result.output
    assert "FAILED" in result.output
    assert result.duration_ms > 0


def test_sandbox_timeout_kills_the_test_process(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_slow.py").write_text(
        "import time\n"
        "import unittest\n"
        "\n"
        "class SlowTests(unittest.TestCase):\n"
        "    def test_slow(self) -> None:\n"
        "        time.sleep(30)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    limits = RunLimits(test_timeout_seconds=0.25)
    started = time.perf_counter()
    result = Sandbox(root, limits).execute(ToolCall(1, "run_tests", {}))
    elapsed = time.perf_counter() - started
    assert result.timed_out
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert result.reason is not None
    assert result.duration_ms < 5_000
    assert elapsed < 5


@pytest.mark.parametrize("limit", [0, 1, 5, 6, 10, 100])
def test_clip_text_never_exceeds_the_token_budget(limit: int) -> None:
    text = "abcdefghij" * 100
    clipped, truncated = clip_text(text, limit)
    assert truncated
    assert estimate_tokens(clipped) <= limit


def test_clip_text_leaves_small_input_untouched() -> None:
    assert clip_text("short", 100) == ("short", False)


def test_span_builder_links_children_and_exports_jsonl(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "traces.jsonl"
    builder = SpanBuilder(trace_path)
    with builder.span(
        "invoke_agent coding-agent",
        {"gen_ai.agent.name": "coding-agent"},
    ) as root:
        with builder.span(
            "execute_tool read_file",
            {"gen_ai.tool.name": "read_file"},
        ) as child:
            assert child.parent_span_id == root.span_id
            assert child.trace_id == root.trace_id

    with pytest.raises(RuntimeError, match="boom"):
        with builder.span("execute_tool boom") as failed:
            raise RuntimeError("boom")

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert all(item["trace_id"] == root.trace_id for item in parsed)
    assert all(item["parent_span_id"] is None for item in parsed[1:])
    assert failed.status == "ERROR"
    assert failed.attributes["error.type"] == "RuntimeError"
    assert failed.events[0]["name"] == "exception"
    assert root.status == "OK"
    assert child.status == "OK"


def test_metrics_count_allowed_calls_once_and_denials_separately(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    limits = RunLimits()
    metrics = MetricsRegistry()
    spans = SpanBuilder()
    ledger = ObservationLedger()
    chain = GateChain((WhitelistGate(ALLOWED_TOOLS),))
    dispatcher = StepDispatcher(
        chain,
        Sandbox(root, limits),
        ledger,
        spans,
        metrics,
        limits,
    )

    refused = dispatcher.dispatch(
        ToolCall(
            1,
            "write_file",
            {"path": "../escape.py", "content": "owned"},
        ),
        "FIX",
    )
    assert refused.status == "sandbox_denied"
    assert metrics.counters["tools_called_total"].value(
        {"tool": "write_file"}
    ) == 1
    assert metrics.histograms["tool_latency_ms"].count(
        {"tool": "write_file"}
    ) == 1
    assert metrics.counters["gate_denials_total"].value({"gate": "whitelist"}) == 0
    assert ledger.cumulative() == 0
    assert spans.spans[0].status == "ERROR"

    denied = dispatcher.dispatch(ToolCall(2, "shell", {}), "SURVEY")
    assert denied.status == "gate_denied"
    assert metrics.counters["tools_called_total"].value(
        {"tool": "write_file"}
    ) == 1
    assert metrics.counters["gate_denials_total"].value({"gate": "whitelist"}) == 1
    assert ledger.cumulative() == 0


def test_metrics_registry_and_exposition_contract() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("tools_called_total", "calls")
    counter.inc({"tool": "read_file"})
    counter.inc({"tool": "read_file"})
    histogram = registry.histogram("tool_latency_ms", "latency")
    histogram.observe(3.5, {"tool": "read_file"})
    histogram.observe(12.0, {"tool": "read_file"})
    assert isinstance(counter, Counter)
    assert registry.histogram("tool_latency_ms", "latency") is histogram

    exposition = prometheus_exposition(registry)
    assert 'tools_called_total{tool="read_file"} 2' in exposition
    assert "# TYPE tool_latency_ms histogram" in exposition
    assert 'tool_latency_ms_bucket{tool="read_file",le="5"} 1' in exposition
    assert 'tool_latency_ms_bucket{tool="read_file",le="25"} 2' in exposition
    assert 'tool_latency_ms_bucket{tool="read_file",le="+Inf"} 2' in exposition
    assert 'tool_latency_ms_count{tool="read_file"} 2' in exposition
    assert len(DEFAULT_LATENCY_BUCKETS) == 11

"""Local gate and clipping throughput measurements; no network or model calls."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from verification_gates import (  # noqa: E402
    BudgetGate,
    GateChain,
    GateContext,
    GatedToolExecutor,
    ObservationLedger,
    RecencyGate,
    RegexGate,
    RegexRule,
    ToolCall,
    VerificationGate,
    WhitelistGate,
    clip_text,
)

ROUNDS = 5
CHAIN_RUNS = 100_000
EXEC_RUNS = 5_000
CLIP_RUNS = 100_000


def _make_chain() -> GateChain:
    gates: list[VerificationGate] = [
        WhitelistGate({"read_file"}),
        RegexGate([RegexRule("__never_matches__", "never")]),
        RecencyGate(10),
        BudgetGate(1_000_000),
    ]
    return GateChain(gates)


def _median_us(samples: list[float]) -> float:
    return statistics.median(samples)


def _bench_chain() -> float:
    ledger = ObservationLedger()
    for turn in range(1, 6):
        ledger.record(turn, "read_file", 10)
    ctx = GateContext(ledger)
    call = ToolCall(
        10,
        "read_file",
        {"path": "src/app.py"},
        observed_turn=9,
    )
    chain = _make_chain()
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(CHAIN_RUNS):
            chain.review(call, ctx)
        elapsed = time.perf_counter() - started
        samples.append(elapsed / CHAIN_RUNS * 1_000_000.0)
    return _median_us(samples)


def _bench_executor() -> float:
    call_log: list[ToolCall] = []

    def dispatch(call: ToolCall) -> object:
        call_log.append(call)
        return "x" * 40

    samples: list[float] = []
    for _ in range(ROUNDS):
        executor = GatedToolExecutor(_make_chain(), dispatch)
        started = time.perf_counter()
        for index in range(EXEC_RUNS):
            executor.execute(ToolCall(index + 1, "read_file", {}))
        elapsed = time.perf_counter() - started
        samples.append(elapsed / EXEC_RUNS * 1_000_000.0)
    return _median_us(samples)


def _bench_clip() -> float:
    text = "x" * 4096
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(CLIP_RUNS):
            clip_text(text, 100)
        elapsed = time.perf_counter() - started
        samples.append(elapsed / CLIP_RUNS * 1_000_000.0)
    return _median_us(samples)


def main() -> int:
    chain_us = _bench_chain()
    executor_us = _bench_executor()
    clip_us = _bench_clip()

    print(
        f"gate chain review : {chain_us:8.3f} us/call  "
        f"{1_000_000.0 / chain_us:,.0f} calls/s"
    )
    print(
        f"gated execution   : {executor_us:8.3f} us/call  "
        f"{1_000_000.0 / executor_us:,.0f} calls/s"
    )
    print(
        f"clip 4 KiB text   : {clip_us:8.3f} us/call  "
        f"{1_000_000.0 / clip_us:,.0f} calls/s"
    )
    for name, value in (
        ("gate_chain_review_us", chain_us),
        ("gated_execution_us", executor_us),
        ("clip_4k_to_100_tokens_us", clip_us),
    ):
        print(
            json.dumps(
                {"benchmark": name, "value": value},
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Local transport benchmark with no network or timing assertions.

The full path parses, validates, dispatches, and serializes every line. The
baseline only decodes the request and encodes the success response, so the
difference is a framing-and-dispatch reference, not a protocol comparison.
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jsonrpc_stdio import Params, StdioTransport  # noqa: E402


def _request_line(index: int) -> bytes:
    message = {
        "jsonrpc": "2.0",
        "id": index,
        "method": "echo",
        "params": {"text": "hello world", "n": index},
    }
    return (
        json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def _echo(method: str, params: Params) -> object:
    return params


def _run_transport(payload: bytes, request_count: int) -> tuple[float, int]:
    stdout = io.BytesIO()
    transport = StdioTransport(_echo, io.BytesIO(payload), stdout)
    start = time.perf_counter()
    transport.serve()
    elapsed = time.perf_counter() - start

    raw = stdout.getvalue()
    lines = raw.splitlines()
    if len(lines) != request_count:
        raise RuntimeError(
            f"expected {request_count} responses, got {len(lines)}"
        )
    for index, line in enumerate(lines):
        message: object = json.loads(line)
        if not isinstance(message, dict) or message.get("id") != index:
            raise RuntimeError(f"bad response id at index {index}")
        if message.get("result") != {"text": "hello world", "n": index}:
            raise RuntimeError(f"bad response result at index {index}")
    return elapsed, len(raw)


def _baseline(payload: bytes) -> tuple[float, int]:
    lines = payload.splitlines()
    output_bytes = 0
    start = time.perf_counter()
    for line in lines:
        request = json.loads(line)
        response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": request["params"],
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        output_bytes += len(response)
    return time.perf_counter() - start, output_bytes


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the local JSON-RPC stdio transport."
    )
    parser.add_argument(
        "--requests",
        type=_positive_int,
        default=20_000,
        help="requests per run (default: 20000)",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=3,
        help="timed runs; the median is reported (default: 3)",
    )
    args = parser.parse_args(argv)

    payload = b"".join(
        _request_line(index) for index in range(args.requests)
    )
    transport_runs = [
        _run_transport(payload, args.requests) for _ in range(args.repeat)
    ]
    baseline_runs = [_baseline(payload) for _ in range(args.repeat)]

    median_seconds = statistics.median(run[0] for run in transport_runs)
    baseline_seconds = statistics.median(run[0] for run in baseline_runs)
    output_bytes = transport_runs[-1][1]
    requests_per_second = args.requests / median_seconds
    baseline_per_second = args.requests / baseline_seconds
    megabytes_per_second = (
        (len(payload) + output_bytes) / median_seconds / (2**20)
    )

    print("JSON-RPC stdio transport — local measurement, not a guarantee")
    print(
        f"Python {platform.python_version()} on {platform.platform()}; "
        f"{args.requests:,} requests, {args.repeat} runs (median)"
    )
    print(
        f"transport: {requests_per_second:,.0f} req/s | "
        f"{1e6 / requests_per_second:.2f} µs/frame | "
        f"{megabytes_per_second:.1f} MiB/s"
    )
    print(f"JSON decode/encode baseline: {baseline_per_second:,.0f} req/s")
    print(
        "overhead factor: "
        f"{baseline_per_second / requests_per_second:.2f}x "
        "(baseline / transport)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

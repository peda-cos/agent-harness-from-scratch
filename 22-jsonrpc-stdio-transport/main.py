"""Offline demo for the JSON-RPC stdio transport.

Run ``python main.py`` for a self-terminating BytesIO transcript, or
``python main.py --serve`` to serve the same deterministic methods over real
standard input and output. No network, clock, or random source is used.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jsonrpc_stdio import (  # noqa: E402
    InvalidParams,
    MethodNotFound,
    Params,
    StdioTransport,
)


class DemoHandler:
    """Deterministic methods used by both the demo and the real-pipe mode."""

    def __init__(self) -> None:
        self.transport: StdioTransport | None = None

    def __call__(self, method: str, params: Params) -> object:
        if method == "demo.echo":
            return params
        if method == "demo.log":
            if isinstance(params, dict):
                return {"logged": params.get("message", "")}
            return params
        if method == "demo.progress":
            transport = self.transport
            if transport is None:
                raise RuntimeError("transport is not bound")
            transport.write_notification(
                "progress", {"step": 1, "total": 2}
            )
            transport.write_notification(
                "progress", {"step": 2, "total": 2}
            )
            return {"completed": True}
        if method == "math.add":
            if not isinstance(params, dict):
                raise InvalidParams("a and b must be numbers")
            return _number(params.get("a")) + _number(params.get("b"))
        if method == "demo.boom":
            raise RuntimeError("demo boom")
        raise MethodNotFound(method)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParams("a and b must be numbers")
    return float(value)


def build_transport(
    stdin: BinaryIO,
    stdout: BinaryIO,
) -> tuple[StdioTransport, DemoHandler]:
    """Create a transport and bind the handler's outbound notification channel."""
    handler = DemoHandler()
    transport = StdioTransport(handler, stdin, stdout)
    handler.transport = transport
    return transport, handler


def _encoded_line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def _demo_input() -> bytes:
    lines = [
        _encoded_line(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "demo.echo",
                "params": {"text": "hello", "n": 1},
            }
        ),
        b'{"jsonrpc":"2.0","method":\n',
        _encoded_line(
            {
                "jsonrpc": "2.0",
                "method": "demo.log",
                "params": {"message": "still alive"},
            }
        ),
        _encoded_line(
            {"jsonrpc": "2.0", "id": "job-1", "method": "demo.progress"}
        ),
        _encoded_line({"jsonrpc": "2.0", "id": 2, "method": "demo.missing"}),
        _encoded_line(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "math.add",
                "params": {"a": 1, "b": "two"},
            }
        ),
        _encoded_line({"jsonrpc": "2.0", "id": 4, "method": "demo.boom"}),
        _encoded_line(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "demo.echo",
                    "params": {"text": "batch"},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "demo.log",
                    "params": {"message": "inside batch"},
                },
                1,
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "math.add",
                    "params": {"a": 1, "b": "two"},
                },
            ]
        ),
        _encoded_line(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "demo.echo",
                "params": {"text": "after errors"},
            }
        ),
    ]
    return b"".join(lines)


def _decode_messages(raw: bytes) -> list[object]:
    return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


def _summary(message: object) -> object:
    if isinstance(message, list):
        return ("batch", tuple(_summary(item) for item in message))
    if not isinstance(message, dict):
        return message
    if "error" in message:
        error = message["error"]
        if isinstance(error, dict):
            code = error.get("code")
            data = error.get("data")
            if isinstance(data, dict) and "exception" in data:
                return ("error", message.get("id"), code, data["exception"])
            return ("error", message.get("id"), code)
        return ("error", message.get("id"), None)
    if "result" in message:
        return ("result", message.get("id"), message.get("result"))
    return ("notification", message.get("method"), message.get("params"))


def _verify_demo(messages: list[object], input_line_count: int) -> None:
    expected = [
        ("result", 1, {"text": "hello", "n": 1}),
        ("error", None, -32700),
        ("notification", "progress", {"step": 1, "total": 2}),
        ("notification", "progress", {"step": 2, "total": 2}),
        ("result", "job-1", {"completed": True}),
        ("error", 2, -32601),
        ("error", 3, -32602),
        ("error", 4, -32603, "RuntimeError"),
        (
            "batch",
            (
                ("result", 5, {"text": "batch"}),
                ("error", None, -32600),
                ("error", 6, -32602),
            ),
        ),
        ("result", 7, {"text": "after errors"}),
    ]
    actual = tuple(_summary(message) for message in messages)
    if actual != tuple(expected):
        raise RuntimeError(
            "demo transcript mismatch:\n"
            f"actual:   {actual!r}\n"
            f"expected: {tuple(expected)!r}"
        )
    if input_line_count != 9:
        raise RuntimeError(
            f"demo expected 9 input lines, got {input_line_count}"
        )


def run_demo() -> int:
    payload = _demo_input()
    stdout = io.BytesIO()
    transport, _handler = build_transport(io.BytesIO(payload), stdout)
    transport.serve()

    messages = _decode_messages(stdout.getvalue())
    input_lines = payload.decode("utf-8").splitlines()

    print("requests:")
    for line in input_lines:
        print("  -->", line)
    print("responses:")
    for message in messages:
        print(
            "  <--",
            json.dumps(
                message, ensure_ascii=False, separators=(",", ":")
            ),
        )

    _verify_demo(messages, len(input_lines))
    print(
        f"demo verified: {len(input_lines)} input lines, "
        f"{len(messages)} output messages"
    )
    return 0


def serve_stdio() -> int:
    transport, _handler = build_transport(
        sys.stdin.buffer, sys.stdout.buffer
    )
    transport.serve()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args == ["--serve"]:
        return serve_stdio()
    if args:
        print("usage: main.py [--serve]", file=sys.stderr)
        return 2
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())

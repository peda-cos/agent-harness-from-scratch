from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
MAIN = MODULE_ROOT / "main.py"


def _line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def test_real_pipes_round_trip_with_parse_isolation_and_batch() -> None:
    payload = b"".join(
        [
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "demo.echo",
                    "params": {"text": "hi"},
                }
            ),
            _line(
                {
                    "jsonrpc": "2.0",
                    "method": "demo.log",
                    "params": {"message": "silent"},
                }
            ),
            b'{"jsonrpc":"2.0","method":\n',
            _line(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "demo.echo",
                        "params": {"text": "batch"},
                    },
                    1,
                ]
            ),
        ]
    )

    result = subprocess.run(
        [sys.executable, str(MAIN), "--serve"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert messages == [
        {"jsonrpc": "2.0", "id": 1, "result": {"text": "hi"}},
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        },
        [
            {"jsonrpc": "2.0", "id": 2, "result": {"text": "batch"}},
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            },
        ],
    ]
    assert result.stderr == b""


def test_serve_mode_clean_eof_writes_nothing() -> None:
    result = subprocess.run(
        [sys.executable, str(MAIN), "--serve"],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""

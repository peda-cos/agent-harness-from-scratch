from __future__ import annotations

import io
import json

import pytest

from jsonrpc_stdio import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Handler,
    InvalidParams,
    MethodNotFound,
    StdioTransport,
)


def _line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def _run(payload: bytes, handler: Handler) -> list[object]:
    stdout = io.BytesIO()
    StdioTransport(handler, io.BytesIO(payload), stdout).serve()
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _echo(method: str, params: object) -> object:
    return {"method": method, "params": params}


def test_request_returns_result_and_echoes_numeric_and_string_ids() -> None:
    payload = _line(
        {"jsonrpc": "2.0", "id": 7, "method": "echo", "params": {"x": 1}}
    ) + _line({"jsonrpc": "2.0", "id": "abc", "method": "echo"})

    messages = _run(payload, _echo)

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"method": "echo", "params": {"x": 1}},
        },
        {
            "jsonrpc": "2.0",
            "id": "abc",
            "result": {"method": "echo", "params": {}},
        },
    ]


def test_positional_and_absent_params_reach_the_handler() -> None:
    seen: list[tuple[str, object]] = []

    def handler(method: str, params: object) -> object:
        seen.append((method, params))
        return None

    payload = _line({"jsonrpc": "2.0", "id": 1, "method": "a"}) + _line(
        {"jsonrpc": "2.0", "id": 2, "method": "b", "params": [10, 20]}
    )

    messages = _run(payload, handler)

    assert seen == [("a", {}), ("b", [10, 20])]
    assert [message["result"] for message in messages] == [None, None]


def test_parse_error_does_not_poison_the_stream() -> None:
    payload = b'{"jsonrpc":"2.0","method":\n' + _line(
        {"jsonrpc": "2.0", "id": 1, "method": "echo"}
    )

    messages = _run(payload, _echo)

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": PARSE_ERROR, "message": "Parse error"},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"method": "echo", "params": {}},
        },
    ]


def test_invalid_envelope_echoes_valid_id_and_uses_null_otherwise() -> None:
    payload = _line({"jsonrpc": "2.0", "id": 9}) + _line(
        {"jsonrpc": "2.0", "params": {}}
    )

    messages = _run(payload, _echo)

    assert messages[0] == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": INVALID_REQUEST, "message": "Invalid Request"},
    }
    assert messages[1] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": INVALID_REQUEST, "message": "Invalid Request"},
    }


def test_method_not_found_uses_the_standard_code() -> None:
    def handler(method: str, params: object) -> object:
        raise MethodNotFound(method)

    messages = _run(
        _line({"jsonrpc": "2.0", "id": 1, "method": "missing"}), handler
    )

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": METHOD_NOT_FOUND, "message": "Method not found"},
        }
    ]


def test_invalid_params_message_and_empty_message_fallback() -> None:
    def handler(method: str, params: object) -> object:
        if method == "empty":
            raise InvalidParams("")
        raise InvalidParams("city must be a string")

    payload = _line(
        {"jsonrpc": "2.0", "id": 1, "method": "named"}
    ) + _line({"jsonrpc": "2.0", "id": 2, "method": "empty"})

    messages = _run(payload, handler)

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": INVALID_PARAMS, "message": "city must be a string"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": INVALID_PARAMS, "message": "Invalid params"},
        },
    ]


def test_internal_error_includes_only_the_exception_class() -> None:
    def handler(method: str, params: object) -> object:
        raise ValueError("secret detail")

    messages = _run(
        _line({"jsonrpc": "2.0", "id": 1, "method": "boom"}), handler
    )

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": INTERNAL_ERROR,
                "message": "Internal error",
                "data": {"exception": "ValueError"},
            },
        }
    ]


@pytest.mark.parametrize(
    ("bad_result", "exception_name"),
    [
        ({1, 2}, "TypeError"),
        (b"bytes", "TypeError"),
        (float("nan"), "ValueError"),
    ],
)
def test_unserializable_results_become_internal_errors(
    bad_result: object,
    exception_name: str,
) -> None:
    def handler(method: str, params: object) -> object:
        if method == "bad":
            return bad_result
        return "ok"

    payload = _line(
        {"jsonrpc": "2.0", "id": 1, "method": "bad"}
    ) + _line({"jsonrpc": "2.0", "id": 2, "method": "after"})

    messages = _run(payload, handler)

    assert messages[0]["error"] == {
        "code": INTERNAL_ERROR,
        "message": "Internal error",
        "data": {"exception": exception_name},
    }
    assert messages[1]["result"] == "ok"


def test_notifications_run_without_response_and_failures_stay_silent() -> None:
    calls: list[str] = []

    def handler(method: str, params: object) -> object:
        calls.append(method)
        if method == "explode":
            raise RuntimeError("not observed")
        return "ignored"

    payload = (
        _line({"jsonrpc": "2.0", "method": "note"})
        + _line({"jsonrpc": "2.0", "method": "explode"})
        + _line({"jsonrpc": "2.0", "id": 1, "method": "after"})
    )

    messages = _run(payload, handler)

    assert calls == ["note", "explode", "after"]
    assert messages == [
        {"jsonrpc": "2.0", "id": 1, "result": "ignored"}
    ]


def test_null_id_request_still_gets_a_response() -> None:
    messages = _run(
        _line(
            {
                "jsonrpc": "2.0",
                "id": None,
                "method": "echo",
                "params": {"a": 1},
            }
        ),
        _echo,
    )

    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "result": {"method": "echo", "params": {"a": 1}},
        }
    ]


def test_batch_returns_one_array_and_skips_notifications() -> None:
    payload = _line(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "echo",
                "params": {"a": 1},
            },
            {"jsonrpc": "2.0", "method": "log"},
            {"jsonrpc": "2.0", "id": "two", "method": "echo"},
        ]
    )

    messages = _run(payload, _echo)

    assert len(messages) == 1
    batch = messages[0]
    assert isinstance(batch, list)
    assert [item["id"] for item in batch] == [1, "two"]
    assert [item["result"]["params"] for item in batch] == [{"a": 1}, {}]

    single = _run(
        _line([{"jsonrpc": "2.0", "id": 3, "method": "echo"}]), _echo
    )
    assert len(single) == 1
    assert isinstance(single[0], list)
    assert len(single[0]) == 1


def test_all_notification_batch_and_empty_batch_shapes() -> None:
    notifications = _line(
        [
            {"jsonrpc": "2.0", "method": "a"},
            {"jsonrpc": "2.0", "method": "b"},
        ]
    )
    assert _run(notifications, _echo) == []

    empty = _run(b"[]\n", _echo)
    assert len(empty) == 1
    assert empty[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": INVALID_REQUEST, "message": "Invalid Request"},
    }


def test_invalid_batch_entries_get_array_errors_and_valid_entries_continue() -> None:
    payload = _line(
        [
            1,
            {"jsonrpc": "2.0", "id": 2, "method": "echo"},
            "x",
        ]
    )

    messages = _run(payload, _echo)

    assert len(messages) == 1
    batch = messages[0]
    assert isinstance(batch, list)
    assert len(batch) == 3
    assert batch[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": INVALID_REQUEST, "message": "Invalid Request"},
    }
    assert batch[1]["id"] == 2
    assert batch[2] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": INVALID_REQUEST, "message": "Invalid Request"},
    }

    single = _run(_line([1]), _echo)
    assert len(single) == 1
    assert isinstance(single[0], list)
    assert len(single[0]) == 1
    assert single[0][0]["error"]["code"] == INVALID_REQUEST


def test_progress_notifications_precede_the_final_response() -> None:
    stdout = io.BytesIO()

    def handler(method: str, params: object) -> object:
        transport.write_notification("progress", {"step": 1})
        transport.write_notification("progress", {"step": 2})
        return {"done": True}

    transport = StdioTransport(
        handler,
        io.BytesIO(_line({"jsonrpc": "2.0", "id": 1, "method": "work"})),
        stdout,
    )
    transport.serve()

    assert [
        json.loads(line) for line in stdout.getvalue().splitlines()
    ] == [
        {
            "jsonrpc": "2.0",
            "method": "progress",
            "params": {"step": 1},
        },
        {
            "jsonrpc": "2.0",
            "method": "progress",
            "params": {"step": 2},
        },
        {"jsonrpc": "2.0", "id": 1, "result": {"done": True}},
    ]


def test_eof_trailing_line_crlf_and_utf8_framing() -> None:
    assert _run(b"", _echo) == []

    without_newline = _line(
        {"jsonrpc": "2.0", "id": 1, "method": "echo"}
    ).rstrip(b"\n")
    assert _run(without_newline, _echo)[0]["id"] == 1

    crlf = _line({"jsonrpc": "2.0", "id": 2, "method": "echo"}).replace(
        b"\n", b"\r\n"
    )
    assert _run(crlf, _echo)[0]["id"] == 2

    stdout = io.BytesIO()
    payload = _line(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "echo",
            "params": {"city": "Lisbon — 東京", "text": "a\nb"},
        }
    )
    StdioTransport(_echo, io.BytesIO(payload), stdout).serve()

    raw = stdout.getvalue()
    assert raw.count(b"\n") == 1
    message = json.loads(raw)
    assert message["result"]["params"] == {
        "city": "Lisbon — 東京",
        "text": "a\nb",
    }


class _FlushCounter(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def test_writer_helpers_emit_compact_single_lines_and_flush() -> None:
    stdout = _FlushCounter()
    transport = StdioTransport(_echo, io.BytesIO(), stdout)

    transport.write_response(7, {"ok": True})
    transport.write_error("x", METHOD_NOT_FOUND, "Method not found")
    transport.write_notification("progress", {"step": 1})
    transport.write_notification("ping")

    assert stdout.getvalue().splitlines() == [
        b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}',
        b'{"jsonrpc":"2.0","id":"x","error":{"code":-32601,"message":"Method not found"}}',
        b'{"jsonrpc":"2.0","method":"progress","params":{"step":1}}',
        b'{"jsonrpc":"2.0","method":"ping"}',
    ]
    assert stdout.flushes == 4


def test_writer_helpers_reject_bad_payloads_without_partial_lines() -> None:
    stdout = io.BytesIO()
    transport = StdioTransport(_echo, io.BytesIO(), stdout)

    with pytest.raises(TypeError):
        transport.write_notification("progress", "scalar")
    assert stdout.getvalue() == b""

    with pytest.raises(TypeError):
        transport.write_response(1, {1, 2})
    assert stdout.getvalue() == b""

    with pytest.raises(ValueError):
        transport.write_error(
            1,
            INTERNAL_ERROR,
            "Internal error",
            {"bad": float("nan")},
        )
    assert stdout.getvalue() == b""

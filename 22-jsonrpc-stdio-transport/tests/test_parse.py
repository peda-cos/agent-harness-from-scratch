from __future__ import annotations

import json

import pytest

from jsonrpc_stdio import (
    InvalidRequest,
    Notification,
    ParseError,
    Request,
    parse_request,
)


def _line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def test_single_request_and_notification_classify() -> None:
    request = parse_request(
        _line(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "echo",
                "params": {"x": 1},
            }
        )
    )
    assert request.is_batch is False
    assert request.items == (Request("echo", {"x": 1}, 7),)

    notification = parse_request(_line({"jsonrpc": "2.0", "method": "log"}))
    assert notification.is_batch is False
    assert notification.items == (Notification("log", {}),)


def test_absent_params_are_fresh_per_request() -> None:
    first = parse_request(_line({"jsonrpc": "2.0", "id": 1, "method": "m"}))
    second = parse_request(_line({"jsonrpc": "2.0", "id": 2, "method": "m"}))
    first_item = first.items[0]
    second_item = second.items[0]
    assert isinstance(first_item, Request)
    assert isinstance(second_item, Request)
    assert first_item.params == {}
    assert first_item.params is not second_item.params


def test_null_id_is_request_and_absent_id_is_notification() -> None:
    null_id = parse_request(
        _line({"jsonrpc": "2.0", "id": None, "method": "m"})
    )
    assert null_id.items == (Request("m", {}, None),)

    absent = parse_request(_line({"jsonrpc": "2.0", "method": "m"}))
    assert isinstance(absent.items[0], Notification)


def test_batch_preserves_order_and_marks_batch() -> None:
    document = parse_request(
        _line(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "a"},
                {"jsonrpc": "2.0", "method": "b"},
                {"jsonrpc": "2.0", "id": "x", "method": "c"},
            ]
        )
    )
    assert document.is_batch is True
    assert document.items == (
        Request("a", {}, 1),
        Notification("b", {}),
        Request("c", {}, "x"),
    )


def test_empty_batch_is_single_invalid_request() -> None:
    document = parse_request(b"[]\n")
    assert document.is_batch is False
    assert len(document.items) == 1
    item = document.items[0]
    assert isinstance(item, InvalidRequest)
    assert item.id is None
    assert "at least one" in item.reason


@pytest.mark.parametrize(
    ("value", "expected_id"),
    [
        ({"id": 9}, 9),
        ({"jsonrpc": "1.0", "method": "m", "id": "n"}, "n"),
        ({"jsonrpc": "2.0", "method": 1, "id": "n"}, "n"),
        ({"jsonrpc": "2.0", "method": "m", "params": "x", "id": 3}, 3),
        ({"jsonrpc": "2.0", "method": "m", "params": None, "id": 4}, 4),
        ({"jsonrpc": "2.0", "method": "m", "id": True}, None),
        ({"jsonrpc": "2.0", "method": "m", "id": {"x": 1}}, None),
        (5, None),
        (None, None),
    ],
)
def test_invalid_envelopes_echo_only_valid_ids(
    value: object,
    expected_id: object,
) -> None:
    item = parse_request(_line(value)).items[0]
    assert isinstance(item, InvalidRequest)
    assert item.id == expected_id


@pytest.mark.parametrize(
    "raw",
    [
        b'{"jsonrpc":"2.0","method":\n',
        b'{"jsonrpc":"2.0"}\n{"jsonrpc":"2.0"}\n',
        b"\xff\n",
        b"\n",
        b"   \r\n",
        b'\xef\xbb\xbf{"jsonrpc":"2.0"}\n',
        b'{"jsonrpc":"2.0","method":"m","params":NaN}\n',
        b'{"jsonrpc":"2.0"',
    ],
)
def test_parse_errors_cover_bad_bytes_and_nonstandard_json(raw: bytes) -> None:
    with pytest.raises(ParseError):
        parse_request(raw)


def test_ids_and_both_param_structures_are_preserved() -> None:
    for raw_id in (1, 1.5, "abc", None):
        document = parse_request(
            _line({"jsonrpc": "2.0", "id": raw_id, "method": "m"})
        )
        item = document.items[0]
        assert isinstance(item, Request)
        assert item.id == raw_id

    named = parse_request(
        _line({"jsonrpc": "2.0", "id": 1, "method": "m", "params": {"a": 1}})
    )
    positional = parse_request(
        _line({"jsonrpc": "2.0", "id": 2, "method": "m", "params": [1, 2]})
    )
    assert named.items == (Request("m", {"a": 1}, 1),)
    assert positional.items == (Request("m", [1, 2], 2),)


def test_extra_envelope_members_are_ignored() -> None:
    document = parse_request(
        _line(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "m",
                "extra": {"ignored": True},
            }
        )
    )
    assert document.items == (Request("m", {}, 1),)

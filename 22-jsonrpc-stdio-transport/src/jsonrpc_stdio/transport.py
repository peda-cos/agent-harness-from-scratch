"""JSON-RPC 2.0 envelopes over newline-delimited stdio.

The transport owns framing, envelope validation, and the five standard error
codes. Application methods live behind ``handler(method, params)``; the
transport never sees a tool registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Final, NoReturn, TypeAlias, cast

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603

Params: TypeAlias = dict[str, Any] | list[Any]
RpcId: TypeAlias = str | int | float | None
Handler: TypeAlias = Callable[[str, Params], object]

_MISSING: Final = object()


@dataclass(frozen=True, slots=True)
class Request:
    """A client request that expects a response."""

    method: str
    params: Params
    id: RpcId


@dataclass(frozen=True, slots=True)
class Notification:
    """A client notification: the server must not answer it."""

    method: str
    params: Params


@dataclass(frozen=True, slots=True)
class InvalidRequest:
    """A malformed envelope. ``reason`` is diagnostic and never sent."""

    reason: str
    id: RpcId = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """One decoded line: a single message or a batch of messages."""

    items: tuple[Request | Notification | InvalidRequest, ...]
    is_batch: bool


class ParseError(ValueError):
    """The line was not one valid UTF-8 JSON value."""


class MethodNotFound(Exception):
    """The handler does not know the requested method."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"method not found: {method}")


class InvalidParams(Exception):
    """The handler rejected the request parameters."""


def _reject_constant(token: str) -> NoReturn:
    """Reject NaN/Infinity: they are not JSON literals."""
    raise ValueError(f"non-standard JSON constant: {token}")


def _extract_id(obj: dict[str, Any]) -> tuple[RpcId, bool, bool]:
    """Return (id, present, valid), rejecting booleans as ids."""
    if "id" not in obj:
        return None, False, True
    value = obj["id"]
    if value is None:
        return None, True, True
    if isinstance(value, bool):
        return None, True, False
    if isinstance(value, (str, int, float)):
        return value, True, True
    return None, True, False


def _classify(value: object) -> Request | Notification | InvalidRequest:
    if not isinstance(value, dict):
        return InvalidRequest("request must be a JSON object")
    obj = cast(dict[str, Any], value)
    request_id, present, id_valid = _extract_id(obj)
    if not id_valid:
        return InvalidRequest("id must be a string, number, or null", None)
    echoed_id = request_id if present else None
    if obj.get("jsonrpc") != "2.0":
        return InvalidRequest('jsonrpc must be exactly "2.0"', echoed_id)
    method = obj.get("method")
    if not isinstance(method, str):
        return InvalidRequest("method must be a string", echoed_id)
    raw_params = obj.get("params", _MISSING)
    if raw_params is _MISSING:
        params: Params = {}
    elif isinstance(raw_params, dict):
        params = cast(dict[str, Any], raw_params)
    elif isinstance(raw_params, list):
        params = raw_params
    else:
        return InvalidRequest("params must be an object or array", echoed_id)
    if not present:
        return Notification(method=method, params=params)
    return Request(method=method, params=params, id=request_id)


def parse_request(raw: bytes) -> ParsedDocument:
    """Parse one newline-framed line into requests, notifications, or errors.

    JSON syntax errors raise :class:`ParseError` so the caller can answer with
    ``-32700`` and keep reading. Envelope errors are returned as
    :class:`InvalidRequest` items so batch entries can be answered separately.
    """
    try:
        text = raw.decode("utf-8")
        value: object = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ParseError(str(exc)) from exc

    if isinstance(value, list):
        if not value:
            return ParsedDocument(
                items=(InvalidRequest("batch must contain at least one request"),),
                is_batch=False,
            )
        return ParsedDocument(
            items=tuple(_classify(item) for item in value),
            is_batch=True,
        )
    return ParsedDocument(items=(_classify(value),), is_batch=False)


class StdioTransport:
    """Serve JSON-RPC over a binary line stream.

    One transport instance is single-threaded and synchronous. Every written
    message is serialized completely, written with one newline, and flushed.
    """

    def __init__(
        self,
        handler: Handler,
        stdin: BinaryIO,
        stdout: BinaryIO,
    ) -> None:
        self._handler = handler
        self._stdin = stdin
        self._stdout = stdout

    @staticmethod
    def _encode(value: object) -> bytes:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return text.encode("utf-8")

    def _write_payload(self, payload: bytes) -> None:
        line = payload + b"\n"
        written = self._stdout.write(line)
        if written != len(line):
            raise OSError(f"short write: wrote {written} of {len(line)} bytes")
        self._stdout.flush()

    @staticmethod
    def _error_object(
        request_id: RpcId,
        code: int,
        message: str,
        data: object = _MISSING,
    ) -> dict[str, object]:
        error: dict[str, object] = {"code": code, "message": message}
        if data is not _MISSING:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _error_payload(
        self,
        request_id: RpcId,
        code: int,
        message: str,
        data: object = _MISSING,
    ) -> bytes:
        return self._encode(
            self._error_object(request_id, code, message, data)
        )

    def write_response(self, request_id: RpcId, result: object) -> None:
        """Write one success response line."""
        self._write_payload(
            self._encode(
                {"jsonrpc": "2.0", "id": request_id, "result": result}
            )
        )

    def write_error(
        self,
        request_id: RpcId,
        code: int,
        message: str,
        data: object | None = None,
    ) -> None:
        """Write one error response line; omit ``data`` when it is None."""
        payload = self._error_payload(
            request_id, code, message, data if data is not None else _MISSING
        )
        self._write_payload(payload)

    def write_notification(
        self,
        method: str,
        params: Params | None = None,
    ) -> None:
        """Write one fire-and-forget notification line."""
        if params is not None and not isinstance(params, (dict, list)):
            raise TypeError("notification params must be an object or array")
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write_payload(self._encode(message))

    def _handle(
        self,
        item: Request | Notification | InvalidRequest,
    ) -> bytes | None:
        if isinstance(item, InvalidRequest):
            return self._error_payload(
                item.id, INVALID_REQUEST, "Invalid Request"
            )
        if isinstance(item, Notification):
            try:
                self._handler(item.method, item.params)
            except Exception:
                # A notification has no response channel; failure is silent.
                pass
            return None

        try:
            result = self._handler(item.method, item.params)
        except MethodNotFound:
            return self._error_payload(
                item.id, METHOD_NOT_FOUND, "Method not found"
            )
        except InvalidParams as exc:
            message = str(exc) or "Invalid params"
            return self._error_payload(item.id, INVALID_PARAMS, message)
        except Exception as exc:
            return self._error_payload(
                item.id,
                INTERNAL_ERROR,
                "Internal error",
                {"exception": type(exc).__name__},
            )

        try:
            return self._encode(
                {"jsonrpc": "2.0", "id": item.id, "result": result}
            )
        except Exception as exc:
            return self._error_payload(
                item.id,
                INTERNAL_ERROR,
                "Internal error",
                {"exception": type(exc).__name__},
            )

    def serve(self) -> None:
        """Read lines until EOF and answer every non-notification."""
        while True:
            raw = self._stdin.readline()
            if raw == b"":
                return
            try:
                document = parse_request(raw)
            except ParseError:
                self._write_payload(
                    self._error_payload(None, PARSE_ERROR, "Parse error")
                )
                continue

            payloads: list[bytes] = []
            for item in document.items:
                payload = self._handle(item)
                if payload is not None:
                    payloads.append(payload)

            if document.is_batch:
                if payloads:
                    self._write_payload(b"[" + b",".join(payloads) + b"]")
            elif payloads:
                self._write_payload(payloads[0])

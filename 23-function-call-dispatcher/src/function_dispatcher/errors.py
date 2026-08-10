"""Typed dispatch failures and their JSON-RPC 2.0 mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

ErrorKind = Literal[
    "timeout",
    "transient",
    "schema",
    "not_found",
    "internal",
    "budget_exceeded",
]

JSONRPC_METHOD_NOT_FOUND: Final = -32601
JSONRPC_INVALID_PARAMS: Final = -32602
JSONRPC_INTERNAL_ERROR: Final = -32603

JSONRPC_CODES: Final[dict[ErrorKind, int]] = {
    "timeout": JSONRPC_INTERNAL_ERROR,
    "transient": JSONRPC_INTERNAL_ERROR,
    "schema": JSONRPC_INVALID_PARAMS,
    "not_found": JSONRPC_METHOD_NOT_FOUND,
    "internal": JSONRPC_INTERNAL_ERROR,
    "budget_exceeded": JSONRPC_INTERNAL_ERROR,
}


class TransientError(Exception):
    """A handler-declared transient failure that is safe to retry."""


@dataclass(frozen=True, slots=True)
class DispatchError:
    """The single failure shape returned by the dispatcher."""

    kind: ErrorKind
    message: str
    attempts: int
    jsonrpc_code: int

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts must be non-negative")
        if self.jsonrpc_code != JSONRPC_CODES[self.kind]:
            raise ValueError(
                f"jsonrpc_code {self.jsonrpc_code} does not match kind {self.kind!r}"
            )

    def to_jsonrpc(self, request_id: str | int | None) -> dict[str, object]:
        """Render the envelope as a JSON-RPC 2.0 error response object."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": self.jsonrpc_code, "message": self.message},
        }


__all__ = [
    "DispatchError",
    "ErrorKind",
    "JSONRPC_CODES",
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_METHOD_NOT_FOUND",
    "TransientError",
]

"""JSON-RPC 2.0 over newline-delimited stdio."""

from __future__ import annotations

from .transport import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Handler,
    InvalidParams,
    InvalidRequest,
    MethodNotFound,
    Notification,
    Params,
    ParseError,
    ParsedDocument,
    Request,
    RpcId,
    StdioTransport,
    parse_request,
)

__version__ = "0.1.0"

__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "Handler",
    "InvalidParams",
    "InvalidRequest",
    "MethodNotFound",
    "Notification",
    "Params",
    "ParseError",
    "ParsedDocument",
    "Request",
    "RpcId",
    "StdioTransport",
    "parse_request",
]

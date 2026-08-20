from __future__ import annotations

import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import TracebackType
from typing import Iterator, Literal, Mapping, Protocol

from .metrics import MetricsRegistry

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
ERROR_TYPE = "error.type"

SpanStatus = Literal["UNSET", "OK", "ERROR"]


class SpanExporter(Protocol):
    def export(self, span: GenAISpan) -> bool: ...


@dataclass
class GenAISpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    attributes: dict[str, object]
    start_unix_nano: int
    end_unix_nano: int | None = None
    status: SpanStatus = "UNSET"
    status_message: str = ""
    events: list[dict[str, object]] = field(default_factory=list)

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append(
            {
                "name": name,
                "time_unix_nano": time.time_ns(),
                "attributes": dict(attributes or {}),
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "attributes": dict(self.attributes),
            "start_unix_nano": self.start_unix_nano,
            "end_unix_nano": self.end_unix_nano,
            "status": self.status,
            "status_message": self.status_message,
            "events": [dict(event) for event in self.events],
        }


def _random_hex(length: int) -> str:
    while True:
        candidate = uuid.uuid4().hex[:length]
        if int(candidate, 16) != 0:
            return candidate


def new_trace_id() -> str:
    return _random_hex(32)


def new_span_id() -> str:
    return _random_hex(16)


_ACTIVE_BUILDER: ContextVar[SpanBuilder | None] = ContextVar(
    "otel_active_builder", default=None
)


def active_builder() -> SpanBuilder | None:
    return _ACTIVE_BUILDER.get()


class SpanBuilder:
    def __init__(
        self,
        exporter: SpanExporter,
        metrics: MetricsRegistry | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.exporter = exporter
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self.trace_id = trace_id if trace_id is not None else new_trace_id()
        self._current: ContextVar[GenAISpan | None] = ContextVar(
            f"otel_current_span_{id(self)}", default=None
        )

    def span(
        self,
        name: str,
        attrs: Mapping[str, object] | None = None,
        parent: GenAISpan | str | None = None,
    ) -> SpanContext:
        return SpanContext(self, name, dict(attrs or {}), parent)

    @contextmanager
    def activate(self) -> Iterator[SpanBuilder]:
        token = _ACTIVE_BUILDER.set(self)
        try:
            yield self
        finally:
            _ACTIVE_BUILDER.reset(token)

    def current_span(self) -> GenAISpan | None:
        return self._current.get()


class SpanContext:
    def __init__(
        self,
        builder: SpanBuilder,
        name: str,
        attrs: dict[str, object],
        parent: GenAISpan | str | None,
    ) -> None:
        self.builder = builder
        self.name = name
        self.attrs = attrs
        self.parent = parent
        self.span: GenAISpan | None = None
        self._span_token: Token[GenAISpan | None] | None = None
        self._builder_token: Token[SpanBuilder | None] | None = None

    def __enter__(self) -> GenAISpan:
        current = self.builder.current_span()
        if isinstance(self.parent, GenAISpan):
            parent_span_id = self.parent.span_id
            trace_id = self.parent.trace_id
        elif isinstance(self.parent, str):
            parent_span_id = self.parent
            trace_id = current.trace_id if current is not None else self.builder.trace_id
        elif current is not None:
            parent_span_id = current.span_id
            trace_id = current.trace_id
        else:
            parent_span_id = None
            trace_id = self.builder.trace_id

        self.span = GenAISpan(
            trace_id=trace_id,
            span_id=new_span_id(),
            parent_span_id=parent_span_id,
            name=self.name,
            attributes=dict(self.attrs),
            start_unix_nano=time.time_ns(),
        )
        self._builder_token = _ACTIVE_BUILDER.set(self.builder)
        self._span_token = self.builder._current.set(self.span)
        return self.span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        span = self.span
        if span is None:
            return False

        try:
            span.end_unix_nano = max(span.start_unix_nano, time.time_ns())
            if exc is not None:
                span.status = "ERROR"
                span.status_message = str(exc) or type(exc).__name__
                span.attributes[ERROR_TYPE] = type(exc).__name__
                span.add_event(
                    "exception",
                    {
                        "exception.type": type(exc).__name__,
                        "exception.message": str(exc),
                        "exception.stacktrace": "".join(
                            traceback.format_exception(type(exc), exc, exc.__traceback__)
                        ),
                    },
                )
            else:
                span.status = "OK"
        finally:
            if self._span_token is not None:
                self.builder._current.reset(self._span_token)
            if self._builder_token is not None:
                _ACTIVE_BUILDER.reset(self._builder_token)
            self.builder.exporter.export(span)
        return False

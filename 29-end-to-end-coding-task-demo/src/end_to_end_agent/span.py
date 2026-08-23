"""A minimal OTel-shaped GenAI span builder with JSONL export."""

from __future__ import annotations

import json
import time
import traceback
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
ERROR_TYPE = "error.type"

AGENT_NAME = "coding-agent"

SpanStatus = Literal["UNSET", "OK", "ERROR"]


@dataclass
class GenAISpan:
    """One finalized operation; JSONL is the offline OTLP stand-in."""

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


class SpanBuilder:
    """Own one trace id and a stack of active parent spans."""

    def __init__(self, export_path: str | Path | None = None) -> None:
        self.trace_id = new_trace_id()
        self.export_path = Path(export_path) if export_path is not None else None
        self.spans: list[GenAISpan] = []
        self._stack: list[GenAISpan] = []

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> Generator[GenAISpan, None, None]:
        if not isinstance(name, str) or not name:
            raise ValueError("span name must be a non-empty string")
        parent = self._stack[-1] if self._stack else None
        span = GenAISpan(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            parent_span_id=parent.span_id if parent is not None else None,
            name=name,
            attributes=dict(attributes or {}),
            start_unix_nano=time.time_ns(),
        )
        self._stack.append(span)
        try:
            yield span
        except BaseException as exc:
            span.status = "ERROR"
            span.status_message = str(exc) or type(exc).__name__
            span.attributes[ERROR_TYPE] = type(exc).__name__
            span.add_event(
                "exception",
                {
                    "exception.type": type(exc).__name__,
                    "exception.message": str(exc),
                    "exception.stacktrace": "".join(
                        traceback.format_exception(
                            type(exc), exc, exc.__traceback__
                        )
                    ),
                },
            )
            raise
        else:
            if span.status == "UNSET":
                span.status = "OK"
        finally:
            span.end_unix_nano = max(span.start_unix_nano, time.time_ns())
            if self._stack and self._stack[-1] is span:
                self._stack.pop()
            self.spans.append(span)
            self._export(span)

    def _export(self, span: GenAISpan) -> None:
        if self.export_path is None:
            return
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            span.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.export_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


__all__ = [
    "AGENT_NAME",
    "ERROR_TYPE",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_TYPE",
    "GenAISpan",
    "SpanBuilder",
    "SpanStatus",
    "new_span_id",
    "new_trace_id",
]

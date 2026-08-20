from __future__ import annotations

import re

import pytest

from otel_observability import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    SpanBuilder,
    wrap_tool_call,
)


def test_wrapped_tool_emits_span_and_metrics_inside_parent(recorder) -> None:
    builder = SpanBuilder(recorder)

    @wrap_tool_call("read_file")
    def read_file(value: str) -> str:
        return value.upper()

    with builder.span("gen_ai.chat") as chat:
        assert read_file("notes") == "NOTES"

    tool, chat_span = recorder.spans
    assert tool.name == "gen_ai.tool.execution"
    assert tool.parent_span_id == chat_span.span_id == chat.span_id
    assert tool.trace_id == chat_span.trace_id == builder.trace_id
    assert tool.status == "OK"
    assert tool.attributes[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert tool.attributes[GEN_AI_TOOL_NAME] == "read_file"
    assert re.fullmatch(r"call_[0-9a-f]{16}", str(tool.attributes[GEN_AI_TOOL_CALL_ID]))

    counter = builder.metrics.counters["tools_called_total"]
    histogram = builder.metrics.histograms["tool_latency_ms"]
    assert counter.value({"tool": "read_file"}) == 1
    assert histogram.count({"tool": "read_file"}) == 1


def test_wrapped_tool_error_records_error_and_still_counts(recorder) -> None:
    builder = SpanBuilder(recorder)

    @wrap_tool_call("explode")
    def explode() -> None:
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        with builder.span("gen_ai.chat"):
            explode()

    tool, chat_span = recorder.spans
    assert tool.status == "ERROR"
    assert tool.status_message == "bad input"
    assert chat_span.status == "ERROR"
    assert tool.events[0]["name"] == "exception"
    assert builder.metrics.counters["tools_called_total"].value(
        {"tool": "explode"}
    ) == 1
    assert builder.metrics.histograms["tool_latency_ms"].count(
        {"tool": "explode"}
    ) == 1


def test_wrapped_tool_is_passthrough_without_any_builder(recorder) -> None:
    @wrap_tool_call("add")
    def add(left: int, right: int) -> int:
        return left + right

    assert add(2, 3) == 5
    assert recorder.spans == []


def test_activate_allows_instrumentation_outside_a_parent_span(recorder) -> None:
    builder = SpanBuilder(recorder, trace_id="d" * 32)

    @wrap_tool_call("ping")
    def ping() -> str:
        return "pong"

    with builder.activate():
        assert ping() == "pong"

    tool = recorder.spans[0]
    assert tool.parent_span_id is None
    assert tool.trace_id == "d" * 32
    assert builder.metrics.counters["tools_called_total"].value({"tool": "ping"}) == 1


def test_wrap_preserves_function_metadata() -> None:
    @wrap_tool_call("documented")
    def documented(value: int) -> int:
        """A documented tool."""
        return value

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "A documented tool."
    assert documented(7) == 7


def test_empty_tool_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        wrap_tool_call("")

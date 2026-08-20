from __future__ import annotations

import re

import pytest

from otel_observability import (
    ERROR_TYPE,
    SpanBuilder,
    new_span_id,
    new_trace_id,
)


def test_identifiers_match_w3c_widths_and_are_nonzero() -> None:
    trace_ids = {new_trace_id() for _ in range(100)}
    span_ids = {new_span_id() for _ in range(100)}

    assert len(trace_ids) == 100
    assert len(span_ids) == 100
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in trace_ids)
    assert all(re.fullmatch(r"[0-9a-f]{16}", value) for value in span_ids)
    assert all(int(value, 16) != 0 for value in trace_ids)
    assert all(int(value, 16) != 0 for value in span_ids)


def test_nested_span_links_parent_and_shares_trace(recorder) -> None:
    builder = SpanBuilder(recorder)
    with builder.span("gen_ai.chat") as parent:
        with builder.span("gen_ai.tool.execution") as child:
            pass

    child_span, parent_span = recorder.spans
    assert parent_span is parent
    assert child_span is child
    assert parent_span.parent_span_id is None
    assert child_span.parent_span_id == parent_span.span_id
    assert child_span.trace_id == parent_span.trace_id == builder.trace_id


def test_explicit_cross_builder_parent_adopts_trace(recorder) -> None:
    first = SpanBuilder(recorder, trace_id="a" * 32)
    with first.span("parent"):
        pass
    parent_span = recorder.spans[0]

    second = SpanBuilder(recorder)
    with second.span("child", parent=parent_span) as child:
        pass

    assert child.trace_id == parent_span.trace_id == "a" * 32
    assert child.parent_span_id == parent_span.span_id


def test_string_parent_sets_only_parent_span_id(recorder) -> None:
    builder = SpanBuilder(recorder, trace_id="b" * 32)
    with builder.span("child", parent="0123456789abcdef") as child:
        pass

    assert child.parent_span_id == "0123456789abcdef"
    assert child.trace_id == "b" * 32


def test_success_sets_ok_status_and_finishes_after_start(recorder) -> None:
    builder = SpanBuilder(recorder)
    with builder.span("ok") as span:
        pass

    assert span.status == "OK"
    assert span.status_message == ""
    assert span.end_unix_nano is not None
    assert span.end_unix_nano >= span.start_unix_nano


def test_exception_marks_error_and_reexecutes(recorder) -> None:
    builder = SpanBuilder(recorder)
    with pytest.raises(RuntimeError, match="boom"):
        with builder.span("failing") as span:
            raise RuntimeError("boom")

    exported = recorder.spans[0]
    assert exported is span
    assert exported.status == "ERROR"
    assert exported.status_message == "boom"
    assert exported.attributes[ERROR_TYPE] == "RuntimeError"
    assert exported.events[0]["name"] == "exception"
    event_attributes = exported.events[0]["attributes"]
    assert isinstance(event_attributes, dict)
    assert event_attributes["exception.type"] == "RuntimeError"
    assert event_attributes["exception.message"] == "boom"
    assert "RuntimeError: boom" in event_attributes["exception.stacktrace"]


def test_add_event_records_timestamp_and_attributes(recorder) -> None:
    builder = SpanBuilder(recorder)
    with builder.span("evented") as span:
        span.add_event("checkpoint", {"step": 3})

    event = span.events[0]
    assert event["name"] == "checkpoint"
    assert isinstance(event["time_unix_nano"], int)
    assert event["attributes"] == {"step": 3}


from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from otel_observability import JSONLExporter, SpanBuilder


def test_span_roundtrips_through_jsonl_with_exact_keys(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    exporter = JSONLExporter(trace_path)
    builder = SpanBuilder(exporter, trace_id="c" * 32)

    with builder.span(
        "gen_ai.tool.execution",
        {"gen_ai.tool.name": "read_file", "attempt": 1},
    ) as span:
        span.add_event("note", {"ok": True})

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) == {
        "trace_id",
        "span_id",
        "parent_span_id",
        "name",
        "attributes",
        "start_unix_nano",
        "end_unix_nano",
        "status",
        "status_message",
        "events",
    }
    assert payload["trace_id"] == "c" * 32
    assert payload["span_id"] == span.span_id
    assert payload["name"] == "gen_ai.tool.execution"
    assert payload["attributes"] == {"gen_ai.tool.name": "read_file", "attempt": 1}
    assert payload["status"] == "OK"
    assert payload["events"][0]["name"] == "note"


def test_exporter_failure_does_not_break_span_exit(tmp_path: Path) -> None:
    exporter = JSONLExporter(tmp_path)
    builder = SpanBuilder(exporter)

    with builder.span("still-fine") as span:
        pass

    assert span.status == "OK"
    assert exporter.errors == 1
    assert exporter.exported == 0


def test_appends_one_json_object_per_span(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    exporter = JSONLExporter(trace_path)
    builder = SpanBuilder(exporter)

    with builder.span("first"):
        pass
    with builder.span("second"):
        pass

    raw = trace_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    lines = raw.splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert [payload["name"] for payload in payloads] == ["first", "second"]
    assert exporter.exported == 2
    assert exporter.errors == 0


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    trace_path = tmp_path / "nested" / "deeper" / "traces.jsonl"
    exporter = JSONLExporter(trace_path)
    builder = SpanBuilder(exporter)

    with builder.span("created"):
        pass

    assert trace_path.is_file()
    assert exporter.exported == 1


def test_unicode_and_newlines_stay_on_one_physical_line(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    exporter = JSONLExporter(trace_path)
    builder = SpanBuilder(exporter)

    with builder.span("unicode", {"note": "line one\nline two — café"}) as span:
        pass

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["span_id"] == span.span_id
    assert payload["attributes"]["note"] == "line one\nline two — café"


def test_concurrent_exports_produce_valid_non_interleaved_lines(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    exporter = JSONLExporter(trace_path)

    def emit(index: int) -> None:
        with SpanBuilder(exporter).span(f"span-{index}"):
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(emit, range(40)))

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40
    payloads = [json.loads(line) for line in lines]
    assert {payload["name"] for payload in payloads} == {
        f"span-{index}" for index in range(40)
    }
    assert exporter.exported == 40
    assert exporter.errors == 0


def test_directory_path_returns_false_and_surfaces_error(
    tmp_path: Path, capsys
) -> None:
    exporter = JSONLExporter(tmp_path)
    builder = SpanBuilder(exporter)

    with builder.span("unwritable") as span:
        pass

    assert span.status == "OK"
    assert exporter.exported == 0
    assert exporter.errors == 1
    assert "IsADirectoryError" in exporter.last_error
    assert "[otel] JSONL export failed" in capsys.readouterr().err


def test_non_serializable_attribute_returns_false(
    tmp_path: Path, capsys
) -> None:
    exporter = JSONLExporter(tmp_path / "traces.jsonl")
    builder = SpanBuilder(exporter)

    with builder.span("bad-attribute") as span:
        span.attributes["bad"] = object()

    assert span.status == "OK"
    assert exporter.errors == 1
    assert "TypeError" in exporter.last_error
    assert "[otel] JSONL export failed" in capsys.readouterr().err

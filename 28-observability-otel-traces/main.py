from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from otel_observability import (  # noqa: E402
    DEFAULT_LATENCY_BUCKETS,
    ERROR_TYPE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    Counter,
    GenAISpan,
    Histogram,
    JSONLExporter,
    MetricsRegistry,
    SpanBuilder,
    SpanContext,
    SpanExporter,
    new_span_id,
    new_trace_id,
    prometheus_exposition,
    wrap_tool_call,
)

__all__ = [
    "DEFAULT_LATENCY_BUCKETS",
    "ERROR_TYPE",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_MAX_TOKENS",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_RESPONSE_ID",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_SYSTEM",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "Counter",
    "GenAISpan",
    "Histogram",
    "JSONLExporter",
    "MetricsRegistry",
    "SpanBuilder",
    "SpanContext",
    "SpanExporter",
    "new_span_id",
    "new_trace_id",
    "prometheus_exposition",
    "wrap_tool_call",
    "read_file",
    "write_file",
    "run_invocation",
    "run_demo",
]

PROVIDER = "openai"
MODEL = "gpt-4o-mini"
MAX_TOKENS = 512
RESPONSE_ID = "chatcmpl_local_demo"
INPUT_TOKENS = 128
OUTPUT_TOKENS = 64
NOTES = "alpha\nbeta\n"


@wrap_tool_call("read_file")
def read_file(path: Path) -> str:
    time.sleep(0.006)
    return path.read_text(encoding="utf-8")


@wrap_tool_call("write_file")
def write_file(path: Path, content: str) -> int:
    time.sleep(0.030)
    path.write_text(content, encoding="utf-8")
    return len(content)


def run_invocation(workspace: Path, builder: SpanBuilder) -> None:
    notes_path = workspace / "notes.txt"
    notes_path.write_text(NOTES, encoding="utf-8")

    attrs: dict[str, object] = {
        GEN_AI_SYSTEM: PROVIDER,
        GEN_AI_PROVIDER_NAME: PROVIDER,
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_REQUEST_MODEL: MODEL,
        GEN_AI_REQUEST_MAX_TOKENS: MAX_TOKENS,
    }

    with builder.activate(), builder.span("gen_ai.chat", attrs) as chat:
        notes = read_file(notes_path)
        try:
            read_file(workspace / "missing.txt")
        except FileNotFoundError:
            pass
        write_file(workspace / "summary.txt", notes.upper())
        chat.attributes[GEN_AI_USAGE_INPUT_TOKENS] = INPUT_TOKENS
        chat.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] = OUTPUT_TOKENS
        chat.attributes[GEN_AI_RESPONSE_MODEL] = MODEL
        chat.attributes[GEN_AI_RESPONSE_ID] = RESPONSE_ID


def run_demo() -> int:
    with tempfile.TemporaryDirectory(prefix="otel-observability-") as tmp:
        workspace = Path(tmp)
        trace_path = workspace / "traces.jsonl"
        exporter = JSONLExporter(trace_path)
        registry = MetricsRegistry()
        builder = SpanBuilder(exporter, metrics=registry)

        run_invocation(workspace, builder)

        lines = trace_path.read_text(encoding="utf-8").splitlines()
        spans: list[dict[str, Any]] = [json.loads(line) for line in lines]
        exposition = prometheus_exposition(registry)

        if exporter.errors != 0:
            raise AssertionError(f"exporter reported {exporter.errors} errors")
        if len(spans) != 4:
            raise AssertionError(f"expected 4 spans, got {len(spans)}")

        required = (
            'tools_called_total{tool="read_file"} 2',
            'tools_called_total{tool="write_file"} 1',
            'tool_latency_ms_count{tool="read_file"} 2',
        )
        for line in required:
            if line not in exposition:
                raise AssertionError(f"missing expected metrics line: {line}")

        print(
            f"Wrote {len(spans)} spans to {trace_path} "
            "(temporary directory removed after demo)"
        )
        print("\nSample spans:")
        chat = next(span for span in spans if span["name"] == "gen_ai.chat")
        tools = [span for span in spans if span["name"] == "gen_ai.tool.execution"]
        for span in [chat, *tools[:2]]:
            start = int(span["start_unix_nano"])
            end = int(span["end_unix_nano"])
            duration_ms = (end - start) / 1_000_000
            print(
                f"- {span['name']} status={span['status']} "
                f"duration_ms={duration_ms:.2f} trace={str(span['trace_id'])[:8]}"
            )

        print("\nPrometheus exposition:")
        print(exposition, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from otel_observability import (
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
    SpanBuilder,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = MODULE_ROOT / "main.py"


def load_demo_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_main_under_test", MAIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_invocation_emits_complete_genai_trace(recorder, tmp_path: Path) -> None:
    module = load_demo_module()
    builder = SpanBuilder(recorder)
    module.run_invocation(tmp_path, builder)

    spans = recorder.spans
    assert len(spans) == 4
    chat = next(span for span in spans if span.name == "gen_ai.chat")
    tools = [span for span in spans if span.name == "gen_ai.tool.execution"]
    assert len(tools) == 3

    assert chat.attributes[GEN_AI_SYSTEM] == "openai"
    assert chat.attributes[GEN_AI_PROVIDER_NAME] == "openai"
    assert chat.attributes[GEN_AI_OPERATION_NAME] == "chat"
    assert chat.attributes[GEN_AI_REQUEST_MODEL] == "gpt-4o-mini"
    assert chat.attributes[GEN_AI_REQUEST_MAX_TOKENS] == 512
    assert chat.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 128
    assert chat.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 64
    assert chat.attributes[GEN_AI_RESPONSE_MODEL] == "gpt-4o-mini"
    assert chat.attributes[GEN_AI_RESPONSE_ID] == "chatcmpl_local_demo"
    assert chat.status == "OK"

    read_tools = [span for span in tools if span.attributes[GEN_AI_TOOL_NAME] == "read_file"]
    write_tools = [span for span in tools if span.attributes[GEN_AI_TOOL_NAME] == "write_file"]
    assert len(read_tools) == 2
    assert len(write_tools) == 1
    assert sorted(span.status for span in read_tools) == ["ERROR", "OK"]
    assert write_tools[0].status == "OK"

    for tool in tools:
        assert tool.trace_id == chat.trace_id
        assert tool.parent_span_id == chat.span_id
        assert re.fullmatch(
            r"call_[0-9a-f]{16}",
            str(tool.attributes[GEN_AI_TOOL_CALL_ID]),
        )

    summary = tmp_path / "summary.txt"
    assert summary.read_text(encoding="utf-8") == "ALPHA\nBETA\n"
    assert builder.metrics.counters["tools_called_total"].value(
        {"tool": "read_file"}
    ) == 2
    assert builder.metrics.counters["tools_called_total"].value(
        {"tool": "write_file"}
    ) == 1


def test_demo_subprocess_is_idempotent_from_foreign_cwd(tmp_path: Path) -> None:
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(MAIN_PATH)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        output = completed.stdout
        assert "Wrote 4 spans" in output
        assert "(temporary directory removed after demo)" in output
        assert "gen_ai.chat" in output
        assert "status=ERROR" in output
        assert "# TYPE tools_called_total counter" in output
        assert "# TYPE tool_latency_ms histogram" in output
        assert 'tools_called_total{tool="read_file"} 2' in output
        assert 'tools_called_total{tool="write_file"} 1' in output
        assert 'tool_latency_ms_count{tool="read_file"} 2' in output
        assert not (tmp_path / "traces.jsonl").exists()

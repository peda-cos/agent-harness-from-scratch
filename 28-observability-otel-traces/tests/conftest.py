from __future__ import annotations

from collections.abc import Iterator

import pytest

from otel_observability import GenAISpan


class RecordingExporter:
    def __init__(self) -> None:
        self.spans: list[GenAISpan] = []
        self.errors = 0

    def export(self, span: GenAISpan) -> bool:
        self.spans.append(span)
        return True


@pytest.fixture
def recorder() -> Iterator[RecordingExporter]:
    yield RecordingExporter()

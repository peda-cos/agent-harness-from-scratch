from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from .span import GenAISpan


class JSONLExporter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.exported = 0
        self.errors = 0
        self.last_error = ""
        self._lock = threading.Lock()

    def export(self, span: GenAISpan) -> bool:
        with self._lock:
            try:
                line = json.dumps(
                    span.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except Exception as exc:
                # The harness must keep running even when telemetry storage fails.
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[otel] JSONL export failed for {self.path}: {self.last_error}",
                    file=sys.stderr,
                )
                return False
            self.exported += 1
            return True

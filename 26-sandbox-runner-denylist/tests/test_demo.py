from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]


def _run_demo() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MODULE_ROOT / "main.py")],
        cwd=MODULE_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_demo_exits_zero_and_reports_all_outcomes() -> None:
    completed = _run_demo()

    assert completed.returncode == 0, completed.stderr
    assert "demo verified:" in completed.stdout
    assert '"denied":true' in completed.stdout
    assert '"timed_out":true' in completed.stdout
    assert '"truncated":true' in completed.stdout


def test_demo_prints_one_json_object_per_call() -> None:
    completed = _run_demo()
    assert completed.returncode == 0, completed.stderr

    lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith("{")
    ]
    assert len(lines) == 13
    for line in lines:
        payload = json.loads(line)
        assert "call" in payload
        assert "result" in payload

from __future__ import annotations

import json
import subprocess
import sys

from conftest import MODULE_ROOT


def test_demo_prints_clean_json_report() -> None:
    completed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=MODULE_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["k"] == 5
    assert payload["total_tasks"] == 5
    assert payload["total_samples"] == 25
    assert payload["pass_at_1"] == 1.0
    assert payload["pass_at_k"] == 1.0
    assert payload["self_reported_pass_rate"] == 1.0
    assert payload["mean_cost_units"] == 0.0
    assert payload["mean_latency_ms"] > 0.0
    assert len(payload["tasks"]) == 5
    assert all(task["passes"] == 5 for task in payload["tasks"])

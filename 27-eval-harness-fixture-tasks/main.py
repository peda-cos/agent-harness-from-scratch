from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from eval_harness import EvalHarness, apply_known_fixes  # noqa: E402

TASKS_DIR = Path(__file__).resolve().parent / "tasks"
DEMO_K = 5


def run_demo() -> int:
    report = EvalHarness(TASKS_DIR, apply_known_fixes, k=DEMO_K).run()
    payload = report.to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))

    if report.total_samples != 25:
        raise AssertionError("demo expected 25 samples")
    if report.pass_at_1 != 1.0 or report.pass_at_k != 1.0:
        raise AssertionError("reference candidate must pass every fixture")
    if report.self_reported_pass_rate != 1.0:
        raise AssertionError("reference candidate must self-report every pass")
    if report.total_candidate_errors != 0:
        raise AssertionError("reference candidate must not raise")
    if any(task.passes != DEMO_K for task in report.tasks):
        raise AssertionError("every task must pass all samples")
    return 0


def main() -> int:
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())

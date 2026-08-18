from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from eval_harness import FixtureTask, SampleResult, apply_known_fixes

MODULE_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = MODULE_ROOT / "tasks"


@pytest.fixture
def reference_candidate() -> Callable[[FixtureTask, str], SampleResult]:
    return apply_known_fixes

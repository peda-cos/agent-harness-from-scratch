"""Public API for the fixture-task eval harness."""

from __future__ import annotations

from .fixtures import load_tasks
from .harness import Candidate, EvalHarness
from .models import (
    EvalReport,
    FixtureError,
    FixtureTask,
    SampleResult,
    TaskReport,
    pass_at_k,
    percentile,
    safe_child,
)
from .reference import apply_known_fixes
from .verifiers import Verifier, VerifierRegistry, builtin_registry

__all__ = [
    "Candidate",
    "EvalHarness",
    "EvalReport",
    "FixtureError",
    "FixtureTask",
    "SampleResult",
    "TaskReport",
    "Verifier",
    "VerifierRegistry",
    "apply_known_fixes",
    "builtin_registry",
    "load_tasks",
    "pass_at_k",
    "percentile",
    "safe_child",
]

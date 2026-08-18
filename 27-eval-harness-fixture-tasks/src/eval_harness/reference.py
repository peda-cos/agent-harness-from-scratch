"""Deterministic reference candidate that applies known literal fixes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .models import FixtureTask, SampleResult, safe_child


@dataclass(frozen=True, slots=True)
class _Patch:
    path: str
    old: str
    new: str


_PATCHES: dict[str, tuple[_Patch, ...]] = {
    "fizzbuzz-off-by-one": (
        _Patch(
            path="fizzbuzz.py",
            old="    for number in range(1, n):",
            new="    for number in range(1, n + 1):",
        ),
    ),
    "factorial-missing-return": (
        _Patch(
            path="factorial.py",
            old="        result *= value\n",
            new="        result *= value\n    return result\n",
        ),
    ),
    "error-message-typo": (
        _Patch(
            path="validation.py",
            old='"invalid portt"',
            new='"invalid port"',
        ),
    ),
    "empty-function-body": (
        _Patch(
            path="slugify.py",
            old="def slugify(text: str) -> str:\n    pass\n",
            new=(
                "def slugify(text: str) -> str:\n"
                "    lowered = text.strip().lower()\n"
                '    return "-".join(lowered.split())\n'
            ),
        ),
    ),
    "linked-list-off-by-one": (
        _Patch(
            path="traversal.py",
            old="    while node.next is not None:\n",
            new="    while node is not None:\n",
        ),
    ),
}


def apply_known_fixes(task: FixtureTask, scratch_dir: str) -> SampleResult:
    """Apply literal source patches for a bundled fixture; never read expected/."""

    started = time.perf_counter()
    patches = _PATCHES.get(task.id)
    edits: list[str] = []
    success = False

    if patches is not None:
        root = Path(scratch_dir)
        success = True
        for patch in patches:
            path = safe_child(root, patch.path)
            try:
                text = path.read_text(encoding="utf-8")
                if patch.old not in text:
                    success = False
                    break
                path.write_text(
                    text.replace(patch.old, patch.new, 1), encoding="utf-8"
                )
            except (OSError, UnicodeError):
                success = False
                break
            edits.append(patch.path)

    latency = (time.perf_counter() - started) * 1000.0
    return SampleResult(
        success_self_reported=success,
        latency_ms=latency,
        cost_units=0.0,
        edits=tuple(edits),
    )

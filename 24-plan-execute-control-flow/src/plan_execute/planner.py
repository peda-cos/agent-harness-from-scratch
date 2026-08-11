"""Pure deterministic planner used by the offline demo and tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from .models import Step

Planner: TypeAlias = Callable[[str, list[Step], str | None], list[Step]]

RATE_LIMITED = "rate_limited"
APPROVAL_REQUIRED = "approval_required"


def deterministic_planner(
    goal: str,
    history: list[Step],
    last_error: str | None,
) -> list[Step]:
    """Choose a plan from the most recent failure, or start with four steps."""

    if last_error is None:
        return [
            Step(
                1,
                "release.inspect",
                {"repo": goal},
                "repository metadata is available",
            ),
            Step(
                2,
                "release.draft_notes",
                {"repo": goal},
                "draft release notes exist",
            ),
            Step(
                3,
                "release.validate",
                {"repo": goal},
                "release notes pass validation",
            ),
            Step(
                4,
                "release.publish",
                {"repo": goal, "channel": "stable"},
                "the stable release is published",
            ),
        ]

    if last_error.startswith(RATE_LIMITED):
        return [
            Step(
                1,
                "notes.read_cached",
                {"repo": goal},
                "cached notes are available",
            ),
            Step(
                2,
                "release.validate",
                {"repo": goal},
                "release notes pass validation",
            ),
            Step(
                3,
                "release.publish",
                {"repo": goal, "channel": "stable"},
                "the stable release is published",
            ),
        ]

    if last_error.startswith(APPROVAL_REQUIRED):
        return [
            Step(
                1,
                "release.queue_for_human",
                {"repo": goal, "reason": "approval_required"},
                "the release is queued for human approval",
            ),
            Step(
                2,
                "notify.escalate",
                {"repo": goal, "queue": "release-approvals"},
                "the release owner is notified",
            ),
        ]

    return []


__all__ = [
    "APPROVAL_REQUIRED",
    "Planner",
    "RATE_LIMITED",
    "deterministic_planner",
]

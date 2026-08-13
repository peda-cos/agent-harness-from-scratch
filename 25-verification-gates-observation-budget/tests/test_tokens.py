from __future__ import annotations

import pytest

from verification_gates.tokens import (
    TRUNCATION_MARKER,
    clip_text,
    estimate_tokens,
)


def test_estimate_tokens_is_len_div_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 400) == 100
    with pytest.raises(TypeError):
        estimate_tokens(1)  # type: ignore[arg-type]


def test_clip_text_leaves_text_within_budget_unchanged() -> None:
    text = "x" * 400
    assert clip_text(text, 100) == (text, False)
    assert clip_text("abc", 0) == ("abc", False)
    assert clip_text("", 0) == ("", False)


def test_clip_text_zero_limit_returns_empty_truncated() -> None:
    assert clip_text("x" * 400, 0) == ("", True)


def test_clip_text_reserves_marker_and_preserves_both_ends() -> None:
    text = "HEAD" + "x" * 1000 + "TAIL"
    clipped, truncated = clip_text(text, 40)

    assert truncated is True
    assert clipped.startswith("HEAD")
    assert clipped.endswith("TAIL")
    assert TRUNCATION_MARKER in clipped
    assert estimate_tokens(clipped) <= 40


def test_clip_text_tiny_budget_falls_back_without_marker() -> None:
    clipped, truncated = clip_text("x" * 400, 2)

    assert truncated is True
    assert clipped == "x" * 8
    assert estimate_tokens(clipped) <= 2


def test_clip_text_marker_only_at_marker_budget() -> None:
    clipped, truncated = clip_text("x" * 400, 4)

    assert truncated is True
    assert clipped == TRUNCATION_MARKER
    assert estimate_tokens(clipped) == 4


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_clip_text_rejects_invalid_limits(limit: object) -> None:
    with pytest.raises(ValueError):
        clip_text("text", limit)  # type: ignore[arg-type]

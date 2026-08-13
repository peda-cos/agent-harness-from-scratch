"""The lesson's intentionally simple token heuristic and budget-aware clipping."""

from __future__ import annotations

TRUNCATION_MARKER = "\n...[truncated]...\n"


def estimate_tokens(text: str) -> int:
    """Estimate tokens as one per four characters, exactly as the lesson specifies."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return len(text) // 4


def clip_text(text: str, token_limit: int) -> tuple[str, bool]:
    """Clip text to a token ceiling, preserving its beginning and end."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(token_limit, bool) or not isinstance(token_limit, int):
        raise ValueError("token_limit must be an integer")
    if token_limit < 0:
        raise ValueError("token_limit must be non-negative")

    if estimate_tokens(text) <= token_limit:
        return text, False
    if token_limit == 0:
        return "", True

    marker_tokens = estimate_tokens(TRUNCATION_MARKER)
    if token_limit < marker_tokens:
        return text[: token_limit * 4], True

    body_tokens = token_limit - marker_tokens
    head_tokens = (body_tokens + 1) // 2
    tail_tokens = body_tokens - head_tokens
    head = text[: head_tokens * 4]
    tail = text[-tail_tokens * 4 :] if tail_tokens else ""
    return head + TRUNCATION_MARKER + tail, True


__all__ = ["TRUNCATION_MARKER", "clip_text", "estimate_tokens"]

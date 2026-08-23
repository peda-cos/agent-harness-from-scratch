"""Observation accounting and the lesson's deterministic token heuristic."""

from __future__ import annotations

from dataclasses import dataclass

TRUNCATION_MARKER = "\n...[truncated]...\n"


def estimate_tokens(text: str) -> int:
    """Estimate tokens as one per four characters, exactly as the lesson does."""

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


@dataclass(frozen=True, slots=True)
class Observation:
    """One clipped tool result as seen by the policy."""

    step: int
    tool: str
    tokens: int
    truncated: bool

    def __post_init__(self) -> None:
        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 1:
            raise ValueError("step must be a positive integer")
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("tool must be a non-empty string")
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int):
            raise ValueError("tokens must be an integer")
        if self.tokens < 0:
            raise ValueError("tokens must be non-negative")
        if not isinstance(self.truncated, bool):
            raise ValueError("truncated must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "tool": self.tool,
            "tokens": self.tokens,
            "truncated": self.truncated,
        }


class ObservationLedger:
    """Append-only observation records with a cumulative token total."""

    def __init__(self) -> None:
        self._rows: list[Observation] = []
        self._cumulative = 0

    def record(
        self,
        step: int,
        tool: str,
        tokens: int,
        *,
        truncated: bool = False,
    ) -> Observation:
        observation = Observation(
            step=step,
            tool=tool,
            tokens=tokens,
            truncated=truncated,
        )
        if self._rows and observation.step < self._rows[-1].step:
            raise ValueError("observation steps must be non-decreasing")
        self._rows.append(observation)
        self._cumulative += observation.tokens
        return observation

    def cumulative(self) -> int:
        return self._cumulative

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


__all__ = [
    "TRUNCATION_MARKER",
    "Observation",
    "ObservationLedger",
    "clip_text",
    "estimate_tokens",
]

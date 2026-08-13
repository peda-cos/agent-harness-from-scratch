"""Wire shapes for tool calls, observations, and gate decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One model-authored tool call and the observation it claims to rely on."""

    turn: int
    name: str
    arguments: Mapping[str, object]
    observed_turn: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.turn, bool) or not isinstance(self.turn, int):
            raise ValueError("turn must be an integer")
        if self.turn < 1:
            raise ValueError("turn must be a positive integer")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("arguments must be a mapping with string keys")
        if any(not isinstance(key, str) for key in self.arguments):
            raise ValueError("arguments must be a mapping with string keys")
        copied = dict(self.arguments)
        try:
            json.dumps(
                copied,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("arguments must be JSON-serializable") from exc
        object.__setattr__(self, "arguments", copied)
        if self.observed_turn is not None:
            if isinstance(self.observed_turn, bool) or not isinstance(
                self.observed_turn, int
            ):
                raise ValueError("observed_turn must be an integer")
            if self.observed_turn < 0:
                raise ValueError("observed_turn must be a non-negative integer")
            if self.observed_turn > self.turn:
                raise ValueError("observed_turn cannot be in the future")

    def to_dict(self) -> dict[str, object]:
        return {
            "turn": self.turn,
            "name": self.name,
            "arguments": dict(self.arguments),
            "observed_turn": self.observed_turn,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """One row of what the model was actually shown."""

    turn: int
    tool: str
    tokens: int
    truncated: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.turn, bool) or not isinstance(self.turn, int):
            raise ValueError("turn must be an integer")
        if self.turn < 1:
            raise ValueError("turn must be a positive integer")
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
            "turn": self.turn,
            "tool": self.tool,
            "tokens": self.tokens,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    """A single gate's structured verdict."""

    allow: bool
    reason: str | None
    gate_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.allow, bool):
            raise ValueError("allow must be a boolean")
        if not isinstance(self.gate_name, str) or not self.gate_name:
            raise ValueError("gate_name must be a non-empty string")
        if self.allow:
            if self.reason is not None:
                raise ValueError("an allow decision cannot carry a reason")
        elif not isinstance(self.reason, str) or not self.reason:
            raise ValueError("a deny decision requires a non-empty reason")

    @classmethod
    def permit(cls, gate_name: str) -> GateDecision:
        return cls(allow=True, reason=None, gate_name=gate_name)

    @classmethod
    def deny(cls, gate_name: str, reason: str) -> GateDecision:
        return cls(allow=False, reason=reason, gate_name=gate_name)

    def to_dict(self) -> dict[str, object]:
        return {
            "allow": self.allow,
            "reason": self.reason,
            "gate_name": self.gate_name,
        }


__all__ = ["GateDecision", "Observation", "ToolCall"]

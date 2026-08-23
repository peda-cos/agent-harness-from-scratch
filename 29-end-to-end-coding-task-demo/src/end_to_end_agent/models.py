"""Wire models shared by the policy, gates, sandbox, and reports."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One policy-authored tool call, numbered by the harness."""

    step: int
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if isinstance(self.step, bool) or not isinstance(self.step, int):
            raise ValueError("step must be an integer")
        if self.step < 1:
            raise ValueError("step must be a positive integer")
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

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    """A single gate's structured verdict."""

    allow: bool
    gate_name: str
    reason: str | None

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
        return cls(allow=True, gate_name=gate_name, reason=None)

    @classmethod
    def deny(cls, gate_name: str, reason: str) -> GateDecision:
        return cls(allow=False, gate_name=gate_name, reason=reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "allow": self.allow,
            "gate_name": self.gate_name,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Global budgets and subprocess limits for one agent run."""

    max_steps: int = 12
    observation_tokens: int = 2000
    test_timeout_seconds: float = 10.0
    max_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int):
            raise ValueError("max_steps must be an integer")
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        if isinstance(self.observation_tokens, bool) or not isinstance(
            self.observation_tokens, int
        ):
            raise ValueError("observation_tokens must be an integer")
        if self.observation_tokens < 0:
            raise ValueError("observation_tokens must be non-negative")

        if isinstance(self.test_timeout_seconds, bool) or not isinstance(
            self.test_timeout_seconds, (int, float)
        ):
            raise ValueError("test_timeout_seconds must be a number")
        timeout = float(self.test_timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("test_timeout_seconds must be finite and positive")
        object.__setattr__(self, "test_timeout_seconds", timeout)

        if isinstance(self.max_output_bytes, bool) or not isinstance(
            self.max_output_bytes, int
        ):
            raise ValueError("max_output_bytes must be an integer")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


DEFAULT_LIMITS = RunLimits()


__all__ = ["DEFAULT_LIMITS", "GateDecision", "RunLimits", "ToolCall"]

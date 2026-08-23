"""The deterministic five-state coding policy."""

from __future__ import annotations

import re
from typing import Literal, Protocol

from .models import ToolCall

PolicyState = Literal[
    "SURVEY",
    "RUN_TESTS",
    "INSPECT",
    "FIX",
    "VERIFY",
    "SUCCESS",
    "FAILURE",
]

TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE"})
FAILURE_SIGNATURE = re.compile(r"fizzbuzz\(15\) must end with 'FizzBuzz'")
OFF_BY_ONE = re.compile(r"for number in range\(1, n\):")
FIXED_LINE = "for number in range(1, n + 1):"


class Policy(Protocol):
    """The model-shaped seam: emit a call, consume its observation."""

    @property
    def state(self) -> str: ...

    @property
    def done(self) -> bool: ...

    @property
    def failure_reason(self) -> str | None: ...

    def next_call(self, step: int) -> ToolCall | None: ...

    def observe(
        self,
        call: ToolCall,
        observation: str,
        exit_code: int | None,
    ) -> None: ...


class CodingAgentPolicy:
    """Survey, test, inspect, fix, verify; then halt."""

    def __init__(self) -> None:
        self._state: PolicyState = "SURVEY"
        self._fixed_source: str | None = None
        self._failure_reason: str | None = None

    @property
    def state(self) -> PolicyState:
        return self._state

    @property
    def done(self) -> bool:
        return self._state in TERMINAL_STATES

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    def next_call(self, step: int) -> ToolCall | None:
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise ValueError("step must be a positive integer")
        if self.done:
            return None

        if self._state == "SURVEY":
            return ToolCall(step, "read_file", {"path": "."})
        if self._state in {"RUN_TESTS", "VERIFY"}:
            return ToolCall(step, "run_tests", {})
        if self._state == "INSPECT":
            return ToolCall(step, "read_file", {"path": "src/fizz.py"})
        if self._state == "FIX":
            if self._fixed_source is None:
                raise ValueError("fix state has no prepared source")
            return ToolCall(
                step,
                "write_file",
                {"path": "src/fizz.py", "content": self._fixed_source},
            )
        raise AssertionError(f"unhandled policy state: {self._state}")

    def observe(
        self,
        call: ToolCall,
        observation: str,
        exit_code: int | None,
    ) -> None:
        if self.done:
            raise ValueError("policy has already halted")
        expected_tool = {
            "SURVEY": "read_file",
            "RUN_TESTS": "run_tests",
            "INSPECT": "read_file",
            "FIX": "write_file",
            "VERIFY": "run_tests",
            "SUCCESS": "",
            "FAILURE": "",
        }[self._state]
        if call.name != expected_tool:
            raise ValueError(
                f"state {self._state} expected tool {expected_tool!r}, "
                f"received {call.name!r}"
            )

        if self._state == "SURVEY":
            self._state = "RUN_TESTS"
        elif self._state == "RUN_TESTS":
            if exit_code == 0:
                self._state = "SUCCESS"
            elif exit_code is not None and FAILURE_SIGNATURE.search(observation):
                self._state = "INSPECT"
            else:
                self._fail("unrecognized test failure")
        elif self._state == "INSPECT":
            if exit_code != 0:
                self._fail("could not read the failing source")
            elif not OFF_BY_ONE.search(observation):
                self._fail("source does not contain the known off-by-one")
            else:
                self._fixed_source = OFF_BY_ONE.sub(
                    FIXED_LINE,
                    observation,
                    count=1,
                )
                self._state = "FIX"
        elif self._state == "FIX":
            if exit_code != 0:
                self._fail("could not write the corrected source")
            else:
                self._state = "VERIFY"
        elif self._state == "VERIFY":
            if exit_code == 0:
                self._state = "SUCCESS"
            else:
                self._fail("tests still fail after the fix")

    def _fail(self, reason: str) -> None:
        self._state = "FAILURE"
        self._failure_reason = reason


__all__ = [
    "FAILURE_SIGNATURE",
    "FIXED_LINE",
    "OFF_BY_ONE",
    "CodingAgentPolicy",
    "Policy",
    "PolicyState",
]

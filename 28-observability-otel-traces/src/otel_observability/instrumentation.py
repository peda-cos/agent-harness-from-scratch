from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from .span import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    active_builder,
)

P = ParamSpec("P")
R = TypeVar("R")


def wrap_tool_call(tool_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    if not tool_name:
        raise ValueError("tool_name must not be empty")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            builder = active_builder()
            if builder is None:
                return func(*args, **kwargs)

            attrs: dict[str, object] = {
                GEN_AI_OPERATION_NAME: "execute_tool",
                GEN_AI_TOOL_NAME: tool_name,
                GEN_AI_TOOL_CALL_ID: f"call_{uuid.uuid4().hex[:16]}",
            }
            started = time.perf_counter_ns()
            try:
                with builder.span("gen_ai.tool.execution", attrs):
                    return func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter_ns() - started) / 1_000_000
                builder.metrics.counter(
                    "tools_called_total",
                    "Total tool invocations by tool.",
                ).inc({"tool": tool_name})
                builder.metrics.histogram(
                    "tool_latency_ms",
                    "Tool call latency in milliseconds.",
                ).observe(duration_ms, {"tool": tool_name})

        return wrapper

    return decorator

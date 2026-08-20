from __future__ import annotations

import math
import re
from collections.abc import Mapping

DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10000,
)

FrozenLabels = tuple[tuple[str, str], ...]

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_metric_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid metric name: {name!r}")


def _freeze_labels(labels: Mapping[str, str] | None) -> FrozenLabels:
    if not labels:
        return ()
    pairs: list[tuple[str, str]] = []
    for key, value in labels.items():
        if not isinstance(key, str) or not _NAME_RE.fullmatch(key):
            raise ValueError(f"invalid label name: {key!r}")
        if not isinstance(value, str):
            raise TypeError(f"label value for {key!r} must be a string")
        pairs.append((key, value))
    pairs.sort(key=lambda pair: pair[0])
    return tuple(pairs)


class Counter:
    def __init__(self, name: str, help_text: str = "") -> None:
        _validate_metric_name(name)
        self.name = name
        self.help_text = help_text
        self._values: dict[FrozenLabels, int] = {}

    def inc(
        self,
        labels: Mapping[str, str] | None = None,
        amount: int = 1,
    ) -> None:
        if amount < 0:
            raise ValueError("counter amount must be non-negative")
        key = _freeze_labels(labels)
        self._values[key] = self._values.get(key, 0) + amount

    def value(self, labels: Mapping[str, str] | None = None) -> int:
        return self._values.get(_freeze_labels(labels), 0)

    def label_sets(self) -> tuple[FrozenLabels, ...]:
        return tuple(sorted(self._values))


class Histogram:
    def __init__(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS,
    ) -> None:
        _validate_metric_name(name)
        bounds = tuple(float(bound) for bound in buckets)
        if not bounds:
            raise ValueError("histogram requires at least one bucket")
        previous = -math.inf
        for bound in bounds:
            if not math.isfinite(bound):
                raise ValueError("histogram bounds must be finite")
            if bound < 0:
                raise ValueError("histogram bounds must be non-negative")
            if bound <= previous:
                raise ValueError("histogram bounds must be strictly increasing")
            previous = bound
        self.name = name
        self.help_text = help_text
        self.buckets = bounds
        self._samples: dict[FrozenLabels, list[float]] = {}

    def observe(
        self,
        value_ms: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        value = float(value_ms)
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observations must be finite and non-negative")
        self._samples.setdefault(_freeze_labels(labels), []).append(value)

    def samples(self, labels: Mapping[str, str] | None = None) -> tuple[float, ...]:
        return tuple(self._samples.get(_freeze_labels(labels), []))

    def count(self, labels: Mapping[str, str] | None = None) -> int:
        return len(self._samples.get(_freeze_labels(labels), []))

    def total(self, labels: Mapping[str, str] | None = None) -> float:
        return float(sum(self._samples.get(_freeze_labels(labels), [])))

    def bucket_counts(
        self,
        labels: Mapping[str, str] | None = None,
    ) -> tuple[int, ...]:
        samples = self._samples.get(_freeze_labels(labels), [])
        counts = [sum(1 for sample in samples if sample <= bound) for bound in self.buckets]
        counts.append(len(samples))
        return tuple(counts)

    def label_sets(self) -> tuple[FrozenLabels, ...]:
        return tuple(sorted(self._samples))


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters: dict[str, Counter] = {}
        self.histograms: dict[str, Histogram] = {}

    def counter(self, name: str, help_text: str = "") -> Counter:
        if name in self.histograms:
            raise ValueError(f"{name!r} is already registered as a histogram")
        existing = self.counters.get(name)
        if existing is not None:
            if existing.help_text != help_text:
                raise ValueError(f"{name!r} is already registered with different help text")
            return existing
        counter = Counter(name, help_text)
        self.counters[name] = counter
        return counter

    def histogram(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS,
    ) -> Histogram:
        if name in self.counters:
            raise ValueError(f"{name!r} is already registered as a counter")
        existing = self.histograms.get(name)
        if existing is not None:
            bounds = tuple(float(bound) for bound in buckets)
            if existing.help_text != help_text:
                raise ValueError(f"{name!r} is already registered with different help text")
            if existing.buckets != bounds:
                raise ValueError(f"{name!r} is already registered with different buckets")
            return existing
        histogram = Histogram(name, help_text, buckets)
        self.histograms[name] = histogram
        return histogram


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _format_bound(bound: float) -> str:
    if bound.is_integer():
        return str(int(bound))
    return format(bound, ".17g")


def _render_sample(
    name: str,
    labels: FrozenLabels,
    value: str,
    extra: tuple[str, str] | None = None,
) -> str:
    pairs = list(labels)
    if extra is not None:
        pairs.append(extra)
    if not pairs:
        return f"{name} {value}"
    rendered = ",".join(
        f'{key}="{_escape_label_value(label_value)}"' for key, label_value in pairs
    )
    return f"{name}{{{rendered}}} {value}"


def prometheus_exposition(registry: MetricsRegistry) -> str:
    families: list[str] = []

    for name in sorted(registry.counters):
        counter = registry.counters[name]
        label_sets = counter.label_sets()
        if not label_sets:
            continue
        lines: list[str] = []
        if counter.help_text:
            lines.append(f"# HELP {name} {_escape_help(counter.help_text)}")
        lines.append(f"# TYPE {name} counter")
        for labels in label_sets:
            lines.append(_render_sample(name, labels, str(counter.value(dict(labels)))))
        families.append("\n".join(lines))

    for name in sorted(registry.histograms):
        histogram = registry.histograms[name]
        label_sets = histogram.label_sets()
        if not label_sets:
            continue
        lines = []
        if histogram.help_text:
            lines.append(f"# HELP {name} {_escape_help(histogram.help_text)}")
        lines.append(f"# TYPE {name} histogram")
        for labels in label_sets:
            values = dict(labels)
            counts = histogram.bucket_counts(values)
            for bound, count in zip(histogram.buckets, counts[:-1]):
                lines.append(
                    _render_sample(
                        f"{name}_bucket",
                        labels,
                        str(count),
                        ("le", _format_bound(bound)),
                    )
                )
            lines.append(
                _render_sample(f"{name}_bucket", labels, str(counts[-1]), ("le", "+Inf"))
            )
            lines.append(
                _render_sample(f"{name}_sum", labels, format(histogram.total(values), ".17g"))
            )
            lines.append(_render_sample(f"{name}_count", labels, str(histogram.count(values))))
        families.append("\n".join(lines))

    if not families:
        return ""
    return "\n\n".join(families) + "\n"

from __future__ import annotations

import math

import pytest

from otel_observability import (
    Counter,
    Histogram,
    MetricsRegistry,
    prometheus_exposition,
)


def test_counter_increments_per_label_set() -> None:
    counter = Counter("tools_called_total")
    counter.inc({"tool": "read_file"})
    counter.inc({"tool": "read_file"})
    counter.inc({"tool": "write_file"})

    assert counter.value({"tool": "read_file"}) == 2
    assert counter.value({"tool": "write_file"}) == 1
    assert counter.value() == 0
    assert counter.label_sets() == (
        (("tool", "read_file"),),
        (("tool", "write_file"),),
    )


def test_counter_rejects_negative_amount() -> None:
    counter = Counter("requests_total")
    with pytest.raises(ValueError, match="non-negative"):
        counter.inc(amount=-1)


def test_metric_and_label_names_are_validated() -> None:
    with pytest.raises(ValueError, match="invalid metric name"):
        Counter("bad-name")
    with pytest.raises(ValueError, match="invalid metric name"):
        Histogram("bad.name")

    counter = Counter("valid_name")
    with pytest.raises(ValueError, match="invalid label name"):
        counter.inc({"bad-label": "value"})
    with pytest.raises(TypeError, match="must be a string"):
        counter.inc({"tool": 3})  # type: ignore[dict-item]


def test_registry_rejects_type_and_help_conflicts() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("calls_total", "Calls.")
    assert registry.counter("calls_total", "Calls.") is counter
    with pytest.raises(ValueError, match="different help"):
        registry.counter("calls_total", "Other help.")
    with pytest.raises(ValueError, match="already registered as a counter"):
        registry.histogram("calls_total")

    histogram = registry.histogram("latency_ms", "Latency.", (1, 2, 3))
    assert registry.histogram("latency_ms", "Latency.", (1, 2, 3)) is histogram
    with pytest.raises(ValueError, match="different buckets"):
        registry.histogram("latency_ms", "Latency.", (1, 2))
    with pytest.raises(ValueError, match="already registered as a histogram"):
        registry.counter("latency_ms")


def test_histogram_computes_cumulative_bucket_counts() -> None:
    histogram = Histogram("tool_latency_ms")
    for value in (1, 5, 6, 10, 25, 10001):
        histogram.observe(value)

    assert histogram.bucket_counts() == (
        2,
        4,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        6,
    )
    assert histogram.count() == 6
    assert histogram.total() == 10048
    assert histogram.samples() == (1, 5, 6, 10, 25, 10001)


def test_histogram_keeps_label_sets_independent() -> None:
    histogram = Histogram("tool_latency_ms")
    histogram.observe(1, {"tool": "read_file"})
    histogram.observe(100, {"tool": "write_file"})

    assert histogram.count({"tool": "read_file"}) == 1
    assert histogram.count({"tool": "write_file"}) == 1
    assert histogram.bucket_counts({"tool": "read_file"})[0] == 1
    assert histogram.bucket_counts({"tool": "write_file"})[0] == 0
    assert histogram.label_sets() == (
        (("tool", "read_file"),),
        (("tool", "write_file"),),
    )


def test_histogram_rejects_invalid_observations() -> None:
    histogram = Histogram("latency_ms")
    for value in (-1, math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError, match="finite and non-negative"):
            histogram.observe(value)


def test_histogram_rejects_invalid_buckets() -> None:
    with pytest.raises(ValueError, match="at least one bucket"):
        Histogram("empty", buckets=())
    with pytest.raises(ValueError, match="strictly increasing"):
        Histogram("unordered", buckets=(5, 5))
    with pytest.raises(ValueError, match="non-negative"):
        Histogram("negative", buckets=(-1, 5))
    with pytest.raises(ValueError, match="finite"):
        Histogram("infinite", buckets=(5, math.inf))


def test_counter_exposition_has_help_type_and_sorted_labels() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("tools_called_total", "Total tool calls.")
    counter.inc({"tool": "write_file"})
    counter.inc({"tool": "read_file"})
    counter.inc({"tool": "read_file"})

    assert prometheus_exposition(registry) == (
        "# HELP tools_called_total Total tool calls.\n"
        "# TYPE tools_called_total counter\n"
        'tools_called_total{tool="read_file"} 2\n'
        'tools_called_total{tool="write_file"} 1\n'
    )


def test_histogram_exposition_has_cumulative_buckets_and_inf() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("tool_latency_ms", "Tool latency.")
    histogram.observe(5, {"tool": "read_file"})
    histogram.observe(20, {"tool": "read_file"})

    text = prometheus_exposition(registry)
    assert "# HELP tool_latency_ms Tool latency." in text
    assert "# TYPE tool_latency_ms histogram" in text
    assert 'tool_latency_ms_bucket{tool="read_file",le="5"} 1' in text
    assert 'tool_latency_ms_bucket{tool="read_file",le="10"} 1' in text
    assert 'tool_latency_ms_bucket{tool="read_file",le="25"} 2' in text
    assert 'tool_latency_ms_bucket{tool="read_file",le="+Inf"} 2' in text
    assert 'tool_latency_ms_sum{tool="read_file"} 25' in text
    assert 'tool_latency_ms_count{tool="read_file"} 2' in text

    bucket_lines = [
        line for line in text.splitlines() if "_bucket{" in line
    ]
    assert bucket_lines == sorted(
        bucket_lines,
        key=lambda line: float("inf")
        if 'le="+Inf"' in line
        else float(line.split('le="', 1)[1].split('"', 1)[0]),
    )


def test_exposition_escapes_label_values() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("escaped_total")
    counter.inc({"tool": 'a"b\\c\nd'})

    text = prometheus_exposition(registry)
    assert 'escaped_total{tool="a\\"b\\\\c\\nd"} 1' in text


def test_exposition_groups_counters_before_histograms_and_sorts_families() -> None:
    registry = MetricsRegistry()
    registry.histogram("z_histogram", "Z.").observe(1)
    registry.histogram("a_histogram", "A.").observe(1)
    registry.counter("z_counter", "Z.").inc()
    registry.counter("a_counter", "A.").inc()

    text = prometheus_exposition(registry)
    assert text.index("# TYPE a_counter") < text.index("# TYPE z_counter")
    assert text.index("# TYPE z_counter") < text.index("# TYPE a_histogram")
    assert text.index("# TYPE a_histogram") < text.index("# TYPE z_histogram")


def test_empty_registry_and_unobserved_metrics_produce_no_text() -> None:
    registry = MetricsRegistry()
    assert prometheus_exposition(registry) == ""
    registry.counter("unused_total", "Unused.")
    assert prometheus_exposition(registry) == ""


def test_help_text_is_optional_and_escaped() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("plain_total")
    counter.inc()
    assert prometheus_exposition(registry) == (
        "# TYPE plain_total counter\nplain_total 1\n"
    )

    escaped = MetricsRegistry()
    escaped.counter("help_total", "One\\nTwo\nThree").inc()
    text = prometheus_exposition(escaped)
    assert "# HELP help_total One\\\\nTwo\\nThree" in text

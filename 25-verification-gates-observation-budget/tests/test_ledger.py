from __future__ import annotations

import pytest

from verification_gates.ledger import ObservationLedger


def test_ledger_tracks_cumulative_per_tool_and_rows() -> None:
    ledger = ObservationLedger()
    first = ledger.record(1, "read_file", 120)
    second = ledger.record(1, "grep", 30, truncated=True)
    third = ledger.record(2, "read_file", 80)

    assert len(ledger) == 3
    assert ledger.cumulative() == 230
    assert ledger.per_tool("read_file") == 200
    assert ledger.per_tool("grep") == 30
    assert ledger.per_tool("never_called") == 0
    assert ledger.last_turn == 2
    assert ledger.observations == (first, second, third)
    assert second.truncated is True


def test_ledger_rejects_bad_rows_without_changing_totals() -> None:
    ledger = ObservationLedger()
    ledger.record(2, "read_file", 10)

    with pytest.raises(ValueError, match="non-decreasing"):
        ledger.record(1, "grep", 10)
    with pytest.raises(ValueError):
        ledger.record(3, "", 10)
    with pytest.raises(ValueError):
        ledger.record(3, "grep", -1)
    with pytest.raises(ValueError):
        ledger.record(3, "grep", True)

    assert len(ledger) == 1
    assert ledger.cumulative() == 10
    assert ledger.per_tool("read_file") == 10


def test_same_turn_and_zero_token_rows_are_legal() -> None:
    ledger = ObservationLedger()
    ledger.record(1, "read_file", 0)
    ledger.record(1, "read_file", 0)

    assert len(ledger) == 2
    assert ledger.cumulative() == 0
    assert ledger.per_tool("read_file") == 0
    assert len(ledger.visible(1, 1)) == 2


def test_visible_and_masked_split_at_the_window_boundary() -> None:
    ledger = ObservationLedger()
    turn_one = ledger.record(1, "read_file", 10)
    turn_two = ledger.record(2, "grep", 20)
    turn_three = ledger.record(3, "read_file", 30)

    assert ledger.visible(3, 2) == (turn_two, turn_three)
    assert ledger.masked(3, 2) == (turn_one,)
    assert ledger.visible(3, 3) == (turn_one, turn_two, turn_three)
    assert ledger.masked(3, 3) == ()
    assert ledger.visible(3, 10) == (turn_one, turn_two, turn_three)
    assert ledger.visible(3, 1) == (turn_three,)
    assert ledger.masked(3, 1) == (turn_one, turn_two)


def test_window_views_reject_invalid_or_retrograde_time() -> None:
    ledger = ObservationLedger()
    ledger.record(3, "read_file", 10)

    with pytest.raises(ValueError, match="precede"):
        ledger.visible(2, 2)
    with pytest.raises(ValueError):
        ledger.visible(3, 0)
    with pytest.raises(ValueError):
        ledger.visible(3, True)
    with pytest.raises(ValueError):
        ledger.visible(0, 2)
    with pytest.raises(ValueError):
        ledger.masked(3, 0)


def test_empty_ledger_answers_zero_and_empty_views() -> None:
    ledger = ObservationLedger()

    assert len(ledger) == 0
    assert ledger.cumulative() == 0
    assert ledger.per_tool("read_file") == 0
    assert ledger.last_turn is None
    assert ledger.observations == ()
    assert ledger.visible(1, 2) == ()
    assert ledger.masked(1, 2) == ()

    with pytest.raises(ValueError):
        ledger.per_tool("")

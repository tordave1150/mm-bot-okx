"""Tests for causal same-tick fill/defense/re-entry ordering."""

from __future__ import annotations

import pytest

from backtest.mm_v1_3d_reentry import audit_reentry, causal_key, fixtures


def test_same_tick_fill_precedes_activation_by_sequence():
    fill = {
        "tick": 10,
        "event_sequence": 103_001,
        "normal_activity_eligible": True,
    }
    activation = {
        "tick": 10,
        "event_sequence": 104_001,
        "event": "TOXIC_FLOW_PAUSE_ENTRY",
    }
    reentry = {
        "tick": 11,
        "event_sequence": 111_001,
        "event": "DEFENSIVE_REENTRY_READY",
    }
    later = {
        "tick": 12,
        "event_sequence": 123_001,
        "normal_activity_eligible": True,
    }
    result = audit_reentry([fill, later], [activation, reentry])
    assert result["same_tick_fill_precedes_activation"]
    assert result["passed"]


def test_tick_only_ordering_is_not_used():
    assert causal_key({"tick": 4, "event_sequence": 43_001}) < causal_key(
        {"tick": 4, "event_sequence": 44_001}
    )


def test_missing_or_mismatched_sequence_fails_closed():
    with pytest.raises(ValueError):
        causal_key({"tick": 1})
    with pytest.raises(ValueError):
        causal_key({"tick": 1, "event_sequence": 23_001})


def test_all_v13d_hand_fixtures_pass():
    rows = fixtures()
    assert len(rows) == 8
    assert all(row["passed"] for row in rows)

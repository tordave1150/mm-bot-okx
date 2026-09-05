"""Offline deterministic tests for cross-run R2 campaign wall-time admission."""

from datetime import datetime, timedelta, timezone

import pytest

from okx_demo_r2_campaign_wall_guard import (
    CampaignWallExpiredError,
    evaluate_campaign_wall,
    require_active_campaign_wall,
)


def test_interruption_gap_counts_against_full_campaign_wall() -> None:
    started = datetime(2026, 9, 4, 15, 45, tzinfo=timezone.utc)
    decision = evaluate_campaign_wall(
        campaign_started_at_utc=started,
        evaluated_at_utc=started + timedelta(hours=6, seconds=1),
    )
    assert decision.active is False
    assert decision.remaining_seconds == 0.0
    assert decision.deadline_utc == datetime(2026, 9, 4, 21, 45, tzinfo=timezone.utc)


def test_deadline_is_fail_closed() -> None:
    started = datetime(2026, 9, 4, 15, 45, tzinfo=timezone.utc)
    with pytest.raises(CampaignWallExpiredError, match="FAIL_CLOSED"):
        require_active_campaign_wall(
            campaign_started_at_utc=started,
            evaluated_at_utc=started + timedelta(hours=6),
        )


def test_pre_deadline_campaign_remains_active() -> None:
    started = datetime(2026, 9, 4, 15, 45, tzinfo=timezone.utc)
    decision = require_active_campaign_wall(
        campaign_started_at_utc=started,
        evaluated_at_utc=started + timedelta(hours=5, minutes=59, seconds=59),
    )
    assert decision.active is True
    assert decision.remaining_seconds == 1.0


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_campaign_wall(
            campaign_started_at_utc=datetime(2026, 9, 4, 15, 45),
            evaluated_at_utc=datetime(2026, 9, 4, 15, 46, tzinfo=timezone.utc),
        )

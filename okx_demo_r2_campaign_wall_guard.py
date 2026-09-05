"""Pure, fail-closed campaign wall-time admission guard for R2 continuations.

This module deliberately has no exchange, credential, or filesystem dependency.
Any future R2 continuation must evaluate it before loading credentials or creating
an adapter so an expired campaign can never be resumed with its old identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class CampaignWallExpiredError(RuntimeError):
    """Raised when a continuation attempts to use an expired campaign."""


@dataclass(frozen=True)
class CampaignWallDecision:
    campaign_started_at_utc: datetime
    evaluated_at_utc: datetime
    deadline_utc: datetime
    maximum_campaign_wall_seconds: int
    remaining_seconds: float
    active: bool


def _require_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def evaluate_campaign_wall(
    *,
    campaign_started_at_utc: datetime,
    evaluated_at_utc: datetime,
    maximum_campaign_wall_seconds: int = 21_600,
) -> CampaignWallDecision:
    """Evaluate full elapsed campaign time, including all interruption gaps.

    The boundary is fail-closed: a campaign is inactive exactly at its deadline.
    """
    if maximum_campaign_wall_seconds <= 0:
        raise ValueError("maximum_campaign_wall_seconds must be positive")
    started = _require_utc(campaign_started_at_utc, field="campaign_started_at_utc")
    evaluated = _require_utc(evaluated_at_utc, field="evaluated_at_utc")
    if evaluated < started:
        raise ValueError("evaluated_at_utc cannot precede campaign_started_at_utc")

    deadline = started + timedelta(seconds=maximum_campaign_wall_seconds)
    remaining = (deadline - evaluated).total_seconds()
    return CampaignWallDecision(
        campaign_started_at_utc=started,
        evaluated_at_utc=evaluated,
        deadline_utc=deadline,
        maximum_campaign_wall_seconds=maximum_campaign_wall_seconds,
        remaining_seconds=max(0.0, remaining),
        active=evaluated < deadline,
    )


def require_active_campaign_wall(**kwargs: object) -> CampaignWallDecision:
    """Return the decision or stop a continuation before any external operation."""
    decision = evaluate_campaign_wall(**kwargs)  # type: ignore[arg-type]
    if not decision.active:
        raise CampaignWallExpiredError(
            "FAIL_CLOSED: campaign wall-time expired; campaign and session identities cannot be resumed or reused"
        )
    return decision

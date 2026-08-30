"""Shared canonical fill-trigger identity for market-maker evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class FillTrigger(StrEnum):
    STRICT_TRADE_THROUGH = "STRICT_TRADE_THROUGH"
    AGGRESSOR_TRADE_AT_QUOTE = "AGGRESSOR_TRADE_AT_QUOTE"
    TERMINAL_EXECUTION = "TERMINAL_EXECUTION"
    HARD_KILL_EXECUTION = "HARD_KILL_EXECUTION"
    EMERGENCY_EXECUTION = "EMERGENCY_EXECUTION"


class LiquidityRole(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


NORMAL_TRIGGERS = frozenset({
    FillTrigger.STRICT_TRADE_THROUGH,
    FillTrigger.AGGRESSOR_TRADE_AT_QUOTE,
})
SPECIAL_TRIGGERS = frozenset({
    FillTrigger.TERMINAL_EXECUTION,
    FillTrigger.HARD_KILL_EXECUTION,
    FillTrigger.EMERGENCY_EXECUTION,
})


@dataclass(frozen=True)
class CanonicalFillClassification:
    """Immutable classification assigned by the execution source."""

    maker_or_taker: LiquidityRole
    fill_trigger: FillTrigger
    special_exit: bool
    normal_activity_eligible: bool
    normal_round_trip_eligible: bool

    @classmethod
    def create(
        cls,
        *,
        maker_or_taker: str | LiquidityRole,
        fill_trigger: str | FillTrigger,
    ) -> "CanonicalFillClassification":
        try:
            role = LiquidityRole(maker_or_taker)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown maker/taker role") from exc
        try:
            trigger = FillTrigger(fill_trigger)
        except (TypeError, ValueError) as exc:
            raise ValueError("missing or unknown fill trigger") from exc
        normal = trigger in NORMAL_TRIGGERS
        special = trigger in SPECIAL_TRIGGERS
        if role is LiquidityRole.MAKER and special:
            raise ValueError("maker fill cannot use a special-exit trigger")
        if role is LiquidityRole.TAKER and normal:
            raise ValueError("taker fill cannot use a normal maker trigger")
        if not normal and not special:
            raise ValueError("fill trigger is not canonically partitioned")
        return cls(
            maker_or_taker=role,
            fill_trigger=trigger,
            special_exit=special,
            normal_activity_eligible=normal,
            normal_round_trip_eligible=normal,
        )

    @classmethod
    def from_record(
        cls, record: dict[str, Any]
    ) -> "CanonicalFillClassification":
        required = (
            "maker_or_taker",
            "fill_trigger",
            "special_exit",
            "normal_activity_eligible",
            "normal_round_trip_eligible",
        )
        if any(key not in record for key in required):
            raise ValueError("canonical classification fields are required")
        classification = cls.create(
            maker_or_taker=record["maker_or_taker"],
            fill_trigger=record["fill_trigger"],
        )
        declared = {
            "special_exit": record["special_exit"],
            "normal_activity_eligible": record["normal_activity_eligible"],
            "normal_round_trip_eligible": record[
                "normal_round_trip_eligible"
            ],
        }
        expected = classification.to_dict()
        if any(type(value) is not bool for value in declared.values()):
            raise ValueError("canonical eligibility fields must be boolean")
        if any(declared[key] != expected[key] for key in declared):
            raise ValueError("declared classification flags are inconsistent")
        return classification

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["maker_or_taker"] = self.maker_or_taker.value
        payload["fill_trigger"] = self.fill_trigger.value
        return payload


def passive_classification(
    trigger: str | FillTrigger = FillTrigger.STRICT_TRADE_THROUGH,
) -> CanonicalFillClassification:
    return CanonicalFillClassification.create(
        maker_or_taker=LiquidityRole.MAKER,
        fill_trigger=trigger,
    )


def special_exit_classification(reason: str) -> CanonicalFillClassification:
    trigger_by_reason = {
        "mm-terminal": FillTrigger.TERMINAL_EXECUTION,
        "mm-hard-kill": FillTrigger.HARD_KILL_EXECUTION,
        "mm-emergency": FillTrigger.EMERGENCY_EXECUTION,
    }
    try:
        trigger = trigger_by_reason[reason]
    except KeyError as exc:
        raise ValueError(
            f"unknown canonical special-exit reason: {reason!r}"
        ) from exc
    return CanonicalFillClassification.create(
        maker_or_taker=LiquidityRole.TAKER,
        fill_trigger=trigger,
    )

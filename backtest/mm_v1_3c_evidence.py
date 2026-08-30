"""Semantic fill, activity, FIFO, quantity, and fee evidence for MM v1.3C."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
from typing import Any, Iterable

from fill_classification import (
    CanonicalFillClassification,
    FillTrigger,
)


D = Decimal
ZERO = D("0")
NORMAL_TRIGGERS = {
    FillTrigger.STRICT_TRADE_THROUGH.value,
    FillTrigger.AGGRESSOR_TRADE_AT_QUOTE.value,
}
SPECIAL_TRIGGERS = {
    FillTrigger.TERMINAL_EXECUTION.value,
    FillTrigger.HARD_KILL_EXECUTION.value,
    FillTrigger.EMERGENCY_EXECUTION.value,
}
REQUIRED_FILL_FIELDS = (
    "fill_id",
    "order_id",
    "profile_id",
    "path_id",
    "tick",
    "side",
    "quantity_btc",
    "fill_price",
    "fee",
    "maker_or_taker",
    "fill_trigger",
    "special_exit",
    "normal_activity_eligible",
    "normal_round_trip_eligible",
    "trigger_event_id",
    "quote_event_id",
    "inventory_before",
    "inventory_after",
)


def _decimal(value: Any) -> Decimal:
    try:
        result = D(str(value))
    except Exception as exc:
        raise ValueError("invalid numeric evidence field") from exc
    if not result.is_finite():
        raise ValueError("non-finite numeric evidence field")
    return result


def validate_fill_record(record: dict[str, Any]) -> None:
    if any(key not in record for key in REQUIRED_FILL_FIELDS):
        raise ValueError("canonical fill identity is incomplete")
    if any(
        not isinstance(record[key], str) or not record[key]
        for key in (
            "fill_id",
            "order_id",
            "profile_id",
            "path_id",
            "trigger_event_id",
            "quote_event_id",
        )
    ):
        raise ValueError("canonical fill references are required")
    if record["side"] not in {"buy", "sell"}:
        raise ValueError("invalid fill side")
    if type(record["tick"]) is not int or record["tick"] < 0:
        raise ValueError("invalid fill tick")
    quantity = _decimal(record["quantity_btc"])
    price = _decimal(record["fill_price"])
    fee = _decimal(record["fee"])
    before = _decimal(record["inventory_before"])
    after = _decimal(record["inventory_after"])
    if quantity <= ZERO or price <= ZERO or fee < ZERO:
        raise ValueError("fill quantity, price, or fee is invalid")
    expected = before + (quantity if record["side"] == "buy" else -quantity)
    if expected != after:
        raise ValueError("fill inventory transition does not reconcile")
    CanonicalFillClassification.from_record(record)


def _grouped(rows: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (str(row["profile_id"]), str(row["path_id"])), []
        ).append(row)
    return [
        sorted(group, key=lambda row: (int(row["tick"]), str(row["fill_id"])))
        for _, group in sorted(groups.items())
    ]


def normal_fifo_evidence(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Match only normal maker pairs; attribute special closures separately."""
    for row in rows:
        validate_fill_record(row)
    trips: list[dict[str, Any]] = []
    special: list[dict[str, Any]] = []
    normal_matched_directional = ZERO
    special_closed_directional = ZERO
    remaining_normal_open = ZERO
    total_directional = sum(
        (_decimal(row["quantity_btc"]) for row in rows), ZERO
    )
    special_unmatched = ZERO
    normal_trip_fill_ids: set[str] = set()

    for group in _grouped(rows):
        open_lots: list[dict[str, Any]] = []
        for row in group:
            classification = CanonicalFillClassification.from_record(row)
            incoming = _decimal(row["quantity_btc"])
            incoming_fee = _decimal(row["fee"])
            if classification.normal_round_trip_eligible:
                while (
                    incoming > ZERO
                    and open_lots
                    and open_lots[0]["fill"]["side"] != row["side"]
                ):
                    lot = open_lots[0]
                    matched = min(lot["remaining"], incoming)
                    entry_fee = lot["fee_remaining"] * matched / lot["remaining"]
                    exit_fee = incoming_fee * matched / incoming
                    entry = lot["fill"]
                    gross = (
                        (_decimal(row["fill_price"]) - _decimal(entry["fill_price"]))
                        * matched
                        if entry["side"] == "buy"
                        else (
                            _decimal(entry["fill_price"])
                            - _decimal(row["fill_price"])
                        )
                        * matched
                    )
                    trip_id = (
                        f"{row['profile_id']}-{row['path_id']}-"
                        f"normal-trip-{len(trips) + 1:06d}"
                    )
                    trips.append({
                        "round_trip_id": trip_id,
                        "profile_id": row["profile_id"],
                        "path_id": row["path_id"],
                        "entry_fill_id": entry["fill_id"],
                        "exit_fill_id": row["fill_id"],
                        "entry_side": entry["side"],
                        "matched_quantity_btc": float(matched),
                        "entry_price": float(_decimal(entry["fill_price"])),
                        "exit_price": float(_decimal(row["fill_price"])),
                        "entry_fee_usdt": float(entry_fee),
                        "exit_fee_usdt": float(exit_fee),
                        "fees_usdt": float(entry_fee + exit_fee),
                        "gross_execution_pnl": float(gross),
                        "net_execution_pnl": float(
                            gross - entry_fee - exit_fee
                        ),
                        "entry_tick": int(entry["tick"]),
                        "exit_tick": int(row["tick"]),
                        "holding_ticks": int(row["tick"]) - int(entry["tick"]),
                        "normal_or_special": "NORMAL",
                    })
                    normal_trip_fill_ids.update(
                        (str(entry["fill_id"]), str(row["fill_id"]))
                    )
                    normal_matched_directional += matched * D("2")
                    lot["remaining"] -= matched
                    lot["fee_remaining"] -= entry_fee
                    incoming -= matched
                    incoming_fee -= exit_fee
                    if lot["remaining"] == ZERO:
                        open_lots.pop(0)
                if incoming > ZERO:
                    open_lots.append({
                        "fill": row,
                        "remaining": incoming,
                        "fee_remaining": incoming_fee,
                    })
                continue

            unmatched_special = incoming
            while (
                unmatched_special > ZERO
                and open_lots
                and open_lots[0]["fill"]["side"] != row["side"]
            ):
                lot = open_lots[0]
                matched = min(lot["remaining"], unmatched_special)
                entry = lot["fill"]
                gross = (
                    (_decimal(row["fill_price"]) - _decimal(entry["fill_price"]))
                    * matched
                    if entry["side"] == "buy"
                    else (
                        _decimal(entry["fill_price"])
                        - _decimal(row["fill_price"])
                    )
                    * matched
                )
                special.append({
                    "profile_id": row["profile_id"],
                    "path_id": row["path_id"],
                    "entry_fill_id": entry["fill_id"],
                    "special_exit_fill_id": row["fill_id"],
                    "fill_trigger": row["fill_trigger"],
                    "closed_quantity_btc": float(matched),
                    "gross_pnl_usdt": float(gross),
                })
                special_closed_directional += matched * D("2")
                lot["remaining"] -= matched
                unmatched_special -= matched
                if lot["remaining"] == ZERO:
                    open_lots.pop(0)
            special_unmatched += unmatched_special
        remaining_normal_open += sum(
            (lot["remaining"] for lot in open_lots), ZERO
        )

    identity_total = (
        normal_matched_directional
        + remaining_normal_open
        + special_closed_directional
    )
    reconciliation = {
        "normal_matched_directional_quantity_btc": float(
            normal_matched_directional
        ),
        "remaining_normal_open_quantity_btc": float(remaining_normal_open),
        "special_exit_closed_directional_quantity_btc": float(
            special_closed_directional
        ),
        "total_directional_fill_quantity_btc": float(total_directional),
        "special_exit_unmatched_quantity_btc": float(special_unmatched),
        "quantity_identity_reconciles": (
            identity_total == total_directional and special_unmatched == ZERO
        ),
        "normal_round_trip_fill_ids_subset_of_activity": (
            normal_trip_fill_ids
            <= {
                str(row["fill_id"])
                for row in rows
                if row["normal_activity_eligible"]
            }
        ),
        "special_exit_in_normal_round_trips": any(
            row["fill_id"] in normal_trip_fill_ids
            for row in rows
            if row["special_exit"]
        ),
    }
    return trips, special, reconciliation


def reconcile_fill_stream(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        try:
            validate_fill_record(row)
        except ValueError as exc:
            errors.append(f"fill[{index}]: {exc}")
    ids = [str(row.get("fill_id", "")) for row in rows]
    triggers = {
        trigger: sum(row.get("fill_trigger") == trigger for row in rows)
        for trigger in sorted(NORMAL_TRIGGERS | SPECIAL_TRIGGERS)
    }
    normal = [
        row for row in rows
        if row.get("normal_activity_eligible") is True
    ]
    terminal = [
        row for row in rows
        if row.get("fill_trigger") == FillTrigger.TERMINAL_EXECUTION.value
    ]
    hard_kill = [
        row for row in rows
        if row.get("fill_trigger") == FillTrigger.HARD_KILL_EXECUTION.value
    ]
    emergency = [
        row for row in rows
        if row.get("fill_trigger") == FillTrigger.EMERGENCY_EXECUTION.value
    ]
    partition_count = len(normal) + len(terminal) + len(hard_kill) + len(emergency)
    unknown = sum(
        row.get("fill_trigger") not in NORMAL_TRIGGERS | SPECIAL_TRIGGERS
        for row in rows
    )
    multiply = sum(
        int(row.get("normal_activity_eligible") is True)
        + int(row.get("special_exit") is True)
        != 1
        for row in rows
    )
    trips: list[dict[str, Any]] = []
    special: list[dict[str, Any]] = []
    quantity: dict[str, Any] = {
        "quantity_identity_reconciles": False,
        "normal_round_trip_fill_ids_subset_of_activity": False,
        "special_exit_in_normal_round_trips": False,
    }
    if not errors:
        trips, special, quantity = normal_fifo_evidence(rows)
    total_fees = sum((_decimal(row.get("fee", 0)) for row in rows), ZERO)
    fee_by_partition = {
        "normal_maker_fees_usdt": sum(
            (_decimal(row["fee"]) for row in normal), ZERO
        ),
        "terminal_fees_usdt": sum(
            (_decimal(row["fee"]) for row in terminal), ZERO
        ),
        "hard_kill_fees_usdt": sum(
            (_decimal(row["fee"]) for row in hard_kill), ZERO
        ),
        "emergency_fees_usdt": sum(
            (_decimal(row["fee"]) for row in emergency), ZERO
        ),
    }
    partition_fee_total = sum(fee_by_partition.values(), ZERO)
    result = {
        "fill_count": len(rows),
        "unique_fill_ids": len(ids) == len(set(ids)),
        "strict_trade_through_maker_fills": triggers[
            FillTrigger.STRICT_TRADE_THROUGH.value
        ],
        "aggressor_at_quote_maker_fills": triggers[
            FillTrigger.AGGRESSOR_TRADE_AT_QUOTE.value
        ],
        "normal_activity_fills": len(normal),
        "terminal_fills": len(terminal),
        "hard_kill_fills": len(hard_kill),
        "emergency_fills": len(emergency),
        "excluded_special_exit_fills": (
            len(terminal) + len(hard_kill) + len(emergency)
        ),
        "unknown_fill_classifications": unknown,
        "multiply_classified_fills": multiply,
        "unclassified_fills": len(errors),
        "fill_partition_reconciles": partition_count == len(rows),
        "activity_identity_reconciles": (
            len(normal)
            == sum(
                row.get("maker_or_taker") == "maker"
                and row.get("normal_activity_eligible") is True
                for row in rows
            )
            and not any(
                row.get("special_exit")
                and row.get("normal_activity_eligible")
                for row in rows
            )
        ),
        "normal_fifo_round_trips": len(trips),
        "special_exit_attributions": len(special),
        "total_unique_fill_fees_usdt": float(total_fees),
        **{key: float(value) for key, value in fee_by_partition.items()},
        "fee_identity_reconciles": total_fees == partition_fee_total,
        **quantity,
        "errors": errors,
    }
    result["passed"] = (
        result["unique_fill_ids"]
        and result["fill_partition_reconciles"]
        and result["activity_identity_reconciles"]
        and result["quantity_identity_reconciles"]
        and result["fee_identity_reconciles"]
        and result["normal_round_trip_fill_ids_subset_of_activity"]
        and not result["special_exit_in_normal_round_trips"]
        and result["unknown_fill_classifications"] == 0
        and result["multiply_classified_fills"] == 0
        and result["unclassified_fills"] == 0
        and all(
            math.isfinite(float(row[key]))
            for row in rows
            for key in ("quantity_btc", "fill_price", "fee")
        )
    )
    return result


def activity_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = deepcopy(rows)
    for row in rows:
        validate_fill_record(row)
    normal = [
        row for row in rows if row["normal_activity_eligible"] is True
    ]
    result = {
        "strict_trade_through_maker_fills": sum(
            row["fill_trigger"] == FillTrigger.STRICT_TRADE_THROUGH.value
            for row in normal
        ),
        "aggressor_at_quote_maker_fills": sum(
            row["fill_trigger"]
            == FillTrigger.AGGRESSOR_TRADE_AT_QUOTE.value
            for row in normal
        ),
        "normal_activity_fills": len(normal),
        "bid_normal_fills": sum(row["side"] == "buy" for row in normal),
        "ask_normal_fills": sum(row["side"] == "sell" for row in normal),
        "terminal_fills": sum(
            row["fill_trigger"] == FillTrigger.TERMINAL_EXECUTION.value
            for row in rows
        ),
        "hard_kill_fills": sum(
            row["fill_trigger"] == FillTrigger.HARD_KILL_EXECUTION.value
            for row in rows
        ),
        "emergency_fills": sum(
            row["fill_trigger"] == FillTrigger.EMERGENCY_EXECUTION.value
            for row in rows
        ),
        "excluded_special_exit_fills": sum(
            row["special_exit"] is True for row in rows
        ),
    }
    if rows != snapshot:
        raise RuntimeError("activity summary mutated canonical fill identity")
    return result


def _fixture_fill(
    fill_id: str,
    *,
    side: str,
    quantity: str,
    price: str,
    fee: str,
    role: str,
    trigger: str,
    before: str,
    tick: int,
    order_id: str | None = None,
) -> dict[str, Any]:
    classification = CanonicalFillClassification.create(
        maker_or_taker=role, fill_trigger=trigger
    )
    q = D(quantity)
    prior = D(before)
    after = prior + (q if side == "buy" else -q)
    return {
        "fill_id": fill_id,
        "order_id": order_id or f"order-{fill_id}",
        "profile_id": "fixture-profile",
        "path_id": "fixture-path",
        "tick": tick,
        "side": side,
        "quantity_btc": float(q),
        "fill_price": float(D(price)),
        "fee": float(D(fee)),
        **classification.to_dict(),
        "trigger_event_id": f"event-{fill_id}",
        "quote_event_id": f"quote-{fill_id}",
        "inventory_before": float(prior),
        "inventory_after": float(after),
    }


def classification_fixtures() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strict = _fixture_fill(
        "strict", side="buy", quantity="0.01", price="100", fee="0.001",
        role="maker", trigger="STRICT_TRADE_THROUGH", before="0", tick=1,
    )
    aggressor = _fixture_fill(
        "aggressor", side="sell", quantity="0.01", price="101", fee="0.00101",
        role="maker", trigger="AGGRESSOR_TRADE_AT_QUOTE", before="0.01", tick=2,
    )
    partial_a = _fixture_fill(
        "partial-a", side="buy", quantity="0.004", price="100", fee="0.0004",
        role="maker", trigger="STRICT_TRADE_THROUGH", before="0", tick=1,
        order_id="partial-order",
    )
    partial_b = _fixture_fill(
        "partial-b", side="buy", quantity="0.006", price="100", fee="0.0006",
        role="maker", trigger="STRICT_TRADE_THROUGH", before="0.004", tick=2,
        order_id="partial-order",
    )
    terminal = _fixture_fill(
        "terminal", side="sell", quantity="0.01", price="99", fee="0.00495",
        role="taker", trigger="TERMINAL_EXECUTION", before="0.01", tick=2,
    )
    hard = _fixture_fill(
        "hard", side="sell", quantity="0.01", price="98", fee="0.0049",
        role="taker", trigger="HARD_KILL_EXECUTION", before="0.01", tick=2,
    )
    emergency = _fixture_fill(
        "emergency", side="sell", quantity="0.01", price="97", fee="0.00485",
        role="taker", trigger="EMERGENCY_EXECUTION", before="0.01", tick=2,
    )
    round_trip = [strict, aggressor]
    normal_terminal = [
        _fixture_fill(
            "nt-normal", side="buy", quantity="0.01", price="100",
            fee="0.001", role="maker", trigger="STRICT_TRADE_THROUGH",
            before="0", tick=1,
        ),
        {**terminal, "fill_id": "nt-terminal", "order_id": "nt-terminal-order",
         "trigger_event_id": "event-nt-terminal",
         "quote_event_id": "quote-nt-terminal"},
    ]
    normal_hard = [
        _fixture_fill(
            "nh-normal", side="buy", quantity="0.01", price="100",
            fee="0.001", role="maker", trigger="STRICT_TRADE_THROUGH",
            before="0", tick=1,
        ),
        {**hard, "fill_id": "nh-hard", "order_id": "nh-hard-order",
         "trigger_event_id": "event-nh-hard",
         "quote_event_id": "quote-nh-hard"},
    ]
    mixed = [
        _fixture_fill(
            "m1", side="buy", quantity="0.01", price="100", fee="0.001",
            role="maker", trigger="STRICT_TRADE_THROUGH", before="0", tick=1,
        ),
        _fixture_fill(
            "m2", side="sell", quantity="0.01", price="101", fee="0.00101",
            role="maker", trigger="AGGRESSOR_TRADE_AT_QUOTE",
            before="0.01", tick=2,
        ),
        _fixture_fill(
            "m3", side="buy", quantity="0.01", price="100", fee="0.001",
            role="maker", trigger="STRICT_TRADE_THROUGH", before="0", tick=3,
        ),
        _fixture_fill(
            "m4", side="sell", quantity="0.01", price="99", fee="0.00495",
            role="taker", trigger="TERMINAL_EXECUTION", before="0.01", tick=4,
        ),
        _fixture_fill(
            "m5", side="sell", quantity="0.01", price="100", fee="0.001",
            role="maker", trigger="STRICT_TRADE_THROUGH", before="0", tick=5,
        ),
        _fixture_fill(
            "m6", side="buy", quantity="0.01", price="102", fee="0.0051",
            role="taker", trigger="HARD_KILL_EXECUTION", before="-0.01", tick=6,
        ),
        _fixture_fill(
            "m7", side="buy", quantity="0.01", price="100", fee="0.001",
            role="maker", trigger="AGGRESSOR_TRADE_AT_QUOTE", before="0", tick=7,
        ),
        _fixture_fill(
            "m8", side="sell", quantity="0.01", price="98", fee="0.0049",
            role="taker", trigger="EMERGENCY_EXECUTION", before="0.01", tick=8,
        ),
    ]
    results: list[dict[str, Any]] = []

    def passed(name: str, assertion: bool, detail: Any) -> None:
        results.append({"fixture": name, "passed": bool(assertion), "detail": detail})

    passed("01_strict_maker_fill", not strict["special_exit"], strict)
    passed("02_aggressor_at_quote_maker_fill", aggressor["normal_activity_eligible"], aggressor)
    partial_result = reconcile_fill_stream([partial_a, partial_b])
    passed(
        "03_partial_normal_maker_fill",
        partial_result["passed"]
        and math.isclose(
            partial_result["remaining_normal_open_quantity_btc"],
            0.01,
            abs_tol=1e-12,
        ),
        partial_result,
    )
    passed("04_terminal_close", terminal["special_exit"] and not terminal["normal_activity_eligible"], terminal)
    passed("05_hard_kill_close", hard["special_exit"] and not hard["normal_round_trip_eligible"], hard)
    passed("06_emergency_close", emergency["special_exit"] and not emergency["normal_activity_eligible"], emergency)
    trips, _, rt_rec = normal_fifo_evidence(round_trip)
    passed("07_normal_maker_round_trip", len(trips) == 1 and activity_counts(round_trip)["normal_activity_fills"] == 2, rt_rec)
    nt_trips, nt_special, nt_rec = normal_fifo_evidence(normal_terminal)
    passed("08_normal_fill_plus_terminal_close", not nt_trips and len(nt_special) == 1 and nt_rec["quantity_identity_reconciles"], nt_rec)
    nh_trips, nh_special, nh_rec = normal_fifo_evidence(normal_hard)
    passed("09_normal_fill_plus_hard_kill_close", not nh_trips and len(nh_special) == 1 and nh_rec["quantity_identity_reconciles"], nh_rec)
    mixed_rec = reconcile_fill_stream(mixed)
    passed("10_mixed_execution_stream", mixed_rec["passed"], mixed_rec)
    invalid_cases = (
        ("11_invalid_maker_terminal", "maker", "TERMINAL_EXECUTION"),
        ("12_invalid_taker_strict", "taker", "STRICT_TRADE_THROUGH"),
    )
    for name, role, trigger in invalid_cases:
        rejected = False
        try:
            CanonicalFillClassification.create(
                maker_or_taker=role, fill_trigger=trigger
            )
        except ValueError:
            rejected = True
        passed(name, rejected, {"rejected": rejected})
    missing = deepcopy(strict)
    del missing["fill_trigger"]
    try:
        validate_fill_record(missing)
        missing_rejected = False
    except ValueError:
        missing_rejected = True
    passed("13_missing_trigger", missing_rejected, {"rejected": missing_rejected})
    unknown = deepcopy(strict)
    unknown["fill_trigger"] = "UNKNOWN"
    try:
        validate_fill_record(unknown)
        unknown_rejected = False
    except ValueError:
        unknown_rejected = True
    passed("14_unknown_trigger", unknown_rejected, {"rejected": unknown_rejected})
    immutable = deepcopy(mixed)
    activity_counts(mixed)
    passed("15_summary_cannot_overwrite_trigger", mixed == immutable, {"immutable": mixed == immutable})
    passed(
        "16_fifo_cannot_consume_special_exit_as_normal",
        not nt_trips
        and not nh_trips
        and not nt_rec["special_exit_in_normal_round_trips"]
        and not nh_rec["special_exit_in_normal_round_trips"],
        {"terminal": nt_rec, "hard_kill": nh_rec},
    )
    return results, mixed_rec

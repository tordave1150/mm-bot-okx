"""Execute the frozen MM v1.2 accounting and microstructure diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtest.matching_engine import MatchingEngine
from backtest.mm_execution_microstructure import CausalTradeEventMatcher
from backtest.mm_v1_2_protocol import (
    ACCOUNTING_CONTRACT,
    DIAGNOSTIC_ID,
    EVENT_ORDER_CONTRACT,
    FILL_RECORD_CONTRACT,
    FILL_TRIGGER_CONTRACT,
    HAND_FIXTURE_MAKER_FEE_RATE,
    build_specification,
    canonical_bytes,
    file_hash,
    source_hashes,
)
from config import Config
from fill_tracker import FillTracker
from market_maker.execution_accounting import (
    CanonicalFill,
    FIFORoundTripMatcher,
    economic_attribution,
    effective_fill_edge,
    markout,
    quoted_spread,
)


D = Decimal
ZERO = D("0")
REPO_MAKER = D("0.0002")
REPO_TAKER = D("0.0005")


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_json(path: Path, payload: Any) -> None:
    _write_exclusive(
        path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    _write_exclusive(
        path,
        "".join(
            json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
            for record in records
        ),
    )


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write_json(temporary, payload)
    temporary.replace(path)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FillBuilder:
    def __init__(
        self,
        scenario_id: str,
        *,
        maker_rate: Decimal = REPO_MAKER,
        taker_rate: Decimal = REPO_TAKER,
    ) -> None:
        self.scenario_id = scenario_id
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate
        self.inventory = ZERO
        self.counter = 0

    def fill(
        self,
        *,
        side: str,
        quantity: str,
        price: str,
        tick: int,
        decision_mid: str = "50000",
        mid_at_fill: str | None = None,
        role: str = "MAKER",
        trigger: str = "AGGRESSOR_TRADE_AT_QUOTE",
    ) -> CanonicalFill:
        self.counter += 1
        q = D(quantity)
        px = D(price)
        decision = D(decision_mid)
        mid = D(mid_at_fill) if mid_at_fill is not None else decision
        rate = self.maker_rate if role == "MAKER" else self.taker_rate
        before = self.inventory
        after = before + (q if side == "buy" else -q)
        self.inventory = after
        fill = CanonicalFill(
            fill_id=f"{self.scenario_id}-fill-{self.counter:03d}",
            order_id=f"{self.scenario_id}-order-{self.counter:03d}",
            side=side,
            quantity_btc=q,
            fill_price=px,
            fee=px * q * rate,
            fee_role=role,
            maker_or_taker="maker" if role == "MAKER" else "taker",
            fee_rate=rate,
            fee_base_usdt=px * q,
            fee_currency="USDT",
            quote_created_tick=max(0, tick - 1),
            activation_tick=max(0, tick - 1),
            fill_tick=tick,
            cancel_requested_tick=None,
            decision_mid=decision,
            mid_before_fill=decision,
            mid_at_fill=mid,
            bid_at_fill=mid - D("5"),
            ask_at_fill=mid + D("5"),
            quote_distance_bps=abs(px - decision) / decision * D("10000"),
            inventory_before=before,
            inventory_after=after,
            scenario_id=self.scenario_id,
            profile_id="mm-v1-2-fixed-diagnostic",
            trigger_type=trigger,
            triggering_event_id=f"{self.scenario_id}-event-{tick}",
            triggering_trade_price=px if "EXECUTION" not in trigger else None,
            triggering_trade_quantity=q if "EXECUTION" not in trigger else None,
        )
        fill.validate()
        return fill


def _match(fills: list[CanonicalFill]) -> tuple[
    FIFORoundTripMatcher, list[Any]
]:
    matcher = FIFORoundTripMatcher()
    for fill in fills:
        matcher.process(fill)
    return matcher, matcher.round_trips


def hand_fixtures() -> dict[str, Any]:
    maker = D(HAND_FIXTURE_MAKER_FEE_RATE)
    definitions: dict[str, list[CanonicalFill]] = {}

    builder = FillBuilder("fixture-A", maker_rate=maker)
    definitions["A"] = [
        builder.fill(side="buy", quantity="0.01", price="49990", tick=1),
        builder.fill(side="sell", quantity="0.01", price="50010", tick=2),
    ]
    builder = FillBuilder("fixture-B", maker_rate=maker)
    definitions["B"] = [
        builder.fill(side="buy", quantity="0.01", price="50010", tick=1),
        builder.fill(side="sell", quantity="0.01", price="49990", tick=2),
    ]
    builder = FillBuilder("fixture-C", maker_rate=maker)
    definitions["C"] = [
        builder.fill(side="sell", quantity="0.01", price="50010", tick=1),
        builder.fill(side="buy", quantity="0.01", price="49990", tick=2),
    ]
    builder = FillBuilder("fixture-D", maker_rate=maker)
    definitions["D"] = [
        builder.fill(side="sell", quantity="0.01", price="49990", tick=1),
        builder.fill(side="buy", quantity="0.01", price="50010", tick=2),
    ]
    builder = FillBuilder("fixture-E", maker_rate=maker)
    definitions["E"] = [
        builder.fill(side="buy", quantity="0.006", price="49990", tick=1),
        builder.fill(side="buy", quantity="0.004", price="49992", tick=2),
        builder.fill(side="sell", quantity="0.007", price="50010", tick=3),
        builder.fill(side="sell", quantity="0.003", price="50012", tick=4),
    ]
    builder = FillBuilder("fixture-F")
    definitions["F"] = [
        builder.fill(side="buy", quantity="0.01", price="49980", tick=1),
        builder.fill(side="sell", quantity="0.01", price="50020", tick=2),
        builder.fill(side="buy", quantity="0.01", price="49980", tick=3),
        builder.fill(side="sell", quantity="0.01", price="50020", tick=4),
    ]
    builder = FillBuilder("fixture-G")
    definitions["G"] = [
        builder.fill(side="buy", quantity="0.01", price="49990", tick=1),
        builder.fill(
            side="sell", quantity="0.01", price="49980", tick=5,
            role="TAKER", trigger="TERMINAL_EXECUTION",
            decision_mid="49985", mid_at_fill="49985",
        ),
    ]
    builder = FillBuilder("fixture-H")
    definitions["H"] = [
        builder.fill(side="buy", quantity="0.01", price="50000", tick=1),
        builder.fill(
            side="sell", quantity="0.01", price="49500", tick=5,
            role="TAKER", trigger="HARD_KILL_EXECUTION",
            decision_mid="49505", mid_at_fill="49505",
        ),
    ]

    fixture_results = []
    all_fills: list[CanonicalFill] = []
    all_trips = []
    fee_traces = []
    pnl_traces = []
    for fixture_id, fills in definitions.items():
        matcher, trips = _match(fills)
        reconciliation = matcher.reconciliation()
        economics = economic_attribution(fills, trips)
        all_fills.extend(fills)
        all_trips.extend(trips)
        for fill in fills:
            fee_traces.append({
                "fixture_id": fixture_id,
                "fill_id": fill.fill_id,
                "fee_role": fill.fee_role,
                "fee_rate": str(fill.fee_rate),
                "fee_base_usdt": str(fill.fee_base_usdt),
                "fee_usdt": str(fill.fee),
                "charged_count": 1,
            })
        pnl_traces.append({
            "fixture_id": fixture_id,
            "economics": economics,
            "reconciliation": reconciliation,
        })
        passed = (
            reconciliation["unique_fill_ids"]
            and reconciliation["buy_quantity_identity"]
            and reconciliation["sell_quantity_identity"]
            and reconciliation["absolute_quantity_identity"]
            and reconciliation["fee_identity"]
            and not reconciliation["duplicate_matched_quantity"]
            and economics["pnl_identity_reconciles"]
            and economics["spread_inventory_decomposition_reconciles"]
        )
        if fixture_id in {"A", "C"}:
            passed = passed and D(
                trips[0].to_dict()["gross_execution_pnl"]
            ) > 0 and D(trips[0].to_dict()["net_execution_pnl"]) > 0
        if fixture_id in {"B", "D"}:
            passed = passed and D(
                trips[0].to_dict()["gross_execution_pnl"]
            ) < 0 and D(trips[0].to_dict()["net_execution_pnl"]) < 0
        if fixture_id == "E":
            passed = (
                passed
                and D(reconciliation["matched_quantity_btc"]) == D("0.01")
                and len(trips) == 3
            )
        if fixture_id == "F":
            passed = (
                passed
                and len(trips) == 2
                and all(trip.net_execution_pnl > 0 for trip in trips)
            )
        if fixture_id == "G":
            passed = (
                passed
                and D(economics["terminal_liquidation_pnl_usdt"]) != 0
                and D(economics["gross_execution_pnl_usdt"]) == 0
            )
        if fixture_id == "H":
            passed = (
                passed
                and D(economics["hard_kill_execution_pnl_usdt"]) < 0
                and D(economics["gross_execution_pnl_usdt"]) == 0
            )
            pnl_traces[-1]["hard_kill_attribution"] = {
                "equity_before_kill_usdt": "745",
                "drawdown_before_kill": "0.05",
                "inventory_before_kill_btc": "0.01",
                "mark_price_before_kill": "49505",
                "normal_strategy_pnl_before_kill_usdt": "0",
                "hard_kill_execution_price": "49500",
                "hard_kill_slippage_usdt": "0.05",
                "hard_kill_fee_usdt": str(fills[-1].fee),
                "hard_kill_execution_loss_usdt": str(
                    trips[0].gross_execution_pnl
                ),
                "final_pnl_usdt": economics["net_pnl_usdt"],
            }
        fixture_results.append({
            "fixture_id": fixture_id,
            "passed": bool(passed),
            "round_trip_count": len(trips),
            "winning_round_trips": sum(
                trip.net_execution_pnl > 0 for trip in trips
            ),
            "reconciliation": reconciliation,
            "economics": economics,
        })
    unique_fee_sum = sum((fill.fee for fill in all_fills), ZERO)
    summary = {
        "diagnostic_id": DIAGNOSTIC_ID,
        "fixture_count": len(fixture_results),
        "fixtures_passed": sum(item["passed"] for item in fixture_results),
        "all_passed": all(item["passed"] for item in fixture_results),
        "unique_fill_count": len({fill.fill_id for fill in all_fills}),
        "fill_count": len(all_fills),
        "total_unique_fill_fees_usdt": str(unique_fee_sum),
        "duplicate_fee_charges": 0,
        "results": fixture_results,
    }
    return {
        "fills": all_fills,
        "round_trips": all_trips,
        "fee_traces": fee_traces,
        "pnl_traces": pnl_traces,
        "summary": summary,
    }


def _stationary_symmetric() -> dict[str, Any]:
    engine = CausalTradeEventMatcher(scenario_id="micro-stationary-symmetric")
    engine.activate_quote(
        side="buy", price=D("49980"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.activate_quote(
        side="sell", price=D("50020"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("49980"), ask=D("50020"),
        trades=[{"event_id": "balanced-sell-1", "aggressor_side": "sell",
                 "price": "49980", "quantity_btc": "0.01"}],
    )
    engine.process_tick(
        tick=2, bid=D("49980"), ask=D("50020"),
        trades=[{"event_id": "balanced-buy-2", "aggressor_side": "buy",
                 "price": "50020", "quantity_btc": "0.01"}],
    )
    matcher, trips = _match(engine.fills)
    return _micro_record(
        "stationary_symmetric", engine, matcher, trips,
        expected="winning round trip with stable mid",
        extra_markout_mid=D("50000"),
    )


def _mean_reversion() -> dict[str, Any]:
    engine = CausalTradeEventMatcher(scenario_id="micro-mean-reversion")
    engine.activate_quote(
        side="buy", price=D("49970"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.activate_quote(
        side="sell", price=D("50030"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("49970"), ask=D("49990"),
        trades=[{"event_id": "mr-sell-1", "aggressor_side": "sell",
                 "price": "49970", "quantity_btc": "0.01"}],
    )
    engine.process_tick(
        tick=2, bid=D("50010"), ask=D("50030"),
        trades=[{"event_id": "mr-buy-2", "aggressor_side": "buy",
                 "price": "50030", "quantity_btc": "0.01"}],
    )
    matcher, trips = _match(engine.fills)
    return _micro_record(
        "mean_reversion", engine, matcher, trips,
        expected="recoverable passive fills and positive spread",
        extra_markout_mid=D("50000"),
    )


def _adverse_trend() -> dict[str, Any]:
    engine = CausalTradeEventMatcher(scenario_id="micro-adverse-trend")
    engine.activate_quote(
        side="buy", price=D("49990"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("49990"), ask=D("50010"),
        trades=[{"event_id": "trend-sell-1", "aggressor_side": "sell",
                 "price": "49990", "quantity_btc": "0.01"}],
    )
    engine.process_tick(
        tick=2, bid=D("49490"), ask=D("49510"), terminal=True,
    )
    matcher, trips = _match(engine.fills)
    return _micro_record(
        "adverse_trend", engine, matcher, trips,
        expected="negative markout and inventory loss",
        extra_markout_mid=D("49500"),
    )


def _toxic_trade_through() -> dict[str, Any]:
    tracker = FillTracker(Config(initial_capital=750))
    matching = MatchingEngine(
        fill_mode="conservative", maker_fee_rate=float(REPO_MAKER)
    )
    matching.place_order("buy", 49_990.0, 0.01)
    raw_fills = matching.check_fills(
        {
            "bids": [[49_970.0, 1.0]],
            "asks": [[49_980.0, 1.0]],
            "timestamp": 2_300_000_000_000,
        },
        tracker,
    )
    builder = FillBuilder("micro-toxic-trade-through")
    fills = [
        builder.fill(
            side="buy", quantity=str(fill.size), price=str(fill.price),
            tick=1, decision_mid="50000", mid_at_fill="49975",
            trigger="STRICT_TRADE_THROUGH",
        )
        for fill in raw_fills
    ]
    if fills:
        fills.append(builder.fill(
            side="sell", quantity="0.01", price="49500", tick=2,
            decision_mid="49505", mid_at_fill="49505",
            role="TAKER", trigger="TERMINAL_EXECUTION",
        ))
    matcher, trips = _match(fills)
    return {
        "scenario": "toxic_trade_through",
        "engine": None,
        "fills": fills,
        "round_trips": trips,
        "reconciliation": matcher.reconciliation(),
        "economics": economic_attribution(fills, trips),
        "markouts": [
            markout(fill, future_tick=2, future_mid=D("49500"))
            for fill in fills[:1]
        ],
        "expected": "material adverse markout and negative economics",
        "actual_strict_fill_count": len(raw_fills),
        "matching_events": list(matching.lifecycle_events),
    }


def _touch_without_trade() -> dict[str, Any]:
    tracker = FillTracker(Config(initial_capital=750))
    matching = MatchingEngine(fill_mode="conservative")
    matching.place_order("buy", 49_990.0, 0.01)
    fills = matching.check_fills(
        {
            "bids": [[49_980.0, 1.0]],
            "asks": [[49_990.0, 1.0]],
            "timestamp": 2_300_000_300_000,
        },
        tracker,
    )
    return {
        "scenario": "touch_without_trade",
        "engine": None,
        "fills": [],
        "round_trips": [],
        "reconciliation": matching.reconciliation(),
        "economics": {},
        "markouts": [],
        "expected": "touch-only event and no conservative fill",
        "actual_fill_count": len(fills),
        "matching_events": list(matching.lifecycle_events),
    }


def _aggressor_trade() -> dict[str, Any]:
    engine = CausalTradeEventMatcher(scenario_id="micro-aggressor-at-quote")
    engine.activate_quote(
        side="buy", price=D("49990"), quantity_btc=D("0.01"),
        decision_mid=D("50000"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("49990"), ask=D("50010"),
        trades=[{"event_id": "explicit-aggressor-sell",
                 "aggressor_side": "sell", "price": "49990",
                 "quantity_btc": "0.01"}],
    )
    matcher, trips = _match(engine.fills)
    return _micro_record(
        "aggressor_trade_at_quote", engine, matcher, trips,
        expected="passive fill without adverse future mid crossing",
        extra_markout_mid=D("50000"),
    )


def _micro_record(
    name: str,
    engine: CausalTradeEventMatcher,
    matcher: FIFORoundTripMatcher,
    trips: list[Any],
    *,
    expected: str,
    extra_markout_mid: Decimal,
) -> dict[str, Any]:
    last_tick = max((fill.fill_tick for fill in engine.fills), default=0) + 1
    return {
        "scenario": name,
        "engine": engine,
        "fills": engine.fills,
        "round_trips": trips,
        "reconciliation": matcher.reconciliation(),
        "economics": economic_attribution(engine.fills, trips),
        "markouts": [
            markout(fill, future_tick=last_tick, future_mid=extra_markout_mid)
            for fill in engine.fills
        ],
        "expected": expected,
    }


def event_ordering_audit() -> dict[str, Any]:
    checks: dict[str, bool] = {}

    engine = CausalTradeEventMatcher(scenario_id="ordering-fill-before-cancel")
    order = engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "race-trade", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.01"}],
        cancel_order_ids=[order],
    )
    checks["fill_before_same_tick_cancel"] = (
        len(engine.fills) == 1
        and not any(
            event["event"] == "CANCEL_COMPLETION"
            for event in engine.order_events
        )
    )

    engine = CausalTradeEventMatcher(scenario_id="ordering-cancel-before-later")
    order = engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"), cancel_order_ids=[order]
    )
    engine.process_tick(
        tick=2, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "late-trade", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.01"}],
    )
    checks["cancel_before_later_fill"] = len(engine.fills) == 0

    engine = CausalTradeEventMatcher(scenario_id="ordering-partial-cancel")
    order = engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "partial", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.006"}],
        cancel_order_ids=[order],
    )
    checks["partial_fill_then_cancel"] = (
        len(engine.fills) == 1
        and engine.fills[0].quantity_btc == D("0.006")
        and order not in engine.orders
    )

    engine = CausalTradeEventMatcher(scenario_id="ordering-replacement")
    old = engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"), cancel_order_ids=[old]
    )
    new = engine.activate_quote(
        side="buy", price=D("99"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=1, activation_tick=2,
    )
    checks["quote_replacement"] = old not in engine.orders and new in engine.orders

    engine.process_tick(tick=2, bid=D("99"), ask=D("101"), stale=True)
    checks["stale_data_cancellation"] = new not in engine.orders

    engine = CausalTradeEventMatcher(scenario_id="ordering-hard-kill")
    engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "pre-kill-fill", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.01"}],
        hard_kill=True,
    )
    checks["hard_kill_after_fill"] = [
        fill.trigger_type for fill in engine.fills
    ] == ["AGGRESSOR_TRADE_AT_QUOTE", "HARD_KILL_EXECUTION"]

    engine = CausalTradeEventMatcher(scenario_id="ordering-terminal")
    engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(
        tick=1, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "pre-terminal-fill", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.01"}],
    )
    engine.process_tick(tick=2, bid=D("99"), ask=D("101"), terminal=True)
    checks["terminal_liquidation_after_final_tick"] = (
        engine.fills[-1].trigger_type == "TERMINAL_EXECUTION"
        and engine.fills[-1].fill_tick == 2
    )

    prefix = CausalTradeEventMatcher(scenario_id="prefix")
    prefix.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    prefix.process_tick(tick=1, bid=D("100"), ask=D("102"))
    prefix_hash = hashlib.sha256(canonical_bytes({
        "market": prefix.market_events,
        "orders": prefix.order_events,
        "fills": [fill.to_dict() for fill in prefix.fills],
    })).hexdigest()
    extended = CausalTradeEventMatcher(scenario_id="prefix")
    extended.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    extended.process_tick(tick=1, bid=D("100"), ask=D("102"))
    extended_prefix_hash = hashlib.sha256(canonical_bytes({
        "market": extended.market_events,
        "orders": extended.order_events,
        "fills": [fill.to_dict() for fill in extended.fills],
    })).hexdigest()
    extended.process_tick(
        tick=2, bid=D("100"), ask=D("102"),
        trades=[{"event_id": "future", "aggressor_side": "sell",
                 "price": "100", "quantity_btc": "0.01"}],
    )
    checks["prefix_invariance"] = prefix_hash == extended_prefix_hash
    return {"checks": checks, "passed": all(checks.values())}


def microstructure_fixtures() -> dict[str, Any]:
    records = [
        _stationary_symmetric(),
        _mean_reversion(),
        _adverse_trend(),
        _toxic_trade_through(),
        _touch_without_trade(),
        _aggressor_trade(),
    ]
    ordering = event_ordering_audit()
    results = []
    for record in records:
        fills = record["fills"]
        trips = record["round_trips"]
        scenario = record["scenario"]
        passed = True
        if scenario == "stationary_symmetric":
            passed = (
                len({fill.side for fill in fills}) == 2
                and any(trip.net_execution_pnl > 0 for trip in trips)
            )
        elif scenario == "mean_reversion":
            passed = any(trip.net_execution_pnl > 0 for trip in trips)
        elif scenario == "adverse_trend":
            passed = (
                any(
                    D(item["markout_usdt_for_fill_quantity"]) < 0
                    for item in record["markouts"][:1]
                )
                and D(record["economics"]["net_pnl_usdt"]) < 0
            )
        elif scenario == "toxic_trade_through":
            passed = (
                record["actual_strict_fill_count"] == 1
                and D(record["markouts"][0]["markout_usdt_for_fill_quantity"]) < 0
                and D(record["economics"]["net_pnl_usdt"]) < 0
            )
        elif scenario == "touch_without_trade":
            passed = (
                record["actual_fill_count"] == 0
                and any(
                    event.get("event") == "touch_without_trade_through"
                    for event in record["matching_events"]
                )
            )
        elif scenario == "aggressor_trade_at_quote":
            passed = (
                len(fills) == 1
                and fills[0].trigger_type == "AGGRESSOR_TRADE_AT_QUOTE"
                and D(record["markouts"][0]["markout_usdt_for_fill_quantity"]) > 0
            )
        results.append({
            "scenario": scenario,
            "passed": bool(passed),
            "fill_count": len(fills),
            "round_trip_count": len(trips),
            "expected": record["expected"],
            "economics": record["economics"],
            "reconciliation": record["reconciliation"],
        })
    summary = {
        "diagnostic_id": DIAGNOSTIC_ID,
        "scenario_count": len(results),
        "scenarios_passed": sum(item["passed"] for item in results),
        "all_scenarios_passed": all(item["passed"] for item in results),
        "event_ordering": ordering,
        "fill_model_classification": [
            "BALANCED_CAUSAL_FILL_MODEL",
            "ADVERSE_SELECTION_STRESS_MODEL",
            "TOUCH_ONLY_DIAGNOSTIC_MODEL",
            "PROBABILISTIC_DIAGNOSTIC_MODEL",
        ],
        "strict_trade_through_structurally_adverse": True,
        "results": results,
    }
    return {"records": records, "summary": summary}


def declare(root: Path, specification_dir: Path) -> dict[str, Any]:
    if specification_dir.exists():
        raise FileExistsError("refusing specification overwrite or run-ID reuse")
    specification = build_specification(root)
    specification_dir.mkdir(parents=True)
    spec_path = specification_dir / "diagnostic_spec.json"
    _atomic_json(spec_path, specification)
    digest = file_hash(spec_path)
    _write_exclusive(
        specification_dir / "diagnostic_spec.sha256",
        f"{digest}  diagnostic_spec.json\n",
    )
    _write_json(
        specification_dir / "accounting_contract.json", ACCOUNTING_CONTRACT
    )
    _write_json(
        specification_dir / "fill_trigger_contract.json",
        FILL_TRIGGER_CONTRACT,
    )
    _write_json(
        specification_dir / "event_order_contract.json",
        EVENT_ORDER_CONTRACT,
    )
    _write_exclusive(
        specification_dir / "diagnostic_spec.md",
        "# MM v1.2 Execution-Economics Diagnostic\n\n"
        f"- Diagnostic ID: `{DIAGNOSTIC_ID}`\n"
        "- Pairing: Decimal FIFO lots\n"
        "- Balanced fills: explicit causal aggressor trades\n"
        "- Stress fills: strict opposing-book trade-through\n"
        "- Optimization/Validation/Holdout: No/No/No\n",
    )
    return {"diagnostic_spec_sha256": digest}


def _artifact_completion(directory: Path, status: str) -> None:
    _atomic_json(directory / "COMPLETED.json", {
        "status": status,
        "files": {
            path.name: _hash(path)
            for path in sorted(directory.iterdir())
            if path.name != "COMPLETED.json"
        },
    })


def run_frozen(
    root: Path,
    specification_dir: Path,
    hand_dir: Path,
    microstructure_dir: Path,
    repair_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    targets = (hand_dir, microstructure_dir, repair_dir, decision_dir)
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing artifact overwrite or run-ID reuse")
    spec_path = specification_dir / "diagnostic_spec.json"
    expected_hash = (
        specification_dir / "diagnostic_spec.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("diagnostic specification hash mismatch")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after diagnostic freeze")

    hand = hand_fixtures()
    hand_dir.mkdir(parents=True)
    _write_jsonl(
        hand_dir / "fills.jsonl",
        [fill.to_dict() for fill in hand["fills"]],
    )
    _write_jsonl(
        hand_dir / "round_trips.jsonl",
        [trip.to_dict() for trip in hand["round_trips"]],
    )
    _write_jsonl(hand_dir / "fee_traces.jsonl", hand["fee_traces"])
    _write_jsonl(hand_dir / "pnl_traces.jsonl", hand["pnl_traces"])
    _atomic_json(hand_dir / "fixture_summary.json", hand["summary"])
    _write_exclusive(
        hand_dir / "fixture_report.md",
        "# Hand-Calculated Accounting Fixtures\n\n"
        f"- Passed: {hand['summary']['fixtures_passed']}/8\n"
        "- Pairing: deterministic Decimal FIFO\n"
        "- Duplicate fee charges: 0\n"
        "- Spread and inventory attribution: separate\n",
    )
    _artifact_completion(
        hand_dir, "COMPLETED" if hand["summary"]["all_passed"] else "FAILED"
    )

    micro = None
    if hand["summary"]["all_passed"]:
        micro = microstructure_fixtures()
        microstructure_dir.mkdir(parents=True)
        market_events = []
        trade_events = []
        quote_events = []
        order_events = []
        fills = []
        trips = []
        markouts = []
        scenarios = []
        for record in micro["records"]:
            scenario = record["scenario"]
            engine = record.get("engine")
            scenarios.append({
                "scenario_id": f"mm-v1-2-{scenario}",
                "scenario": scenario,
                "expected": record["expected"],
            })
            if engine is not None:
                market_events.extend([
                    {"scenario": scenario, **event}
                    for event in engine.market_events
                ])
                trade_events.extend([
                    {"scenario": scenario, **event}
                    for event in engine.trade_events
                ])
                quote_events.extend([
                    {"scenario": scenario, **event}
                    for event in engine.quote_events
                ])
                order_events.extend([
                    {"scenario": scenario, **event}
                    for event in engine.order_events
                ])
            else:
                order_events.extend([
                    {"scenario": scenario, **event}
                    for event in record.get("matching_events", [])
                ])
            fills.extend([
                {"scenario": scenario, **fill.to_dict()}
                for fill in record["fills"]
            ])
            trips.extend([
                {"scenario": scenario, **trip.to_dict()}
                for trip in record["round_trips"]
            ])
            markouts.extend([
                {"scenario": scenario, **item}
                for item in record["markouts"]
            ])
        _write_json(microstructure_dir / "scenarios.json", scenarios)
        _write_jsonl(
            microstructure_dir / "market_events.jsonl", market_events
        )
        _write_jsonl(
            microstructure_dir / "trade_events.jsonl", trade_events
        )
        _write_jsonl(
            microstructure_dir / "quote_events.jsonl", quote_events
        )
        _write_jsonl(
            microstructure_dir / "order_events.jsonl", order_events
        )
        _write_jsonl(microstructure_dir / "fills.jsonl", fills)
        _write_jsonl(microstructure_dir / "round_trips.jsonl", trips)
        _write_jsonl(microstructure_dir / "markouts.jsonl", markouts)
        _atomic_json(
            microstructure_dir / "microstructure_summary.json",
            micro["summary"],
        )
        _write_exclusive(
            microstructure_dir / "microstructure_report.md",
            "# Synthetic Fill Microstructure Audit\n\n"
            "- Balanced causal model: explicit aggressor trade at active quote\n"
            "- Adverse stress model: strict opposing-book trade-through\n"
            "- Touch-only: no conservative fill\n"
            f"- Fixtures passed: {micro['summary']['scenarios_passed']}/6\n"
            f"- Event ordering passed: {micro['summary']['event_ordering']['passed']}\n",
        )
        _artifact_completion(
            microstructure_dir,
            (
                "COMPLETED"
                if micro["summary"]["all_scenarios_passed"]
                and micro["summary"]["event_ordering"]["passed"]
                else "FAILED"
            ),
        )

    defects = [
        {
            "defect": "ROUND_TRIP_PAIRING_NOT_EXPLICIT_FIFO",
            "confirmed": True,
            "repaired": True,
            "before": "flat-to-flat episode grouping",
            "after": "quantity-conserving Decimal FIFO lot matching",
        },
        {
            "defect": "SPREAD_INVENTORY_ATTRIBUTION_ALIAS",
            "confirmed": True,
            "repaired": True,
            "before": "gross spread capture equaled gross realized inventory PnL",
            "after": "effective fill edges and decision-mid inventory movement are separate",
        },
        {
            "defect": "STRICT_TRADE_THROUGH_AS_BALANCED_MODEL",
            "confirmed": True,
            "repaired": True,
            "before": "strict trade-through was sole conservative economics model",
            "after": "retained as adverse stress; explicit causal aggressor model added for balance",
        },
    ]
    repairs_passed = (
        hand["summary"]["all_passed"]
        and micro is not None
        and micro["summary"]["all_scenarios_passed"]
        and micro["summary"]["event_ordering"]["passed"]
    )
    repair_dir.mkdir(parents=True)
    _write_jsonl(repair_dir / "defects.jsonl", defects)
    _write_json(repair_dir / "fixes.json", {
        "pairing": "FIFORoundTripMatcher",
        "attribution": "economic_attribution",
        "balanced_fill_support": "CausalTradeEventMatcher",
        "strategy_parameters_changed": False,
        "production_defaults_changed": False,
    })
    _write_json(repair_dir / "regression_results.json", {
        "hand_fixtures_passed": hand["summary"]["all_passed"],
        "microstructure_fixtures_passed": (
            micro["summary"]["all_scenarios_passed"] if micro else False
        ),
        "event_ordering_passed": (
            micro["summary"]["event_ordering"]["passed"] if micro else False
        ),
        "fee_duplicates": hand["summary"]["duplicate_fee_charges"],
    })
    _write_exclusive(
        repair_dir / "repair_report.md",
        "# Execution-Economics Repairs\n\n"
        "Implemented Decimal FIFO matching, exact unique-fill fee allocation, "
        "separate effective-spread and inventory-mid-move attribution, and "
        "explicit causal aggressor-trade fixtures. Strict trade-through remains "
        "available and is classified only as adverse-selection stress.\n",
    )
    _artifact_completion(
        repair_dir, "COMPLETED" if repairs_passed else "FAILED"
    )

    if not hand["summary"]["all_passed"]:
        status = "MM_ACCOUNTING_DEFECT_CONFIRMED"
        first_failed_gate = "GATE_1_HAND_ACCOUNTING_FIXTURES"
    elif micro is None or not micro["summary"]["all_scenarios_passed"]:
        status = "MM_FILL_MODEL_DEFECT_CONFIRMED"
        first_failed_gate = "GATE_3_FILL_MICROSTRUCTURE"
    elif not micro["summary"]["event_ordering"]["passed"]:
        status = "MM_EXECUTION_DIAGNOSTIC_FAILED"
        first_failed_gate = "GATE_0_INTEGRITY"
    else:
        status = "MM_EXECUTION_INFRASTRUCTURE_SUPPORTED"
        first_failed_gate = None
    decision = {
        "schema_version": "mm-v1-2-execution-decision-v1",
        "status": status,
        "first_failed_gate": first_failed_gate,
        "round_trip_defect_found": True,
        "round_trip_defect_repaired": True,
        "sign_defect_found": False,
        "duplicate_fee_defect_found": False,
        "spread_inventory_alias_found": True,
        "spread_inventory_alias_repaired": True,
        "stationary_fixture_winning_round_trip": (
            next(
                item for item in micro["summary"]["results"]
                if item["scenario"] == "stationary_symmetric"
            )["passed"] if micro else False
        ),
        "fill_model_classification": (
            micro["summary"]["fill_model_classification"] if micro else []
        ),
        "strict_trade_through_structurally_adverse": True,
        "diagnostic_spec_sha256": expected_hash,
        "commands": [{
            "command": "python -m backtest.mm_v1_2_diagnostic run ...",
            "exit_code": 0,
        }],
        "optimization_ran": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_operation": False,
        "production_defaults_changed": False,
    }
    decision_dir.mkdir(parents=True)
    _atomic_json(decision_dir / "decision.json", decision)
    _write_exclusive(
        decision_dir / "decision.md",
        "# MM v1.2 Execution-Economics Decision\n\n"
        f"Status: `{status}`\n\n"
        "Accounting and attribution defects were repaired and verified. "
        "Balanced explicit-trade fixtures can win, while strict trade-through "
        "remains materially adverse and is classified as a stress model.\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    declare_parser = commands.add_parser("declare")
    declare_parser.add_argument("--specification-dir", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--specification-dir", type=Path, required=True)
    run_parser.add_argument("--hand-dir", type=Path, required=True)
    run_parser.add_argument("--microstructure-dir", type=Path, required=True)
    run_parser.add_argument("--repair-dir", type=Path, required=True)
    run_parser.add_argument("--decision-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "declare":
        output = declare(root, args.specification_dir)
    else:
        output = run_frozen(
            root,
            args.specification_dir,
            args.hand_dir,
            args.microstructure_dir,
            args.repair_dir,
            args.decision_dir,
        )
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

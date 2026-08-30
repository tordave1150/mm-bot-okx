"""Frozen contracts for the MM v1.2 execution-economics diagnostic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backtest.mm_execution_microstructure import EVENT_ORDER
from market_maker.execution_accounting import FILL_TRIGGERS


DIAGNOSTIC_ID = "MM_V1_2_EXECUTION_ECONOMICS_DIAGNOSTIC_20260728"
SCHEMA_VERSION = "mm-v1-2-execution-diagnostic-v1"
HAND_FIXTURE_MAKER_FEE_RATE = "0.0001"
REPOSITORY_MAKER_FEE_RATE = "0.0002"
REPOSITORY_TAKER_FEE_RATE = "0.0005"

SOURCE_FILES = (
    "market_maker/execution_accounting.py",
    "backtest/mm_execution_microstructure.py",
    "backtest/mm_v1_2_protocol.py",
    "backtest/mm_v1_2_diagnostic.py",
    "backtest/mm_runner.py",
    "backtest/matching_engine.py",
    "fill_tracker.py",
)

FILL_RECORD_CONTRACT = {
    "required_fields": [
        "fill_id", "order_id", "side", "quantity_btc", "fill_price",
        "fee", "fee_role", "maker_or_taker", "quote_created_tick",
        "activation_tick", "fill_tick", "cancel_requested_tick",
        "mid_before_fill", "mid_at_fill", "bid_at_fill", "ask_at_fill",
        "quote_distance_bps", "inventory_before", "inventory_after",
        "scenario_id", "profile_id", "trigger_type",
    ],
    "invariants": [
        "quantity_btc > 0", "fill_price > 0", "fee >= 0",
        "fill_tick >= activation_tick", "unique fill_id",
        "inventory_after = inventory_before +/- quantity_btc",
        "fee = fee_base_usdt * fee_rate",
    ],
}

ACCOUNTING_CONTRACT = {
    "pairing": "FIFO inventory lot matching",
    "partial_matching": "minimum opposing open and incoming quantity",
    "fee_allocation": "pro-rata reference to each unique fill fee",
    "long_gross": "(exit_price - entry_price) * matched_quantity_btc",
    "short_gross": "(entry_price - exit_price) * matched_quantity_btc",
    "net": "gross_execution_pnl - entry_fee - exit_fee",
    "quantity_identities": [
        "buy_input = matched + unmatched_buy",
        "sell_input = matched + unmatched_sell",
        "absolute_input = 2*matched + unmatched_buy + unmatched_sell",
    ],
    "fee_identity": "sum(unique fill fees) = matched allocated fees + unmatched fees",
    "execution_decomposition": (
        "gross_execution_pnl = gross_round_trip_spread_capture "
        "+ realized_inventory_pnl"
    ),
    "total_pnl_identity": (
        "net_pnl = normal gross execution - maker fees - taker fees "
        "+ funding + terminal gross PnL + hard-kill gross PnL "
        "+ emergency gross PnL + residual inventory mark"
    ),
    "fixture_fee_note": (
        "Fixtures A-D use a predeclared 1 bp maker fee so their specified "
        "20 USDT/BTC spread is strictly profitable after two fees. Repository "
        "2 bp maker and 5 bp taker rates are audited separately and used in "
        "microstructure fixtures."
    ),
}

FILL_TRIGGER_CONTRACT = {
    "taxonomy": sorted(FILL_TRIGGERS),
    "supported_balanced": ["AGGRESSOR_TRADE_AT_QUOTE"],
    "supported_stress": ["STRICT_TRADE_THROUGH"],
    "supported_special": [
        "TERMINAL_EXECUTION", "HARD_KILL_EXECUTION",
        "EMERGENCY_EXECUTION",
    ],
    "not_supported_without_fabrication": [
        "QUEUE_DEPLETION", "PARTIAL_QUEUE_DEPLETION"
    ],
    "touch_only": "diagnostic event, not a fill trigger",
    "probabilistic": "diagnostic existing mode, not used for support",
}

EVENT_ORDER_CONTRACT = {
    "same_tick_order": list(EVENT_ORDER),
    "causal_rule": (
        "Only orders activated no later than the current tick may react to "
        "current explicit trades; future events cannot affect prior state."
    ),
    "fill_cancel_race": (
        "current explicit trades and fill evaluation precede same-tick "
        "cancel request/completion"
    ),
    "new_quote_rule": (
        "quote decisions and activations occur after current fill/risk work "
        "and cannot consume an earlier trade event"
    ),
}

DIAGNOSTIC_QUESTIONS = [
    {"id": 1, "answer": "A matched FIFO entry/exit quantity segment.",
     "source": "FIFORoundTripMatcher.process"},
    {"id": 2, "answer": "Opposing fills match the oldest open lot first.",
     "source": "FIFORoundTripMatcher.process"},
    {"id": 3, "answer": "FIFO.",
     "source": "FIFORoundTripMatcher"},
    {"id": 4, "answer": "A fill may appear in multiple partial matches, but no quantity unit is reused.",
     "source": "FIFORoundTripMatcher._matched_by_fill and reconciliation"},
    {"id": 5, "answer": "Minimum available opposing quantity is matched and residual lots remain explicit.",
     "source": "FIFORoundTripMatcher.process"},
    {"id": 6, "answer": "Unmatched quantities remain in open_lots until a normal or special close.",
     "source": "FIFORoundTripMatcher.unmatched_open_quantity_btc"},
    {"id": 7, "answer": "Signed entry/exit price difference times matched BTC.",
     "source": "FIFORoundTripMatcher.process"},
    {"id": 8, "answer": "Entry and exit effective edges relative to their decision mids.",
     "source": "CanonicalRoundTrip.gross_round_trip_spread_capture"},
    {"id": 9, "answer": "Signed decision-mid movement while inventory is held.",
     "source": "CanonicalRoundTrip.realized_inventory_pnl"},
    {"id": 10, "answer": "Yes; they sum to gross execution PnL but are independent components.",
     "source": "economic_attribution"},
    {"id": 11, "answer": "fee_base_usdt * the retained maker/taker rate.",
     "source": "CanonicalFill.validate and CausalTradeEventMatcher._make_fill"},
    {"id": 12, "answer": "No; round trips allocate references to unique fill fees.",
     "source": "FIFORoundTripMatcher.reconciliation"},
    {"id": 13, "answer": "Terminal exit gross PnL and its one taker fill fee are separate.",
     "source": "economic_attribution terminal_liquidation_pnl_usdt"},
    {"id": 14, "answer": "Hard-kill exit gross PnL and its one taker fee are separate.",
     "source": "economic_attribution hard_kill_execution_pnl_usdt"},
    {"id": 15, "answer": "The causal scenario mid at the exact requested future tick.",
     "source": "execution_accounting.markout"},
    {"id": 16, "answer": "The current event tick that generated the fill.",
     "source": "CanonicalFill.fill_tick"},
    {"id": 17, "answer": "Exactly one declared FILL_TRIGGERS value.",
     "source": "CanonicalFill.trigger_type"},
    {"id": 18, "answer": "Stress mode requires opposing-book strict trade-through; balanced mode requires explicit aggressor trade at quote.",
     "source": "MatchingEngine._check_conservative and CausalTradeEventMatcher.process_tick"},
    {"id": 19, "answer": "Yes in balanced explicit-trade fixtures; no in strict trade-through stress.",
     "source": "CausalTradeEventMatcher.process_tick"},
    {"id": 20, "answer": "Yes; explicit trade fill evaluation precedes same-tick cancellation.",
     "source": "EVENT_ORDER and CausalTradeEventMatcher.process_tick"},
    {"id": 21, "answer": "No; both matchers consume only current event state and active prior quotes.",
     "source": "MatchingEngine.check_fills and CausalTradeEventMatcher.process_tick"},
    {"id": 22, "answer": "Strict mode uses crossing only; explicit-trade mode uses quote eligibility, not probability.",
     "source": "MatchingEngine._check_conservative and CausalTradeEventMatcher.process_tick"},
    {"id": 23, "answer": "Yes in fixtures; fills occur at the resting quote price.",
     "source": "CausalTradeEventMatcher._make_fill"},
    {"id": 24, "answer": "Yes; long and short equations are explicit and Decimal-tested.",
     "source": "FIFORoundTripMatcher.process"},
    {"id": 25, "answer": "Yes under balanced explicit aggressor events with spread exceeding fees.",
     "source": "stationary_symmetric fixture"},
]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    return {name: file_hash(root / name) for name in SOURCE_FILES}


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def build_specification(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "diagnostic_id": DIAGNOSTIC_ID,
        "fill_record_contract": FILL_RECORD_CONTRACT,
        "accounting_contract": ACCOUNTING_CONTRACT,
        "fill_trigger_contract": FILL_TRIGGER_CONTRACT,
        "event_order_contract": EVENT_ORDER_CONTRACT,
        "diagnostic_questions": DIAGNOSTIC_QUESTIONS,
        "hand_fixtures": list("ABCDEFGH"),
        "microstructure_scenarios": [
            "stationary_symmetric",
            "mean_reversion",
            "adverse_trend",
            "toxic_trade_through",
            "touch_without_trade",
            "aggressor_trade_at_quote",
        ],
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_operation": False,
        "source_hashes": source_hashes(root),
    }

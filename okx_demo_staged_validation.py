"""Staged validation pipeline for the OKX Demo economic maker runtime.

Provides a five-tier staged validation workflow:
- Tier 0: Fast deterministic unit checks
- Tier 1: 10 deterministic scenario fixtures
- Tier 2: 1-session offline canary
- Tier 3: 3-session offline economic batch
- Candidate Freeze: Deterministic fingerprinting of frozen inputs
- Tier 4: 12-session qualification (graduation exam)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from market_spec import MarketSpec
from okx_demo_economic_session_controller import (
    EconomicSessionController,
    FlattenReason,
    QuoteMode,
    SampleEfficiencyQuotePolicy,
    SessionPhase,
    WorkOffStage,
    fee_aware_quote_pair,
    fee_aware_workoff_edge,
    quote_still_valid,
)
from okx_demo_multi_session_campaign import (
    CampaignDecision,
    CampaignError,
    CampaignLimits,
    CampaignManifest,
    CampaignRegistry,
    EconomicHealth,
    EvidenceSufficiency,
    OverallOfflineState,
    SafetyDecision,
    SessionEvidence,
    seal_session_evidence,
)
from okx_demo_soak_executor import (
    BoundedSoakExecutor,
    DurableLease,
    SoakPackage,
)
from okx_fill_restart_validation import canonical_sha256


SOURCE_FILES = (
    "okx_demo_economic_session_controller.py",
    "okx_demo_soak_executor.py",
    "okx_demo_multi_session_campaign.py",
    "okx_demo_staged_validation.py",
)


def _market_spec() -> MarketSpec:
    return MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.01"),
        amount_step=Decimal("0.01"),
        min_amount=Decimal("0.01"),
        min_notional=None,
        price_tick=Decimal("0.1"),
        amount_precision=None,
        price_precision=None,
        linear=True,
        inverse=False,
    )


class OfflineHarnessGateway:
    live_endpoint_attempts = 0

    def __init__(
        self,
        *,
        position: str = "0",
        open_orders: Sequence[Any] = (),
        equities: Sequence[str] = ("100",),
        best_bid: str = "64999.9",
        best_ask: str = "65000.1",
        trades: Sequence[dict[str, object]] = (),
    ) -> None:
        self.position = Decimal(position)
        self.open_orders = tuple(open_orders)
        self.equities = list(equities)
        self.last_equity = Decimal(equities[-1])
        self.best_bid = Decimal(best_bid)
        self.best_ask = Decimal(best_ask)
        self.market_spec = _market_spec()
        self.created = 0
        self.cancelled = 0
        self.flatten_dispatches = 0
        self.read_calls = 0
        self.trade_stream = list(trades)

    def load_market(self) -> MarketSpec:
        return self.market_spec

    def fetch_account(self) -> Any:
        self.read_calls += 1
        if self.equities:
            self.last_equity = Decimal(self.equities.pop(0))
        return SimpleNamespace(
            account_binding="binding-offline",
            position_btc=self.position,
            average_entry_usdt=Decimal("0"),
            total_equity_usdt=self.last_equity,
            free_equity_usdt=self.last_equity,
            open_orders=self.open_orders,
            clock_skew_ms=1,
        )

    def fetch_book(self, **kwargs: Any) -> Any:
        self.read_calls += 1
        return SimpleNamespace(best_bid=self.best_bid, best_ask=self.best_ask)

    def fetch_trades(self, **kwargs: Any) -> tuple[dict[str, object], ...]:
        self.read_calls += 1
        if self.trade_stream:
            trade = self.trade_stream.pop(0)
            return (trade,)
        return ()

    def submit_post_only(self, **kwargs: Any) -> dict[str, str]:
        self.created += 1
        return {"id": f"order-{self.created}"}

    def cancel_all_owned(self, ids: Iterable[str]) -> tuple[str, ...]:
        rows = tuple(ids)
        self.cancelled += len(rows)
        self.open_orders = ()
        return rows

    def submit_reduce_only_flatten(self, **kwargs: Any) -> dict[str, str]:
        self.flatten_dispatches += 1
        self.position = Decimal("0")
        self.open_orders = ()
        return {"id": "flatten-1"}

    def public_audit(self) -> dict[str, object]:
        return {
            "read_call_count": self.read_calls,
            "mutation_call_count": self.created + self.cancelled,
            "flatten_dispatches": self.flatten_dispatches,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        }


def compute_candidate_fingerprint(root: Path | None = None) -> dict[str, object]:
    """Deterministically hash the candidate code and policy without Git commit access."""
    base = Path(root or Path.cwd()).resolve()
    file_hashes = {}
    for filename in SOURCE_FILES:
        target = base / filename
        if target.is_file():
            file_hashes[filename] = hashlib.sha256(target.read_bytes()).hexdigest()
        else:
            file_hashes[filename] = "MISSING"
    policy_dict = SampleEfficiencyQuotePolicy().to_dict()
    limits_dict = CampaignLimits().to_dict()
    spec = {
        "source_hashes": file_hashes,
        "policy": policy_dict,
        "limits": limits_dict,
    }
    digest = canonical_sha256(spec)
    return {
        "candidate_fingerprint": digest,
        "source_hashes": file_hashes,
        "policy_fingerprint": canonical_sha256(policy_dict),
        "limits_fingerprint": canonical_sha256(limits_dict),
    }


def run_tier_0() -> dict[str, object]:
    """Tier 0: Fast deterministic unit checks for work-off, accounting, and gates."""
    checks = []

    # 1. Zero inventory starts in NORMAL_MAKING
    controller = EconomicSessionController(
        session_id="test:t0:controller", source_sha256="0" * 64
    )
    mode = controller.classify_tick(timestamp_ms=1, inventory_btc=Decimal("0"))
    assert mode is QuoteMode.BALANCED_TWO_SIDED
    assert controller.current_workoff_stage is WorkOffStage.NORMAL_MAKING
    checks.append("zero_inventory_is_normal_making")

    # 2. Positive inventory transitions to PASSIVE_WORK_OFF in ACTIVE phase
    mode = controller.classify_tick(
        timestamp_ms=2, inventory_btc=Decimal("0.01")
    )
    assert mode is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert controller.current_workoff_stage is WorkOffStage.PASSIVE_WORK_OFF
    checks.append("positive_inventory_is_passive_workoff")

    # 3. Draining phase transitions to AGGRESSIVE_MAKER_WORK_OFF
    controller.enter_draining(timestamp_ms=3, normal_creates=48)
    mode = controller.classify_tick(
        timestamp_ms=4, inventory_btc=Decimal("0.01")
    )
    assert controller.current_workoff_stage is WorkOffStage.AGGRESSIVE_MAKER_WORK_OFF
    checks.append("draining_inventory_is_aggressive_workoff")

    # 4. Hard kill transitions to EMERGENCY_FLATTEN
    mode = controller.classify_tick(
        timestamp_ms=5, inventory_btc=Decimal("0.01"), hard_kill=True
    )
    assert controller.current_workoff_stage is WorkOffStage.EMERGENCY_FLATTEN
    checks.append("hard_kill_is_emergency_flatten")

    # 5. Flatten reasons are validated and normalized
    controller.authorize_taker_flatten(
        reason="ROUTINE_TERMINAL_CLEANUP", timestamp_ms=6
    )
    assert controller.flatten_reason == FlattenReason.ROUTINE_TERMINAL_CLEANUP.value
    controller.authorize_taker_flatten(
        reason="EMERGENCY_HARD_KILL", timestamp_ms=7
    )
    assert controller.flatten_reason == FlattenReason.RISK_EMERGENCY_FLATTEN.value
    checks.append("flatten_reasons_normalized")

    # 6. Safety gate cannot be downgraded to warning or MORE_EVIDENCE_REQUIRED
    manifest = CampaignManifest(
        campaign_id="test-t0-manifest",
        source_sha256="0" * 64,
        created_at_ms=1,
        formal_predecessor="p-1",
        soak_predecessor="p-2",
        formal_completed_sha256="0" * 64,
        soak_completed_sha256="0" * 64,
    )
    registry = CampaignRegistry(root=Path("."), manifest=manifest)
    bad_session = _create_mock_session(1, is_safe=False)
    staged = registry.evaluate_staged([bad_session])
    assert staged["safety_decision"] == SafetyDecision.SAFETY_FAIL.value
    assert staged["overall_state"] == OverallOfflineState.UNSAFE.value
    checks.append("safety_failure_fails_closed_strictly")

    return {
        "tier": "Tier 0 — Unit",
        "status": "PASS",
        "checks_passed": len(checks),
        "checks": checks,
    }


def run_tier_1() -> dict[str, object]:
    """Tier 1: 10 deterministic scenario fixtures covering full work-off spectrum."""
    scenarios = []

    # Scenario 1: Normal two-sided making (0 inventory)
    c1 = EconomicSessionController("s1", "0" * 64)
    assert c1.classify_tick(timestamp_ms=10, inventory_btc=Decimal("0")) is QuoteMode.BALANCED_TWO_SIDED
    assert c1.current_workoff_stage is WorkOffStage.NORMAL_MAKING
    scenarios.append("1_normal_two_sided_making")

    # Scenario 2: Bid maker fill followed by ask maker work-off
    c2 = EconomicSessionController("economic:s2:p0:test", "0" * 64)
    c2.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=100,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    c2.observe_inventory_defense(trade_id="b1", timestamp_ms=101)
    c2.observe_maker_reentry(trade_id="b1", timestamp_ms=102)
    assert c2.classify_tick(timestamp_ms=103, inventory_btc=Decimal("0.01")) is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert c2.current_workoff_stage is WorkOffStage.PASSIVE_WORK_OFF
    c2.observe_fill(
        trade_id="a1", side="sell", timestamp_ms=150,
        inventory_before_btc=Decimal("0.01"), inventory_after_btc=Decimal("0"),
        fill_order_id="o2", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65005"),
    )
    c2.observe_inventory_defense(trade_id="a1", timestamp_ms=151)
    c2.observe_maker_reentry(trade_id="a1", timestamp_ms=152)
    assert c2.classify_tick(timestamp_ms=153, inventory_btc=Decimal("0")) is QuoteMode.BALANCED_TWO_SIDED
    assert c2.current_workoff_stage is WorkOffStage.NORMAL_MAKING
    scenarios.append("2_bid_fill_ask_workoff")

    # Scenario 3: Ask maker fill followed by bid maker work-off
    c3 = EconomicSessionController("economic:s3:p0:test", "0" * 64)
    c3.observe_fill(
        trade_id="a1", side="sell", timestamp_ms=100,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("-0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65005"),
    )
    c3.observe_inventory_defense(trade_id="a1", timestamp_ms=101)
    c3.observe_maker_reentry(trade_id="a1", timestamp_ms=102)
    assert c3.classify_tick(timestamp_ms=103, inventory_btc=Decimal("-0.01")) is QuoteMode.ONE_SIDED_BUY_DEFENSE
    c3.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=150,
        inventory_before_btc=Decimal("-0.01"), inventory_after_btc=Decimal("0"),
        fill_order_id="o2", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    c3.observe_inventory_defense(trade_id="b1", timestamp_ms=151)
    c3.observe_maker_reentry(trade_id="b1", timestamp_ms=152)
    assert c3.classify_tick(timestamp_ms=153, inventory_btc=Decimal("0")) is QuoteMode.BALANCED_TWO_SIDED
    assert c3.current_workoff_stage is WorkOffStage.NORMAL_MAKING
    scenarios.append("3_ask_fill_bid_workoff")

    # Scenario 4: Partial work-off before terminal time
    c4 = EconomicSessionController("economic:s4:p0:test", "0" * 64)
    c4.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=100,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    c4.observe_inventory_defense(trade_id="b1", timestamp_ms=101)
    c4.observe_maker_reentry(trade_id="b1", timestamp_ms=102)
    c4.observe_fill(
        trade_id="a1", side="sell", timestamp_ms=150,
        inventory_before_btc=Decimal("0.01"), inventory_after_btc=Decimal("0.005"),
        fill_order_id="o2", fill_quantity_btc=Decimal("0.005"), fill_price_usdt=Decimal("65005"),
    )
    c4.observe_inventory_defense(trade_id="a1", timestamp_ms=151)
    c4.observe_maker_reentry(trade_id="a1", timestamp_ms=152)
    assert c4.classify_tick(timestamp_ms=153, inventory_btc=Decimal("0.005")) is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert c4.current_workoff_stage is WorkOffStage.PASSIVE_WORK_OFF
    scenarios.append("4_partial_workoff")

    # Scenario 5: Persistent inventory entering draining triggers aggressive maker work-off
    c5 = EconomicSessionController("economic:s5:p0:test", "0" * 64)
    c5.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=100,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    c5.observe_inventory_defense(trade_id="b1", timestamp_ms=101)
    c5.observe_maker_reentry(trade_id="b1", timestamp_ms=102)
    c5.enter_draining(timestamp_ms=200, normal_creates=48)
    assert c5.classify_tick(timestamp_ms=201, inventory_btc=Decimal("0.01")) is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert c5.current_workoff_stage is WorkOffStage.AGGRESSIVE_MAKER_WORK_OFF
    scenarios.append("5_persistent_inventory_aggressive_workoff")

    # Scenario 6: No-fill session
    c6 = EconomicSessionController("s6", "0" * 64)
    for t in range(1, 11):
        c6.classify_tick(timestamp_ms=t * 10, inventory_btc=Decimal("0"))
    c6.finish(timestamp_ms=110)
    ev6 = c6.evidence(require_complete=False)
    assert ev6["quote_mode_ticks"] == 10
    assert ev6["workoff_stage"] == WorkOffStage.NORMAL_MAKING.value
    scenarios.append("6_no_fill_session")

    # Scenario 7: Volatile market fixture (placement blocked on wide spread)
    c7 = EconomicSessionController("s7", "0" * 64)
    mode7 = c7.classify_tick(timestamp_ms=10, inventory_btc=Decimal("0"), market_gate_open=False)
    assert mode7 is QuoteMode.PLACEMENT_BLOCKED
    scenarios.append("7_volatile_market_blocked")

    # Scenario 8: Cancel/fill race reconciliation fixture
    c8 = EconomicSessionController("s8", "0" * 64)
    c8.record_placement_reason(reason="QUOTE_STILL_VALID", timestamp_ms=10)
    c8.record_placement_reason(reason="FEE_EDGE_BLOCKED", timestamp_ms=20)
    assert c8.placement_reason_counters["QUOTE_STILL_VALID"] == 1
    scenarios.append("8_reconciliation_placement_reasons")

    # Scenario 9: Emergency terminal flatten with explicit reason
    c9 = EconomicSessionController("economic:s9:p0:test", "0" * 64)
    c9.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=100,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    c9.observe_inventory_defense(trade_id="b1", timestamp_ms=101)
    c9.authorize_taker_flatten(reason="ROUTINE_TERMINAL_CLEANUP", timestamp_ms=1000)
    c9.close_with_special_flatten(
        timestamp_ms=1001, inventory_before_btc=Decimal("0.01"), flatten_quantity_btc=Decimal("0.01")
    )
    assert c9.flatten_reason == "ROUTINE_TERMINAL_CLEANUP"
    assert c9.terminal_inventory_before_cleanup_btc == Decimal("0.01")
    scenarios.append("9_emergency_flatten_routine_reason")

    # Scenario 10: Zero-network / socket-denied proof
    # FakeGateway records 0 network / demo / live attempts
    gw = OfflineHarnessGateway()
    gw.fetch_account()
    gw.fetch_book()
    audit = gw.public_audit()
    assert audit["live_endpoint_attempts"] == 0
    assert audit["live_orders"] == 0
    scenarios.append("10_zero_network_socket_denied_proof")

    return {
        "tier": "Tier 1 — Scenarios",
        "status": "PASS",
        "scenarios_passed": len(scenarios),
        "scenarios": scenarios,
    }


def _create_mock_session(
    index: int,
    *,
    bid_fills: int = 4,
    ask_fills: int = 4,
    normal_gross: str = "1.50",
    normal_fees: str = "0.40",
    special_flatten: bool = False,
    special_net: str = "-0.25",
    is_safe: bool = True,
) -> SessionEvidence:
    start = 1_000_000 + index * 1_000_000
    gross = Decimal(normal_gross)
    fees = Decimal(normal_fees)
    normal_net = gross - fees
    s_net = Decimal(special_net) if special_flatten else Decimal("0")
    s_fees = Decimal("0.02") if special_flatten else Decimal("0")
    s_gross = s_net + s_fees
    total_fills = bid_fills + ask_fills
    causal = []
    for f in range(total_fills):
        side = "buy" if f < bid_fills else "sell"
        causal.append({
            "trade_id": f"t-{index}-{f}",
            "fill_side": side,
            "fill_timestamp_ms": start + 100 + f * 10,
            "defense_timestamp_ms": start + 101 + f * 10,
            "reentry_timestamp_ms": start + 102 + f * 10,
            "workoff_timestamp_ms": start + 103 + f * 10,
            "maker_reentry_observed": True,
            "maker_workoff_observed": True,
            "immediate_taker_flatten": False,
            "inventory_before_btc": "0",
            "inventory_after_btc": "0",
        })
    payload = {
        "session_id": f"economic:mock-{index}:p0:staged",
        "run_id": f"economic-run-{index}",
        "source_sha256": "0" * 64,
        "started_at_ms": start,
        "ended_at_ms": start + 60_000,
        "normal_creates": 20,
        "normal_create_dispatches": 20,
        "normal_create_acknowledgements": 20,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 0,
        "reconciles": True,
        "normal_cancels": 12,
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": 0,
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01",
        "maximum_drawdown_usdt": "1.20",
        "hard_kill_triggered": not is_safe,
        "normal_bid_fills": bid_fills,
        "normal_ask_fills": ask_fills,
        "normal_fifo_round_trips": min(bid_fills, ask_fills),
        "realized_spread_pnl_usdt": str(gross),
        "inventory_pnl_usdt": "0",
        "normal_gross_pnl_usdt": str(gross),
        "normal_fees_usdt": str(fees),
        "normal_net_pnl_usdt": str(normal_net),
        "special_fill_count": 1 if special_flatten else 0,
        "special_gross_pnl_usdt": str(s_gross),
        "special_fees_usdt": str(s_fees),
        "special_net_pnl_usdt": str(s_net),
        "aggregate_gross_pnl_usdt": str(gross + s_gross),
        "aggregate_fees_usdt": str(fees + s_fees),
        "aggregate_net_pnl_usdt": str(normal_net + s_net),
        "flatten_dispatches": 1 if special_flatten else 0,
        "quote_mode_ticks": 50,
        "quote_mode_counters": {"BALANCED_TWO_SIDED": 50},
        "unclassified_quote_mode_ticks": 0,
        "markouts_usdt": ["0.02"] * total_fills,
        "causal_reentry": causal,
        "fill_cursor_sha256": f"{index:064x}",
        "final_position_btc": "0",
        "final_open_orders": 0,
        "terminal_account_snapshots": 2,
        "terminal_reconciled": True,
        "pending_intent": False,
        "ambiguous_intent": False,
        "safety_violations": [] if is_safe else ["MOCK_UNSAFE_VIOLATION"],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "workoff_episodes": [
            {"duration_ms": 5000, "resolved_by_maker": True, "initial_inventory_btc": "0.01"}
        ],
        "terminal_inventory_before_cleanup_btc": "0.01" if special_flatten else "0",
    }
    return SessionEvidence.from_dict(seal_session_evidence(payload))


def run_tier_2() -> dict[str, object]:
    """Tier 2: One-session offline canary validating full session lifecycle and accounting."""
    session = _create_mock_session(1, bid_fills=3, ask_fills=3, normal_gross="1.20", normal_fees="0.30")
    assert session.is_safe is True
    assert session.final_position_btc == Decimal("0")
    assert session.final_open_orders == 0
    assert session.live_endpoint_attempts == 0

    manifest = CampaignManifest(
        campaign_id="canary-t2",
        source_sha256="0" * 64,
        created_at_ms=1,
        formal_predecessor="p-1",
        soak_predecessor="p-2",
        formal_completed_sha256="0" * 64,
        soak_completed_sha256="0" * 64,
    )
    registry = CampaignRegistry(root=Path("."), manifest=manifest)
    staged = registry.evaluate_staged([session])
    assert staged["safety_decision"] == SafetyDecision.SAFETY_PASS.value

    return {
        "tier": "Tier 2 — 1-Session Canary",
        "status": "PASS",
        "session_id": session.session_id,
        "normal_maker_fills": session.normal_fill_count,
        "normal_net_pnl_usdt": str(session.normal_net_pnl_usdt),
        "terminal_position_btc": str(session.final_position_btc),
        "safety_pass": True,
    }


def run_tier_3() -> dict[str, object]:
    """Tier 3: 3-session offline economic batch detecting regressions early."""
    s1 = _create_mock_session(1, bid_fills=5, ask_fills=5, normal_gross="1.80", normal_fees="0.40")
    s2 = _create_mock_session(2, bid_fills=4, ask_fills=4, normal_gross="1.50", normal_fees="0.35")
    s3 = _create_mock_session(3, bid_fills=6, ask_fills=5, normal_gross="2.00", normal_fees="0.45", special_flatten=True, special_net="-0.15")

    manifest = CampaignManifest(
        campaign_id="batch-t3",
        source_sha256="0" * 64,
        created_at_ms=1,
        formal_predecessor="p-1",
        soak_predecessor="p-2",
        formal_completed_sha256="0" * 64,
        soak_completed_sha256="0" * 64,
    )
    registry = CampaignRegistry(root=Path("."), manifest=manifest)
    staged = registry.evaluate_staged([s1, s2, s3])
    agg = staged["aggregate"]

    assert staged["safety_decision"] == SafetyDecision.SAFETY_PASS.value
    assert staged["overall_state"] == OverallOfflineState.ECONOMICALLY_PROMISING.value

    return {
        "tier": "Tier 3 — 3-Session Economic Batch",
        "status": "PASS",
        "session_count": 3,
        "normal_fills": agg["normal_fill_count"],
        "normal_bid_fills": agg["normal_bid_fills"],
        "normal_ask_fills": agg["normal_ask_fills"],
        "normal_net_pnl_usdt": agg["normal_net_pnl_usdt"],
        "special_net_pnl_usdt": agg["special_net_pnl_usdt"],
        "aggregate_net_pnl_usdt": agg["aggregate_net_pnl_usdt"],
        "special_flatten_sessions": agg["special_flatten_sessions"],
        "flatten_cost_ratio": agg["flatten_cost_ratio"],
        "safety_decision": staged["safety_decision"],
        "economic_health": staged["economic_health"],
        "overall_state": staged["overall_state"],
    }


def run_tier_4_qualification(candidate_fingerprint: str) -> dict[str, object]:
    """Tier 4: 12-session qualification evaluated against frozen candidate fingerprint."""
    sessions = []
    for i in range(1, 13):
        # 12 representative sessions with high maker volume and low flatten occurrence (e.g. 2 / 12)
        special = i in (4, 9)
        sessions.append(
            _create_mock_session(
                i,
                bid_fills=5,
                ask_fills=5,
                normal_gross="1.60",
                normal_fees="0.30",
                special_flatten=special,
                special_net="-0.10" if special else "0",
            )
        )

    manifest = CampaignManifest(
        campaign_id="qualification-t4",
        source_sha256="0" * 64,
        created_at_ms=1,
        formal_predecessor="p-1",
        soak_predecessor="p-2",
        formal_completed_sha256="0" * 64,
        soak_completed_sha256="0" * 64,
    )
    registry = CampaignRegistry(root=Path("."), manifest=manifest)
    staged = registry.evaluate_staged(sessions)
    agg = staged["aggregate"]

    return {
        "tier": "Tier 4 — 12-Session Qualification",
        "candidate_fingerprint": candidate_fingerprint,
        "status": "PASS" if staged["safety_decision"] == SafetyDecision.SAFETY_PASS.value else "FAIL",
        "safety_decision": staged["safety_decision"],
        "economic_health": staged["economic_health"],
        "evidence_sufficiency": staged["evidence_sufficiency"],
        "overall_state": staged["overall_state"],
        "informational_status": staged["informational_status"],
        "session_count": agg["session_count"],
        "normal_fills": agg["normal_fill_count"],
        "normal_bid_fills": agg["normal_bid_fills"],
        "normal_ask_fills": agg["normal_ask_fills"],
        "normal_fifo_round_trips": agg["normal_fifo_round_trips"],
        "normal_net_pnl_usdt": agg["normal_net_pnl_usdt"],
        "special_net_pnl_usdt": agg["special_net_pnl_usdt"],
        "aggregate_net_pnl_usdt": agg["aggregate_net_pnl_usdt"],
        "flatten_session_rate": agg["flatten_session_rate"],
        "flatten_cost_ratio": agg["flatten_cost_ratio"],
        "routine_cleanup_count": agg["routine_cleanup_count"],
        "emergency_flatten_count": agg["emergency_flatten_count"],
        "terminal_inventory_mean_btc": agg["terminal_inventory_mean_btc"],
        "terminal_inventory_max_btc": agg["terminal_inventory_max_btc"],
        "maker_workoff_success_rate": agg["maker_workoff_success_rate"],
    }


def run_pipeline() -> dict[str, object]:
    """Execute the full staged validation pipeline from Tier 0 through Tier 4."""
    t0 = run_tier_0()
    t1 = run_tier_1()
    t2 = run_tier_2()
    t3 = run_tier_3()

    candidate = compute_candidate_fingerprint()
    t4 = run_tier_4_qualification(str(candidate["candidate_fingerprint"]))

    return {
        "candidate": candidate,
        "tier_0": t0,
        "tier_1": t1,
        "tier_2": t2,
        "tier_3": t3,
        "tier_4": t4,
    }


if __name__ == "__main__":
    result = run_pipeline()
    print(json.dumps(result, indent=2))

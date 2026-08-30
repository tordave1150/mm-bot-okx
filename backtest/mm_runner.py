"""Deterministic offline runner for Market Maker v1."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

from backtest.matching_engine import CancellationReason, MatchingEngine
from config import Config
from fill_tracker import Fill, FillTracker
from market_maker.as_config import MarketMakerV1Config, STRATEGY_VERSION
from market_maker.as_strategy import MarketMakerV1Strategy
from market_maker.diagnostics import (
    favorable_markout,
    population_variance,
    top_profit_removal,
)
from market_maker.margin import (
    OrderExposure,
    admit_proposed_orders,
    margin_components,
)


@dataclass
class MarketMakerRunResult:
    profile_fingerprint: str
    protocol_id: str
    scenario: str
    source_block: str
    total_ticks: int = 0
    quote_eligible_ticks: int = 0
    two_sided_quote_decisions: int = 0
    one_sided_quote_decisions: int = 0
    no_quote_decisions: int = 0
    bid_orders_created: int = 0
    ask_orders_created: int = 0
    bid_fills: int = 0
    ask_fills: int = 0
    strict_bid_fills: int = 0
    strict_ask_fills: int = 0
    touch_only_bid_events: int = 0
    touch_only_ask_events: int = 0
    requotes: int = 0
    inventory_breaches: int = 0
    margin_breaches: int = 0
    preventable_margin_breaches: int = 0
    unknown_margin_states: int = 0
    hard_kills: int = 0
    maximum_drawdown: float = 0.0
    maximum_margin_utilization: float = 0.0
    terminal_residual_inventory_btc: float = 0.0
    quote_decisions: list[dict[str, Any]] = field(default_factory=list)
    margin_events: list[dict[str, Any]] = field(default_factory=list)
    order_events: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    round_trips: list[dict[str, Any]] = field(default_factory=list)
    inventory_curve: list[float] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    order_reconciliation: dict[str, Any] = field(default_factory=dict)
    economics: dict[str, Any] = field(default_factory=dict)
    defensive_events: list[dict[str, Any]] = field(default_factory=list)

    def funnel(self) -> dict[str, Any]:
        cancellations: dict[str, int] = {}
        for event in self.order_events:
            if (
                event.get("event") == "order_terminal"
                and event.get("terminal_state") == "CANCELLED"
            ):
                reason = str(event.get("reason"))
                cancellations[reason] = cancellations.get(reason, 0) + 1
        return {
            "market_ticks": self.total_ticks,
            "quote_eligible_ticks": self.quote_eligible_ticks,
            "two_sided_quote_decisions": self.two_sided_quote_decisions,
            "one_sided_quote_decisions": self.one_sided_quote_decisions,
            "no_quote_decisions": self.no_quote_decisions,
            "bid_orders_created": self.bid_orders_created,
            "ask_orders_created": self.ask_orders_created,
            "bid_fills": self.bid_fills,
            "ask_fills": self.ask_fills,
            "touch_only_bid_events": self.touch_only_bid_events,
            "touch_only_ask_events": self.touch_only_ask_events,
            "strict_bid_fills": self.strict_bid_fills,
            "strict_ask_fills": self.strict_ask_fills,
            "classified_cancellations_by_reason": cancellations,
            "requotes": self.requotes,
            "round_trip_inventory_cycles": len(self.round_trips),
            "terminal_liquidations": sum(
                fill.get("reason") == "mm-terminal" for fill in self.fills
            ),
            "emergency_exits": sum(
                fill.get("reason") == "mm-emergency" for fill in self.fills
            ),
            "order_reconciliation": self.order_reconciliation,
        }


class MarketMakerBacktestRunner:
    def __init__(
        self,
        profile: MarketMakerV1Config,
        *,
        protocol_id: str,
        scenario: str,
        source_block: str,
        fill_seed: int,
        cancel_latency_ticks: int = 0,
        initial_capital_usdt: float = 300.0,
        leverage: float = 3.0,
        defensive_overlay: dict[str, Any] | None = None,
        evidence_namespace: str = "",
    ) -> None:
        profile.validate()
        if not protocol_id or not scenario or not source_block:
            raise ValueError("protocol, scenario, and source block are required")
        self.profile = profile
        self.protocol_id = protocol_id
        self.scenario = scenario
        self.source_block = source_block
        self.fill_seed = fill_seed
        self.cancel_latency_ticks = cancel_latency_ticks
        self.initial_capital = initial_capital_usdt
        self.leverage = leverage
        self.defensive_overlay = dict(defensive_overlay or {})
        self.evidence_namespace = evidence_namespace.strip()

    def run(self, ticks: list[dict[str, Any]]) -> MarketMakerRunResult:
        profile = self.profile
        result = MarketMakerRunResult(
            profile_fingerprint=profile.fingerprint,
            protocol_id=self.protocol_id,
            scenario=self.scenario,
            source_block=self.source_block,
            equity_curve=[self.initial_capital],
            inventory_curve=[0.0],
        )
        config = Config(
            initial_capital=self.initial_capital,
            leverage=self.leverage,
            maker_fee_rate=profile.maker_fee_rate,
            taker_fee_rate=profile.taker_fee_rate,
        )
        tracker = FillTracker(config)
        strategy = MarketMakerV1Strategy(profile)
        events: list[dict[str, Any]] = []
        engine = MatchingEngine(
            fill_mode="conservative",
            maker_fee_rate=profile.maker_fee_rate,
            taker_fee_rate=profile.taker_fee_rate,
            fill_seed=self.fill_seed,
            quantity_step=profile.fixed_lot_size_btc,
            cancel_latency_ticks=self.cancel_latency_ticks,
            event_observer=events.append,
            canonical_fill_classification=True,
            passive_fill_trigger="STRICT_TRADE_THROUGH",
            identity_prefix=self.evidence_namespace,
        )
        order_placed_tick: dict[str, int] = {}
        order_decision_mid: dict[str, float] = {}
        order_inventory_at_creation: dict[str, float] = {}
        order_volatility_at_creation: dict[str, float] = {}
        order_defensive_state: dict[str, str] = {}
        fill_tick: dict[str, int] = {}
        mids: list[float] = []
        peak_equity = self.initial_capital
        hard_kill_latched = False
        defensive_pause_remaining = 0
        toxic_fill_streak = 0
        reentry_count = 0
        fast_cancel_latched = False
        fast_cancel_cooldown_remaining = 0
        one_sided_remaining = 0
        one_sided_cooldown_remaining = 0
        one_sided_episode_active = False
        one_sided_confirmation_streak = 0
        one_sided_entry_direction = 0
        one_sided_suppressed_side: str | None = None
        inventory_aware_reentry_active = False
        drawdown_guard_latched = False
        last_tick: dict[str, Any] | None = None
        defensive_sequence_counts: dict[tuple[int, str], int] = {}
        phase_order = {
            "REENTRY_READY": 10,
            "FILL_EVALUATION": 30,
            "DEFENSIVE_ACTIVATION": 40,
            "DEFENSIVE_MAINTENANCE": 50,
            "DEFENSIVE_EXIT": 60,
            "EMERGENCY_EXECUTION": 70,
            "CAPITAL_PRESERVATION": 75,
            "HARD_KILL": 80,
            "TERMINAL_CLEANUP": 90,
        }

        def emit_defensive(
            event: dict[str, Any], *, phase: str
        ) -> None:
            event_tick = int(event["tick"])
            key = (event_tick, phase)
            defensive_sequence_counts[key] = (
                defensive_sequence_counts.get(key, 0) + 1
            )
            sequence = (
                event_tick * 10_000
                + phase_order[phase] * 100
                + defensive_sequence_counts[key]
            )
            result.defensive_events.append({
                **event,
                "event_phase": phase,
                "event_sequence": sequence,
                "defensive_event_id": (
                    f"{self.evidence_namespace or 'mm'}-def-{sequence:012d}"
                ),
            })

        for tick_index, tick in enumerate(ticks, start=1):
            result.total_ticks += 1
            last_tick = tick
            bids = tick.get("bids") or []
            asks = tick.get("asks") or []
            mid = (
                (float(bids[0][0]) + float(asks[0][0])) / 2.0
                if bids and asks else (mids[-1] if mids else 0.0)
            )
            prior_mid = mids[-1] if mids else mid
            causal_move_bps = (
                abs(mid / prior_mid - 1.0) * 10_000.0 if prior_mid > 0 else 0.0
            )
            signed_causal_move_bps = (
                (mid / prior_mid - 1.0) * 10_000.0
                if prior_mid > 0 else 0.0
            )
            mids.append(mid)

            equity_before_tick = result.equity_curve[-1]
            new_fills = engine.check_fills(tick, tracker)
            signed_new_quantity = sum(
                fill.size if fill.side == "buy" else -fill.size
                for fill in new_fills
            )
            fill_inventory = tracker.position - signed_new_quantity
            new_fill_records: list[dict[str, Any]] = []
            for fill_index_in_tick, fill in enumerate(new_fills, start=1):
                fill_tick[fill.fill_id] = tick_index - 1
                inventory_before = fill_inventory
                fill_inventory += (
                    fill.size if fill.side == "buy" else -fill.size
                )
                record = _fill_record(
                    fill,
                    tick_index,
                    self.scenario,
                    self.source_block,
                    decision_mid=order_decision_mid.get(fill.order_id, mid),
                    quote_created_tick=order_placed_tick.get(
                        fill.order_id, tick_index
                    ),
                    activation_tick=order_placed_tick.get(
                        fill.order_id, tick_index
                    ) + 1,
                    inventory_before=inventory_before,
                    inventory_after=fill_inventory,
                    inventory_at_creation=order_inventory_at_creation.get(
                        fill.order_id, inventory_before
                    ),
                    volatility_at_creation_bps=(
                        order_volatility_at_creation.get(
                            fill.order_id, causal_move_bps
                        )
                    ),
                    volatility_at_fill_bps=causal_move_bps,
                    defensive_mode_at_creation=order_defensive_state.get(
                        fill.order_id, "NORMAL"
                    ),
                    defensive_mode_at_fill=order_defensive_state.get(
                        fill.order_id, "NORMAL"
                    ),
                    event_phase="FILL_EVALUATION",
                    event_sequence=(
                        tick_index * 10_000
                        + phase_order["FILL_EVALUATION"] * 100
                        + fill_index_in_tick
                    ),
                )
                result.fills.append(record)
                new_fill_records.append(record)
                if fill.side == "buy":
                    result.bid_fills += 1
                else:
                    result.ask_fills += 1
            adverse_new_fill = any(
                (
                    fill.side == "buy" and mid < fill.price
                )
                or (
                    fill.side == "sell" and mid > fill.price
                )
                for fill in new_fills
            )
            toxic_signal = bool(new_fills) and (
                adverse_new_fill
                if self.defensive_overlay.get(
                    "toxic_requires_adverse_fill", False
                )
                else True
            )
            if toxic_signal:
                toxic_fill_streak += 1
            elif new_fills:
                toxic_fill_streak = 0
            toxic_streak_required = max(
                1,
                int(
                    self.defensive_overlay.get(
                        "toxic_fill_streak_required", 1
                    )
                ),
            )
            if (
                toxic_signal
                and toxic_fill_streak >= toxic_streak_required
                and self.defensive_overlay.get("toxic_pause_ticks", 0)
            ):
                defensive_pause_remaining = max(
                    defensive_pause_remaining,
                    int(self.defensive_overlay["toxic_pause_ticks"]),
                )
                emit_defensive({
                    "event": "TOXIC_FLOW_PAUSE_ENTRY", "tick": tick_index,
                    "trigger_fill_ids": [fill.fill_id for fill in new_fills],
                    "pause_ticks": defensive_pause_remaining,
                    "toxic_fill_streak": toxic_fill_streak,
                    "toxic_fill_streak_required": toxic_streak_required,
                    "adverse_fill_observed": adverse_new_fill,
                }, phase="DEFENSIVE_ACTIVATION")
                toxic_fill_streak = 0

            unrealized = tracker.compute_unrealized_pnl(mid) if mid > 0 else 0.0
            equity = self.initial_capital + tracker.realized_pnl + unrealized
            peak_equity = max(peak_equity, equity)
            drawdown = (
                max(0.0, (peak_equity - equity) / peak_equity)
                if peak_equity > 0 else 1.0
            )
            result.maximum_drawdown = max(result.maximum_drawdown, drawdown)
            result.equity_curve.append(equity)
            result.inventory_curve.append(tracker.position)
            for record in new_fill_records:
                record["pnl_before_fill_usdt"] = (
                    equity_before_tick - self.initial_capital
                )
                record["pnl_after_fill_usdt"] = equity - self.initial_capital
                record["drawdown_after_fill"] = drawdown
            if abs(tracker.position) > profile.maximum_inventory_btc + 1e-12:
                result.inventory_breaches += 1

            active_exposures = [
                OrderExposure(
                    side=order.side,
                    price_usdt_per_btc=order.price,
                    quantity_btc=order.size,
                    order_id=order.order_id,
                    reduce_only=order.reduce_only,
                )
                for order in engine.pending_orders
            ]
            try:
                current_margin = margin_components(
                    inventory_btc=tracker.position,
                    mid_price_usdt_per_btc=mid,
                    current_equity_usdt=equity,
                    leverage=self.leverage,
                    active_orders=active_exposures,
                    maker_fee_rate=profile.maker_fee_rate,
                )
                margin_utilization = current_margin.margin_utilization
            except ValueError:
                result.unknown_margin_states += 1
                margin_utilization = math.inf
                current_margin = None
            if (
                margin_utilization > profile.maximum_margin_utilization + 1e-12
                and engine.pending_count
            ):
                engine.cancel_all(
                    reason=CancellationReason.CANCEL_MARGIN_PROTECTION,
                    source_component="MarketMakerBacktestRunner",
                    source_event="active_order_margin_release",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version=STRATEGY_VERSION,
                    profile_fingerprint=profile.fingerprint,
                    protocol_id=self.protocol_id,
                )
                result.margin_events.append({
                    "tick": tick_index,
                    "event": "ACTIVE_RESERVE_RELEASE_REQUESTED",
                    "pre_release_utilization": margin_utilization,
                })
                active_exposures = [
                    OrderExposure(
                        side=order.side,
                        price_usdt_per_btc=order.price,
                        quantity_btc=order.size,
                        order_id=order.order_id,
                        reduce_only=order.reduce_only,
                    )
                    for order in engine.pending_orders
                ]
                current_margin = margin_components(
                    inventory_btc=tracker.position,
                    mid_price_usdt_per_btc=mid,
                    current_equity_usdt=equity,
                    leverage=self.leverage,
                    active_orders=active_exposures,
                    maker_fee_rate=profile.maker_fee_rate,
                )
                margin_utilization = current_margin.margin_utilization
            result.maximum_margin_utilization = max(
                result.maximum_margin_utilization, margin_utilization
            )
            if margin_utilization > profile.maximum_margin_utilization + 1e-12:
                result.margin_breaches += 1

            drawdown_guard_threshold = self.defensive_overlay.get(
                "drawdown_guard_pct"
            )
            if drawdown_guard_latched:
                result.no_quote_decisions += 1
                result.quote_decisions.append({
                    "tick": tick_index,
                    "quote_allowed": False,
                    "quote_reason": "NO_QUOTE",
                    "suppression_reason": "DRAWDOWN_GUARD_LATCHED",
                    "current_equity_usdt": equity,
                    "current_inventory_btc": tracker.position,
                    "bid_admitted": False,
                    "ask_admitted": False,
                    "activated_order_ids": [],
                    "margin_admission": [],
                })
                emit_defensive({
                    "event": "DRAWDOWN_GUARD_TICK",
                    "tick": tick_index,
                    "drawdown": drawdown,
                    "threshold": float(drawdown_guard_threshold),
                }, phase="CAPITAL_PRESERVATION")
                continue
            if (
                drawdown_guard_threshold is not None
                and drawdown >= float(drawdown_guard_threshold)
                and drawdown < profile.hard_kill_drawdown_pct
            ):
                drawdown_guard_latched = True
                result.no_quote_decisions += 1
                result.quote_decisions.append({
                    "tick": tick_index,
                    "quote_allowed": False,
                    "quote_reason": "NO_QUOTE",
                    "suppression_reason": "DRAWDOWN_GUARD_TRIGGERED",
                    "current_equity_usdt": equity,
                    "current_inventory_btc": tracker.position,
                    "bid_admitted": False,
                    "ask_admitted": False,
                    "activated_order_ids": [],
                    "margin_admission": [],
                })
                cancelled_order_ids = engine.cancel_all(
                    reason=CancellationReason.CANCEL_RISK_SOFT_STOP,
                    source_component="MarketMakerBacktestRunner",
                    source_event="drawdown_guard",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version=STRATEGY_VERSION,
                    profile_fingerprint=profile.fingerprint,
                    protocol_id=self.protocol_id,
                )
                inventory_before_flatten = tracker.position
                flatten = engine.execute_market_flatten(
                    tick,
                    tracker,
                    slippage_bps=float(
                        self.defensive_overlay.get(
                            "drawdown_guard_slippage_bps",
                            profile.terminal_slippage_bps * 2,
                        )
                    ),
                    reason="mm-emergency",
                )
                if flatten is not None:
                    post_guard_equity = (
                        self.initial_capital + tracker.realized_pnl
                    )
                    post_guard_drawdown = (
                        max(
                            0.0,
                            (peak_equity - post_guard_equity) / peak_equity,
                        )
                        if peak_equity > 0 else 1.0
                    )
                    result.maximum_drawdown = max(
                        result.maximum_drawdown, post_guard_drawdown
                    )
                    result.equity_curve[-1] = post_guard_equity
                    result.inventory_curve[-1] = tracker.position
                    result.fills.append(_fill_record(
                        flatten,
                        tick_index,
                        self.scenario,
                        self.source_block,
                        decision_mid=mid,
                        quote_created_tick=tick_index,
                        activation_tick=tick_index,
                        inventory_before=inventory_before_flatten,
                        inventory_after=tracker.position,
                        inventory_at_creation=inventory_before_flatten,
                        volatility_at_creation_bps=causal_move_bps,
                        volatility_at_fill_bps=causal_move_bps,
                        defensive_mode_at_creation="DRAWDOWN_GUARD",
                        defensive_mode_at_fill="DRAWDOWN_GUARD",
                        pnl_before_fill_usdt=equity - self.initial_capital,
                        pnl_after_fill_usdt=tracker.realized_pnl,
                        drawdown_after_fill=post_guard_drawdown,
                        event_phase="EMERGENCY_EXECUTION",
                        event_sequence=(
                            tick_index * 10_000
                            + phase_order["EMERGENCY_EXECUTION"] * 100
                            + 1
                        ),
                    ))
                    drawdown = post_guard_drawdown
                emit_defensive({
                    "event": "DRAWDOWN_GUARD_ENTRY",
                    "tick": tick_index,
                    "drawdown": drawdown,
                    "threshold": float(drawdown_guard_threshold),
                    "cancelled_order_ids": cancelled_order_ids,
                    "inventory_before_flatten": inventory_before_flatten,
                    "inventory_after_flatten": tracker.position,
                    "emergency_fill_id": (
                        flatten.fill_id if flatten is not None else None
                    ),
                }, phase="CAPITAL_PRESERVATION")
                continue

            if drawdown >= profile.hard_kill_drawdown_pct:
                if not hard_kill_latched:
                    result.hard_kills += 1
                    hard_kill_latched = True
                result.no_quote_decisions += 1
                result.quote_decisions.append({
                    "tick": tick_index,
                    "quote_allowed": False,
                    "quote_reason": "NO_QUOTE",
                    "suppression_reason": "HARD_KILL_LATCHED",
                    "current_equity_usdt": equity,
                    "current_inventory_btc": tracker.position,
                    "bid_admitted": False,
                    "ask_admitted": False,
                    "margin_admission": [],
                })
                engine.cancel_all(
                    reason=CancellationReason.CANCEL_RISK_HARD_KILL,
                    source_component="MarketMakerBacktestRunner",
                    source_event="hard_kill",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version=STRATEGY_VERSION,
                    profile_fingerprint=profile.fingerprint,
                    protocol_id=self.protocol_id,
                )
                inventory_before_flatten = tracker.position
                flatten = engine.execute_market_flatten(
                    tick,
                    tracker,
                    slippage_bps=profile.terminal_slippage_bps * 2,
                    reason="mm-hard-kill",
                )
                if flatten is not None:
                    result.fills.append(_fill_record(
                        flatten,
                        tick_index,
                        self.scenario,
                        self.source_block,
                        decision_mid=mid,
                        quote_created_tick=tick_index,
                        activation_tick=tick_index,
                        inventory_before=inventory_before_flatten,
                        inventory_after=tracker.position,
                        inventory_at_creation=inventory_before_flatten,
                        volatility_at_creation_bps=causal_move_bps,
                        volatility_at_fill_bps=causal_move_bps,
                        defensive_mode_at_creation="HARD_KILL",
                        defensive_mode_at_fill="HARD_KILL",
                        pnl_before_fill_usdt=(
                            equity - self.initial_capital
                        ),
                        pnl_after_fill_usdt=(
                            self.initial_capital + tracker.realized_pnl
                            - self.initial_capital
                        ),
                        drawdown_after_fill=drawdown,
                        event_phase="HARD_KILL",
                        event_sequence=(
                            tick_index * 10_000
                            + phase_order["HARD_KILL"] * 100
                            + 1
                        ),
                    ))
                continue

            shock_threshold = float(
                self.defensive_overlay.get("shock_threshold_bps", math.inf)
            )
            shock_active = causal_move_bps >= shock_threshold
            fast_cancel_exit_threshold = self.defensive_overlay.get(
                "fast_cancel_exit_threshold_bps"
            )
            if (
                fast_cancel_latched
                and fast_cancel_exit_threshold is not None
                and causal_move_bps
                <= float(fast_cancel_exit_threshold)
            ):
                fast_cancel_latched = False
                emit_defensive({
                    "event": "DEFENSIVE_MODE_EXIT",
                    "mode": "FAST_CANCEL_ON_VOLATILITY",
                    "tick": tick_index,
                    "shock_value_bps": causal_move_bps,
                    "exit_threshold_bps": float(
                        fast_cancel_exit_threshold
                    ),
                }, phase="DEFENSIVE_EXIT")
                reentry_count += 1
                emit_defensive({
                    "event": "DEFENSIVE_REENTRY_READY",
                    "mode": "FAST_CANCEL_ON_VOLATILITY",
                    "tick": tick_index + 1,
                    "reentry_count": reentry_count,
                }, phase="REENTRY_READY")
            if fast_cancel_cooldown_remaining > 0:
                fast_cancel_cooldown_remaining -= 1
            fast_cancel_can_activate = (
                shock_active
                and self.defensive_overlay.get("fast_cancel", False)
                and not fast_cancel_latched
                and fast_cancel_cooldown_remaining == 0
            )
            if fast_cancel_can_activate:
                cancelled_order_ids: list[str] = []
                fast_cancel_scope = self.defensive_overlay.get(
                    "fast_cancel_scope", "all"
                )
                if fast_cancel_scope == "adverse_side":
                    adverse_side = "sell" if mid >= prior_mid else "buy"
                    for pending_order in tuple(engine.pending_orders):
                        if pending_order.side != adverse_side:
                            continue
                        if engine.cancel_order(
                            pending_order.order_id,
                            reason=CancellationReason.CANCEL_VOLATILITY_LIMIT,
                            source_component="MarketMakerBacktestRunner",
                            source_event="defensive_fast_cancel_adverse_side",
                            timestamp_ms=int(tick.get("timestamp", 0)),
                            strategy_version=STRATEGY_VERSION,
                            profile_fingerprint=profile.fingerprint,
                            protocol_id=self.protocol_id,
                        ):
                            cancelled_order_ids.append(
                                pending_order.order_id
                            )
                elif fast_cancel_scope == "all":
                    cancelled_order_ids.extend(engine.cancel_all(
                        reason=CancellationReason.CANCEL_VOLATILITY_LIMIT,
                        source_component="MarketMakerBacktestRunner",
                        source_event="defensive_fast_cancel",
                        timestamp_ms=int(tick.get("timestamp", 0)),
                        strategy_version=STRATEGY_VERSION,
                        profile_fingerprint=profile.fingerprint,
                        protocol_id=self.protocol_id,
                    ))
                else:
                    raise ValueError("unknown fast-cancel scope")
                if fast_cancel_exit_threshold is not None:
                    fast_cancel_latched = True
                fast_cancel_cooldown_remaining = max(
                    0,
                    int(
                        self.defensive_overlay.get(
                            "fast_cancel_cooldown_ticks", 0
                        )
                    ),
                )
                emit_defensive({
                    "event": "FAST_CANCEL_ON_VOLATILITY", "tick": tick_index,
                    "shock_value_bps": causal_move_bps,
                    "shock_threshold_bps": shock_threshold,
                    "cancel_scope": fast_cancel_scope,
                    "cancelled_order_ids": cancelled_order_ids,
                    "cooldown_ticks": fast_cancel_cooldown_remaining,
                }, phase="DEFENSIVE_ACTIVATION")
            if defensive_pause_remaining > 0:
                engine.cancel_all(
                    reason=CancellationReason.CANCEL_VOLATILITY_LIMIT,
                    source_component="MarketMakerBacktestRunner",
                    source_event="defensive_pause",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version=STRATEGY_VERSION,
                    profile_fingerprint=profile.fingerprint,
                    protocol_id=self.protocol_id,
                )
                result.no_quote_decisions += 1
                result.quote_decisions.append({
                    "tick": tick_index, "quote_allowed": False,
                    "quote_reason": "NO_QUOTE",
                    "suppression_reason": "TOXIC_FLOW_PAUSE",
                    "current_equity_usdt": equity,
                    "current_inventory_btc": tracker.position,
                    "bid_admitted": False, "ask_admitted": False,
                    "activated_order_ids": [], "margin_admission": [],
                })
                emit_defensive({
                    "event": "TOXIC_FLOW_PAUSE_TICK", "tick": tick_index,
                    "remaining_before": defensive_pause_remaining,
                }, phase="DEFENSIVE_MAINTENANCE")
                defensive_pause_remaining -= 1
                if defensive_pause_remaining == 0:
                    reentry_count += 1
                    emit_defensive({
                        "event": "DEFENSIVE_REENTRY_READY",
                        "tick": tick_index + 1, "reentry_count": reentry_count,
                    }, phase="REENTRY_READY")
                continue

            stale = bool(tick.get("stale", False))
            decision = strategy.decide(
                tick,
                inventory_btc=tracker.position,
                market_data_age_ms=2_000 if stale else 0,
            )
            if decision is None:
                result.no_quote_decisions += 1
                result.quote_decisions.append({
                    "tick": tick_index,
                    "quote_allowed": False,
                    "quote_reason": "NO_QUOTE",
                    "suppression_reason": (
                        "STALE_OR_INVALID_MARKET"
                        if stale or not bids or not asks
                        else "VOLATILITY_WARMUP"
                    ),
                    "current_equity_usdt": equity,
                    "current_inventory_btc": tracker.position,
                    "bid_admitted": False,
                    "ask_admitted": False,
                    "activated_order_ids": [],
                    "margin_admission": [],
                })
                if engine.pending_count:
                    engine.cancel_all(
                        reason=(
                            CancellationReason.CANCEL_MARKET_DATA_STALE
                            if stale or not bids or not asks
                            else CancellationReason.CANCEL_VOLATILITY_LIMIT
                        ),
                        source_component="MarketMakerBacktestRunner",
                        source_event="no_quote",
                        timestamp_ms=int(tick.get("timestamp", 0)),
                        strategy_version=STRATEGY_VERSION,
                        profile_fingerprint=profile.fingerprint,
                        protocol_id=self.protocol_id,
                    )
                continue

            result.quote_eligible_ticks += 1
            desired = {
                "buy": (
                    decision.rounded_bid,
                    decision.bid_size_btc,
                    decision.bid_suppressed,
                ),
                "sell": (
                    decision.rounded_ask,
                    decision.ask_size_btc,
                    decision.ask_suppressed,
                ),
            }
            spread_multiplier = 1.0
            active_defensive_modes: list[str] = []
            if self.defensive_overlay.get("volatility_spread_guard", False):
                spread_multiplier = min(
                    float(self.defensive_overlay.get("maximum_spread_multiplier", 2.5)),
                    1.0 + causal_move_bps
                    * float(self.defensive_overlay.get("spread_multiplier_per_bps", 0.15)),
                )
                if spread_multiplier > 1.0:
                    for side, (price, size, suppressed) in list(desired.items()):
                        distance = abs(price - mid) * spread_multiplier
                        guarded = mid - distance if side == "buy" else mid + distance
                        desired[side] = (guarded, size, suppressed)
                    emit_defensive({
                        "event": "VOLATILITY_SPREAD_GUARD", "tick": tick_index,
                        "shock_value_bps": causal_move_bps,
                        "spread_multiplier": spread_multiplier,
                    }, phase="DEFENSIVE_ACTIVATION")
                    active_defensive_modes.append("VOLATILITY_SPREAD_GUARD")
            defensive_condition = shock_active or bool(new_fills)
            if (
                self.defensive_overlay.get("inventory_reduction_priority", False)
                and defensive_condition and abs(tracker.position) > 1e-12
            ):
                increasing = "buy" if tracker.position > 0 else "sell"
                price, size, _ = desired[increasing]
                desired[increasing] = (price, size, True)
                emit_defensive({
                    "event": "INVENTORY_REDUCTION_PRIORITY", "tick": tick_index,
                    "inventory_btc": tracker.position,
                    "suppressed_side": increasing,
                    "reducing_side": "sell" if increasing == "buy" else "buy",
                }, phase="DEFENSIVE_ACTIVATION")
                active_defensive_modes.append("INVENTORY_REDUCTION_PRIORITY")
            one_sided_enabled = self.defensive_overlay.get(
                "one_sided_defensive", False
            )
            one_sided_max_ticks = self.defensive_overlay.get(
                "one_sided_max_ticks"
            )
            one_sided_active = False
            if one_sided_enabled and one_sided_max_ticks is not None:
                if one_sided_cooldown_remaining > 0:
                    one_sided_cooldown_remaining -= 1
                if (
                    not one_sided_episode_active
                    and one_sided_cooldown_remaining == 0
                    and defensive_condition
                ):
                    one_sided_episode_active = True
                    one_sided_remaining = max(
                        1, int(one_sided_max_ticks)
                    )
                    one_sided_confirmation_streak = 0
                    one_sided_entry_direction = (
                        1 if signed_causal_move_bps >= 0 else -1
                    )
                    one_sided_suppressed_side = (
                        "sell" if mid >= prior_mid else "buy"
                    )
                    emit_defensive({
                        "event": "ONE_SIDED_DEFENSIVE_ENTRY",
                        "tick": tick_index,
                        "suppressed_side": one_sided_suppressed_side,
                        "maximum_episode_ticks": one_sided_remaining,
                    }, phase="DEFENSIVE_ACTIVATION")
                one_sided_active = one_sided_episode_active
            elif one_sided_enabled:
                one_sided_active = defensive_condition
                one_sided_suppressed_side = (
                    "sell" if mid >= prior_mid else "buy"
                )
            if one_sided_active:
                suppressed_side = str(one_sided_suppressed_side)
                price, size, _ = desired[suppressed_side]
                desired[suppressed_side] = (price, size, True)
                emit_defensive({
                    "event": "ONE_SIDED_DEFENSIVE_MODE", "tick": tick_index,
                    "selected_side": (
                        "buy" if suppressed_side == "sell" else "sell"
                    ),
                    "suppressed_side": suppressed_side,
                    "remaining_episode_ticks": one_sided_remaining,
                }, phase=(
                    "DEFENSIVE_MAINTENANCE"
                    if one_sided_max_ticks is not None
                    else "DEFENSIVE_ACTIVATION"
                ))
                active_defensive_modes.append("ONE_SIDED_DEFENSIVE_MODE")
                if one_sided_max_ticks is not None:
                    one_sided_remaining = max(
                        0, one_sided_remaining - 1
                    )
                    hold_until_confirmed = bool(
                        self.defensive_overlay.get(
                            "one_sided_hold_until_reentry_confirmation",
                            False,
                        )
                    )
                    confirmation_signal = (
                        causal_move_bps
                        <= float(
                            self.defensive_overlay.get(
                                "one_sided_reentry_threshold_bps",
                                self.defensive_overlay.get(
                                    "shock_threshold_bps", math.inf
                                ),
                            )
                        )
                        or (
                            one_sided_entry_direction != 0
                            and signed_causal_move_bps
                            * one_sided_entry_direction < 0
                        )
                    )
                    if (
                        one_sided_remaining == 0
                        and hold_until_confirmed
                    ):
                        one_sided_confirmation_streak = (
                            one_sided_confirmation_streak + 1
                            if confirmation_signal else 0
                        )
                        emit_defensive({
                            "event": "ONE_SIDED_REENTRY_CONFIRMATION",
                            "tick": tick_index,
                            "confirmation_signal": confirmation_signal,
                            "confirmation_streak": (
                                one_sided_confirmation_streak
                            ),
                            "required_streak": max(
                                1,
                                int(
                                    self.defensive_overlay.get(
                                        "one_sided_reentry_confirmation_ticks",
                                        1,
                                    )
                                ),
                            ),
                            "signed_causal_move_bps": (
                                signed_causal_move_bps
                            ),
                        }, phase="DEFENSIVE_MAINTENANCE")
                    exit_ready = (
                        one_sided_remaining == 0
                        and (
                            not hold_until_confirmed
                            or one_sided_confirmation_streak >= max(
                                1,
                                int(
                                    self.defensive_overlay.get(
                                        "one_sided_reentry_confirmation_ticks",
                                        1,
                                    )
                                ),
                            )
                        )
                    )
                    if exit_ready:
                        one_sided_cooldown_remaining = max(
                            1,
                            int(
                                self.defensive_overlay.get(
                                    "one_sided_cooldown_ticks", 2
                                )
                            ),
                        )
                        emit_defensive({
                            "event": "DEFENSIVE_MODE_EXIT",
                            "mode": "ONE_SIDED_DEFENSIVE_MODE",
                            "tick": tick_index,
                            "suppressed_side": suppressed_side,
                        }, phase="DEFENSIVE_EXIT")
                        one_sided_episode_active = False
                        reentry_count += 1
                        emit_defensive({
                            "event": "DEFENSIVE_REENTRY_READY",
                            "mode": "ONE_SIDED_DEFENSIVE_MODE",
                            "tick": tick_index + 1,
                            "reentry_count": reentry_count,
                        }, phase="REENTRY_READY")
                        inventory_aware_reentry_active = (
                            bool(
                                self.defensive_overlay.get(
                                    "inventory_aware_reentry", False
                                )
                            )
                            and abs(tracker.position) > 1e-12
                        )
                        one_sided_suppressed_side = None
                        one_sided_confirmation_streak = 0
                        one_sided_entry_direction = 0
            if inventory_aware_reentry_active and not one_sided_active:
                if abs(tracker.position) <= 1e-12:
                    inventory_aware_reentry_active = False
                    emit_defensive({
                        "event": "INVENTORY_AWARE_REENTRY_COMPLETE",
                        "tick": tick_index,
                    }, phase="DEFENSIVE_EXIT")
                else:
                    increasing = "buy" if tracker.position > 0 else "sell"
                    price, size, _ = desired[increasing]
                    desired[increasing] = (price, size, True)
                    emit_defensive({
                        "event": "INVENTORY_AWARE_REENTRY",
                        "tick": tick_index,
                        "inventory_btc": tracker.position,
                        "suppressed_side": increasing,
                        "reducing_side": (
                            "sell" if increasing == "buy" else "buy"
                        ),
                    }, phase="DEFENSIVE_MAINTENANCE")
                    active_defensive_modes.append(
                        "INVENTORY_AWARE_REENTRY"
                    )
            for side, (price, size, suppressed) in desired.items():
                pending = next(
                    (order for order in engine.pending_orders if order.side == side),
                    None,
                )
                if pending is not None:
                    age = tick_index - order_placed_tick.get(
                        pending.order_id, tick_index
                    )
                    price_ticks = (
                        abs(pending.price - price) / profile.tick_size_usdt
                    )
                    size_ratio = (
                        abs(pending.size - size) / pending.size
                        if pending.size > 0 else math.inf
                    )
                    reason = None
                    source_event = ""
                    if suppressed:
                        reason = CancellationReason.CANCEL_INVENTORY_LIMIT
                        source_event = "inventory_side_suppression"
                    elif age >= profile.maximum_order_age_ticks:
                        reason = CancellationReason.CANCEL_MAXIMUM_ORDER_AGE
                        source_event = "maximum_order_age"
                    elif (
                        age >= profile.minimum_order_lifetime_ticks
                        and price_ticks >= profile.requote_threshold_ticks
                    ):
                        reason = CancellationReason.CANCEL_REQUOTE_PRICE_CHANGE
                        source_event = "requote_price_change"
                    elif (
                        age >= profile.minimum_order_lifetime_ticks
                        and size_ratio >= profile.minimum_size_change_ratio
                    ):
                        reason = CancellationReason.CANCEL_REQUOTE_SIZE_CHANGE
                        source_event = "requote_size_change"
                    if reason is not None:
                        if engine.cancel_order(
                            pending.order_id,
                            reason=reason,
                            source_component="MarketMakerBacktestRunner",
                            source_event=source_event,
                            timestamp_ms=int(tick.get("timestamp", 0)),
                            strategy_version=STRATEGY_VERSION,
                            profile_fingerprint=profile.fingerprint,
                            protocol_id=self.protocol_id,
                        ):
                            result.requotes += 1

            active_exposures = [
                OrderExposure(
                    side=order.side,
                    price_usdt_per_btc=order.price,
                    quantity_btc=order.size,
                    order_id=order.order_id,
                    reduce_only=order.reduce_only,
                )
                for order in engine.pending_orders
            ]
            proposals = [
                OrderExposure(
                    side=side,
                    price_usdt_per_btc=price,
                    quantity_btc=size,
                )
                for side, (price, size, suppressed) in desired.items()
                if (
                    not suppressed
                    and size >= profile.fixed_lot_size_btc - 1e-12
                    and not any(
                        order.side == side for order in engine.pending_orders
                    )
                )
            ]
            admissions = admit_proposed_orders(
                inventory_btc=tracker.position,
                mid_price_usdt_per_btc=mid,
                current_equity_usdt=equity,
                leverage=self.leverage,
                maximum_margin_utilization=profile.maximum_margin_utilization,
                active_orders=active_exposures,
                proposed_orders=proposals,
                maker_fee_rate=profile.maker_fee_rate,
            )
            activated_order_ids: list[str] = []
            for admission in admissions:
                event = {
                    "tick": tick_index,
                    "candidate_side": admission.side,
                    "projected_utilization": admission.projected_utilization,
                    "inventory_impact": admission.inventory_impact,
                    "decision": "ADMIT" if admission.allowed else "SUPPRESS",
                    "reason": admission.reason,
                    "components": admission.components.to_dict(),
                }
                result.margin_events.append(event)
                if not admission.allowed:
                    continue
                proposal = next(
                    item for item in proposals if item.side == admission.side
                )
                order_id = engine.place_order(
                    proposal.side,
                    proposal.price_usdt_per_btc,
                    proposal.quantity_btc,
                )
                if order_id:
                    order_placed_tick[order_id] = tick_index
                    order_decision_mid[order_id] = mid
                    order_inventory_at_creation[order_id] = tracker.position
                    order_volatility_at_creation[order_id] = causal_move_bps
                    order_defensive_state[order_id] = (
                        "+".join(sorted(active_defensive_modes))
                        if active_defensive_modes else "NORMAL"
                    )
                    activated_order_ids.append(order_id)
                    if proposal.side == "buy":
                        result.bid_orders_created += 1
                    else:
                        result.ask_orders_created += 1

            active_sides = {order.side for order in engine.pending_orders}
            enabled_sides = len(active_sides)
            if enabled_sides == 2:
                result.two_sided_quote_decisions += 1
            elif enabled_sides == 1:
                result.one_sided_quote_decisions += 1
            else:
                result.no_quote_decisions += 1
            decision_record = asdict(decision)
            decision_record.update({
                "tick": tick_index,
                "current_equity_usdt": equity,
                "current_inventory_btc": tracker.position,
                "current_margin_components": (
                    current_margin.to_dict() if current_margin is not None else None
                ),
                "bid_admitted": "buy" in active_sides,
                "ask_admitted": "sell" in active_sides,
                "activated_order_ids": activated_order_ids,
            })
            decision_record["margin_admission"] = [
                {
                    "side": item.side,
                    "allowed": item.allowed,
                    "reason": item.reason,
                    "projected_utilization": item.projected_utilization,
                    "inventory_impact": item.inventory_impact,
                    "components": item.components.to_dict(),
                }
                for item in admissions
            ]
            result.quote_decisions.append(decision_record)

        engine.cancel_all(
            reason=CancellationReason.CANCEL_TERMINAL_CLEANUP,
            source_component="MarketMakerBacktestRunner",
            source_event="terminal_finalize",
            timestamp_ms=int(last_tick.get("timestamp", 0)) if last_tick else 0,
            strategy_version=STRATEGY_VERSION,
            profile_fingerprint=profile.fingerprint,
            protocol_id=self.protocol_id,
        )
        if last_tick is not None and abs(tracker.position) > 1e-12:
            inventory_before_terminal = tracker.position
            terminal = engine.execute_market_flatten(
                last_tick,
                tracker,
                slippage_bps=profile.terminal_slippage_bps,
                reason="mm-terminal",
            )
            if terminal is not None:
                result.fills.append(_fill_record(
                    terminal,
                    len(ticks),
                    self.scenario,
                    self.source_block,
                    decision_mid=(
                        (
                            float(last_tick["bids"][0][0])
                            + float(last_tick["asks"][0][0])
                        ) / 2.0
                    ),
                    quote_created_tick=len(ticks),
                    activation_tick=len(ticks),
                    inventory_before=inventory_before_terminal,
                    inventory_after=tracker.position,
                    inventory_at_creation=inventory_before_terminal,
                    volatility_at_creation_bps=0.0,
                    volatility_at_fill_bps=0.0,
                    defensive_mode_at_creation="TERMINAL",
                    defensive_mode_at_fill="TERMINAL",
                    pnl_before_fill_usdt=(
                        result.equity_curve[-1] - self.initial_capital
                    ),
                    pnl_after_fill_usdt=tracker.realized_pnl,
                    drawdown_after_fill=result.maximum_drawdown,
                    event_phase="TERMINAL_CLEANUP",
                    event_sequence=(
                        len(ticks) * 10_000
                        + phase_order["TERMINAL_CLEANUP"] * 100
                        + 1
                    ),
                ))
        result.terminal_residual_inventory_btc = abs(tracker.position)
        result.order_events = list(engine.lifecycle_events)
        result.order_reconciliation = engine.reconciliation()
        for event in result.order_events:
            if event.get("event") == "strict_trade_through":
                if event.get("side") == "buy":
                    result.strict_bid_fills += 1
                else:
                    result.strict_ask_fills += 1
            if event.get("event") == "touch_without_trade_through":
                if event.get("side") == "buy":
                    result.touch_only_bid_events += 1
                else:
                    result.touch_only_ask_events += 1

        result.round_trips = _round_trips(result.fills)
        _attach_markouts(result.fills, mids)
        result.economics = _economics(
            result,
            initial_capital=self.initial_capital,
            total_fees=engine.total_fees,
            gross_realized=tracker.gross_realized_pnl,
            net_realized=tracker.realized_pnl,
            terminal_fees=engine.flatten_fees,
            terminal_slippage=engine.flatten_slippage,
        )
        return result


def _fill_record(
    fill: Fill,
    tick_index: int,
    scenario: str,
    source_block: str,
    *,
    decision_mid: float | None = None,
    quote_created_tick: int | None = None,
    activation_tick: int | None = None,
    inventory_before: float | None = None,
    inventory_after: float | None = None,
    inventory_at_creation: float | None = None,
    volatility_at_creation_bps: float | None = None,
    volatility_at_fill_bps: float | None = None,
    defensive_mode_at_creation: str | None = None,
    defensive_mode_at_fill: str | None = None,
    pnl_before_fill_usdt: float | None = None,
    pnl_after_fill_usdt: float | None = None,
    drawdown_after_fill: float | None = None,
    event_phase: str | None = None,
    event_sequence: int | None = None,
) -> dict[str, Any]:
    if fill.classification is None:
        raise ValueError("canonical fill classification must exist at creation")
    record = {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "side": fill.side,
        "price": fill.price,
        "size": fill.size,
        "fee": fill.fee,
        "liquidity": fill.liquidity,
        "timestamp": fill.timestamp,
        "reason": fill.reason,
        "tick": tick_index,
        "scenario": scenario,
        "source_block": source_block,
        **fill.classification.to_dict(),
    }
    if decision_mid is not None:
        record["decision_mid"] = float(decision_mid)
    optional = {
        "quote_created_tick": quote_created_tick,
        "activation_tick": activation_tick,
        "inventory_before": inventory_before,
        "inventory_after": inventory_after,
        "inventory_at_creation": inventory_at_creation,
        "volatility_at_creation_bps": volatility_at_creation_bps,
        "volatility_at_fill_bps": volatility_at_fill_bps,
        "defensive_mode_at_creation": defensive_mode_at_creation,
        "defensive_mode_at_fill": defensive_mode_at_fill,
        "pnl_before_fill_usdt": pnl_before_fill_usdt,
        "pnl_after_fill_usdt": pnl_after_fill_usdt,
        "drawdown_after_fill": drawdown_after_fill,
        "event_phase": event_phase,
        "event_sequence": event_sequence,
    }
    record.update({
        key: value for key, value in optional.items() if value is not None
    })
    return record


def _attach_markouts(fills: list[dict[str, Any]], mids: list[float]) -> None:
    for fill in fills:
        fill_index = max(0, int(fill["tick"]) - 1)
        mid_at_fill = float(mids[fill_index]) if mids else 0.0
        fill["mid_at_fill_usdt_per_btc"] = mid_at_fill
        fill["quote_distance_bps"] = (
            abs(float(fill["price"]) - mid_at_fill) / mid_at_fill * 10_000.0
            if mid_at_fill > 0 else 0.0
        )
        for horizon in (1, 5, 10):
            future_index = min(len(mids) - 1, fill_index + horizon)
            markout = (
                favorable_markout(
                    side=fill["side"],
                    fill_price=float(fill["price"]),
                    future_mid=float(mids[future_index]),
                )
                if mids and future_index >= 0 else 0.0
            )
            fill[f"markout_{horizon}_tick_usdt_per_btc"] = markout


def _round_trips(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_lots: list[dict[str, Any]] = []
    trips: list[dict[str, Any]] = []
    for fill in fills:
        incoming_quantity = float(fill["size"])
        incoming_fee = float(fill["fee"])
        while (
            incoming_quantity > 1e-12
            and open_lots
            and open_lots[0]["fill"]["side"] != fill["side"]
        ):
            lot = open_lots[0]
            matched = min(float(lot["remaining"]), incoming_quantity)
            entry_fee = float(lot["fee_remaining"]) * matched / float(
                lot["remaining"]
            )
            exit_fee = incoming_fee * matched / incoming_quantity
            entry = lot["fill"]
            if entry["side"] == "buy":
                gross = (
                    float(fill["price"]) - float(entry["price"])
                ) * matched
                spread = (
                    (
                        float(entry.get("decision_mid", entry["price"]))
                        - float(entry["price"])
                    )
                    + (
                        float(fill["price"])
                        - float(fill.get("decision_mid", fill["price"]))
                    )
                ) * matched
            else:
                gross = (
                    float(entry["price"]) - float(fill["price"])
                ) * matched
                spread = (
                    (
                        float(entry["price"])
                        - float(entry.get("decision_mid", entry["price"]))
                    )
                    + (
                        float(fill.get("decision_mid", fill["price"]))
                        - float(fill["price"])
                    )
                ) * matched
            inventory_pnl = gross - spread
            # Retain the v1.1 compatibility field for consumers that supplied
            # no decision-mid context.  The v1.2 canonical fields below never
            # infer effective spread from execution PnL.
            legacy_spread = (
                spread
                if "decision_mid" in entry and "decision_mid" in fill
                else gross
            )
            trips.append({
                "round_trip_id": f"mm-round-trip-{len(trips) + 1:06d}",
                "entry_fill_id": entry.get("fill_id"),
                "exit_fill_id": fill.get("fill_id"),
                "entry_side": entry["side"],
                "matched_quantity_btc": matched,
                "entry_price": float(entry["price"]),
                "exit_price": float(fill["price"]),
                "entry_fee_usdt": entry_fee,
                "exit_fee_usdt": exit_fee,
                "entry_tick": entry["tick"],
                "exit_tick": fill["tick"],
                "holding_duration_ticks": (
                    int(fill["tick"]) - int(entry["tick"])
                ),
                "round_trip_notional_usdt": (
                    (
                        float(entry["price"]) + float(fill["price"])
                    ) * matched / 2.0
                ),
                "gross_spread_capture_usdt": legacy_spread,
                "gross_execution_pnl_usdt": gross,
                "gross_round_trip_spread_capture_usdt": spread,
                "realized_inventory_pnl_usdt": inventory_pnl,
                "attribution_basis": (
                    "DECISION_MID"
                    if "decision_mid" in entry and "decision_mid" in fill
                    else "DECISION_MID_UNAVAILABLE"
                ),
                "fees_usdt": entry_fee + exit_fee,
                "net_pnl_usdt": gross - entry_fee - exit_fee,
                "terminal_exit": fill.get("reason") == "mm-terminal",
                "hard_kill_exit": fill.get("reason") == "mm-hard-kill",
                "emergency_exit": fill.get("reason") == "mm-emergency",
            })
            lot["remaining"] = float(lot["remaining"]) - matched
            lot["fee_remaining"] = float(lot["fee_remaining"]) - entry_fee
            incoming_quantity -= matched
            incoming_fee -= exit_fee
            if float(lot["remaining"]) <= 1e-12:
                open_lots.pop(0)
        if incoming_quantity > 1e-12:
            open_lots.append({
                "fill": fill,
                "remaining": incoming_quantity,
                "fee_remaining": incoming_fee,
            })
    return trips


def _economics(
    result: MarketMakerRunResult,
    *,
    initial_capital: float,
    total_fees: float,
    gross_realized: float,
    net_realized: float,
    terminal_fees: float,
    terminal_slippage: float,
) -> dict[str, Any]:
    trip_pnls = [float(item["net_pnl_usdt"]) for item in result.round_trips]
    profits = sum(max(0.0, value) for value in trip_pnls)
    losses = sum(max(0.0, -value) for value in trip_pnls)
    profit_factor = (
        profits / losses if losses > 0 else 10.0 if profits > 0 else 0.0
    )
    maker_fills = [
        fill for fill in result.fills if fill["liquidity"] == "maker"
    ]
    markout_5 = [
        float(fill["markout_5_tick_usdt_per_btc"]) * float(fill["size"])
        for fill in maker_fills
    ]
    added_2bps = sum(
        float(fill["price"]) * float(fill["size"]) * 0.0002
        for fill in maker_fills
    )
    normal_trips = [
        trip for trip in result.round_trips
        if (
            not trip["terminal_exit"]
            and not trip["hard_kill_exit"]
            and not trip["emergency_exit"]
        )
    ]
    terminal_trips = [
        trip for trip in result.round_trips if trip["terminal_exit"]
    ]
    hard_kill_trips = [
        trip for trip in result.round_trips if trip["hard_kill_exit"]
    ]
    emergency_trips = [
        trip for trip in result.round_trips if trip["emergency_exit"]
    ]
    gross_execution = sum(
        float(item["gross_execution_pnl_usdt"]) for item in normal_trips
    )
    gross_spread = sum(
        float(item["gross_round_trip_spread_capture_usdt"])
        for item in normal_trips
    )
    inventory_pnl = sum(
        float(item["realized_inventory_pnl_usdt"]) for item in normal_trips
    )
    terminal_pnl = sum(
        float(item["gross_execution_pnl_usdt"]) for item in terminal_trips
    )
    hard_kill_pnl = sum(
        float(item["gross_execution_pnl_usdt"]) for item in hard_kill_trips
    )
    emergency_pnl = sum(
        float(item["gross_execution_pnl_usdt"]) for item in emergency_trips
    )
    net_pnl = net_realized
    pnl_identity = (
        gross_execution
        - total_fees
        + terminal_pnl
        + hard_kill_pnl
        + emergency_pnl
    )
    return {
        "gross_execution_pnl_usdt": gross_execution,
        "gross_spread_capture_usdt": gross_spread,
        "net_spread_capture_usdt": gross_spread - total_fees,
        "realized_inventory_pnl_usdt": inventory_pnl,
        "inventory_mark_to_market_usdt": 0.0,
        "maker_fees_usdt": sum(
            float(fill["fee"]) for fill in maker_fills
        ),
        "taker_fees_usdt": total_fees - sum(
            float(fill["fee"]) for fill in maker_fills
        ),
        "funding_usdt": 0.0,
        "adverse_selection_markout_loss_usdt": sum(
            max(0.0, -value) for value in markout_5
        ),
        "average_5_tick_markout_usdt": mean(markout_5) if markout_5 else 0.0,
        "terminal_liquidation_cost_usdt": terminal_fees + terminal_slippage,
        "terminal_liquidation_pnl_usdt": terminal_pnl,
        "hard_kill_execution_pnl_usdt": hard_kill_pnl,
        "emergency_execution_cost_usdt": sum(
            float(item["exit_fee_usdt"]) for item in emergency_trips
        ),
        "emergency_execution_pnl_usdt": emergency_pnl,
        "net_pnl_usdt": net_pnl,
        "pnl_identity_reconciles": math.isclose(
            net_pnl, pnl_identity, rel_tol=0.0, abs_tol=1e-7
        ),
        "spread_inventory_decomposition_reconciles": math.isclose(
            gross_execution,
            gross_spread + inventory_pnl,
            rel_tol=0.0,
            abs_tol=1e-7,
        ),
        "legacy_weighted_average_gross_realized_usdt": gross_realized,
        "average_net_spread_capture_per_round_trip_usdt": (
            mean(trip_pnls) if trip_pnls else 0.0
        ),
        "profit_factor": profit_factor,
        "net_pnl_after_added_2bps_usdt": net_pnl - added_2bps,
        "after_top_10pct_removal_usdt": top_profit_removal(trip_pnls),
        "top_10pct_profit_contribution_usdt": (
            sum(trip_pnls) - top_profit_removal(trip_pnls)
        ),
        "quote_uptime": (
            result.quote_eligible_ticks / max(result.total_ticks, 1)
        ),
        "quote_churn": result.requotes / max(
            result.bid_orders_created + result.ask_orders_created, 1
        ),
        "cancel_to_fill_ratio": sum(
            event.get("event") == "order_terminal"
            and event.get("terminal_state") == "CANCELLED"
            for event in result.order_events
        ) / max(len(maker_fills), 1),
        "inventory_variance": population_variance(result.inventory_curve),
        "initial_capital_usdt": initial_capital,
    }

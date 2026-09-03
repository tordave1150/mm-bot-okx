"""Source-bound executor for one separately armed bounded OKX Demo soak.

The package ``describe`` path is offline.  ``start`` is deliberately narrow:
it verifies one immutable package, consumes one arm token, creates one durable
marker and lease, then permits only Demo post-only create/cancel and at most
one reduce-only flatten.  Runtime accounting is persisted through the same
fill-cursor/ledger engine used by the formal predecessor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    SampleEfficiencyQuotePolicy,
    SessionPhase,
    fee_aware_quote_pair,
    fee_aware_workoff_edge,
    quote_still_valid,
)
from okx_demo_economic_fill_engine import EconomicSessionEngine
from okx_demo_multi_session_campaign import (
    SessionEvidence,
    seal_session_evidence,
)
from okx_demo_profile import load_promoted_profile
from okx_demo_soak_failure_injection import BoundedSoakFaultHarness
from okx_fill_restart_executor import HashChainStream, _build_gateway
from okx_fill_restart_gateway import PostOnlyCreateRejected, PostOnlyWouldCross
from okx_fill_restart_offline import _sha256
from okx_fill_restart_validation import (
    AuthoritativeSnapshot,
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    RiskBudget,
    RuntimeBinding,
    canonical_sha256,
)


ROOT = Path(__file__).resolve().parent
ARTIFACT_ROOT = Path("artifacts") / "okx_demo_soak_validation"
SYMBOL = "BTC/USDT:USDT"


class SoakExecutionError(RuntimeError):
    pass


def _json_payload(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_json_payload(value))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SoakExecutionError(f"artifact reuse refused: {path.name}") from exc


def _replace_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise SoakExecutionError("durable state temporary path already exists")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json_payload(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True)
class SoakPackage:
    root: Path
    output: Path
    spec: dict[str, Any]

    @property
    def expected_arm_token(self) -> str:
        return f"OKX_DEMO:{self.spec['session_id']}"


def load_package(root: Path, package_id: str) -> SoakPackage:
    if not package_id.startswith("soak-package-"):
        raise SoakExecutionError("soak package identity is invalid")
    output = root.resolve() / ARTIFACT_ROOT / package_id
    terminal_path = output / "SOAK_PACKAGE_COMPLETED.json"
    spec_path = output / "specification/soak_package_spec.json"
    hashes_path = output / "specification/source_hashes.json"
    completion_path = output / "completion_hashes.json"
    required = (terminal_path, spec_path, hashes_path, completion_path)
    if not all(path.is_file() for path in required):
        raise SoakExecutionError("soak package is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if any((
        terminal.get("status") != "BOUNDED_DEMO_SOAK_PACKAGE_FROZEN_OFFLINE",
        terminal.get("package_id") != package_id,
        terminal.get("soak_executed") is not False,
        terminal.get("execution_marker_created") is not False,
        spec.get("package_id") != package_id,
        spec.get("soak_executed") is not False,
    )):
        raise SoakExecutionError("soak package boundary is invalid")
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in completion.items()
        if not (output / relative).is_file()
        or _sha256(output / relative) != expected
    ]
    if failures or _sha256(completion_path) != terminal.get(
        "completion_hashes_sha256"
    ):
        raise SoakExecutionError("soak package completion hash mismatch")
    source_hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_hashes.items()
        if not (root / relative).is_file() or _sha256(root / relative) != expected
    ]
    if source_failures:
        raise SoakExecutionError("soak package source hash mismatch")
    canonical = dict(spec)
    expected_spec_hash = canonical.pop("specification_sha256", "")
    if canonical_sha256(canonical) != expected_spec_hash:
        raise SoakExecutionError("soak specification hash mismatch")
    return SoakPackage(root.resolve(), output, spec)


def _verify_campaign_authorization(package: SoakPackage) -> dict[str, object]:
    """Require a durable single-slot supervisor gate for economic sessions."""
    if package.spec.get("protocol_id") != "okx-demo-multi-session-economic-soak-v1":
        return {}
    gate_path = package.output / "campaign_authorization.json"
    if not gate_path.is_file():
        raise SoakExecutionError("economic session lacks campaign authorization")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    canonical_gate = dict(gate)
    expected_gate_hash = canonical_gate.pop("authorization_sha256", "")
    if canonical_sha256(canonical_gate) != expected_gate_hash:
        raise SoakExecutionError("economic campaign authorization hash mismatch")
    campaign_package_id = str(package.spec.get("campaign_package_id") or "")
    campaign_output = (
        package.root
        / "artifacts"
        / "okx_demo_multi_session_economic_soak"
        / "packages"
        / campaign_package_id
    )
    marker_path = campaign_output / "campaign_run/A2_CAMPAIGN_ARMED.json"
    state_path = campaign_output / "campaign_run/state/supervisor_state.json"
    if not marker_path.is_file() or not state_path.is_file():
        raise SoakExecutionError("economic campaign durable authorization is incomplete")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    canonical_state = dict(state)
    expected_state_hash = canonical_state.pop("state_sha256", "")
    if canonical_sha256(canonical_state) != expected_state_hash:
        raise SoakExecutionError("economic campaign supervisor state hash mismatch")
    if any((
        gate.get("campaign_package_id") != campaign_package_id,
        gate.get("campaign_id") != package.spec.get("campaign_id"),
        gate.get("campaign_slot") != package.spec.get("campaign_slot"),
        gate.get("session_package_id") != package.spec.get("package_id"),
        gate.get("session_run_id") != package.spec.get("run_id"),
        gate.get("session_id") != package.spec.get("session_id"),
        gate.get("source_manifest_sha256")
        != package.spec.get("source_manifest_sha256"),
        gate.get("campaign_arm_marker_sha256") != _sha256(marker_path),
        gate.get("session_arm_token_sha256")
        != hashlib.sha256(package.expected_arm_token.encode("utf-8")).hexdigest(),
        gate.get("session_arm_token_serialized") is not False,
        gate.get("status") != "ACTIVE_SINGLE_SESSION",
        gate.get("normal_post_only_orders_authorized") is not True,
        gate.get("single_flight_reduce_only_flatten_authorized") is not True,
        gate.get("live_authorized") is not False,
        gate.get("account_configuration_mutation_authorized") is not False,
        gate.get("production_authorized") is not False,
        canonical_state.get("active_slot") != package.spec.get("campaign_slot"),
        canonical_state.get("terminal_decision") is not None,
    )):
        raise SoakExecutionError("economic campaign authorization binding mismatch")
    return gate


class DurableLease:
    """One process lease with explicit collision and stale recovery evidence."""

    def __init__(self, path: Path, owner: str, *, now: Callable[[], float] = time.time):
        self.path = path
        self.owner = owner
        self.now = now
        self.active = False

    def acquire(self, ttl_ms: int) -> None:
        if ttl_ms <= 0:
            raise SoakExecutionError("lease TTL is invalid")
        now_ms = int(self.now() * 1000)
        payload = {
            "owner": self.owner,
            "status": "ACTIVE",
            "acquired_at_ms": now_ms,
            "expires_at_ms": now_ms + ttl_ms,
        }
        try:
            _write_new(self.path, payload)
        except SoakExecutionError:
            try:
                prior = json.loads(self.path.read_text(encoding="utf-8"))
                expiry = int(prior.get("expires_at_ms", 0))
                status = str(prior.get("status", ""))
            except Exception as exc:
                raise SoakExecutionError("lease state is unreadable") from exc
            if status == "ACTIVE" and expiry >= now_ms:
                raise SoakExecutionError("single-instance lease collision")
            stale_hash = _sha256(self.path)[:16]
            stale = self.path.with_name(f"lease.stale.{stale_hash}.json")
            if stale.exists():
                raise SoakExecutionError("stale lease evidence collision")
            os.replace(self.path, stale)
            _write_new(self.path, payload)
        self.active = True

    def release(self) -> None:
        if not self.active:
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SoakExecutionError("lease release state is unreadable") from exc
        if value.get("owner") != self.owner or value.get("status") != "ACTIVE":
            raise SoakExecutionError("lease ownership changed before release")
        value["status"] = "RELEASED"
        value["released_at_ms"] = int(self.now() * 1000)
        _replace_json(self.path, value)
        self.active = False


class BoundedSoakExecutor:
    def __init__(
        self,
        package: SoakPackage,
        gateway: Any,
        *,
        credentials: Sequence[str] = (),
        lease: DurableLease | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.package = package
        self.gateway = gateway
        self.credentials = tuple(value for value in credentials if value)
        self.lease = lease
        self.sleep = sleep
        self.now = now
        self.harness = BoundedSoakFaultHarness()
        self.owned: set[str] = set()
        self.owned_quotes: dict[str, tuple[str, Decimal]] = {}
        self.draining_workoff_quote_observations: dict[str, int] = {}
        self.draining_workoff_refreshes = 0
        self.pending_intent = ""
        self.normal_creates = 0
        self.normal_create_acknowledgements = 0
        self.normal_create_rejections = 0
        self.read_retries = 0
        self.peak_equity_usdt = Decimal("0")
        self.maximum_drawdown_usdt = Decimal("0")
        self.maximum_inventory_btc_observed = Decimal("0")
        self.maximum_owned_bid_observed = 0
        self.maximum_owned_ask_observed = 0
        self.hard_kill_triggered = False
        self.session_started_at_ms = 0
        self.last_mid_usdt: Decimal | None = None
        self.trade_since_ms = 0
        self.engine: FillRestartEngine | None = None
        self.market_bootstrap_completed = False
        self.events = HashChainStream(
            package.output / "soak_run/streams/events.jsonl"
        )
        self.controller = HashChainStream(
            package.output / "soak_run/state/controller_state.jsonl"
        )
        self.terminal_written = False
        self.economic_mode = (
            package.spec.get("protocol_id")
            == "okx-demo-multi-session-economic-soak-v1"
        )
        self.activity: EconomicSessionController | None = None
        self.sample_efficiency_policy: SampleEfficiencyQuotePolicy | None = None
        self.profile: Any | None = None
        if self.economic_mode:
            budget = dict(package.spec["risk_budget"])
            self.activity = EconomicSessionController(
                session_id=str(package.spec["session_id"]),
                source_sha256=str(package.spec["source_manifest_sha256"]),
                admission_create_cap=int(budget.get("admission_create_cap", 48)),
                workoff_create_reserve=int(
                    budget.get("maker_workoff_create_reserve", 12)
                ),
            )
            if budget.get("economic_repair_version") in {
                "r0-sample-efficiency-v1", "r0-terminal-workoff-v2",
            }:
                self.sample_efficiency_policy = SampleEfficiencyQuotePolicy(
                    minimum_half_spread_bps=Decimal(
                        str(budget["minimum_half_spread_bps"])
                    ),
                    maker_fee_rate=Decimal(str(budget["maker_fee_rate"])),
                    fee_edge_safety_buffer_usdt=Decimal(
                        str(budget["fee_edge_safety_buffer_usdt"])
                    ),
                    balanced_retention_threshold_ticks=int(
                        budget["balanced_quote_retention_threshold_ticks"]
                    ),
                    defense_retention_threshold_ticks=int(
                        budget["defense_quote_retention_threshold_ticks"]
                    ),
                    draining_workoff_retention_threshold_ticks=int(
                        budget.get("draining_workoff_retention_threshold_ticks", 5)
                    ),
                    draining_workoff_max_quote_observations=int(
                        budget.get("draining_workoff_max_quote_observations", 6)
                    ),
                    draining_workoff_max_refreshes=int(
                        budget.get("draining_workoff_max_refreshes", 3)
                    ),
                    admission_create_cap=int(budget["admission_create_cap"]),
                    workoff_create_reserve=int(
                        budget["maker_workoff_create_reserve"]
                    ),
                )
                self.sample_efficiency_policy.validate()

    @property
    def repaired_economic_mode(self) -> bool:
        return bool(
            self.economic_mode
            and self.package.spec["risk_budget"].get("economic_repair_version")
            in {
                "a2-ack-accounting-v2", "r0-sample-efficiency-v1",
                "r0-terminal-workoff-v2",
            }
        )

    @property
    def sample_efficiency_mode(self) -> bool:
        return bool(
            self.repaired_economic_mode
            and self.sample_efficiency_policy is not None
        )

    @staticmethod
    def _is_transient_read_error(exc: Exception) -> bool:
        return isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
            "RequestTimeout",
            "RateLimitExceeded",
            "NetworkError",
            "ExchangeNotAvailable",
            "DDoSProtection",
        }

    def _read(self, name: str, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        attempts = int(self.package.spec["risk_budget"]["read_retry_attempts"])
        for attempt in range(1, attempts + 1):
            try:
                return call(*args, **kwargs)
            except Exception as exc:
                if not self._is_transient_read_error(exc):
                    raise
                self.event(
                    "READ_RETRY",
                    read=name,
                    attempt=attempt,
                    maximum_attempts=attempts,
                    mutation_retry=False,
                )
                self.read_retries += 1
                if attempt == attempts:
                    raise SoakExecutionError(
                        f"read retry budget exhausted: {name}"
                    ) from exc
                self.sleep(min(float(attempt), 3.0))
        raise SoakExecutionError(f"read retry budget exhausted: {name}")

    def event(self, name: str, **payload: object) -> None:
        self.events.append({"event": name, **payload})

    def _commit_controller(self, phase: str, **payload: object) -> None:
        ledger_inventory = (
            str(self.engine.state.ledger.inventory_btc)
            if self.engine is not None else "UNKNOWN"
        )
        self.controller.append({
            "run_id": self.package.spec["run_id"],
            "phase": phase,
            "normal_creates": self.normal_creates,
            "normal_create_dispatches": self.normal_creates,
            "normal_create_acknowledgements": self.normal_create_acknowledgements,
            "normal_create_rejections": self.normal_create_rejections,
            "owned_client_ids": sorted(self.owned),
            "pending_intent": self.pending_intent,
            "controller_inventory_btc": ledger_inventory,
            **payload,
        })

    @staticmethod
    def _client_id(value: dict[str, Any]) -> str:
        return str(
            value.get("clientOrderId")
            or (value.get("info") or {}).get("clOrdId")
            or ""
        )

    def _activity_time(self) -> int:
        return max(
            int(self.now() * 1000),
            1 if self.activity is None else self.activity.last_timestamp_ms,
            1,
        )

    def _remove_filled_ownership(self) -> None:
        if self.engine is None:
            return
        filled = {
            client_id
            for client_id in self.owned
            if client_id in self.engine.state.owned_orders
            and self.engine.state.owned_orders[client_id].status == "FILLED"
        }
        for client_id in filled:
            self.owned.discard(client_id)
            self.owned_quotes.pop(client_id, None)
            self.draining_workoff_quote_observations.pop(client_id, None)
        if filled:
            self._commit_controller(
                "FILLED_ORDER_OWNERSHIP_RELEASED",
                client_order_ids=sorted(filled),
            )

    def _fee_aware_quote_plan(
        self, *, account: Any, book: Any
    ) -> tuple[tuple[str, Decimal], ...]:
        if not self.repaired_economic_mode:
            if self.economic_mode and account.position_btc > 0:
                return (("sell", Decimal(str(book.best_ask))),)
            if self.economic_mode and account.position_btc < 0:
                return (("buy", Decimal(str(book.best_bid))),)
            return (
                ("buy", Decimal(str(book.best_bid))),
                ("sell", Decimal(str(book.best_ask))),
            )
        if self.profile is None or self.gateway.market_spec is None:
            raise SoakExecutionError("fee-aware quote metadata is unavailable")
        budget = self.package.spec["risk_budget"]
        tick = Decimal(str(self.gateway.market_spec.price_tick))
        quantity = Decimal(str(budget["maximum_inventory_btc"]))
        fee_rate = Decimal(str(budget["maker_fee_rate"]))
        buffer = Decimal(str(budget["fee_edge_safety_buffer_usdt"]))
        decision = fee_aware_quote_pair(
            best_bid_usdt=Decimal(str(book.best_bid)),
            best_ask_usdt=Decimal(str(book.best_ask)),
            quantity_btc=quantity,
            maker_fee_rate=fee_rate,
            minimum_half_spread_bps=Decimal(
                str(budget["minimum_half_spread_bps"])
            ),
            tick_size_usdt=tick,
            safety_buffer_usdt=buffer,
        )
        if not decision.eligible:
            if self.activity is not None:
                self.activity.record_placement_reason(
                    reason="FEE_EDGE_BLOCKED", timestamp_ms=self._activity_time()
                )
            return ()
        inventory = Decimal(str(account.position_btc))
        if (
            self.activity is not None
            and self.activity.phase is SessionPhase.DRAINING
            and inventory == 0
        ):
            self.activity.record_placement_reason(
                reason="DRAINING_NEW_EXPOSURE_BLOCKED",
                timestamp_ms=self._activity_time(),
            )
            return ()
        if inventory == 0:
            planned = [
                ("buy", decision.bid_price_usdt),
                ("sell", decision.ask_price_usdt),
            ]
        else:
            side = "sell" if inventory > 0 else "buy"
            price = (
                decision.ask_price_usdt
                if side == "sell" else decision.bid_price_usdt
            )
            causal_rows = () if self.activity is None else (
                *self.activity.pending_fills.values(),
                *self.activity.completed_fills.values(),
            )
            entries = [
                item
                for item in causal_rows
                if item.fill_side == ("buy" if side == "sell" else "sell")
                and item.remaining_workoff_btc > 0
                and item.fill_price_usdt > 0
            ]
            if not entries:
                raise SoakExecutionError(
                    "inventory defense lacks bound causal entry price"
                )
            entry = (
                max(item.fill_price_usdt for item in entries)
                if side == "sell"
                else min(item.fill_price_usdt for item in entries)
            )
            if side == "sell":
                required = (
                    entry * quantity * (Decimal("1") + fee_rate) + buffer
                ) / (quantity * (Decimal("1") - fee_rate))
                required = (required / tick).to_integral_value(
                    rounding=ROUND_CEILING
                ) * tick
                price = max(price, required)
                entry_side = "buy"
            else:
                required = (
                    entry * quantity * (Decimal("1") - fee_rate) - buffer
                ) / (quantity * (Decimal("1") + fee_rate))
                required = (required / tick).to_integral_value(
                    rounding=ROUND_FLOOR
                ) * tick
                price = min(price, required)
                entry_side = "sell"
            if fee_aware_workoff_edge(
                entry_price_usdt=entry,
                exit_price_usdt=price,
                entry_side=entry_side,
                quantity_btc=quantity,
                maker_fee_rate=fee_rate,
                safety_buffer_usdt=buffer,
            ) <= 0:
                if self.activity is not None:
                    self.activity.record_placement_reason(
                        reason="FEE_EDGE_BLOCKED",
                        timestamp_ms=self._activity_time(),
                    )
                return ()
            planned = [(side, price)]
        result: list[tuple[str, Decimal]] = []
        for side, price in planned:
            if self.activity is not None and self.activity.side_worsens_fill_imbalance(side):
                self.activity.record_placement_reason(
                    reason="FILL_IMBALANCE_BLOCKED",
                    timestamp_ms=self._activity_time(),
                )
                continue
            result.append((side, price))
        return tuple(result)

    def _account_public(self, account: Any) -> dict[str, object]:
        if hasattr(account, "public_dict"):
            return dict(account.public_dict())
        return {
            "account_binding": str(account.account_binding),
            "position_btc": str(account.position_btc),
            "average_entry_usdt": str(getattr(account, "average_entry_usdt", 0)),
            "total_equity_usdt": str(getattr(account, "total_equity_usdt", 0)),
            "free_equity_usdt": str(getattr(account, "free_equity_usdt", 0)),
            "open_orders": len(account.open_orders),
            "clock_skew_ms": int(account.clock_skew_ms),
        }

    def _account_pair(self) -> tuple[Any, Any]:
        first = self._read("fetch_account", self.gateway.fetch_account)
        self.sleep(0)
        second = self._read("fetch_account", self.gateway.fetch_account)
        for account in (first, second):
            if account.position_btc != 0 or account.open_orders:
                raise SoakExecutionError("account is not flat and empty")
            if account.clock_skew_ms > int(
                self.package.spec["risk_budget"]["maximum_clock_skew_ms"]
            ):
                raise SoakExecutionError("account clock skew exceeds budget")
        if first.account_binding != second.account_binding:
            raise SoakExecutionError("account binding changed")
        return first, second

    def _account_only_pair(self) -> tuple[Any, Any]:
        """Collect terminal reporting snapshots without authorizing mutation."""
        terminal_reader = getattr(self.gateway, "fetch_terminal_account_only", None)
        if not callable(terminal_reader):
            terminal_reader = self.gateway.fetch_account
        first = self._read("fetch_terminal_account_only", terminal_reader)
        self.sleep(0)
        second = self._read("fetch_terminal_account_only", terminal_reader)
        if first.account_binding != second.account_binding:
            raise SoakExecutionError("account binding changed")
        self._commit_controller(
            "PRE_MARKET_BOOTSTRAP_TERMINAL_RECONCILED",
            terminal_reconciliation_only=True,
            first_position_btc=str(first.position_btc),
            second_position_btc=str(second.position_btc),
            first_open_orders=len(first.open_orders),
            second_open_orders=len(second.open_orders),
            mutation_retry=False,
        )
        return first, second

    def _build_engine(self, account: Any, market: Any) -> None:
        spec = self.package.spec
        budget = spec["risk_budget"]
        if getattr(market, "fingerprint", "") != spec["market_fingerprint"]:
            raise SoakExecutionError("soak market fingerprint drift")
        profile = load_promoted_profile(self.package.root)
        self.profile = profile
        binding = RuntimeBinding(
            run_id=spec["run_id"],
            execution_mode="OKX_DEMO",
            account_binding=account.account_binding,
            market_fingerprint=spec["market_fingerprint"],
            profile_binding_sha256=profile.binding_sha256,
            runtime_configuration_sha256=canonical_sha256(budget),
            source_manifest_sha256=spec["source_manifest_sha256"],
        )
        risk = RiskBudget(
            maximum_wall_minutes=int(budget["session_wall_minutes"]),
            maximum_normal_creates=int(budget["session_normal_create_cap"]),
            maximum_owned_bid=int(budget["maximum_owned_bid"]),
            maximum_owned_ask=int(budget["maximum_owned_ask"]),
            maximum_inventory_btc=Decimal(budget["maximum_inventory_btc"]),
            capital_usdt=Decimal(budget["capital_usdt"]),
            leverage=int(budget["leverage"]),
            soft_guard_usdt=Decimal(budget["soft_guard_usdt"]),
            hard_kill_usdt=Decimal(budget["hard_kill_usdt"]),
            maximum_unresolved_flatten=int(budget["maximum_unresolved_flatten"]),
        )
        engine_type = EconomicSessionEngine if self.economic_mode else FillRestartEngine
        self.engine = engine_type.create(
            store=HashChainStateStore(
                self.package.output / "soak_run/state/validation_state.json"
            ),
            binding=binding,
            risk_budget=risk,
        )
        self.trade_since_ms = int(self.now() * 1000)
        self.engine.record_authoritative_snapshot(self._snapshot(account))

    def _snapshot(self, account: Any, *, trades_complete: bool = True) -> AuthoritativeSnapshot:
        if self.engine is None:
            raise SoakExecutionError("validation engine is unavailable")
        rows = tuple({
            "client_order_id": self._client_id(row),
            "order_id": str(row.get("id") or ""),
            "side": str(row.get("side") or ""),
        } for row in account.open_orders)
        return AuthoritativeSnapshot(
            sequence=len(self.controller._records()) + 1,
            account_binding=account.account_binding,
            symbol=SYMBOL,
            market_fingerprint=self.package.spec["market_fingerprint"],
            position_btc=Decimal(account.position_btc),
            average_entry_price=Decimal(str(getattr(account, "average_entry_usdt", 0))),
            run_fees_usdt=self.engine.state.ledger.total_fees_usdt,
            open_orders=rows,
            trades_complete=trades_complete,
        )

    def _fill_pages(self, trades: Iterable[dict[str, Any]]) -> list[dict[str, object]]:
        if self.engine is None or self.gateway.market_spec is None:
            raise SoakExecutionError("fill accounting metadata is unavailable")
        converted: list[dict[str, object]] = []
        for trade in trades:
            client_id = self._client_id(trade)
            order = self.engine.state.owned_orders.get(client_id)
            if order is None:
                raise SoakExecutionError("new fill does not map to a durable owned order")
            fee = trade.get("fee") or {}
            converted.append({
                "trade_id": str(trade.get("id") or ""),
                "order_id": str(trade.get("order") or ""),
                "client_order_id": client_id,
                "timestamp_ms": int(trade.get("timestamp") or 0),
                "side": str(trade.get("side") or ""),
                "price": str(trade.get("price") or 0),
                "quantity_btc": str(self.gateway.market_spec.contracts_to_base(
                    Decimal(str(trade.get("amount") or 0))
                )),
                "fee_cost": str(abs(Decimal(str(fee.get("cost") or 0)))),
                "fee_currency": str(fee.get("currency") or "").upper(),
                "liquidity": str(trade.get("takerOrMaker") or "").lower(),
                "reduce_only": order.reduce_only,
            })
        rows = sorted(
            converted,
            key=lambda row: (int(row["timestamp_ms"]), str(row["trade_id"])),
        )
        chunks = [rows[index:index + 100] for index in range(0, len(rows), 100)]
        if not chunks:
            chunks = [[]]
        return [{
            "page_index": index,
            "cursor": "" if index == 0 else f"page-{index}",
            "next_cursor": "" if index == len(chunks) - 1 else f"page-{index + 1}",
            "trades": chunk,
        } for index, chunk in enumerate(chunks)]

    def _ingest_trades(self) -> int:
        if self.engine is None:
            raise SoakExecutionError("validation engine is unavailable")
        trades = self._read(
            "fetch_trades",
            self.gateway.fetch_trades,
            since_ms=self.trade_since_ms,
        )
        pages = self._fill_pages(trades)
        prior_fill_ids = set(self.engine.state.ledger.fill_fingerprints)
        new_rows = [
            dict(row)
            for page in pages
            for row in page["trades"]
            if str(row["trade_id"]) not in prior_fill_ids
        ]
        inventory_before = self.engine.state.ledger.inventory_btc
        applied = self.engine.ingest_fill_pages(pages)
        if self.activity is not None and new_rows:
            observed_at_ms = max(
                int(self.now() * 1000), self.activity.last_timestamp_ms
            )
            running_inventory = inventory_before
            for row in sorted(
                new_rows,
                key=lambda item: (int(item["timestamp_ms"]), str(item["trade_id"])),
            ):
                quantity = Decimal(str(row["quantity_btc"]))
                signed = quantity if row["side"] == "buy" else -quantity
                next_inventory = running_inventory + signed
                if not bool(row["reduce_only"]):
                    trade_id = str(row["trade_id"])
                    self.activity.observe_fill(
                        trade_id=trade_id,
                        side=str(row["side"]),
                        timestamp_ms=int(row["timestamp_ms"]),
                        observed_at_ms=observed_at_ms,
                        inventory_before_btc=running_inventory,
                        inventory_after_btc=next_inventory,
                        fill_order_id=str(row["order_id"]),
                        fill_quantity_btc=quantity,
                        fill_price_usdt=Decimal(str(row["price"])),
                    )
                    # Exchange fill timestamps may be slightly ahead of the
                    # workstation while still inside the frozen clock-skew
                    # budget.  The durable fill event advances the activity
                    # clock, so every causal continuation must use that
                    # monotonic clock rather than the raw local observation
                    # time.
                    causal_timestamp_ms = self._activity_time()
                    self.activity.observe_inventory_defense(
                        trade_id=trade_id,
                        timestamp_ms=causal_timestamp_ms,
                    )
                    if self.last_mid_usdt is not None:
                        price = Decimal(str(row["price"]))
                        markout = quantity * (
                            self.last_mid_usdt - price
                            if row["side"] == "buy"
                            else price - self.last_mid_usdt
                        )
                        self.activity.observe_markout(
                            trade_id=trade_id,
                            timestamp_ms=causal_timestamp_ms,
                            markout_usdt=markout,
                        )
                running_inventory = next_inventory
        if applied:
            self._remove_filled_ownership()
            self.event(
                "FILLS_APPLIED",
                count=applied,
                inventory_btc=str(self.engine.state.ledger.inventory_btc),
                total_fees_usdt=str(self.engine.state.ledger.total_fees_usdt),
            )
        return applied

    def _reconcile_economic_fill_latch(self, account: Any) -> Any:
        """Reconcile a fill before any subsequent create can be dispatched."""
        if (
            not isinstance(self.engine, EconomicSessionEngine)
            or not self.engine.state.placement_halted_for_fill
        ):
            return account
        if self.owned:
            self._cancel_owned()
        attempts = int(self.package.spec["risk_budget"]["read_retry_attempts"])
        for attempt in range(1, attempts + 1):
            current = self._read("fetch_account", self.gateway.fetch_account)
            self._ingest_trades()
            if (
                Decimal(str(current.position_btc))
                == self.engine.state.ledger.inventory_btc
                and not current.open_orders
                and not self.engine.state.open_orders
            ):
                if self.engine.state.placement_halted_for_fill:
                    self.engine.confirm_fill_reconciliation(
                        snapshot=self._snapshot(current)
                    )
                if not self.engine.state.can_submit:
                    raise SoakExecutionError(
                        "economic placement gate remained closed after reconciliation"
                    )
                self.event(
                    "ECONOMIC_FILL_RECONCILED",
                    attempt=attempt,
                    inventory_btc=str(self.engine.state.ledger.inventory_btc),
                    mutation_retry=False,
                )
                self._commit_controller("ECONOMIC_FILL_RECONCILED")
                return current
            if attempt < attempts:
                self.sleep(min(float(attempt), 3.0))
        raise SoakExecutionError(
            "economic fill/account reconciliation exhausted before placement"
        )

    def _reconcile_account_engine_inventory(self, account: Any) -> Any:
        """Allow bounded read-tail convergence before declaring inventory drift."""
        if self.engine is None:
            return account
        attempts = int(self.package.spec["risk_budget"]["read_retry_attempts"])
        current = account
        for attempt in range(1, attempts + 1):
            self._ingest_trades()
            if Decimal(str(current.position_btc)) == self.engine.state.ledger.inventory_btc:
                current = self._reconcile_economic_fill_latch(current)
                self._commit_controller(
                    "ACCOUNT_ENGINE_INVENTORY_RECONCILED",
                    attempt=attempt,
                    inventory_btc=str(current.position_btc),
                    mutation_retry=False,
                )
                return current
            if attempt < attempts:
                self.sleep(min(float(attempt), 3.0))
                current = self._read("fetch_account", self.gateway.fetch_account)
        raise SoakExecutionError(
            "economic controller/engine/account inventory mismatch"
        )

    def _reconcile_draining_cancel_boundary(self, account: Any) -> tuple[Any, bool]:
        """Close the cancel/fill race before a DRAINING session may exit."""
        if self.activity is None or self.activity.phase is not SessionPhase.DRAINING:
            raise SoakExecutionError("draining cancel reconciliation is out of phase")
        if self.owned:
            self._cancel_owned()
        # A post-only order may fill concurrently with its owned-only cancel.
        # Re-read durable fills and account state before deciding that maker
        # work-off is complete.  This is read reconciliation, never a retry of
        # the cancel or any other mutation.
        self._ingest_trades()
        refreshed = self._read("fetch_account", self.gateway.fetch_account)
        refreshed = self._reconcile_economic_fill_latch(refreshed)
        ready = (
            Decimal(str(refreshed.position_btc)) == 0
            and not self.activity.pending_fills
            and not self.owned
        )
        self._commit_controller(
            "DRAINING_CANCEL_BOUNDARY_RECONCILED",
            final_exit_ready=ready,
            position_btc=str(refreshed.position_btc),
            pending_causal_fill_count=len(self.activity.pending_fills),
            mutation_retry=False,
        )
        return refreshed, ready

    def _require_placement_gate(self, side: str) -> None:
        if self.engine is not None and self.engine.state.can_submit:
            return
        self.event(
            "PLACEMENT_GATE_BLOCKED_BEFORE_DISPATCH",
            side=side,
            normal_create_dispatches=self.normal_creates,
            mutation_retry=False,
        )
        raise SoakExecutionError("placement gate closed before create dispatch")

    def _loss_guard(self, account: Any) -> bool:
        equity = Decimal(str(getattr(account, "total_equity_usdt", 0)))
        if equity <= 0:
            raise SoakExecutionError("authoritative equity is unavailable")
        self.peak_equity_usdt = max(self.peak_equity_usdt, equity)
        drawdown = self.peak_equity_usdt - equity
        self.maximum_drawdown_usdt = max(self.maximum_drawdown_usdt, drawdown)
        budget = self.package.spec["risk_budget"]
        self._commit_controller("RISK_CHECKED", drawdown_usdt=str(drawdown))
        if drawdown >= Decimal(budget["hard_kill_usdt"]):
            self.hard_kill_triggered = True
            if self.activity is not None:
                self.activity.classify_tick(
                    timestamp_ms=max(
                        int(self.now() * 1000), self.activity.last_timestamp_ms
                    ),
                    inventory_btc=Decimal(str(account.position_btc)),
                    hard_kill=True,
                )
            self.event("HARD_KILL_TRIGGERED", drawdown_usdt=str(drawdown))
            raise SoakExecutionError("hard loss guard reached")
        if drawdown >= Decimal(budget["soft_guard_usdt"]):
            if self.activity is not None:
                self.activity.classify_tick(
                    timestamp_ms=max(
                        int(self.now() * 1000), self.activity.last_timestamp_ms, 1
                    ),
                    inventory_btc=Decimal(str(account.position_btc)),
                    account_gate_open=False,
                )
            self.event("SOFT_GUARD_TRIGGERED", drawdown_usdt=str(drawdown))
            return True
        return False

    def _cancel_owned(self) -> None:
        expected = tuple(sorted(self.owned))
        if not expected:
            return
        bind_cancel_context = getattr(
            self.gateway, "set_cancel_reconciliation_context", None
        )
        if callable(bind_cancel_context):
            bind_cancel_context(
                since_ms=self.trade_since_ms,
                read_attempts=int(
                    self.package.spec["risk_budget"]["read_retry_attempts"]
                ),
            )
        cancelled = self.gateway.cancel_all_owned(expected)
        self.event("OWNED_CANCEL_CONFIRMED", count=len(cancelled))
        if self.engine is not None and all(
            client_id in self.engine.state.owned_orders for client_id in expected
        ):
            attempts = int(self.package.spec["risk_budget"]["read_retry_attempts"])
            for attempt in range(1, attempts + 1):
                account = self._read("fetch_account", self.gateway.fetch_account)
                applied = self._ingest_trades()
                live_client_ids = {
                    self._client_id(row) for row in account.open_orders
                }
                stale_cancel_ids = sorted(set(expected) & live_client_ids)
                if stale_cancel_ids:
                    self.event(
                        "CANCEL_ACCOUNT_VIEW_STALE",
                        attempt=attempt,
                        maximum_attempts=attempts,
                        client_order_ids=stale_cancel_ids,
                        mutation_retry=False,
                    )
                    if attempt == attempts:
                        raise SoakExecutionError(
                            "cancelled order remained authoritative-open"
                        )
                    self.sleep(min(float(attempt), 3.0))
                    continue
                snapshot = self._snapshot(account)
                if account.position_btc == self.engine.state.ledger.inventory_btc:
                    self.engine.confirm_cancellations(
                        confirmed_client_ids=expected,
                        snapshot=snapshot,
                    )
                    break
                if not applied:
                    self.engine.observe_position_before_trade(
                        self._snapshot(account, trades_complete=False)
                    )
                if attempt == attempts:
                    raise SoakExecutionError("cancel/fill reconciliation exhausted")
                self.sleep(1)
        self.owned.clear()
        self.owned_quotes.clear()
        self.draining_workoff_quote_observations.clear()
        self.pending_intent = ""
        self._commit_controller("CANCEL_RECONCILED")

    def _flatten(self, account: Any) -> bool:
        if account.position_btc == 0:
            return True
        flatten_inventory = Decimal(str(account.position_btc))
        if self.activity is not None:
            self.activity.authorize_taker_flatten(
                reason="EMERGENCY_HARD_KILL" if self.hard_kill_triggered else "SHUTDOWN",
                timestamp_ms=max(
                    int(self.now() * 1000), self.activity.last_timestamp_ms
                ),
            )
        client_id = "fr" + canonical_sha256({
            "run_id": self.package.spec["run_id"], "flatten": 1
        })[:28]
        self.pending_intent = client_id
        self.owned.add(client_id)
        self._commit_controller("FLATTEN_INTENT_DURABLE")
        self.event("FLATTEN_INTENT", client_order_id=client_id)
        response = self.gateway.submit_reduce_only_flatten(
            client_order_id=client_id,
            position_btc=account.position_btc,
        )
        order_id = str(response.get("id") or "")
        if not order_id:
            raise SoakExecutionError("flatten acknowledgement identity is missing")
        engine_bound = (
            self.engine is not None
            and self.engine.state.ledger.inventory_btc == account.position_btc
        )
        if engine_bound:
            self.engine.record_special_order_ack(OwnedOrder(
                client_order_id=client_id,
                order_id=order_id,
                side="sell" if account.position_btc > 0 else "buy",
                quantity_btc=abs(Decimal(account.position_btc)),
                remaining_btc=abs(Decimal(account.position_btc)),
                reduce_only=True,
                post_only_acknowledged=False,
            ))
        self.pending_intent = ""
        self._commit_controller("FLATTEN_ACKNOWLEDGED", order_id=order_id)
        for _ in range(8):
            self.sleep(2)
            account = self._read("fetch_account", self.gateway.fetch_account)
            if engine_bound:
                self._ingest_trades()
            if account.position_btc == 0 and not account.open_orders:
                if engine_bound:
                    if self.engine.state.ledger.inventory_btc != 0:
                        continue
                    self.engine.record_authoritative_snapshot(self._snapshot(account))
                    if self.activity is not None:
                        self.activity.close_with_special_flatten(
                            timestamp_ms=self._activity_time(),
                            inventory_before_btc=flatten_inventory,
                            flatten_quantity_btc=abs(flatten_inventory),
                        )
                self.owned.clear()
                self.owned_quotes.clear()
                self.draining_workoff_quote_observations.clear()
                self._commit_controller("FLATTEN_RECONCILED")
                return engine_bound
        raise SoakExecutionError("single-flight flatten did not reconcile")

    def _shutdown(self) -> tuple[Any, Any, bool]:
        engine_reconciled = True
        cancel_reconciliation_error: Exception | None = None
        if self.owned:
            try:
                self._cancel_owned()
            except Exception as exc:
                # Account-only shutdown must still be attempted after a
                # controller/engine failure.  It can prove safety, not success.
                engine_reconciled = False
                cancel_reconciliation_error = exc
                self.event(
                    "SHUTDOWN_CANCEL_RECONCILIATION_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    mutation_retry=False,
                )
        account = self._read("fetch_account", self.gateway.fetch_account)
        if account.position_btc != 0:
            if cancel_reconciliation_error is not None:
                raise SoakExecutionError(
                    "shutdown cancel reconciliation unresolved before flatten"
                ) from cancel_reconciliation_error
            engine_reconciled = self._flatten(account) and engine_reconciled
        first, second = self._account_pair()
        if self.engine is None:
            engine_reconciled = False
        elif engine_reconciled:
            self._ingest_trades()
            for account in (first, second):
                self.engine.record_authoritative_snapshot(self._snapshot(account))
            engine_reconciled = (
                self.engine.state.ledger.inventory_btc == 0
                and not self.engine.state.open_orders
            )
        self.owned.clear()
        self.owned_quotes.clear()
        self.pending_intent = ""
        self._commit_controller(
            "TERMINAL_RECONCILED" if engine_reconciled else "TERMINAL_ACCOUNT_ONLY",
            final_position_btc=str(second.position_btc),
            final_open_orders=len(second.open_orders),
        )
        return first, second, engine_reconciled

    def _gateway_audit(self) -> dict[str, object]:
        if hasattr(self.gateway, "public_audit"):
            return dict(self.gateway.public_audit())
        return {
            "mutation_call_count": self.normal_creates,
            "flatten_dispatches": int(getattr(self.gateway, "flatten_dispatches", 0)),
            "live_endpoint_attempts": int(
                getattr(self.gateway, "live_endpoint_attempts", 0)
            ),
            "live_orders": 0,
        }

    def _sync_normal_create_dispatches(self) -> int:
        audit = self._gateway_audit()
        method_counts = dict(audit.get("mutation_method_counts") or {})
        observed = int(audit.get(
            "normal_create_dispatches",
            int(method_counts.get("create_order", 0))
            - int(audit.get("flatten_dispatches", 0)),
        ))
        if observed < self.normal_creates or observed < 0:
            raise SoakExecutionError("normal create dispatch counter regressed")
        self.normal_creates = observed
        return observed

    def _create_counter_audit(self) -> dict[str, object]:
        dispatches = self._sync_normal_create_dispatches()
        unresolved = (
            dispatches
            - self.normal_create_acknowledgements
            - self.normal_create_rejections
        )
        if unresolved < 0:
            raise SoakExecutionError("normal create acknowledgement counters exceed dispatches")
        return {
            "normal_create_dispatches": dispatches,
            "normal_create_acknowledgements": self.normal_create_acknowledgements,
            "normal_create_rejections": self.normal_create_rejections,
            "normal_create_unresolved": unresolved,
            "mutation_retries": 0,
            "reconciles": (
                dispatches
                == self.normal_create_acknowledgements
                + self.normal_create_rejections
                + unresolved
            ),
        }

    def _secret_scan(self) -> dict[str, object]:
        runtime = self.package.output / "soak_run"
        matches: set[str] = set()
        files = [path for path in runtime.rglob("*") if path.is_file()]
        assignment = re.compile(
            r"(?i)(OKX_API_KEY|OKX_SECRET|OKX_PASSPHRASE)\s*[=:]\s*\S+"
        )
        for path in files:
            try:
                raw = path.read_bytes()
            except OSError:
                matches.add(str(path.relative_to(runtime)))
                continue
            text = raw.decode("utf-8", errors="ignore")
            if assignment.search(text) or any(
                value.encode("utf-8") in raw for value in self.credentials
            ):
                matches.add(str(path.relative_to(runtime)))
        return {
            "passed": not matches,
            "files_scanned": len(files),
            "credential_values_reported": False,
            "matched_file_count": len(matches),
        }

    def _runtime_hashes(self) -> dict[str, str]:
        excluded = {
            "soak_run/completion_hashes.json",
            "COMPLETED.json",
            "soak_run/FAILED.json",
        }
        files = [
            path for path in self.package.output.rglob("*")
            if path.is_file()
            and path.relative_to(self.package.output).as_posix().startswith("soak_run/")
            and path.relative_to(self.package.output).as_posix() not in excluded
        ]
        return {
            path.relative_to(self.package.output).as_posix(): _sha256(path)
            for path in sorted(files)
        }

    def _economics(self, *, require_complete_activity: bool = False) -> dict[str, object]:
        if self.engine is None:
            return {"engine_reconciled": False}
        ledger = self.engine.state.ledger
        realized_spread = sum(
            (
                Decimal(str(row["gross_pnl_usdt"]))
                for row in ledger.normal_round_trips
            ),
            Decimal("0"),
        )
        matched_fragment_quantity = sum(
            (
                Decimal(str(row["quantity_btc"]))
                for row in ledger.normal_fifo_match_fragments
            ),
            Decimal("0"),
        )
        completed_lot_quantity = sum(
            (
                Decimal(str(row["quantity_btc"]))
                for row in ledger.normal_round_trips
            ),
            Decimal("0"),
        )
        open_lot_quantity = sum(
            (
                abs(Decimal(str(row["quantity_signed_btc"])))
                for row in ledger.normal_fifo_lots
            ),
            Decimal("0"),
        )
        completed_entry_ids = {
            str(row["entry_fill_id"]) for row in ledger.normal_round_trips
        }
        completed_exit_ids = {
            str(exit_id)
            for row in ledger.normal_round_trips
            for exit_id in row.get("exit_fill_ids", [])
        }
        exit_reference_counts: dict[str, int] = {}
        for row in ledger.normal_fifo_match_fragments:
            exit_id = str(row["exit_fill_id"])
            exit_reference_counts[exit_id] = exit_reference_counts.get(exit_id, 0) + 1
        multi_partial_completed_lots = sum(
            1
            for row in ledger.normal_round_trips
            if len(set(str(item) for item in row.get("exit_fill_ids", []))) > 1
        )
        multi_lot_exit_fills = sum(
            1 for count in exit_reference_counts.values() if count > 1
        )
        result: dict[str, object] = {
            "engine_reconciled": (
                ledger.inventory_btc == 0 and not self.engine.state.open_orders
            ),
            "normal_bid_fills": ledger.normal_bid_fills,
            "normal_ask_fills": ledger.normal_ask_fills,
            "normal_fifo_round_trips": len(ledger.normal_round_trips),
            "accounting_audit": {
                "normal_fifo_match_fragments": len(
                    ledger.normal_fifo_match_fragments
                ),
                "normal_fifo_completed_lots": len(ledger.normal_round_trips),
                "normal_inventory_cycles": len(ledger.normal_inventory_cycles),
                "normal_matched_fragment_quantity_btc": str(
                    matched_fragment_quantity
                ),
                "normal_completed_lot_quantity_btc": str(
                    completed_lot_quantity
                ),
                "open_fifo_lot_quantity_btc": str(open_lot_quantity),
                "normal_fifo_unique_completed_entry_fills": len(
                    completed_entry_ids
                ),
                "normal_fifo_unique_exit_fills": len(completed_exit_ids),
                "normal_fifo_evidence_trade_ids": len(
                    completed_entry_ids | completed_exit_ids
                ),
                "normal_fifo_exit_fill_reuse_count": (
                    len(ledger.normal_fifo_match_fragments)
                    - len(exit_reference_counts)
                ),
                "normal_fifo_multi_partial_completed_lots": (
                    multi_partial_completed_lots
                ),
                "normal_fifo_multi_lot_exit_fills": multi_lot_exit_fills,
            },
            "special_fill_count": ledger.special_fill_count,
            "normal_gross_realized_pnl_usdt": str(
                ledger.normal_gross_realized_pnl_usdt
            ),
            "normal_fees_usdt": str(ledger.normal_fees_usdt),
            "normal_net_realized_pnl_usdt": str(
                ledger.normal_net_realized_pnl_usdt
            ),
            "special_gross_realized_pnl_usdt": str(
                ledger.special_gross_realized_pnl_usdt
            ),
            "special_fees_usdt": str(ledger.special_fees_usdt),
            "special_net_realized_pnl_usdt": str(
                ledger.special_net_realized_pnl_usdt
            ),
            "aggregate_gross_realized_pnl_usdt": str(ledger.gross_realized_pnl_usdt),
            "aggregate_fees_usdt": str(ledger.total_fees_usdt),
            "aggregate_net_realized_pnl_usdt": str(ledger.net_realized_pnl_usdt),
            "realized_spread_pnl_usdt": str(realized_spread),
            "inventory_pnl_usdt": str(
                ledger.normal_gross_realized_pnl_usdt - realized_spread
            ),
            "fill_cursor_sha256": canonical_sha256(
                self.engine.state.fill_cursor.to_dict()
            ),
        }
        if self.activity is not None:
            result.update(self.activity.evidence(
                require_complete=require_complete_activity
            ))
        return result

    def _economic_session_evidence(
        self,
        *,
        second: Any,
        gateway_audit: dict[str, object],
        economics: dict[str, object],
    ) -> dict[str, object]:
        if self.activity is None:
            raise SoakExecutionError("economic activity controller is unavailable")
        method_counts = dict(gateway_audit.get("mutation_method_counts") or {})
        create_counter_audit = self._create_counter_audit()
        if int(create_counter_audit["normal_create_unresolved"]) != 0:
            raise SoakExecutionError(
                "successful economic session has unresolved create acknowledgement"
            )
        raw: dict[str, object] = {
            "session_id": str(self.package.spec["session_id"]),
            "run_id": str(self.package.spec["run_id"]),
            "source_sha256": str(self.package.spec["source_manifest_sha256"]),
            "started_at_ms": self.session_started_at_ms,
            "ended_at_ms": max(
                int(self.now() * 1000), self.session_started_at_ms + 1
            ),
            "normal_creates": self.normal_creates,
            **create_counter_audit,
            "normal_cancels": int(method_counts.get("cancel_order", 0)),
            "order_amends": int(method_counts.get("amend_order", 0)),
            "self_trades": 0,
            "read_retries": self.read_retries,
            "mutation_retries": 0,
            "maximum_owned_bid_observed": self.maximum_owned_bid_observed,
            "maximum_owned_ask_observed": self.maximum_owned_ask_observed,
            "maximum_inventory_btc_observed": str(
                self.maximum_inventory_btc_observed
            ),
            "maximum_drawdown_usdt": str(self.maximum_drawdown_usdt),
            "hard_kill_triggered": self.hard_kill_triggered,
            "normal_bid_fills": int(economics["normal_bid_fills"]),
            "normal_ask_fills": int(economics["normal_ask_fills"]),
            "normal_fifo_round_trips": int(economics["normal_fifo_round_trips"]),
            "realized_spread_pnl_usdt": str(
                economics["realized_spread_pnl_usdt"]
            ),
            "inventory_pnl_usdt": str(economics["inventory_pnl_usdt"]),
            "normal_gross_pnl_usdt": str(
                economics["normal_gross_realized_pnl_usdt"]
            ),
            "normal_fees_usdt": str(economics["normal_fees_usdt"]),
            "normal_net_pnl_usdt": str(
                economics["normal_net_realized_pnl_usdt"]
            ),
            "special_fill_count": int(economics["special_fill_count"]),
            "special_gross_pnl_usdt": str(
                economics["special_gross_realized_pnl_usdt"]
            ),
            "special_fees_usdt": str(economics["special_fees_usdt"]),
            "special_net_pnl_usdt": str(
                economics["special_net_realized_pnl_usdt"]
            ),
            "aggregate_gross_pnl_usdt": str(
                economics["aggregate_gross_realized_pnl_usdt"]
            ),
            "aggregate_fees_usdt": str(economics["aggregate_fees_usdt"]),
            "aggregate_net_pnl_usdt": str(
                economics["aggregate_net_realized_pnl_usdt"]
            ),
            "flatten_dispatches": int(gateway_audit.get("flatten_dispatches", 0)),
            "quote_mode_ticks": int(economics["quote_mode_ticks"]),
            "quote_mode_counters": dict(economics["quote_mode_counters"]),
            "unclassified_quote_mode_ticks": int(
                economics["unclassified_quote_mode_ticks"]
            ),
            "markouts_usdt": list(economics["markouts_usdt"]),
            "causal_reentry": list(economics["causal_reentry"]),
            "special_closed_causal_fills": list(
                economics.get("special_closed_causal_fills", [])
            ),
            "special_closed_markouts_usdt": list(
                economics.get("special_closed_markouts_usdt", [])
            ),
            "accounting_audit": dict(economics["accounting_audit"]),
            "control_audit": {
                "session_phase": economics["session_phase"],
                "create_budget_partition": dict(
                    economics["create_budget_partition"]
                ),
                "placement_reason_counters": dict(
                    economics["placement_reason_counters"]
                ),
                "normal_bid_fill_quantity_btc": str(
                    economics["normal_bid_fill_quantity_btc"]
                ),
                "normal_ask_fill_quantity_btc": str(
                    economics["normal_ask_fill_quantity_btc"]
                ),
                "create_counter_audit": create_counter_audit,
            },
            "fill_cursor_sha256": str(economics["fill_cursor_sha256"]),
            "final_position_btc": str(second.position_btc),
            "final_open_orders": len(second.open_orders),
            "terminal_account_snapshots": 2,
            "terminal_reconciled": bool(economics["engine_reconciled"]),
            "pending_intent": bool(self.pending_intent),
            "ambiguous_intent": bool(self.harness.ambiguous_intent),
            "safety_violations": [],
            "live_endpoint_attempts": int(
                gateway_audit.get("live_endpoint_attempts", 0)
            ),
            "live_orders": int(gateway_audit.get("live_orders", 0)),
        }
        if self.sample_efficiency_policy is not None:
            raw["control_audit"]["sample_efficiency_policy"] = (
                self.sample_efficiency_policy.to_dict()
            )
        if not self.repaired_economic_mode:
            raw.pop("accounting_audit", None)
            raw.pop("control_audit", None)
        sealed = seal_session_evidence(raw)
        SessionEvidence.from_dict(sealed)
        return sealed

    def _release_lease(self) -> None:
        if self.lease is not None:
            self.lease.release()

    def _finalize_success(self, first: Any, second: Any) -> dict[str, object]:
        self._release_lease()
        gateway_audit = self._gateway_audit()
        economics = self._economics(
            require_complete_activity=self.economic_mode
        )
        create_counter_audit = self._create_counter_audit()
        result = {
            "status": (
                "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
                if self.economic_mode else "OKX_DEMO_BOUNDED_SOAK_SUPPORT"
            ),
            "package_id": self.package.spec["package_id"],
            "run_id": self.package.spec["run_id"],
            "normal_creates": self.normal_creates,
            **create_counter_audit,
            "final_position_btc": str(second.position_btc),
            "final_open_orders": len(second.open_orders),
            "flatten_dispatches": gateway_audit.get("flatten_dispatches", 0),
            "live_endpoint_attempts": gateway_audit.get("live_endpoint_attempts", 0),
            "live_orders": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        }
        _write_new(self.package.output / "soak_run/raw_result.json", result)
        _write_new(
            self.package.output / "soak_run/terminal/account_snapshots.json",
            {"first": self._account_public(first), "second": self._account_public(second)},
        )
        _write_new(self.package.output / "soak_run/audits/gateway_audit.json", gateway_audit)
        _write_new(self.package.output / "soak_run/audits/economics.json", economics)
        if self.economic_mode:
            _write_new(
                self.package.output / "soak_run/audits/economic_session_evidence.json",
                self._economic_session_evidence(
                    second=second,
                    gateway_audit=gateway_audit,
                    economics=economics,
                ),
            )
        scan = self._secret_scan()
        _write_new(self.package.output / "soak_run/audits/secret_scan.json", scan)
        if not scan["passed"]:
            raise SoakExecutionError("runtime secret scan failed")
        _write_new(self.package.output / "soak_run/decision/decision.json", {
            **result,
            "decision": "TERMINAL_COMPLETE",
            "two_flat_empty_snapshots": True,
            "controller_engine_gateway_reconciled": bool(
                economics.get("engine_reconciled")
            ),
        })
        hashes = self._runtime_hashes()
        _write_new(self.package.output / "soak_run/completion_hashes.json", hashes)
        terminal = {
            **result,
            "completion_files_checked": len(hashes),
            "completion_hashes_sha256": _sha256(
                self.package.output / "soak_run/completion_hashes.json"
            ),
            "two_flat_empty_snapshots": True,
            "controller_engine_gateway_reconciled": bool(
                economics.get("engine_reconciled")
            ),
            "completed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if not terminal["controller_engine_gateway_reconciled"]:
            raise SoakExecutionError("terminal controller/engine/gateway mismatch")
        _write_new(self.package.output / "COMPLETED.json", terminal)
        self.terminal_written = True
        return terminal

    def _finalize_failure(
        self,
        exc: Exception,
        terminal_pair: tuple[Any, Any] | None,
        shutdown_error: Exception | None,
    ) -> dict[str, object]:
        try:
            self._release_lease()
        except Exception as lease_exc:
            shutdown_error = shutdown_error or lease_exc
        gateway_audit = self._gateway_audit()
        try:
            economics = self._economics()
        except Exception as economics_exc:
            economics = {
                "engine_reconciled": False,
                "economics_error": (
                    f"{type(economics_exc).__name__}:{economics_exc}"
                ),
            }
        first_terminal = terminal_pair[0] if terminal_pair else None
        second_terminal = terminal_pair[1] if terminal_pair else None
        two_flat_empty = bool(
            first_terminal
            and second_terminal
            and first_terminal.position_btc == 0
            and second_terminal.position_btc == 0
            and not first_terminal.open_orders
            and not second_terminal.open_orders
        )
        method_counts = dict(gateway_audit.get("mutation_method_counts") or {})
        create_counter_audit = self._create_counter_audit()
        failure_detail = ""
        if str(exc) in {"ORDER_ACK_REJECTED", "SPECIAL_ORDER_ACK_REJECTED"}:
            cause = exc.__cause__
            if cause is not None:
                failure_detail = f"{type(cause).__name__}:{cause}"
        pre_market_bootstrap_failure = bool(
            not self.market_bootstrap_completed
            and self.engine is None
            and not self.owned
            and self.normal_creates == 0
        )
        failure = {
            "status": (
                "OKX_DEMO_ECONOMIC_SESSION_FAILED"
                if self.economic_mode else "OKX_DEMO_BOUNDED_SOAK_FAILED"
            ),
            "reason": f"{type(exc).__name__}:{exc}",
            "failure_detail": failure_detail,
            "shutdown_reason": (
                "" if shutdown_error is None
                else f"{type(shutdown_error).__name__}:{shutdown_error}"
            ),
            "package_id": self.package.spec["package_id"],
            "run_id": self.package.spec["run_id"],
            "session_id": self.package.spec["session_id"],
            "source_sha256": self.package.spec["source_manifest_sha256"],
            "failure_stage": (
                "PRE_MARKET_BOOTSTRAP"
                if pre_market_bootstrap_failure else "POST_BOOTSTRAP"
            ),
            "market_bootstrap_completed": self.market_bootstrap_completed,
            "terminal_reconciliation_mode": (
                "ACCOUNT_ONLY_TWO_SNAPSHOT"
                if pre_market_bootstrap_failure and terminal_pair else "UNRESOLVED"
                if pre_market_bootstrap_failure else "FULL_SESSION_SHUTDOWN"
            ),
            "terminal_account_flat_empty": two_flat_empty,
            "two_flat_empty_snapshots": two_flat_empty,
            "terminal_account_snapshots": 2 if terminal_pair else 0,
            "terminal_account_authoritative": bool(terminal_pair),
            "final_position_btc": (
                None if second_terminal is None
                else str(second_terminal.position_btc)
            ),
            "final_open_orders": (
                None if second_terminal is None
                else len(second_terminal.open_orders)
            ),
            "controller_engine_gateway_reconciled": bool(
                economics.get("engine_reconciled")
            ),
            "normal_creates": self.normal_creates,
            **create_counter_audit,
            "normal_cancels": int(method_counts.get("cancel_order", 0)),
            "normal_bid_fills": int(economics.get("normal_bid_fills", 0)),
            "normal_ask_fills": int(economics.get("normal_ask_fills", 0)),
            "normal_fifo_round_trips": int(
                economics.get("normal_fifo_round_trips", 0)
            ),
            "normal_net_pnl_usdt": str(
                economics.get("normal_net_realized_pnl_usdt", "0")
            ),
            "normal_gross_pnl_usdt": str(
                economics.get("normal_gross_realized_pnl_usdt", "0")
            ),
            "normal_fees_usdt": str(
                economics.get("normal_fees_usdt", "0")
            ),
            "realized_spread_pnl_usdt": str(
                economics.get("realized_spread_pnl_usdt", "0")
            ),
            "inventory_pnl_usdt": str(
                economics.get("inventory_pnl_usdt", "0")
            ),
            "special_fill_count": int(economics.get("special_fill_count", 0)),
            "special_gross_pnl_usdt": str(
                economics.get("special_gross_realized_pnl_usdt", "0")
            ),
            "special_fees_usdt": str(
                economics.get("special_fees_usdt", "0")
            ),
            "special_net_pnl_usdt": str(
                economics.get("special_net_realized_pnl_usdt", "0")
            ),
            "aggregate_gross_pnl_usdt": str(
                economics.get("aggregate_gross_realized_pnl_usdt", "0")
            ),
            "aggregate_fees_usdt": str(
                economics.get("aggregate_fees_usdt", "0")
            ),
            "aggregate_net_pnl_usdt": str(
                economics.get("aggregate_net_realized_pnl_usdt", "0")
            ),
            "maximum_drawdown_usdt": str(self.maximum_drawdown_usdt),
            "markouts_usdt": list(economics.get("markouts_usdt", [])),
            "causal_reentry": list(economics.get("causal_reentry", [])),
            "unclassified_quote_mode_ticks": int(
                economics.get("unclassified_quote_mode_ticks", 0)
            ),
            "pending_causal_fill_count": len(
                economics.get("pending_causal_fills", [])
            ),
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": gateway_audit.get("live_endpoint_attempts", 0),
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        }
        failure_path = self.package.output / "soak_run/UNRESOLVED_FAILURE.json"
        if not failure_path.exists():
            _write_new(failure_path, failure)
        audit_path = self.package.output / "soak_run/audits/gateway_audit.json"
        if not audit_path.exists():
            _write_new(audit_path, gateway_audit)
        economics_path = self.package.output / "soak_run/audits/economics.json"
        if not economics_path.exists():
            _write_new(economics_path, economics)
        snapshots_path = (
            self.package.output / "soak_run/terminal/account_snapshots.json"
        )
        if terminal_pair and not snapshots_path.exists():
            _write_new(snapshots_path, {
                "first": self._account_public(first_terminal),
                "second": self._account_public(second_terminal),
            })
        failure_evidence_path = (
            self.package.output
            / "soak_run/audits/economic_session_failure_evidence.json"
        )
        if self.economic_mode and not failure_evidence_path.exists():
            failure_payload = {
                **failure,
                "evidence_kind": "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
                "accounting_audit": dict(
                    economics.get("accounting_audit") or {}
                ),
                "control_audit": {
                    "session_phase": economics.get("session_phase", "UNKNOWN"),
                    "create_budget_partition": dict(
                        economics.get("create_budget_partition") or {}
                    ),
                    "placement_reason_counters": dict(
                        economics.get("placement_reason_counters") or {}
                    ),
                    "normal_bid_fill_quantity_btc": str(
                        economics.get("normal_bid_fill_quantity_btc", "0")
                    ),
                    "normal_ask_fill_quantity_btc": str(
                        economics.get("normal_ask_fill_quantity_btc", "0")
                    ),
                    "create_counter_audit": create_counter_audit,
                },
            }
            failure_payload["evidence_sha256"] = canonical_sha256(failure_payload)
            _write_new(failure_evidence_path, failure_payload)
        scan_path = self.package.output / "soak_run/audits/secret_scan.json"
        if not scan_path.exists():
            _write_new(scan_path, self._secret_scan())
        decision_path = self.package.output / "soak_run/decision/decision.json"
        if not decision_path.exists():
            _write_new(decision_path, {
                **failure,
                "decision": "FAIL_CLOSED",
                "attempted_session_accounted": self.economic_mode,
            })
        completion_path = self.package.output / "soak_run/completion_hashes.json"
        if not completion_path.exists():
            _write_new(completion_path, self._runtime_hashes())
        failed_path = self.package.output / "soak_run/FAILED.json"
        if not failed_path.exists():
            _write_new(failed_path, {
                **failure,
                "completion_hashes_sha256": _sha256(completion_path),
                "failed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "terminal_written_last": True,
            })
        self.terminal_written = True
        return failure

    def run(self) -> dict[str, object]:
        spec = self.package.spec
        budget = spec["risk_budget"]
        terminal_pair: tuple[Any, Any] | None = None
        try:
            market = self._read("load_market", self.gateway.load_market)
            self.market_bootstrap_completed = True
            first, _ = self._account_pair()
            self._build_engine(first, market)
            self.harness.binding = first.account_binding
            self.peak_equity_usdt = Decimal(str(first.total_equity_usdt))
            self._commit_controller("PREARM_PASSED")
            self.event("PREARM_PASSED", account_binding=first.account_binding)
            started = self.now()
            self.session_started_at_ms = max(int(started * 1000), 1)
            activity_wall_seconds = int(budget["session_wall_minutes"]) * 60
            if self.repaired_economic_mode:
                reserve = int(
                    budget.get("shutdown_reconciliation_reserve_seconds", 60)
                )
                if reserve <= 0 or reserve >= activity_wall_seconds:
                    raise SoakExecutionError(
                        "shutdown reconciliation reserve is invalid"
                    )
                activity_wall_seconds -= reserve
            # At the frozen create cap, a draining session may still wait for
            # an already-acknowledged maker work-off quote.  Exiting here
            # would cancel that quote and force a special taker flatten even
            # though no further create is permitted or needed.
            while self.now() - started < activity_wall_seconds:
                if (
                    not self.repaired_economic_mode
                    and self.normal_creates
                    >= int(budget["session_normal_create_cap"])
                ):
                    break
                account = self._read("fetch_account", self.gateway.fetch_account)
                if self._loss_guard(account):
                    break
                at_create_cap_workoff = False
                if self.repaired_economic_mode:
                    account = self._reconcile_account_engine_inventory(account)
                    elapsed = self.now() - started
                    timebox_workoff = bool(
                        self.activity is not None
                        and Decimal(str(account.position_btc)) != 0
                        and elapsed >= max(activity_wall_seconds - 300, 0)
                    )
                    if (
                        self.activity is not None
                        and (
                            self.normal_creates >= self.activity.admission_create_cap
                            or timebox_workoff
                        )
                        and self.activity.phase is SessionPhase.ACTIVE
                    ):
                        self.activity.enter_draining(
                            timestamp_ms=self._activity_time(),
                            normal_creates=self.normal_creates,
                            timebox_expiring=timebox_workoff,
                        )
                    if (
                        self.activity is not None
                        and self.activity.phase is SessionPhase.DRAINING
                        and Decimal(str(account.position_btc)) == 0
                        and not self.activity.pending_fills
                    ):
                        account, exit_ready = (
                            self._reconcile_draining_cancel_boundary(account)
                        )
                        if exit_ready:
                            break
                self.maximum_inventory_btc_observed = max(
                    self.maximum_inventory_btc_observed,
                    abs(Decimal(str(account.position_btc))),
                )
                if abs(account.position_btc) > Decimal(budget["maximum_inventory_btc"]):
                    raise SoakExecutionError("inventory budget exceeded")
                if account.position_btc != 0 and not self.economic_mode:
                    break
                if (
                    self.economic_mode
                    and self.engine is not None
                    and Decimal(str(account.position_btc))
                    != self.engine.state.ledger.inventory_btc
                ):
                    raise SoakExecutionError(
                        "economic controller/engine/account inventory mismatch"
                    )
                try:
                    book = self._read(
                        "fetch_book",
                        self.gateway.fetch_book,
                        maximum_age_ms=int(budget["maximum_market_age_ms"]),
                        maximum_clock_skew_ms=int(budget["maximum_clock_skew_ms"]),
                    )
                except Exception:
                    if self.activity is not None:
                        self.activity.classify_tick(
                            timestamp_ms=max(
                                int(self.now() * 1000),
                                self.activity.last_timestamp_ms,
                                1,
                            ),
                            inventory_btc=Decimal(str(account.position_btc)),
                            market_gate_open=False,
                        )
                    raise
                self.last_mid_usdt = (
                    Decimal(str(book.best_bid)) + Decimal(str(book.best_ask))
                ) / Decimal("2")
                quotes = self._fee_aware_quote_plan(account=account, book=book)
                if self.repaired_economic_mode:
                    targets = dict(quotes)
                    at_create_cap_workoff = bool(
                        self.activity is not None
                        and self.activity.phase is SessionPhase.DRAINING
                        and Decimal(str(account.position_btc)) != 0
                        and self.normal_creates
                        >= int(budget["session_normal_create_cap"])
                    )
                    invalid_existing = [
                        client_id
                        for client_id, (side, price) in self.owned_quotes.items()
                        if side not in targets
                        or not quote_still_valid(
                            existing_price_usdt=price,
                            target_price_usdt=targets[side],
                            tick_size_usdt=Decimal(
                                str(self.gateway.market_spec.price_tick)
                            ),
                            threshold_ticks=(
                                self.sample_efficiency_policy.retention_threshold_ticks(
                                    Decimal(str(account.position_btc)),
                                    draining=(
                                        self.activity is not None
                                        and self.activity.phase is SessionPhase.DRAINING
                                    ),
                                )
                                if self.sample_efficiency_policy is not None
                                else int(budget["quote_retention_threshold_ticks"])
                            ),
                        )
                    ]
                    draining_workoff_quote_ids = [
                        client_id
                        for client_id, (side, _) in self.owned_quotes.items()
                        if (
                            self.activity is not None
                            and self.activity.phase is SessionPhase.DRAINING
                            and Decimal(str(account.position_btc)) != 0
                            and side in targets
                        )
                    ]
                    for client_id in draining_workoff_quote_ids:
                        self.draining_workoff_quote_observations[client_id] = (
                            self.draining_workoff_quote_observations.get(client_id, 0) + 1
                        )
                    refresh_due = bool(
                        self.sample_efficiency_policy is not None
                        and self.normal_creates
                        < int(budget["session_normal_create_cap"])
                        and any(
                            self.sample_efficiency_policy.refresh_draining_workoff(
                                observations=self.draining_workoff_quote_observations[
                                    client_id
                                ],
                                refreshes=self.draining_workoff_refreshes,
                                inventory_btc=Decimal(str(account.position_btc)),
                            )
                            for client_id in draining_workoff_quote_ids
                        )
                    )
                    if refresh_due:
                        invalid_existing = list(self.owned_quotes)
                        self.draining_workoff_refreshes += 1
                        if self.activity is not None:
                            self.activity.record_placement_reason(
                                reason="DRAINING_WORKOFF_REFRESH_DUE",
                                timestamp_ms=self._activity_time(),
                            )
                    retain_workoff_at_cap = bool(
                        at_create_cap_workoff
                        and invalid_existing
                        and self.owned_quotes
                        and all(
                            side in targets
                            for side, _ in self.owned_quotes.values()
                        )
                    )
                    if invalid_existing and not retain_workoff_at_cap:
                        self._cancel_owned()
                        quotes = ()
                    elif retain_workoff_at_cap and self.activity is not None:
                        # There is no remaining create budget for a safe
                        # replacement.  Keep the owned, correctly-sided,
                        # post-only work-off quote and observe it only.
                        self.activity.record_placement_reason(
                            reason="DRAINING_WORKOFF_RETAINED_AT_CAP",
                            timestamp_ms=self._activity_time(),
                        )
                    existing_sides = {
                        side for side, _ in self.owned_quotes.values()
                    }
                    retained = [side for side in targets if side in existing_sides]
                    for _ in retained:
                        if self.activity is not None:
                            self.activity.record_placement_reason(
                                reason="QUOTE_STILL_VALID",
                                timestamp_ms=self._activity_time(),
                            )
                    quotes = tuple(
                        (side, price)
                        for side, price in quotes
                        if side not in existing_sides
                    )
                if self.activity is not None:
                    self.activity.classify_tick(
                        timestamp_ms=self._activity_time(),
                        inventory_btc=Decimal(str(account.position_btc)),
                        fee_gate_open=bool(quotes or self.owned_quotes),
                        unresolved_intent=bool(self.pending_intent),
                    )
                if at_create_cap_workoff and self.owned_quotes:
                    # Read-only observation is deliberately paced; it gives
                    # the retained maker order a chance to fill without
                    # consuming a create or opening new exposure.
                    self.sleep(int(budget["observation_interval_ms"]) / 1000)
                    self._ingest_trades()
                    continue
                if (
                    self.normal_creates
                    >= int(budget["session_normal_create_cap"])
                    and not self.owned_quotes
                ):
                    break
                for side, price in quotes:
                    if self.normal_creates >= int(budget["session_normal_create_cap"]):
                        break
                    self._require_placement_gate(side)
                    client_id = "fr" + canonical_sha256({
                        "run_id": spec["run_id"],
                        "sequence": self.normal_creates + 1,
                    })[:28]
                    self.harness.begin_create(client_id)
                    self.pending_intent = client_id
                    self.owned.add(client_id)
                    self._commit_controller(
                        "CREATE_INTENT_DURABLE", side=side, price=str(price)
                    )
                    self.event("CREATE_INTENT", client_order_id=client_id, side=side)
                    dispatched_before = self.normal_creates

                    def before_dispatch() -> None:
                        self.harness.dispatch_create()
                        self._commit_controller(
                            "CREATE_DISPATCHED_DURABLE",
                            side=side,
                            mutation_retry=False,
                        )

                    try:
                        response = self.gateway.submit_post_only(
                            client_order_id=client_id,
                            side=side,
                            price=price,
                            quantity_btc=Decimal("0.01"),
                            maximum_age_ms=int(budget["maximum_market_age_ms"]),
                            maximum_clock_skew_ms=int(budget["maximum_clock_skew_ms"]),
                            before_dispatch=before_dispatch,
                        )
                        self._sync_normal_create_dispatches()
                        if self.harness.pending_intent == client_id:
                            before_dispatch()
                    except PostOnlyWouldCross as exc:
                        self._sync_normal_create_dispatches()
                        if self.normal_creates != dispatched_before:
                            raise SoakExecutionError(
                                "pre-dispatch post-only block changed mutation counters"
                            ) from exc
                        self.harness.block_before_dispatch(
                            client_id,
                            classification="PRE_DISPATCH_POST_ONLY_WOULD_CROSS",
                        )
                        self.owned.discard(client_id)
                        self.owned_quotes.pop(client_id, None)
                        self.draining_workoff_quote_observations.pop(client_id, None)
                        self.pending_intent = ""
                        if self.activity is not None:
                            self.activity.record_placement_reason(
                                reason="POST_ONLY_CROSS_BLOCKED",
                                timestamp_ms=self._activity_time(),
                            )
                        self._commit_controller(
                            "CREATE_BLOCKED_BEFORE_DISPATCH",
                            classification=(
                                "PRE_DISPATCH_POST_ONLY_WOULD_CROSS"
                            ),
                            side=side,
                            price=str(price),
                            best_bid=str(exc.best_bid),
                            best_ask=str(exc.best_ask),
                            mutation_dispatched=False,
                            mutation_retry=False,
                        )
                        self.event(
                            "CREATE_BLOCKED_BEFORE_DISPATCH",
                            classification=(
                                "PRE_DISPATCH_POST_ONLY_WOULD_CROSS"
                            ),
                            side=side,
                            mutation_dispatched=False,
                            mutation_retry=False,
                        )
                        continue
                    except PostOnlyCreateRejected as exc:
                        self._sync_normal_create_dispatches()
                        if self.harness.pending_intent == client_id:
                            before_dispatch()
                        self.harness.acknowledge_rejected_create(
                            client_id,
                            classification="REJECTED_TERMINAL_ABSENT",
                        )
                        self.normal_create_rejections += 1
                        self.owned.discard(client_id)
                        self.owned_quotes.pop(client_id, None)
                        self.draining_workoff_quote_observations.pop(client_id, None)
                        self.pending_intent = ""
                        if self.activity is not None:
                            self.activity.record_placement_reason(
                                reason="POST_ONLY_REJECTED",
                                timestamp_ms=self._activity_time(),
                            )
                        self._commit_controller(
                            "CREATE_REJECTION_RECONCILED",
                            classification="REJECTED_TERMINAL_ABSENT",
                            exchange_code=exc.exchange_code,
                            mutation_retry=False,
                        )
                        self.event(
                            "CREATE_REJECTION_RECONCILED",
                            classification="REJECTED_TERMINAL_ABSENT",
                            exchange_code=exc.exchange_code,
                            mutation_retry=False,
                        )
                        continue
                    except Exception:
                        self._sync_normal_create_dispatches()
                        if (
                            self.normal_creates > dispatched_before
                            and self.harness.pending_intent == client_id
                        ):
                            before_dispatch()
                        if (
                            self.normal_creates == dispatched_before
                            and self.harness.pending_intent == client_id
                        ):
                            self.harness.timeout_before_dispatch()
                            self.owned.discard(client_id)
                            self.pending_intent = ""
                            self._commit_controller(
                                "CREATE_NOT_DISPATCHED",
                                side=side,
                                mutation_retry=False,
                            )
                        raise
                    order_id = str(response.get("id") or "")
                    self.harness.acknowledge_create(client_id, order_id)
                    self.engine.record_owned_order_ack(OwnedOrder(
                        client_order_id=client_id,
                        order_id=order_id,
                        side=side,
                        quantity_btc=Decimal("0.01"),
                        remaining_btc=Decimal("0.01"),
                        reduce_only=False,
                        post_only_acknowledged=True,
                    ))
                    self.normal_create_acknowledgements += 1
                    self.owned_quotes[client_id] = (side, Decimal(str(price)))
                    self.draining_workoff_quote_observations[client_id] = 0
                    if self.activity is not None and account.position_btc != 0:
                        self.activity.bind_maker_reentry_order(
                            client_order_id=client_id,
                            side=side,
                            quantity_btc=Decimal("0.01"),
                            timestamp_ms=self._activity_time(),
                        )
                    if side == "buy":
                        self.maximum_owned_bid_observed = max(
                            self.maximum_owned_bid_observed, 1
                        )
                    else:
                        self.maximum_owned_ask_observed = max(
                            self.maximum_owned_ask_observed, 1
                        )
                    self.pending_intent = ""
                    self._commit_controller("CREATE_ACKNOWLEDGED", order_id=order_id)
                    self.event("CREATE_ACK", client_order_id=client_id, side=side)
                self.sleep(int(budget["observation_interval_ms"]) / 1000)
                if self.repaired_economic_mode:
                    self._ingest_trades()
                else:
                    self._cancel_owned()
                if (
                    not self.economic_mode
                    and self.engine
                    and self.engine.state.ledger.normal_fill_count
                ):
                    break
            first_terminal, second_terminal, reconciled = self._shutdown()
            terminal_pair = (first_terminal, second_terminal)
            if not reconciled:
                raise SoakExecutionError("terminal accounting reconciliation failed")
            if self.repaired_economic_mode and self.activity is not None:
                self.activity.finish(timestamp_ms=self._activity_time())
            return self._finalize_success(first_terminal, second_terminal)
        except Exception as exc:
            shutdown_error: Exception | None = None
            if terminal_pair is None:
                try:
                    if self.engine is None and not self.owned:
                        # A pre-arm non-flat/open/foreign account is outside
                        # controller ownership.  Report it but never mutate it.
                        terminal_pair = self._account_only_pair()
                    else:
                        first_terminal, second_terminal, _ = self._shutdown()
                        terminal_pair = (first_terminal, second_terminal)
                except Exception as shutdown_exc:
                    shutdown_error = shutdown_exc
            return self._finalize_failure(exc, terminal_pair, shutdown_error)


def _start_failure(
    package: SoakPackage,
    exc: Exception,
    lease: DurableLease | None,
) -> dict[str, object]:
    try:
        if lease is not None:
            lease.release()
    except Exception:
        pass
    failure = {
        "status": "OKX_DEMO_BOUNDED_SOAK_FAILED",
        "reason": f"{type(exc).__name__}:{exc}",
        "normal_creates": 0,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
    }
    path = package.output / "soak_run/UNRESOLVED_FAILURE.json"
    if not path.exists():
        _write_new(path, failure)
    failed = package.output / "soak_run/FAILED.json"
    if not failed.exists():
        _write_new(failed, failure)
    return failure


def start(package: SoakPackage, arm_token: str) -> dict[str, object]:
    if arm_token != package.expected_arm_token:
        raise SoakExecutionError("soak arm token mismatch")
    _verify_campaign_authorization(package)
    marker = package.output / "soak_run/SOAK_EXECUTION_ARMED.json"
    _write_new(marker, {
        "package_id": package.spec["package_id"],
        "run_id": package.spec["run_id"],
        "session_id": package.spec["session_id"],
        "arm_token_sha256": hashlib.sha256(arm_token.encode("utf-8")).hexdigest(),
        "arm_token_serialized": False,
        "production_authorized": False,
    })
    lease = DurableLease(
        package.output / "soak_run/state/lease.json",
        package.spec["session_id"],
    )
    ttl_ms = (
        int(package.spec["risk_budget"]["session_wall_minutes"]) + 5
    ) * 60 * 1000
    try:
        lease.acquire(ttl_ms)
        gateway, credentials = _build_gateway()
    except Exception as exc:
        return _start_failure(package, exc, lease)
    try:
        return BoundedSoakExecutor(
            package,
            gateway,
            credentials=credentials,
            lease=lease,
        ).run()
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("describe", "start"))
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--arm-token", default="")
    args = parser.parse_args()
    try:
        package = load_package(ROOT, args.package_id)
        result = (
            {
                "package_id": package.spec["package_id"],
                "run_id": package.spec["run_id"],
                "session_id": package.spec["session_id"],
                "expected_arm_token": package.expected_arm_token,
                "execution_marker_exists": (
                    package.output / "soak_run/SOAK_EXECUTION_ARMED.json"
                ).exists(),
                "production_authorized": False,
            }
            if args.command == "describe" else start(package, args.arm_token)
        )
    except Exception as exc:
        print(json.dumps({"status": "SOAK_EXECUTOR_FAILED", "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") != "OKX_DEMO_BOUNDED_SOAK_FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Opt-in, fail-closed OKX demo adapter with no live execution path."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from market_spec import MarketSpec, OrderValidationError, validate_order
from okx_demo_profile import PromotedProfile
from okx_demo_state import (
    DEMO_ENVIRONMENT,
    DemoRuntimeState,
    DemoStateError,
    DemoStateStore,
)
from okx_execution_safety import OrderIntent
from utils import fetch_market_info


class ExecutionMode(str, Enum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    OKX_DEMO = "OKX_DEMO"
    LIVE = "LIVE"


class DemoAdapterError(RuntimeError):
    """Known fail-closed adapter error."""


class ClockSkewBudgetError(DemoAdapterError):
    """Sanitized proof that the frozen clock-health bound was exceeded."""

    def __init__(self, *, clock_skew_ms: int, maximum_clock_skew_ms: int) -> None:
        self.clock_skew_ms = clock_skew_ms
        self.maximum_clock_skew_ms = maximum_clock_skew_ms
        super().__init__("clock skew exceeds frozen limit")


class AmbiguousExchangeState(DemoAdapterError):
    """Exchange outcome could not be proven and all new orders must halt."""


@dataclass(frozen=True)
class DemoAdapterConfig:
    mode: ExecutionMode
    symbol: str = "BTC/USDT:USDT"
    margin_mode: str = "isolated"
    position_mode: str = "net_mode"
    leverage: int = 3
    maximum_clock_skew_ms: int = 1_500
    explicit_arm_token: str = ""

    def validate(self, session_id: str) -> None:
        if self.mode is ExecutionMode.LIVE:
            raise DemoAdapterError("LIVE mode is unavailable in this protocol")
        if self.mode is ExecutionMode.OKX_DEMO:
            expected = f"OKX_DEMO:{session_id}"
            if self.explicit_arm_token != expected:
                raise DemoAdapterError("OKX demo is not explicitly armed for this session")
        if self.symbol != "BTC/USDT:USDT":
            raise DemoAdapterError("only the frozen BTC/USDT:USDT symbol is allowed")
        if self.margin_mode != "isolated" or self.position_mode != "net_mode":
            raise DemoAdapterError("frozen demo account mode mismatch")
        if self.leverage != 3:
            raise DemoAdapterError("frozen leverage must be 3")


@dataclass(frozen=True)
class AccountSnapshot:
    account_uid: str
    position_mode: str
    leverage: float
    total_equity_usdt: float
    free_equity_usdt: float
    position_btc: float
    average_entry_price: float
    maintenance_margin_usdt: float
    open_orders: tuple[dict[str, Any], ...]
    recent_trades: tuple[dict[str, Any], ...]
    clock_skew_ms: int
    maker_fee_rate: float | None
    taker_fee_rate: float | None

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["account_uid"] = "present"
        payload["open_orders"] = len(self.open_orders)
        payload["recent_trades"] = len(self.recent_trades)
        return payload


@dataclass(frozen=True)
class SubmittedOrder:
    order_id: str
    client_order_id: str
    side: str
    price: float
    contracts: float
    reduce_only: bool
    post_only_confirmed: bool


def build_ccxt_demo_exchange(*, api_key: str, api_secret: str, passphrase: str) -> Any:
    """Construct an OKX instance and prove simulated-trading mode locally."""
    if not all((api_key, api_secret, passphrase)):
        raise DemoAdapterError("key, secret, and passphrase are all required")
    import ccxt

    exchange = ccxt.okx({
        "apiKey": api_key,
        "secret": api_secret,
        "password": passphrase,
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
            "adjustForTimeDifference": True,
        },
    })
    exchange.set_sandbox_mode(True)
    if exchange.options.get("sandboxMode") is not True:
        raise DemoAdapterError("CCXT sandboxMode did not latch")
    simulated = str(exchange.headers.get("x-simulated-trading", ""))
    if simulated != "1":
        raise DemoAdapterError("OKX simulated-trading header is absent")
    return exchange


class OkxDemoAdapter:
    """Narrow adapter used only by the frozen demo protocol runner."""

    def __init__(
        self,
        *,
        exchange: Any,
        config: DemoAdapterConfig,
        promoted_profile: PromotedProfile,
        session_id: str,
        state_store: DemoStateStore,
    ) -> None:
        config.validate(session_id)
        self.exchange = exchange
        self.config = config
        self.profile = promoted_profile
        self.session_id = session_id
        self.state_store = state_store
        self.market_info: dict[str, Any] = {}
        self.market_spec: MarketSpec | None = None
        self.state: DemoRuntimeState | None = None
        self.latest_snapshot: AccountSnapshot | None = None
        self.halted_reason = ""
        self.last_new_trade_count = 0
        self.last_new_trades: tuple[dict[str, Any], ...] = ()
        self.market_metadata_quarantine: tuple[dict[str, Any], ...] = ()
        self.last_account_only_diagnostic: dict[str, object] | None = None
        self.dispatched_order_ids: set[str] = set()
        self.dispatched_client_order_ids: set[str] = set()
        self.session_owned_fills: list[dict[str, Any]] = []
        self.foreign_fills_observed: list[dict[str, Any]] = []
        self.session_fifo_round_trips: int = 0
        self.session_owned_realized_pnl: Decimal = Decimal("0.0")
        self.session_owned_fees_usdt: Decimal = Decimal("0.0")

    @property
    def session_maker_bid_fills(self) -> int:
        return sum(
            1 for fill in self.session_owned_fills
            if fill.get("side") == "buy" and (
                fill.get("takerOrMaker") == "maker"
                or (fill.get("info") or {}).get("execType") in {"M", "maker"}
            )
        )

    @property
    def session_maker_ask_fills(self) -> int:
        return sum(
            1 for fill in self.session_owned_fills
            if fill.get("side") == "sell" and (
                fill.get("takerOrMaker") == "maker"
                or (fill.get("info") or {}).get("execType") in {"M", "maker"}
            )
        )

    @property
    def session_maker_fills_total(self) -> int:
        return self.session_maker_bid_fills + self.session_maker_ask_fills

    @property
    def session_taker_fills_total(self) -> int:
        return sum(
            1 for fill in self.session_owned_fills
            if fill.get("takerOrMaker") == "taker"
            or (fill.get("info") or {}).get("execType") in {"T", "taker"}
        )

    @property
    def session_filled_btc_quantity(self) -> float:
        if self.market_spec is None:
            return 0.0
        total_contracts = sum(
            Decimal(str(fill.get("amount") or 0)) for fill in self.session_owned_fills
        )
        return float(self.market_spec.contracts_to_base(total_contracts))

    def _halt(self, reason: str) -> None:
        self.halted_reason = reason

    def _latch_halt(self, reason: str) -> None:
        """Halt now and persist the halt across a supervisor restart when possible."""
        self._halt(reason)
        if self.state is None:
            return
        self.state.kill_switch.activate(reason)
        try:
            self.state_store.save(self.state)
        except DemoStateError:
            # The caller is already on a fail-closed path. Preserve the original
            # exchange ambiguity while the in-memory latch blocks this process.
            pass

    @property
    def allow_new_orders(self) -> bool:
        return not self.halted_reason and self.state is not None

    def verify_demo_transport(self) -> None:
        if self.config.mode is ExecutionMode.OFFLINE_FIXTURE:
            return
        if self.exchange.options.get("sandboxMode") is not True:
            raise DemoAdapterError("exchange is not in sandbox mode")
        if str(self.exchange.headers.get("x-simulated-trading", "")) != "1":
            raise DemoAdapterError("simulated-trading header is missing")

    def preflight(self) -> AccountSnapshot:
        """Read and reconcile every authoritative input before order permission."""
        self.verify_demo_transport()
        try:
            if self.config.mode is ExecutionMode.OKX_DEMO:
                raw_markets = list(self.exchange.fetch_markets())
                invalid = [
                    row for row in raw_markets
                    if not row.get("id") or not row.get("symbol")
                ]
                valid = [row for row in raw_markets if row not in invalid]
                if not valid or not any(
                    row.get("symbol") == self.config.symbol for row in valid
                ):
                    raise DemoAdapterError("selected market missing after metadata validation")
                self.market_metadata_quarantine = tuple({
                    "type": row.get("type"),
                    "instrument_type": (row.get("info") or {}).get("instType"),
                    "state": (row.get("info") or {}).get("state"),
                    "reason": "missing_id_or_symbol",
                } for row in invalid)
                self.exchange.set_markets(valid)
            self.market_info = fetch_market_info(
                self.exchange, self.config.symbol, allow_fallback=False
            )
            self.market_spec = self.market_info["market_spec"]
            if self.market_spec.symbol != self.config.symbol:
                raise DemoAdapterError("market symbol mismatch")
            if not self.market_spec.linear or self.market_spec.inverse:
                raise DemoAdapterError("market must be a linear USDT contract")
            if self.market_spec.contract_size != Decimal("0.01"):
                raise DemoAdapterError("contract size does not match one frozen lot")

            observed_before_ms = int(time.time() * 1000)
            server_ms = int(self.exchange.fetch_time())
            observed_after_ms = int(time.time() * 1000)
            # The gate retains the full observation interval: transport latency
            # cannot make an out-of-budget clock look healthy.
            clock_skew = max(
                abs(observed_before_ms - server_ms),
                abs(observed_after_ms - server_ms),
            )
            if clock_skew > self.config.maximum_clock_skew_ms:
                raise ClockSkewBudgetError(
                    clock_skew_ms=clock_skew,
                    maximum_clock_skew_ms=self.config.maximum_clock_skew_ms,
                )

            balance = self.exchange.fetch_balance()
            usdt = balance.get("USDT", {})
            total = float(usdt.get("total") or balance.get("total", {}).get("USDT") or 0.0)
            free = float(usdt.get("free") or balance.get("free", {}).get("USDT") or 0.0)
            if total <= 0 or free < 0:
                raise DemoAdapterError("invalid authoritative USDT balance")

            account_response = self.exchange.privateGetAccountConfig()
            account_rows = account_response.get("data") or []
            if len(account_rows) != 1:
                raise DemoAdapterError("unknown account configuration")
            account = account_rows[0]
            account_uid = str(account.get("uid") or "")
            position_mode = str(account.get("posMode") or "")
            if not account_uid or not position_mode:
                raise DemoAdapterError("account identity/mode is incomplete")

            positions = self.exchange.fetch_positions([self.config.symbol])
            position_btc = Decimal("0")
            entry_price = 0.0
            maintenance_margin = 0.0
            for position in positions:
                if position.get("symbol") is None and Decimal(
                    str(position.get("contracts") or 0)
                ) != 0:
                    raise DemoAdapterError("nonzero position lacks a market identity")
                if position.get("symbol") != self.config.symbol:
                    continue
                contracts = Decimal(str(position.get("contracts") or 0))
                if contracts == 0:
                    continue
                side = str(position.get("side") or "")
                if side not in {"long", "short"}:
                    raise DemoAdapterError("unknown nonzero position side")
                base = self.market_spec.contracts_to_base(abs(contracts))
                position_btc += base if side == "long" else -base
                entry_price = float(position.get("entryPrice") or 0.0)
                mm = position.get("maintenanceMargin")
                if mm is None:
                    mm = (position.get("info") or {}).get("mmr")
                if mm is None:
                    raise DemoAdapterError("nonzero position lacks maintenance margin")
                maintenance_margin += float(mm)

            open_orders = tuple(self.exchange.fetch_open_orders(self.config.symbol))
            recent_trades = tuple(
                self.exchange.fetch_my_trades(self.config.symbol, limit=100)
            )
            leverage_response = self.exchange.fetch_leverage(
                self.config.symbol, {"mgnMode": self.config.margin_mode}
            )
            leverage = float(
                leverage_response.get("longLeverage")
                or leverage_response.get("shortLeverage")
                or leverage_response.get("leverage")
                or 0.0
            )
            if leverage <= 0:
                raise DemoAdapterError("authoritative leverage is unknown")

            fee = self.exchange.fetch_trading_fee(self.config.symbol)
            maker_fee = fee.get("maker")
            taker_fee = fee.get("taker")
            if maker_fee is None or taker_fee is None:
                raise DemoAdapterError("authoritative trading fees are incomplete")
            snapshot = AccountSnapshot(
                account_uid=account_uid,
                position_mode=position_mode,
                leverage=leverage,
                total_equity_usdt=total,
                free_equity_usdt=free,
                position_btc=float(position_btc),
                average_entry_price=entry_price,
                maintenance_margin_usdt=maintenance_margin,
                open_orders=open_orders,
                recent_trades=recent_trades,
                clock_skew_ms=clock_skew,
                maker_fee_rate=float(maker_fee) if maker_fee is not None else None,
                taker_fee_rate=float(taker_fee) if taker_fee is not None else None,
            )
            self.last_account_only_diagnostic = self._account_only_diagnostic(
                snapshot=snapshot,
                positions=positions,
            )
            self._restore_or_create_state(snapshot)
            self._reconcile_snapshot(snapshot, startup=True)
            self.latest_snapshot = snapshot
            return snapshot
        except Exception as exc:
            self._halt(f"PREFLIGHT_FAILED:{type(exc).__name__}")
            if isinstance(exc, DemoAdapterError):
                raise
            raise DemoAdapterError("authoritative preflight failed") from exc

    @staticmethod
    def _identifier_digest(
        *, kind: str, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> str:
        identifiers: list[dict[str, str]] = []
        for row in rows:
            info = row.get("info") or {}
            if not isinstance(info, dict):
                info = {}
            if kind == "position":
                identifiers.append({
                    "id": str(row.get("id") or info.get("posId") or ""),
                    "instrument": str(
                        row.get("symbol") or info.get("instId") or ""
                    ),
                    "side": str(row.get("side") or info.get("posSide") or ""),
                })
            else:
                identifiers.append({
                    "id": str(row.get("id") or info.get("ordId") or ""),
                    "client_id": str(
                        row.get("clientOrderId") or info.get("clOrdId") or ""
                    ),
                    "instrument": str(
                        row.get("symbol") or info.get("instId") or ""
                    ),
                })
        canonical = json.dumps(
            sorted(
                identifiers,
                key=lambda value: (
                    value.get("id", ""),
                    value.get("client_id", ""),
                    value.get("instrument", ""),
                    value.get("side", ""),
                ),
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _account_only_diagnostic(
        self,
        *,
        snapshot: AccountSnapshot,
        positions: list[dict[str, Any]],
    ) -> dict[str, object]:
        signed_position = Decimal(str(snapshot.position_btc))
        if signed_position == 0:
            signed_position = Decimal("0")
        return {
            "signed_position_btc": format(signed_position, "f"),
            "position_row_count": len(positions),
            "open_order_count": len(snapshot.open_orders),
            "account_binding_sha256": hashlib.sha256(
                snapshot.account_uid.encode("utf-8")
            ).hexdigest(),
            "position_identifiers_sha256": self._identifier_digest(
                kind="position", rows=positions
            ),
            "open_order_identifiers_sha256": self._identifier_digest(
                kind="order", rows=snapshot.open_orders
            ),
        }

    def _restore_or_create_state(self, snapshot: AccountSnapshot) -> None:
        assert self.market_spec is not None
        account_binding = hashlib.sha256(
            snapshot.account_uid.encode("utf-8")
        ).hexdigest()
        restored = self.state_store.load(
            account_uid=account_binding,
            symbol=self.config.symbol,
            market_fingerprint=self.market_spec.fingerprint,
            profile_binding_sha256=self.profile.binding_sha256,
        )
        if restored is None:
            if snapshot.open_orders or abs(snapshot.position_btc) > 1e-12:
                raise DemoAdapterError("fresh session has unowned orders or exposure")
            restored = DemoRuntimeState(
                environment=DEMO_ENVIRONMENT,
                account_uid=account_binding,
                symbol=self.config.symbol,
                market_fingerprint=self.market_spec.fingerprint,
                profile_binding_sha256=self.profile.binding_sha256,
                session_id=self.session_id,
                inventory_btc=0.0,
                peak_equity_usdt=snapshot.total_equity_usdt,
                current_equity_usdt=snapshot.total_equity_usdt,
            )
            restored.fill_cursor.select_new(snapshot.recent_trades)
            self.state_store.save(restored)
        elif restored.session_id != self.session_id:
            if restored.owned_open_orders or restored.kill_switch.active:
                raise DemoAdapterError("prior session has unresolved state")
            restored.session_id = self.session_id
            restored.client_order_generation = 0
        if restored.owned_open_orders:
            for cid, rec in restored.owned_open_orders.items():
                self.dispatched_client_order_ids.add(cid)
                if rec.get("order_id"):
                    self.dispatched_order_ids.add(str(rec["order_id"]))
        self.state = restored

    @staticmethod
    def _client_id(order: dict[str, Any]) -> str:
        return str(
            order.get("clientOrderId")
            or (order.get("info") or {}).get("clOrdId")
            or ""
        )

    def _reconcile_snapshot(self, snapshot: AccountSnapshot, *, startup: bool = False) -> None:
        if self.state is None or self.market_spec is None:
            raise DemoAdapterError("runtime state is unavailable")
        position_before_trades = Decimal(str(self.state.inventory_btc))
        expected = set(self.state.owned_open_orders)
        new_trades = self.state.fill_cursor.select_new(snapshot.recent_trades)
        self.last_new_trade_count = len(new_trades)
        self.last_new_trades = tuple(new_trades)
        for trade in new_trades:
            order_id = str(trade.get("order") or "")
            client_id = self._client_id(trade)
            is_owned = (
                (order_id and order_id in self.dispatched_order_ids)
                or (client_id and client_id in self.dispatched_client_order_ids)
                or (client_id and client_id in expected)
                or (order_id and any(row.get("order_id") == order_id for row in self.state.owned_open_orders.values()))
                or (order_id and self.state.flatten_order_id == order_id)
                or (client_id and self.state.flatten_client_order_id == client_id)
            )
            self._apply_trade_accounting(trade, is_owned=is_owned)
            if is_owned:
                self.session_owned_fills.append(trade)
            else:
                self.foreign_fills_observed.append(trade)
        live_by_client = {self._client_id(order): order for order in snapshot.open_orders}
        foreign = [
            str(order.get("id") or "unknown")
            for order in snapshot.open_orders
            if self._client_id(order) not in expected
        ]
        if foreign:
            raise DemoAdapterError("foreign or unowned open orders detected")
        duplicates = [
            side for side in ("buy", "sell")
            if sum(1 for order in snapshot.open_orders if order.get("side") == side) > 1
        ]
        if duplicates:
            raise DemoAdapterError("duplicate same-side open orders detected")
        missing = expected - set(live_by_client)
        if missing:
            missing_order_ids = {
                self.state.owned_open_orders[cid]["order_id"] for cid in missing
            }
            traded_order_ids = {
                str(row.get("order") or "") for row in snapshot.recent_trades
            }
            unresolved = missing_order_ids - traded_order_ids
            if unresolved:
                raise DemoAdapterError("missing expected orders are unresolved")
            for cid in missing:
                self.state.owned_open_orders.pop(cid, None)
        calculated_position = Decimal(str(self.state.inventory_btc))
        exchange_position = Decimal(str(snapshot.position_btc))
        position_mismatch = abs(exchange_position - calculated_position)
        same_flatten_direction = (
            exchange_position == 0
            or position_before_trades == 0
            or (exchange_position > 0) == (position_before_trades > 0)
        )
        flatten_progress_or_lag = (
            self.state.flatten_state == "SUBMITTED"
            and same_flatten_direction
            and abs(exchange_position) <= abs(position_before_trades)
        )
        if position_mismatch > Decimal("1e-12") and not flatten_progress_or_lag:
            raise DemoAdapterError("position and fill accounting do not reconcile")
        # Empty exchange position is authoritative and explicitly zeros local state.
        self.state.inventory_btc = snapshot.position_btc
        self.state.average_entry_price = (
            snapshot.average_entry_price if snapshot.position_btc else 0.0
        )
        self.state.current_equity_usdt = snapshot.total_equity_usdt
        self.state.peak_equity_usdt = max(
            self.state.peak_equity_usdt, snapshot.total_equity_usdt
        )
        self.state_store.save(self.state)

    def _apply_trade_accounting(self, trade: dict[str, Any], *, is_owned: bool = True) -> None:
        if self.state is None or self.market_spec is None:
            raise DemoAdapterError("runtime state is unavailable")
        side = str(trade.get("side") or "")
        price = Decimal(str(trade.get("price") or 0))
        contracts = Decimal(str(trade.get("amount") or 0))
        if side not in {"buy", "sell"} or price <= 0 or contracts <= 0:
            raise DemoAdapterError("trade accounting input is incomplete")
        quantity = self.market_spec.contracts_to_base(contracts)
        signed_trade = quantity if side == "buy" else -quantity
        current = Decimal(str(self.state.inventory_btc))
        average = Decimal(str(self.state.average_entry_price or 0))
        realized = Decimal("0")
        resulting = current + signed_trade
        same_direction = current == 0 or current * signed_trade > 0
        if same_direction:
            total_quantity = abs(current) + abs(signed_trade)
            new_average = (
                (abs(current) * average + abs(signed_trade) * price)
                / total_quantity
            )
        else:
            closed = min(abs(current), abs(signed_trade))
            realized = closed * (price - average) * (
                Decimal("1") if current > 0 else Decimal("-1")
            )
            if is_owned and self.profile and hasattr(self.profile, "strategy") and self.profile.strategy:
                lot_size = Decimal(str(self.profile.strategy.fixed_lot_size_btc))
                if lot_size > 0 and closed >= lot_size:
                    self.session_fifo_round_trips += int(closed / lot_size)
            if resulting == 0:
                new_average = Decimal("0")
            elif current * resulting > 0:
                new_average = average
            else:
                new_average = price
        fee = trade.get("fee") or {}
        fee_cost = abs(Decimal(str(fee.get("cost") or 0)))
        fee_currency = str(fee.get("currency") or "USDT").upper()
        if fee_currency == "USDT":
            fee_usdt = fee_cost
        elif fee_currency == "BTC":
            fee_usdt = fee_cost * price
        else:
            raise DemoAdapterError("unsupported trade fee currency")
        self.state.inventory_btc = float(resulting)
        self.state.average_entry_price = float(new_average)
        if is_owned:
            self.session_owned_realized_pnl += realized
            self.session_owned_fees_usdt += fee_usdt
            self.state.gross_realized_pnl_usdt += float(realized)
            self.state.total_fees_usdt += float(fee_usdt)
            self.state.net_realized_pnl_usdt = (
                self.state.gross_realized_pnl_usdt - self.state.total_fees_usdt
            )

    def configure_and_verify_account_mode(self) -> None:
        """Set demo-only net mode/leverage, then prove the resulting state."""
        if self.config.mode is not ExecutionMode.OKX_DEMO:
            return
        if self.state is None:
            raise DemoAdapterError("preflight must run before account configuration")
        if (
            self.state.owned_open_orders
            or abs(self.state.inventory_btc) > 1e-12
            or self.state.kill_switch.active
        ):
            raise DemoAdapterError("cannot configure account mode with exposure")
        self.exchange.set_position_mode(False, self.config.symbol)
        self.exchange.set_leverage(
            self.config.leverage,
            self.config.symbol,
            {"mgnMode": self.config.margin_mode},
        )
        position_mode = self.exchange.fetch_position_mode(self.config.symbol)
        if position_mode.get("hedged") is not False:
            raise DemoAdapterError("position mode did not reconcile to net mode")
        leverage = self.exchange.fetch_leverage(
            self.config.symbol, {"mgnMode": self.config.margin_mode}
        )
        values = {
            float(value) for value in (
                leverage.get("longLeverage"), leverage.get("shortLeverage"),
                leverage.get("leverage"),
            ) if value not in (None, "")
        }
        if float(self.config.leverage) not in values:
            raise DemoAdapterError("leverage did not reconcile to frozen value")

    def _resolve_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return self.exchange.fetch_order(
                client_order_id,
                self.config.symbol,
                {"clientOrderId": client_order_id},
            )
        except Exception as exc:
            name = type(exc).__name__.lower()
            message = str(exc).lower()
            if "ordernotfound" in name or "not found" in message or "does not exist" in message:
                return None
            raise AmbiguousExchangeState("client-order resolution failed") from exc

    def submit_post_only(
        self,
        *,
        side: str,
        price: float,
        best_bid: float,
        best_ask: float,
        market_timestamp_ms: int,
    ) -> SubmittedOrder:
        if not self.allow_new_orders or self.state is None or self.market_spec is None:
            raise DemoAdapterError("new orders are halted")
        if self.latest_snapshot is None:
            raise DemoAdapterError("authoritative account snapshot is unavailable")
        if self.state.kill_switch.active:
            raise DemoAdapterError("kill switch is active")
        if side not in {"buy", "sell"}:
            raise DemoAdapterError("invalid order side")
        market_age_ms = int(time.time() * 1000) - int(market_timestamp_ms)
        if market_age_ms < -self.config.maximum_clock_skew_ms or market_age_ms > 1_000:
            raise DemoAdapterError("order intent market snapshot is stale")
        if best_bid <= 0 or best_ask <= best_bid:
            raise DemoAdapterError("order intent market snapshot is invalid")
        if (side == "buy" and price >= best_ask) or (
            side == "sell" and price <= best_bid
        ):
            raise DemoAdapterError("order intent would not be post-only")
        if any(
            row.get("side") == side and not row.get("reduce_only", False)
            for row in self.state.owned_open_orders.values()
        ):
            raise DemoAdapterError("duplicate same-side order intent")
        base_quantity = Decimal(str(self.profile.strategy.fixed_lot_size_btc))
        contracts = self.market_spec.base_to_contracts(base_quantity, exact=True)
        open_margin = sum(
            float(row["price"]) * self.profile.strategy.fixed_lot_size_btc
            / self.config.leverage
            for row in self.state.owned_open_orders.values()
            if not row.get("reduce_only", False)
        )
        proposed_margin = price * float(base_quantity) / self.config.leverage
        capital = float(self.profile.capital_policy["capital_usdt"])
        authoritative_free = self.latest_snapshot.free_equity_usdt
        authoritative_total = self.latest_snapshot.total_equity_usdt
        maintenance_margin = self.latest_snapshot.maintenance_margin_usdt
        if authoritative_total <= maintenance_margin or authoritative_free < 0:
            raise DemoAdapterError("authoritative margin state is unsafe")
        capital_copy_available = min(capital, authoritative_free)
        margin_limit = min(
            capital * float(self.profile.capital_policy["maximum_margin_utilization"]),
            authoritative_free,
        )
        if open_margin + proposed_margin > margin_limit:
            raise DemoAdapterError("local capital-copy margin limit exceeded")
        validate_order(
            spec=self.market_spec,
            side=side,
            price=Decimal(str(price)),
            amount=contracts,
            best_bid=Decimal(str(best_bid)),
            best_ask=Decimal(str(best_ask)),
            current_inventory=Decimal(str(self.state.inventory_btc)),
            max_inventory=Decimal(str(self.profile.strategy.maximum_inventory_btc)),
            available_equity=Decimal(str(capital_copy_available)),
            leverage=Decimal(str(self.config.leverage)),
            maker_fee_rate=Decimal(str(self.profile.strategy.maker_fee_rate)),
            taker_fee_rate=Decimal(str(self.profile.strategy.taker_fee_rate)),
            reduce_only=False,
            reserved_margin=Decimal(str(open_margin)),
        )
        self.state.client_order_generation += 1
        intent = OrderIntent(
            session_id=self.session_id,
            generation=self.state.client_order_generation,
            symbol=self.config.symbol,
            side=side,
            price=Decimal(str(price)),
            contracts=contracts,
        )
        client_id = intent.client_order_id
        self.state.owned_open_orders[client_id] = {
            "order_id": "",
            "side": side,
            "price": price,
            "contracts": float(contracts),
            "reduce_only": False,
            "status": "INTENT_PENDING",
        }
        try:
            self.state_store.save(self.state)
        except DemoStateError:
            self._halt("STATE_SAVE_BEFORE_CREATE_FAILED")
            raise
        try:
            response = self.exchange.create_order(
                self.config.symbol,
                "limit",
                side,
                float(contracts),
                price,
                {
                    "postOnly": True,
                    "reduceOnly": False,
                    "tdMode": self.config.margin_mode,
                    "clOrdId": client_id,
                },
            )
        except Exception as exc:
            resolved = self._resolve_by_client_id(client_id)
            self._latch_halt("AMBIGUOUS_CREATE_RESPONSE")
            if resolved is None:
                raise AmbiguousExchangeState(
                    "create failed and absence cannot authorize an automatic retry"
                ) from exc
            response = resolved
        order_id = str(response.get("id") or "")
        response_client_id = self._client_id(response) or client_id
        if not order_id or response_client_id != client_id:
            self._latch_halt("ORDER_ACK_IDENTITY_MISMATCH")
            raise AmbiguousExchangeState("order acknowledgement identity mismatch")
        post_only = bool(response.get("postOnly")) or str(
            (response.get("info") or {}).get("ordType", "")
        ).lower() in {"post_only", "post-only"}
        if not post_only:
            resolved = self._resolve_by_client_id(client_id)
            post_only = bool(resolved and (
                resolved.get("postOnly")
                or str((resolved.get("info") or {}).get("ordType", "")).lower()
                in {"post_only", "post-only"}
            ))
        if not post_only:
            self._latch_halt("POST_ONLY_ACK_NOT_PROVEN")
            raise AmbiguousExchangeState("post-only acknowledgement not proven")
        self.state.owned_open_orders[client_id] = {
            "order_id": order_id,
            "side": side,
            "price": price,
            "contracts": float(contracts),
            "reduce_only": False,
            "status": "ACKNOWLEDGED",
        }
        try:
            self.state_store.save(self.state)
        except DemoStateError:
            self._halt("STATE_SAVE_AFTER_CREATE_FAILED")
            try:
                self.exchange.cancel_order(order_id, self.config.symbol)
                live = tuple(self.exchange.fetch_open_orders(self.config.symbol))
                if any(self._client_id(row) == client_id for row in live):
                    raise AmbiguousExchangeState(
                        "state failure cancellation was not confirmed"
                    )
            except Exception as cancel_exc:
                raise AmbiguousExchangeState(
                    "state failure left order outcome ambiguous"
                ) from cancel_exc
            raise
        self.dispatched_order_ids.add(order_id)
        self.dispatched_client_order_ids.add(client_id)
        return SubmittedOrder(
            order_id, client_id, side, price, float(contracts), False, True
        )

    def cancel_confirmed(self, order: SubmittedOrder) -> bool:
        if self.state is None:
            raise DemoAdapterError("runtime state is unavailable")
        try:
            self.exchange.cancel_order(order.order_id, self.config.symbol)
        except Exception:
            # Outcome remains unknown until an authoritative open-order snapshot.
            pass
        try:
            open_orders = tuple(self.exchange.fetch_open_orders(self.config.symbol))
        except Exception as exc:
            self._latch_halt("CANCEL_CONFIRMATION_UNAVAILABLE")
            raise AmbiguousExchangeState("cancel confirmation unavailable") from exc
        still_open = [
            row for row in open_orders if self._client_id(row) == order.client_order_id
        ]
        if still_open:
            self._latch_halt("CANCEL_NOT_CONFIRMED")
            return False
        self.state.owned_open_orders.pop(order.client_order_id, None)
        self.state_store.save(self.state)
        return True

    def cancel_all_owned(self) -> list[str]:
        if self.state is None:
            raise DemoAdapterError("runtime state is unavailable")
        try:
            live = tuple(self.exchange.fetch_open_orders(self.config.symbol))
        except Exception as exc:
            self._latch_halt("AUTHORITATIVE_CANCEL_SCOPE_UNAVAILABLE")
            raise AmbiguousExchangeState("cannot enumerate authoritative orders") from exc
        expected = set(self.state.owned_open_orders)
        if any(self._client_id(row) not in expected for row in live):
            self._latch_halt("FOREIGN_ORDER_DURING_CANCEL")
            raise AmbiguousExchangeState("foreign order prevents automatic cancel-all")
        cancelled: list[str] = []
        for row in live:
            cid = self._client_id(row)
            record = self.state.owned_open_orders[cid]
            submitted = SubmittedOrder(
                str(row.get("id") or record["order_id"]), cid,
                str(row.get("side") or record["side"]),
                float(row.get("price") or record["price"]),
                float(row.get("amount") or record["contracts"]),
                bool(record.get("reduce_only", False)), True,
            )
            if not self.cancel_confirmed(submitted):
                raise AmbiguousExchangeState("owned order remains open")
            cancelled.append(submitted.order_id)
        return cancelled

    def submit_emergency_flatten(
        self, *, position_btc: float, reference_price: float
    ) -> SubmittedOrder | None:
        """Submit at most one reduce-only market flatten for this state epoch."""
        if self.state is None or self.market_spec is None:
            raise DemoAdapterError("runtime state is unavailable")
        if abs(Decimal(str(position_btc))) <= Decimal("1e-12"):
            self.state.flatten_state = "CONFIRMED"
            self.state.inventory_btc = 0.0
            self.state_store.save(self.state)
            return None
        if self.state.flatten_state in {"SUBMITTED", "UNKNOWN"}:
            raise AmbiguousExchangeState("flatten already submitted; reconcile before any retry")
        if self.state.flatten_attempts >= 1:
            raise AmbiguousExchangeState("frozen flatten retry budget exhausted")
        side = "sell" if position_btc > 0 else "buy"
        contracts = self.market_spec.base_to_contracts(
            Decimal(str(abs(position_btc))), exact=True
        )
        self.state.client_order_generation += 1
        intent = OrderIntent(
            session_id=self.session_id,
            generation=self.state.client_order_generation,
            symbol=self.config.symbol,
            side=side,
            price=Decimal(str(reference_price)),
            contracts=contracts,
            reduce_only=True,
        )
        client_id = intent.client_order_id
        self.state.flatten_state = "SUBMITTED"
        self.state.flatten_client_order_id = client_id
        self.state.flatten_attempts += 1
        self.state_store.save(self.state)
        try:
            response = self.exchange.create_order(
                self.config.symbol,
                "market",
                side,
                float(contracts),
                None,
                {
                    "reduceOnly": True,
                    "tdMode": self.config.margin_mode,
                    "clOrdId": client_id,
                },
            )
        except Exception as exc:
            resolved = self._resolve_by_client_id(client_id)
            if resolved is None:
                self.state.flatten_state = "UNKNOWN"
                self.state_store.save(self.state)
                self._latch_halt("AMBIGUOUS_FLATTEN_RESPONSE")
                raise AmbiguousExchangeState("flatten outcome is unknown") from exc
            response = resolved
        order_id = str(response.get("id") or "")
        response_client_id = self._client_id(response) or client_id
        if not order_id or response_client_id != client_id:
            self.state.flatten_state = "UNKNOWN"
            self.state_store.save(self.state)
            self._latch_halt("FLATTEN_ACK_IDENTITY_MISMATCH")
            raise AmbiguousExchangeState("flatten acknowledgement identity mismatch")
        self.state.flatten_order_id = order_id
        self.state_store.save(self.state)
        self.dispatched_order_ids.add(order_id)
        self.dispatched_client_order_ids.add(client_id)
        return SubmittedOrder(
            order_id, client_id, side, reference_price, float(contracts), True, False
        )

    def reconcile_flatten(self, snapshot: AccountSnapshot) -> bool:
        if self.state is None or self.market_spec is None:
            raise DemoAdapterError("runtime state is unavailable")
        self.state.inventory_btc = snapshot.position_btc
        if abs(Decimal(str(snapshot.position_btc))) <= Decimal("1e-12"):
            self.state.inventory_btc = 0.0
            self.state.flatten_state = "CONFIRMED"
            self.state_store.save(self.state)
            return True
        self.state_store.save(self.state)
        return False

    def close(self) -> None:
        try:
            self.exchange.close()
        except Exception:
            pass

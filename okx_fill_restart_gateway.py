"""Narrow OKX Demo transport for the separately armed fill/restart executor.

The gateway has no LIVE construction path.  It verifies the installed CCXT
Demo transport immediately before every external dispatch, exposes a bounded
set of read methods, and permits only the two frozen mutation shapes: normal
post-only create/cancel and one reduce-only market flatten.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable, Iterable

from market_spec import MarketSpec
from okx_demo_runtime import signed_book_age_ms
from utils import fetch_market_info


SYMBOL = "BTC/USDT:USDT"
INSTRUMENT_ID = "BTC-USDT-SWAP"
FROZEN_BTC_USDT_SWAP_CONTRACT_SIZE = Decimal("0.01")
OKX_HOSTNAME = "www.okx.com"
DEMO_HEADER = "x-simulated-trading"
FILL_HISTORY_PAGINATION_CALLS = 3
FILL_RECENT_TAIL_LIMIT = 100
CANCEL_RECONCILIATION_READ_ATTEMPTS = 3
CANCEL_RECONCILIATION_INTERVAL_SECONDS = 2


class FormalGatewayError(RuntimeError):
    """The formal transport or an authoritative exchange result is unsafe."""


class PostOnlyCreateRejected(FormalGatewayError):
    """An explicitly terminal post-only create that is authoritatively absent."""

    def __init__(
        self,
        *,
        client_order_id: str,
        status: str,
        exchange_code: str,
    ) -> None:
        self.client_order_id = client_order_id
        self.status = status
        self.exchange_code = exchange_code
        super().__init__("post-only create was explicitly rejected and reconciled absent")

    def public_dict(self) -> dict[str, object]:
        return {
            "classification": "REJECTED_TERMINAL_ABSENT",
            "client_order_id": self.client_order_id,
            "status": self.status,
            "exchange_code": self.exchange_code,
            "mutation_retry": False,
            "authoritative_absence": True,
        }


class PostOnlyWouldCross(FormalGatewayError):
    """A normal quote became marketable before the create was dispatched."""

    def __init__(
        self,
        *,
        client_order_id: str,
        side: str,
        price: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
    ) -> None:
        self.client_order_id = client_order_id
        self.side = side
        self.price = price
        self.best_bid = best_bid
        self.best_ask = best_ask
        super().__init__("formal post-only order would cross current BBO")

    def public_dict(self) -> dict[str, object]:
        return {
            "classification": "PRE_DISPATCH_POST_ONLY_WOULD_CROSS",
            "client_order_id": self.client_order_id,
            "side": self.side,
            "price": str(self.price),
            "best_bid": str(self.best_bid),
            "best_ask": str(self.best_ask),
            "mutation_dispatched": False,
            "mutation_retry": False,
        }


class FormalBookStalenessError(FormalGatewayError):
    """A structurally valid book is outside one signed-age boundary."""

    def __init__(
        self,
        *,
        signed_age_ms: int,
        exchange_timestamp_ms: int,
        observed_at_ms: int,
        maximum_age_ms: int,
        maximum_clock_skew_ms: int,
    ) -> None:
        self.signed_age_ms = signed_age_ms
        self.exchange_timestamp_ms = exchange_timestamp_ms
        self.observed_at_ms = observed_at_ms
        self.maximum_age_ms = maximum_age_ms
        self.maximum_clock_skew_ms = maximum_clock_skew_ms
        self.retryable_prearm = signed_age_ms > maximum_age_ms
        self.reason = (
            "POSITIVE_STALE"
            if self.retryable_prearm else "FUTURE_BEYOND_CLOCK_SKEW"
        )
        super().__init__(
            f"authoritative order book is stale or invalid: {self.reason}"
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "event": "ORDER_BOOK_SIGNED_AGE_REJECTED",
            "reason": self.reason,
            "signed_age_ms": self.signed_age_ms,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "observed_at_ms": self.observed_at_ms,
            "maximum_age_ms": self.maximum_age_ms,
            "maximum_clock_skew_ms": self.maximum_clock_skew_ms,
            "retryable_prearm": self.retryable_prearm,
        }


@dataclass(frozen=True)
class GatewayAccountSnapshot:
    account_binding: str
    permissions: tuple[str, ...]
    position_mode: str
    leverage: Decimal
    total_equity_usdt: Decimal
    free_equity_usdt: Decimal
    position_btc: Decimal
    average_entry_usdt: Decimal
    maintenance_margin_usdt: Decimal
    open_orders: tuple[dict[str, Any], ...]
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    clock_skew_ms: int

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["account_binding"] = self.account_binding
        value["open_orders"] = len(self.open_orders)
        for name in (
            "leverage", "total_equity_usdt", "free_equity_usdt",
            "position_btc", "average_entry_usdt", "maintenance_margin_usdt",
            "maker_fee_rate", "taker_fee_rate",
        ):
            value[name] = str(value[name])
        return value


@dataclass(frozen=True)
class GatewayTerminalAccountSnapshot:
    """Sanitized account-only terminal state for pre-market failures.

    This intentionally carries only the fields needed to prove that the
    account is flat and has no open orders.  It does not depend on CCXT market
    metadata and never exposes the raw account UID.
    """

    account_binding: str
    position_btc: Decimal
    open_orders: tuple[dict[str, Any], ...]
    clock_skew_ms: int

    def public_dict(self) -> dict[str, object]:
        return {
            "account_binding": self.account_binding,
            "position_btc": str(self.position_btc),
            "open_orders": len(self.open_orders),
            "clock_skew_ms": self.clock_skew_ms,
            "terminal_reconciliation_only": True,
        }


@dataclass(frozen=True)
class GatewayBook:
    timestamp_ms: int
    observed_at_ms: int
    best_bid: Decimal
    best_ask: Decimal
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    @property
    def age_ms(self) -> int:
        return self.observed_at_ms - self.timestamp_ms

    def public_dict(self) -> dict[str, object]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "observed_at_ms": self.observed_at_ms,
            "age_ms": self.age_ms,
            "best_bid": str(self.best_bid),
            "best_ask": str(self.best_ask),
        }


class FormalDemoGateway:
    """Strict Demo-only dispatch wrapper around an installed CCXT OKX object."""

    def __init__(self, exchange: Any):
        self.exchange = exchange
        self.market_spec: MarketSpec | None = None
        self.read_calls: list[str] = []
        self.mutation_calls: list[str] = []
        self.live_endpoint_attempts = 0
        self.flatten_dispatches = 0
        self.normal_create_dispatches = 0
        self.fill_history_queries = 0
        self.fill_recent_tail_queries = 0
        self.fill_union_duplicates = 0
        self.fill_union_conflicts = 0
        self.last_fill_union_audit: dict[str, int] = {}
        self.cancel_fill_since_ms = 0
        self.cancel_reconciliation_read_attempts = (
            CANCEL_RECONCILIATION_READ_ATTEMPTS
        )
        self.last_cancel_reconciliation_audit: dict[str, object] = {}
        self.last_terminal_partial_diagnostic: dict[str, object] | None = None

    def _verify_transport(self) -> None:
        sandbox = self.exchange.options.get("sandboxMode") is True
        simulated = str(self.exchange.headers.get(DEMO_HEADER, "")) == "1"
        hostname = str(getattr(self.exchange, "hostname", ""))
        if not sandbox or not simulated or hostname != OKX_HOSTNAME:
            self.live_endpoint_attempts += 1
            raise FormalGatewayError("OKX Demo transport identity is not proven")

    def _read(self, name: str, *args: object, **kwargs: object) -> Any:
        self._verify_transport()
        method = getattr(self.exchange, name)
        self.read_calls.append(name)
        return method(*args, **kwargs)

    def _mutate(
        self,
        name: str,
        *args: object,
        before_dispatch: Callable[[], None] | None = None,
        normal_create: bool = False,
        **kwargs: object,
    ) -> Any:
        self._verify_transport()
        if name not in {"create_order", "cancel_order"}:
            raise FormalGatewayError("formal mutation method is not allow-listed")
        if before_dispatch is not None:
            before_dispatch()
        if normal_create:
            self.normal_create_dispatches += 1
        self.mutation_calls.append(name)
        return getattr(self.exchange, name)(*args, **kwargs)

    @staticmethod
    def client_order_id(value: dict[str, Any]) -> str:
        return str(
            value.get("clientOrderId")
            or (value.get("info") or {}).get("clOrdId")
            or ""
        )

    @staticmethod
    def _post_only(value: dict[str, Any]) -> bool:
        return bool(value.get("postOnly")) or str(
            (value.get("info") or {}).get("ordType", "")
        ).lower() in {"post_only", "post-only"}

    def load_market(self) -> MarketSpec:
        markets = self._read("fetch_markets")
        valid = [
            row for row in markets
            if isinstance(row, dict) and row.get("id") and row.get("symbol")
        ]
        if not valid:
            raise FormalGatewayError("OKX market metadata is empty")
        # set_markets is strictly local CCXT metadata normalization.
        self.exchange.set_markets(valid)
        info = fetch_market_info(self.exchange, SYMBOL, allow_fallback=False)
        spec = info["market_spec"]
        if (
            spec.symbol != SYMBOL
            or spec.contract_size != Decimal("0.01")
            or not spec.linear
            or spec.inverse
        ):
            raise FormalGatewayError("frozen OKX swap market specification drift")
        self.market_spec = spec
        return spec

    def fetch_book(
        self,
        *,
        maximum_age_ms: int,
        maximum_clock_skew_ms: int,
    ) -> GatewayBook:
        if (
            type(maximum_age_ms) is not int
            or type(maximum_clock_skew_ms) is not int
            or maximum_age_ms <= 0
            or maximum_clock_skew_ms <= 0
        ):
            raise FormalGatewayError(
                "authoritative order book is stale or invalid: budgets"
            )
        raw = self._read("fetch_order_book", SYMBOL)
        bids = raw.get("bids") or []
        asks = raw.get("asks") or []
        if not bids or not asks:
            raise FormalGatewayError("authoritative order book is empty")
        try:
            best_bid = Decimal(str(bids[0][0]))
            best_ask = Decimal(str(asks[0][0]))
        except (IndexError, TypeError, ValueError, ArithmeticError) as exc:
            raise FormalGatewayError("authoritative order book is invalid") from exc
        timestamp_ms = raw.get("timestamp")
        observed_at_ms = int(time.time() * 1000)
        if type(timestamp_ms) is not int or timestamp_ms <= 0:
            raise FormalGatewayError("authoritative order book timestamp is invalid")
        try:
            age = signed_book_age_ms(
                observed_at_ms=observed_at_ms,
                exchange_timestamp_ms=timestamp_ms,
                maximum_age_ms=maximum_age_ms,
                maximum_clock_skew_ms=maximum_clock_skew_ms,
            )
        except ValueError as exc:
            raise FormalBookStalenessError(
                signed_age_ms=observed_at_ms - timestamp_ms,
                exchange_timestamp_ms=timestamp_ms,
                observed_at_ms=observed_at_ms,
                maximum_age_ms=maximum_age_ms,
                maximum_clock_skew_ms=maximum_clock_skew_ms,
            ) from exc
        if best_bid <= 0 or best_ask <= best_bid:
            raise FormalGatewayError("authoritative order book is stale or invalid")
        return GatewayBook(
            timestamp_ms=timestamp_ms,
            observed_at_ms=observed_at_ms,
            best_bid=best_bid,
            best_ask=best_ask,
            bids=tuple((float(row[0]), float(row[1])) for row in bids),
            asks=tuple((float(row[0]), float(row[1])) for row in asks),
        )

    def fetch_account(self) -> GatewayAccountSnapshot:
        if self.market_spec is None:
            raise FormalGatewayError("market metadata must load before account state")
        server_ms = int(self._read("fetch_time"))
        clock_skew_ms = abs(int(time.time() * 1000) - server_ms)
        config = self._read("privateGetAccountConfig")
        rows = config.get("data") or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise FormalGatewayError("account configuration snapshot is incomplete")
        uid = str(rows[0].get("uid") or "")
        permissions = tuple(sorted({
            item.strip()
            for item in str(rows[0].get("perm") or "").split(",")
            if item.strip()
        }))
        if (
            not uid
            or not {"read_only", "trade"}.issubset(set(permissions))
            or "withdraw" in permissions
        ):
            raise FormalGatewayError("Demo credential permission snapshot is unsafe")
        position_mode = str(rows[0].get("posMode") or "")
        if position_mode != "net_mode":
            raise FormalGatewayError("account position mode drift")

        balance = self._read("fetch_balance")
        usdt = balance.get("USDT") or {}
        total = Decimal(str(usdt.get("total") or 0))
        free = Decimal(str(usdt.get("free") or 0))
        if total <= 0 or free < 0:
            raise FormalGatewayError("authoritative USDT balance is incomplete")

        position_btc = Decimal("0")
        average_entry = Decimal("0")
        maintenance = Decimal("0")
        for position in self._read("fetch_positions", [SYMBOL]):
            if position.get("symbol") not in (None, SYMBOL):
                continue
            contracts = Decimal(str(position.get("contracts") or 0))
            if contracts == 0:
                continue
            side = str(position.get("side") or "")
            if side not in {"long", "short"}:
                raise FormalGatewayError("nonzero position side is unknown")
            base = self.market_spec.contracts_to_base(abs(contracts))
            position_btc += base if side == "long" else -base
            average_entry = Decimal(str(position.get("entryPrice") or 0))
            raw_margin = position.get("maintenanceMargin")
            if raw_margin is None:
                raw_margin = (position.get("info") or {}).get("mmr")
            if raw_margin is None:
                raise FormalGatewayError("maintenance margin is unavailable")
            maintenance += Decimal(str(raw_margin))

        open_orders = tuple(self._read("fetch_open_orders", SYMBOL))
        leverage_row = self._read(
            "fetch_leverage", SYMBOL, {"mgnMode": "isolated"}
        )
        leverage = Decimal(str(
            leverage_row.get("longLeverage")
            or leverage_row.get("shortLeverage")
            or leverage_row.get("leverage")
            or 0
        ))
        fee = self._read("fetch_trading_fee", SYMBOL)
        maker = fee.get("maker")
        taker = fee.get("taker")
        if leverage != Decimal("3") or maker is None or taker is None:
            raise FormalGatewayError("leverage or fee schedule is incomplete")
        return GatewayAccountSnapshot(
            account_binding=hashlib.sha256(uid.encode("utf-8")).hexdigest(),
            permissions=permissions,
            position_mode=position_mode,
            leverage=leverage,
            total_equity_usdt=total,
            free_equity_usdt=free,
            position_btc=position_btc,
            average_entry_usdt=(average_entry if position_btc else Decimal("0")),
            maintenance_margin_usdt=maintenance,
            open_orders=open_orders,
            maker_fee_rate=Decimal(str(maker)),
            taker_fee_rate=Decimal(str(taker)),
            clock_skew_ms=clock_skew_ms,
        )

    def fetch_terminal_account_only(self) -> GatewayTerminalAccountSnapshot:
        """Read terminal position/order state without market bootstrap.

        This is restricted to failure reporting before the market is available.
        It is never used for quote admission, risk sizing, or order mutation.
        """
        config = self._read("privateGetAccountConfig")
        rows = config.get("data") or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise FormalGatewayError("account configuration snapshot is incomplete")
        uid = str(rows[0].get("uid") or "")
        permissions = tuple(sorted({
            item.strip()
            for item in str(rows[0].get("perm") or "").split(",")
            if item.strip()
        }))
        if any((
            not uid,
            not {"read_only", "trade"}.issubset(set(permissions)),
            "withdraw" in permissions,
            str(rows[0].get("posMode") or "") != "net_mode",
        )):
            raise FormalGatewayError("Demo terminal account snapshot is unsafe")

        # Use raw OKX reads here. Unified CCXT position/order helpers may
        # implicitly call load_markets(), which is exactly the unavailable
        # dependency on this terminal failure path.
        position_response = self._read(
            "privateGetAccountPositions", {"instType": "SWAP", "instId": INSTRUMENT_ID}
        )
        position_rows = position_response.get("data") or []
        if not isinstance(position_rows, list):
            raise FormalGatewayError("terminal position snapshot is malformed")
        position_btc = Decimal("0")
        for position in position_rows:
            if not isinstance(position, dict):
                raise FormalGatewayError("terminal position row is malformed")
            if str(position.get("instId") or "") != INSTRUMENT_ID:
                continue
            contracts = Decimal(str(position.get("pos") or 0))
            if contracts == 0:
                continue
            side = str(position.get("posSide") or "net")
            if side == "net":
                signed_contracts = contracts
            elif side == "long":
                signed_contracts = abs(contracts)
            elif side == "short":
                signed_contracts = -abs(contracts)
            else:
                raise FormalGatewayError("nonzero terminal position side is unknown")
            position_btc += signed_contracts * FROZEN_BTC_USDT_SWAP_CONTRACT_SIZE
        order_response = self._read(
            "privateGetTradeOrdersPending", {"instType": "SWAP", "instId": INSTRUMENT_ID}
        )
        order_rows = order_response.get("data") or []
        if not isinstance(order_rows, list) or any(
            not isinstance(row, dict) for row in order_rows
        ):
            raise FormalGatewayError("terminal open-order snapshot is malformed")
        open_orders = tuple(
            row for row in order_rows
            if str(row.get("instId") or "") == INSTRUMENT_ID
        )
        self.last_terminal_partial_diagnostic = {
            "account_binding": hashlib.sha256(uid.encode("utf-8")).hexdigest(),
            "position_btc": str(position_btc),
            "open_orders": len(open_orders),
            "clock_verified": False,
            "terminal_reconciliation_only": True,
        }
        # Clock verification remains mandatory for an authoritative terminal
        # result, but is deliberately last so a network failure still leaves a
        # sanitized, non-authoritative account-only diagnostic.
        server_ms = int(self._read("fetch_time"))
        clock_skew_ms = abs(int(time.time() * 1000) - server_ms)
        self.last_terminal_partial_diagnostic = None
        return GatewayTerminalAccountSnapshot(
            account_binding=hashlib.sha256(uid.encode("utf-8")).hexdigest(),
            position_btc=position_btc,
            open_orders=open_orders,
            clock_skew_ms=clock_skew_ms,
        )

    def fetch_trades(self, *, since_ms: int) -> tuple[dict[str, Any], ...]:
        if since_ms <= 0:
            raise FormalGatewayError("formal trade start timestamp is invalid")
        # OKX/CCXT can temporarily omit the newest fill from a paginated query
        # anchored by `since`.  A non-paginated recent tail is therefore read
        # immediately afterwards and unioned by immutable trade identity.
        history = self._read(
            "fetch_my_trades",
            SYMBOL,
            since_ms,
            None,
            {
                "paginate": True,
                "paginationCalls": FILL_HISTORY_PAGINATION_CALLS,
            },
        )
        self.fill_history_queries += 1
        recent = self._read(
            "fetch_my_trades",
            SYMBOL,
            None,
            FILL_RECENT_TAIL_LIMIT,
            {},
        )
        self.fill_recent_tail_queries += 1
        if not isinstance(history, list) or not isinstance(recent, list):
            raise FormalGatewayError("formal fill query result is malformed")

        merged: dict[str, tuple[tuple[object, ...], dict[str, Any]]] = {}
        duplicate_count = 0
        eligible_history = 0
        eligible_recent = 0
        for source, rows in (("history", history), ("recent_tail", recent)):
            for row in rows:
                if not isinstance(row, dict):
                    raise FormalGatewayError("formal fill row is malformed")
                timestamp_ms = int(row.get("timestamp") or 0)
                if timestamp_ms < since_ms:
                    continue
                if source == "history":
                    eligible_history += 1
                else:
                    eligible_recent += 1
                fingerprint = self._trade_fingerprint(row)
                trade_id = str(row.get("id") or "")
                prior = merged.get(trade_id)
                if prior is not None:
                    duplicate_count += 1
                    if prior[0] != fingerprint:
                        self.fill_union_conflicts += 1
                        raise FormalGatewayError(
                            "history/recent-tail fill identity conflict"
                        )
                    continue
                merged[trade_id] = (fingerprint, row)
        self.fill_union_duplicates += duplicate_count
        self.last_fill_union_audit = {
            "history_rows": len(history),
            "recent_tail_rows": len(recent),
            "eligible_history_rows": eligible_history,
            "eligible_recent_tail_rows": eligible_recent,
            "deduplicated_overlap_rows": duplicate_count,
            "union_rows": len(merged),
        }
        rows = [value[1] for value in merged.values()]
        return tuple(sorted(
            rows,
            key=lambda row: (int(row.get("timestamp") or 0), str(row.get("id") or "")),
        ))

    @staticmethod
    def _trade_fingerprint(row: dict[str, Any]) -> tuple[object, ...]:
        info = row.get("info") or {}
        fee = row.get("fee") or {}
        try:
            trade_id = str(row.get("id") or "")
            order_id = str(row.get("order") or "")
            client_id = str(info.get("clOrdId") or "")
            timestamp_ms = int(row.get("timestamp") or 0)
            side = str(row.get("side") or "").lower()
            price = Decimal(str(row.get("price")))
            amount = Decimal(str(row.get("amount")))
            fee_cost = Decimal(str(fee.get("cost")))
            fee_currency = str(fee.get("currency") or "").upper()
            liquidity = str(row.get("takerOrMaker") or "").lower()
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise FormalGatewayError("formal fill identity is malformed") from exc
        if (
            not trade_id
            or not order_id
            or not client_id
            or timestamp_ms <= 0
            or side not in {"buy", "sell"}
            or not price.is_finite()
            or price <= 0
            or not amount.is_finite()
            or amount <= 0
            or not fee_cost.is_finite()
            or fee_currency not in {"USDT", "BTC"}
            or liquidity not in {"maker", "taker"}
        ):
            raise FormalGatewayError("formal fill identity is incomplete")
        return (
            trade_id,
            order_id,
            client_id,
            timestamp_ms,
            side,
            price,
            amount,
            fee_cost,
            fee_currency,
            liquidity,
        )

    def authoritative_open_orders(
        self, expected_client_ids: Iterable[str]
    ) -> tuple[dict[str, Any], ...]:
        rows = tuple(self._read("fetch_open_orders", SYMBOL))
        expected = set(expected_client_ids)
        observed = {self.client_order_id(row) for row in rows}
        if "" in observed or observed - expected:
            raise FormalGatewayError("foreign or unowned open order detected")
        sides = [str(row.get("side") or "") for row in rows]
        if sides.count("buy") > 1 or sides.count("sell") > 1:
            raise FormalGatewayError("duplicate same-side open order detected")
        return rows

    def set_cancel_reconciliation_context(
        self, *, since_ms: int, read_attempts: int
    ) -> None:
        """Bind read-only cancel outcome resolution to the durable fill cursor."""
        if since_ms <= 0 or not 1 <= read_attempts <= 3:
            raise FormalGatewayError("cancel reconciliation context is invalid")
        self.cancel_fill_since_ms = since_ms
        self.cancel_reconciliation_read_attempts = read_attempts

    def submit_post_only(
        self,
        *,
        client_order_id: str,
        side: str,
        price: Decimal,
        quantity_btc: Decimal,
        maximum_age_ms: int,
        maximum_clock_skew_ms: int,
        before_dispatch: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if self.market_spec is None:
            raise FormalGatewayError("market metadata is unavailable")
        if (
            not client_order_id.startswith("fr")
            or len(client_order_id) > 32
            or side not in {"buy", "sell"}
            or quantity_btc != Decimal("0.01")
        ):
            raise FormalGatewayError("formal normal order identity or quantity drift")
        book = self.fetch_book(
            maximum_age_ms=maximum_age_ms,
            maximum_clock_skew_ms=maximum_clock_skew_ms,
        )
        if (side == "buy" and price >= book.best_ask) or (
            side == "sell" and price <= book.best_bid
        ):
            raise PostOnlyWouldCross(
                client_order_id=client_order_id,
                side=side,
                price=price,
                best_bid=book.best_bid,
                best_ask=book.best_ask,
            )
        contracts = self.market_spec.base_to_contracts(quantity_btc, exact=True)
        response = self._mutate(
            "create_order",
            SYMBOL,
            "limit",
            side,
            float(contracts),
            float(price),
            {
                "postOnly": True,
                "reduceOnly": False,
                "tdMode": "isolated",
                "clOrdId": client_order_id,
            },
            before_dispatch=before_dispatch,
            normal_create=True,
        )
        response_client = self.client_order_id(response) or client_order_id
        status = str(
            response.get("status")
            or (response.get("info") or {}).get("state")
            or ""
        ).lower()
        terminal_absent = status in {
            "canceled", "cancelled", "rejected", "expired", "mmp_canceled",
        }
        if terminal_absent:
            resolved = self.resolve_create(client_order_id)
            if resolved.get("authoritative_status") != "absent":
                raise FormalGatewayError(
                    "terminal post-only acknowledgement did not reconcile absent"
                )
            order = resolved.get("order")
            filled = Decimal(str(
                (order or response).get("filled") or 0
                if isinstance(order or response, dict) else 0
            ))
            if filled != 0:
                raise FormalGatewayError(
                    "terminal post-only acknowledgement has nonzero fill"
                )
            info = response.get("info") or {}
            raise PostOnlyCreateRejected(
                client_order_id=client_order_id,
                status=status,
                exchange_code=str(info.get("sCode") or response.get("code") or ""),
            )
        if not response.get("id") or response_client != client_order_id:
            resolved = self.resolve_create(client_order_id)
            order = resolved.get("order")
            if (
                resolved.get("authoritative_status") not in {"open", "closed"}
                or not isinstance(order, dict)
                or not order.get("id")
                or self.client_order_id(order) != client_order_id
            ):
                raise FormalGatewayError(
                    "formal create acknowledgement identity is unresolved"
                )
            response = order
        if not self._post_only(response):
            resolved = self.resolve_create(client_order_id)
            if not resolved.get("post_only_confirmed"):
                raise FormalGatewayError("post-only acknowledgement is unproven")
        return response

    def resolve_create(self, client_order_id: str) -> dict[str, object]:
        """Resolve by client identity without any automatic create retry."""
        try:
            order = self._read(
                "fetch_order",
                client_order_id,
                SYMBOL,
                {"clientOrderId": client_order_id},
            )
        except Exception:
            live = tuple(self._read("fetch_open_orders", SYMBOL))
            matches = [row for row in live if self.client_order_id(row) == client_order_id]
            if len(matches) > 1:
                raise FormalGatewayError("ambiguous create resolved to duplicate orders")
            if not matches:
                return {
                    "authoritative_status": "absent",
                    "post_only_confirmed": False,
                    "order": None,
                }
            order = matches[0]
        status = str(order.get("status") or "").lower()
        if status not in {
            "open", "new", "live", "closed", "filled",
            "canceled", "cancelled", "rejected", "expired",
        }:
            raise FormalGatewayError("resolved create status is unknown")
        authoritative = "open" if status in {"open", "new", "live"} else "closed"
        if status in {"canceled", "cancelled", "rejected", "expired"}:
            authoritative = "absent"
        return {
            "authoritative_status": authoritative,
            "post_only_confirmed": self._post_only(order),
            "order": order,
        }

    def cancel_all_owned(self, expected_client_ids: Iterable[str]) -> tuple[str, ...]:
        expected = set(expected_client_ids)
        live = self.authoritative_open_orders(expected)
        if not expected:
            self.last_cancel_reconciliation_audit = {
                "expected_client_ids": 0,
                "cancel_dispatches": 0,
                "mutation_retries": 0,
                "read_attempts": 1,
                "classifications": {},
                "resolved": True,
            }
            return ()
        live_by_client = {self.client_order_id(order): order for order in live}
        dispatch: dict[str, dict[str, object]] = {}
        for order in live:
            order_id = str(order.get("id") or "")
            client_id = self.client_order_id(order)
            if not order_id or client_id not in expected:
                raise FormalGatewayError("cancel target identity is incomplete")
            try:
                response = self._mutate("cancel_order", order_id, SYMBOL)
                dispatch[client_id] = {
                    "returned": True,
                    "response": response if isinstance(response, dict) else {},
                }
            except Exception as exc:
                # Never retry the mutation.  Only authoritative reads may follow.
                dispatch[client_id] = {
                    "returned": False,
                    "error_type": type(exc).__name__,
                    "response": {},
                }

        absence_streak = {client_id: 0 for client_id in expected}
        classifications: dict[str, str] = {}
        attempts_used = 0
        for attempt in range(1, self.cancel_reconciliation_read_attempts + 1):
            attempts_used = attempt
            remaining_rows = self.authoritative_open_orders(expected)
            remaining = {
                self.client_order_id(order): order for order in remaining_rows
            }
            trades = (
                self.fetch_trades(since_ms=self.cancel_fill_since_ms)
                if self.cancel_fill_since_ms > 0 else ()
            )
            fills_by_client: dict[str, list[dict[str, Any]]] = {
                client_id: [] for client_id in expected
            }
            for trade in trades:
                client_id = self.client_order_id(trade)
                if client_id in fills_by_client:
                    fills_by_client[client_id].append(trade)

            classifications = {}
            for client_id in sorted(expected):
                if client_id in remaining:
                    absence_streak[client_id] = 0
                    classifications[client_id] = "STILL_OPEN"
                    continue
                absence_streak[client_id] += 1
                original = live_by_client.get(client_id, {})
                order_id = str(original.get("id") or client_id)
                status = ""
                order: dict[str, Any] = {}
                try:
                    fetched = self._read(
                        "fetch_order",
                        order_id,
                        SYMBOL,
                        {"clientOrderId": client_id},
                    )
                    if isinstance(fetched, dict):
                        order = fetched
                        status = str(
                            fetched.get("status")
                            or (fetched.get("info") or {}).get("state")
                            or ""
                        ).lower()
                except Exception:
                    pass
                rows = fills_by_client[client_id]
                response = dict(dispatch.get(client_id, {}).get("response") or {})
                response_status = str(
                    response.get("status")
                    or (response.get("info") or {}).get("state")
                    or ""
                ).lower()
                cancel_returned = dispatch.get(client_id, {}).get("returned") is True
                terminal_cancel = status in {
                    "canceled", "cancelled", "rejected", "expired", "mmp_canceled",
                } or response_status in {
                    "canceled", "cancelled", "rejected", "expired", "mmp_canceled",
                }
                # CCXT may normalize both an OKX cancel and a completed order to
                # ``closed``.  A closed status by itself is therefore not fill
                # evidence.  Calling it a fill used to make the executor wait
                # for a trade that did not exist (R2 Session 1), despite a zero
                # fill amount and an absent order.  A fill classification must
                # be backed by the owned trade union and its quantity.
                terminal_fill_claimed = status in {"filled", "closed"}
                # A restart/visibility race can make the initial open-order
                # snapshot empty.  In that case the terminal owned order is
                # the only authoritative quantity available for matching its
                # trade union.
                original_amount = Decimal(str(
                    original.get("amount") or order.get("amount") or 0
                ))
                filled_amount = sum(
                    (Decimal(str(row.get("amount") or 0)) for row in rows),
                    Decimal("0"),
                )
                full_fill_proven = (
                    original_amount > 0 and filled_amount >= original_amount
                )
                order_filled_amount = Decimal(str(order.get("filled") or 0))
                terminal_fill_proven = terminal_fill_claimed and full_fill_proven
                if terminal_cancel:
                    classifications[client_id] = (
                        "PARTIAL_FILL_THEN_CANCEL_CONFIRMED"
                        if rows else "CANCEL_CONFIRMED"
                    )
                elif terminal_fill_proven or full_fill_proven:
                    classifications[client_id] = "FILLED_DURING_CANCEL"
                elif terminal_fill_claimed and order_filled_amount > 0:
                    # A terminal order claims a fill, but the bounded owned
                    # trade union cannot prove it.  Do not invent a fill and do
                    # not treat the order as cancelled; this remains a
                    # fail-closed reconciliation blocker.
                    classifications[client_id] = "FILL_CLAIM_UNPROVEN"
                elif terminal_fill_claimed and order_filled_amount == 0:
                    # With zero reported fill, stable absence is a safe cancel
                    # outcome even when CCXT exposes it as generic ``closed``.
                    classifications[client_id] = "CANCEL_CONFIRMED"
                elif cancel_returned and absence_streak[client_id] >= 2:
                    classifications[client_id] = (
                        "PARTIAL_FILL_THEN_CANCEL_CONFIRMED"
                        if rows else "CANCEL_CONFIRMED"
                    )
                elif order and status not in {"", "open", "new", "live"}:
                    classifications[client_id] = "UNKNOWN_TERMINAL_STATUS"
                else:
                    classifications[client_id] = "ABSENCE_NOT_YET_AUTHORITATIVE"

            unresolved = {
                client_id: classification
                for client_id, classification in classifications.items()
                if classification in {
                    "STILL_OPEN",
                    "ABSENCE_NOT_YET_AUTHORITATIVE",
                    "UNKNOWN_TERMINAL_STATUS",
                    "FILL_CLAIM_UNPROVEN",
                }
            }
            if not unresolved:
                break
            if attempt < self.cancel_reconciliation_read_attempts:
                time.sleep(CANCEL_RECONCILIATION_INTERVAL_SECONDS)

        self.last_cancel_reconciliation_audit = {
            "expected_client_ids": len(expected),
            "initial_open_orders": len(live),
            "cancel_dispatches": len(live),
            "mutation_retries": 0,
            "read_attempts": attempts_used,
            "classifications": classifications,
            "history_recent_tail_union_used": self.cancel_fill_since_ms > 0,
            "fill_union_audit": dict(self.last_fill_union_audit),
            "resolved": not unresolved,
        }
        if unresolved:
            raise FormalGatewayError(
                "owned cancellation outcome remains unresolved after bounded reads"
            )
        return tuple(sorted(expected))

    def submit_reduce_only_flatten(
        self,
        *,
        client_order_id: str,
        position_btc: Decimal,
    ) -> dict[str, Any]:
        if self.market_spec is None or position_btc == 0:
            raise FormalGatewayError("flatten requires a known nonzero position")
        if self.flatten_dispatches >= 1:
            raise FormalGatewayError("single-flight flatten dispatch is exhausted")
        side = "sell" if position_btc > 0 else "buy"
        contracts = self.market_spec.base_to_contracts(abs(position_btc), exact=True)
        self.flatten_dispatches += 1
        try:
            return self._mutate(
                "create_order",
                SYMBOL,
                "market",
                side,
                float(contracts),
                None,
                {
                    "reduceOnly": True,
                    "tdMode": "isolated",
                    "clOrdId": client_order_id,
                },
            )
        except Exception as exc:
            resolved = self.resolve_create(client_order_id)
            if resolved["authoritative_status"] not in {"open", "closed"}:
                raise FormalGatewayError(
                    "flatten create absence/identity is unresolved; no retry allowed"
                ) from exc
            order = resolved.get("order")
            if not isinstance(order, dict):
                raise FormalGatewayError("resolved flatten order payload is missing")
            return order

    def public_audit(self) -> dict[str, object]:
        return {
            "sandbox_mode": self.exchange.options.get("sandboxMode") is True,
            "simulated_trading_header": (
                str(self.exchange.headers.get(DEMO_HEADER, "")) == "1"
            ),
            "hostname": str(getattr(self.exchange, "hostname", "")),
            "read_call_count": len(self.read_calls),
            "read_methods": sorted(set(self.read_calls)),
            "mutation_call_count": len(self.mutation_calls),
            "mutation_methods": sorted(set(self.mutation_calls)),
            "mutation_method_counts": {
                name: self.mutation_calls.count(name)
                for name in ("create_order", "cancel_order")
            },
            "normal_create_dispatches": self.normal_create_dispatches,
            "flatten_dispatches": self.flatten_dispatches,
            "fill_history_queries": self.fill_history_queries,
            "fill_recent_tail_queries": self.fill_recent_tail_queries,
            "fill_union_duplicates": self.fill_union_duplicates,
            "fill_union_conflicts": self.fill_union_conflicts,
            "last_fill_union_audit": dict(self.last_fill_union_audit),
            "last_cancel_reconciliation_audit": dict(
                self.last_cancel_reconciliation_audit
            ),
            "live_endpoint_attempts": self.live_endpoint_attempts,
            "live_orders": 0,
        }

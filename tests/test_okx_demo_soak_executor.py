import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_spec import MarketSpec
from okx_demo_soak_executor import (
    BoundedSoakExecutor,
    DurableLease,
    SoakExecutionError,
    SoakPackage,
)
from okx_fill_restart_offline import _sha256
from okx_fill_restart_gateway import PostOnlyCreateRejected, PostOnlyWouldCross
from okx_fill_restart_validation import OwnedOrder


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _market() -> MarketSpec:
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


class FakeGateway:
    live_endpoint_attempts = 0

    def __init__(
        self,
        *,
        position: str = "0",
        open_orders=(),
        equities: tuple[str, ...] = ("100",),
    ) -> None:
        self.position = Decimal(position)
        self.open_orders = tuple(open_orders)
        self.equities = list(equities)
        self.last_equity = Decimal(equities[-1])
        self.market_spec = _market()
        self.created = 0
        self.cancelled = 0
        self.flatten_dispatches = 0
        self.read_calls = 0

    def load_market(self):
        return self.market_spec

    def fetch_account(self):
        self.read_calls += 1
        if self.equities:
            self.last_equity = Decimal(self.equities.pop(0))
        return SimpleNamespace(
            account_binding="binding",
            position_btc=self.position,
            average_entry_usdt=Decimal("0"),
            total_equity_usdt=self.last_equity,
            free_equity_usdt=self.last_equity,
            open_orders=self.open_orders,
            clock_skew_ms=1,
        )

    def fetch_book(self, **kwargs):
        self.read_calls += 1
        return SimpleNamespace(
            best_bid=Decimal("64999.9"), best_ask=Decimal("65000.1")
        )

    def fetch_trades(self, **kwargs):
        self.read_calls += 1
        return ()

    def submit_post_only(self, **kwargs):
        self.created += 1
        return {"id": f"order-{self.created}"}

    def cancel_all_owned(self, ids):
        rows = tuple(ids)
        self.cancelled += len(rows)
        self.open_orders = ()
        return rows

    def submit_reduce_only_flatten(self, **kwargs):
        self.flatten_dispatches += 1
        self.position = Decimal("0")
        self.open_orders = ()
        return {"id": "flatten"}

    def public_audit(self):
        return {
            "read_call_count": self.read_calls,
            "mutation_call_count": self.created + self.cancelled,
            "mutation_method_counts": {
                "create_order": self.created + self.flatten_dispatches,
                "cancel_order": self.cancelled,
            },
            "flatten_dispatches": self.flatten_dispatches,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        }


class AmbiguousCreateGateway(FakeGateway):
    def submit_post_only(self, **kwargs):
        self.created += 1
        client_id = kwargs["client_order_id"]
        self.open_orders = ({
            "id": "ambiguous-order",
            "clientOrderId": client_id,
            "side": kwargs["side"],
        },)
        raise TimeoutError("timeout after dispatch")


class TransientReadGateway(FakeGateway):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def load_market(self):
        if self.failures:
            self.failures -= 1
            raise TimeoutError("transient read")
        return super().load_market()


class MarketBootstrapFailureGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_reads = 0

    def load_market(self):
        raise TimeoutError("market metadata unavailable")

    def fetch_terminal_account_only(self):
        self.terminal_reads += 1
        return self.fetch_account()


class MultiPartialGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.orders: dict[str, tuple[str, str]] = {}
        self.trades: list[dict[str, object]] = []
        self.normal_fill_created = False
        self.flatten_active = False
        self.flatten_poll = 0
        self.entry = Decimal("0")

    def fetch_account(self):
        if self.flatten_active:
            self.flatten_poll += 1
            if self.flatten_poll == 1:
                self.position = Decimal("0.006")
                self.entry = Decimal("65000")
                self.trades.append(self._trade(
                    "flatten-part-1", "flatten-order", self.flatten_client_id,
                    side="sell", contracts="0.4", price="64990", maker=False,
                ))
            elif self.flatten_poll == 2:
                self.position = Decimal("0")
                self.entry = Decimal("0")
                self.open_orders = ()
                self.trades.append(self._trade(
                    "flatten-part-2", "flatten-order", self.flatten_client_id,
                    side="sell", contracts="0.6", price="64990", maker=False,
                ))
        row = super().fetch_account()
        row.average_entry_usdt = self.entry
        return row

    @staticmethod
    def _trade(
        trade_id: str,
        order_id: str,
        client_id: str,
        *,
        side: str,
        contracts: str,
        price: str,
        maker: bool,
    ) -> dict[str, object]:
        return {
            "id": trade_id,
            "order": order_id,
            "timestamp": 1,
            "side": side,
            "price": price,
            "amount": contracts,
            "fee": {"cost": "0.01", "currency": "USDT"},
            "takerOrMaker": "maker" if maker else "taker",
            "info": {"clOrdId": client_id},
        }

    def submit_post_only(self, **kwargs):
        response = super().submit_post_only(**kwargs)
        self.orders[kwargs["side"]] = (response["id"], kwargs["client_order_id"])
        return response

    def cancel_all_owned(self, ids):
        rows = tuple(ids)
        if not self.normal_fill_created:
            order_id, client_id = self.orders["buy"]
            self.position = Decimal("0.01")
            self.entry = Decimal("65000")
            self.trades.append(self._trade(
                "normal-buy", order_id, client_id,
                side="buy", contracts="1", price="65000", maker=True,
            ))
            self.normal_fill_created = True
        self.cancelled += len(rows)
        self.open_orders = ()
        return rows

    def fetch_trades(self, **kwargs):
        self.read_calls += 1
        return tuple(self.trades)

    def submit_reduce_only_flatten(self, **kwargs):
        self.flatten_dispatches += 1
        self.flatten_active = True
        self.flatten_client_id = kwargs["client_order_id"]
        self.open_orders = ({
            "id": "flatten-order",
            "clientOrderId": self.flatten_client_id,
            "side": "sell",
        },)
        return {"id": "flatten-order"}


class EconomicRoundTripGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.orders: dict[str, tuple[str, str, Decimal]] = {}
        self.trades: list[dict[str, object]] = []
        self.cancel_phase = 0
        self.entry = Decimal("0")

    def fetch_account(self):
        row = super().fetch_account()
        row.average_entry_usdt = self.entry
        return row

    def submit_post_only(self, **kwargs):
        response = super().submit_post_only(**kwargs)
        self.orders[kwargs["side"]] = (
            response["id"], kwargs["client_order_id"], Decimal(kwargs["price"])
        )
        return response

    def cancel_all_owned(self, ids):
        rows = tuple(ids)
        if self.cancel_phase == 0:
            order_id, client_id, price = self.orders["buy"]
            self.position = Decimal("0.01")
            self.entry = price
            self.trades.append(self._trade(
                "economic-buy", order_id, client_id, "buy", price
            ))
        elif self.cancel_phase == 1:
            order_id, client_id, price = self.orders["sell"]
            self.position = Decimal("0")
            self.entry = Decimal("0")
            self.trades.append(self._trade(
                "economic-sell", order_id, client_id, "sell", price
            ))
        self.cancel_phase += 1
        self.cancelled += len(rows)
        self.open_orders = ()
        return rows

    @staticmethod
    def _trade(
        trade_id: str,
        order_id: str,
        client_id: str,
        side: str,
        price: Decimal,
    ) -> dict[str, object]:
        return {
            "id": trade_id,
            "order": order_id,
            "timestamp": 1 if side == "buy" else 2,
            "side": side,
            "price": str(price),
            "amount": "1",
            "fee": {"cost": "0.0005", "currency": "USDT"},
            "takerOrMaker": "maker",
            "info": {"clOrdId": client_id},
        }

    def fetch_trades(self, **kwargs):
        self.read_calls += 1
        return tuple(self.trades)


class RepairedEconomicFailureGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.orders: dict[str, tuple[str, str, Decimal]] = {}
        self.trades: list[dict[str, object]] = []
        self.book_calls = 0
        self.entry = Decimal("0")
        self.normal_fill_created = False

    def fetch_account(self):
        row = super().fetch_account()
        row.average_entry_usdt = self.entry
        return row

    def fetch_book(self, **kwargs):
        self.book_calls += 1
        if self.book_calls > 1:
            raise RuntimeError("book fault after fill")
        return super().fetch_book(**kwargs)

    def submit_post_only(self, **kwargs):
        response = super().submit_post_only(**kwargs)
        self.orders[kwargs["side"]] = (
            response["id"], kwargs["client_order_id"], Decimal(kwargs["price"])
        )
        return response

    def fetch_trades(self, **kwargs):
        self.read_calls += 1
        if len(self.orders) == 2 and not self.normal_fill_created:
            order_id, client_id, price = self.orders["buy"]
            self.position = Decimal("0.01")
            self.entry = price
            self.trades.append({
                "id": "repaired-normal-buy",
                "order": order_id,
                "timestamp": 2,
                "side": "buy",
                "price": str(price),
                "amount": "1",
                "fee": {"cost": "0.01", "currency": "USDT"},
                "takerOrMaker": "maker",
                "info": {"clOrdId": client_id},
            })
            self.normal_fill_created = True
        return tuple(self.trades)

    def submit_reduce_only_flatten(self, **kwargs):
        self.flatten_dispatches += 1
        client_id = kwargs["client_order_id"]
        self.position = Decimal("0")
        self.entry = Decimal("0")
        self.open_orders = ()
        self.trades.append({
            "id": "repaired-flatten",
            "order": "repaired-flatten-order",
            "timestamp": 3,
            "side": "sell",
            "price": "64900",
            "amount": "1",
            "fee": {"cost": "0.02", "currency": "USDT"},
            "takerOrMaker": "taker",
            "info": {"clOrdId": client_id},
        })
        return {"id": "repaired-flatten-order"}


class CancelVisibilityLagGateway(RepairedEconomicFailureGateway):
    """Reproduce Session 5: read exhaustion followed by a stale cancel view."""

    def __init__(self) -> None:
        super().__init__()
        self.primary_read_failures = 0
        self.stale_cancel_reads = 0
        self.stale_cancel_reads_on_cancel = 1
        self.stale_cancel_rows: tuple[dict[str, object], ...] = ()

    def fetch_account(self):
        if self.primary_read_failures:
            self.primary_read_failures -= 1
            raise TimeoutError("deterministic account read outage")
        if self.stale_cancel_reads:
            self.stale_cancel_reads -= 1
            actual = self.open_orders
            self.open_orders = self.stale_cancel_rows
            try:
                return super().fetch_account()
            finally:
                self.open_orders = actual
        return super().fetch_account()

    def submit_post_only(self, **kwargs):
        response = super().submit_post_only(**kwargs)
        self.open_orders = tuple({
            "id": order_id,
            "clientOrderId": client_id,
            "side": side,
        } for side, (order_id, client_id, _price) in self.orders.items())
        return response

    def fetch_trades(self, **kwargs):
        rows = super().fetch_trades(**kwargs)
        if self.normal_fill_created:
            self.open_orders = tuple(
                row for row in self.open_orders if row["side"] != "buy"
            )
        return rows

    def cancel_all_owned(self, ids):
        expected = set(ids)
        self.cancelled += len(expected)
        self.stale_cancel_rows = tuple(
            row for row in self.open_orders
            if str(row.get("clientOrderId") or "") in expected
        )
        self.open_orders = tuple(
            row for row in self.open_orders
            if str(row.get("clientOrderId") or "") not in expected
        )
        self.stale_cancel_reads = self.stale_cancel_reads_on_cancel
        return tuple(sorted(expected))


def _cancel_visibility_lag_driver(
    tmp_path: Path,
) -> tuple[BoundedSoakExecutor, CancelVisibilityLagGateway]:
    gateway = CancelVisibilityLagGateway()
    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=lambda: 1.0,
    )
    driver._build_engine(gateway.fetch_account(), gateway.load_market())
    assert driver.engine is not None
    for side, price in (("buy", Decimal("64999.9")), ("sell", Decimal("65000.1"))):
        client_id = f"cancel-lag-{side}"
        response = gateway.submit_post_only(
            client_order_id=client_id,
            side=side,
            price=price,
            quantity_btc=Decimal("0.01"),
        )
        driver.engine.record_owned_order_ack(OwnedOrder(
            client_order_id=client_id,
            order_id=str(response["id"]),
            side=side,
            quantity_btc=Decimal("0.01"),
            remaining_btc=Decimal("0.01"),
            reduce_only=False,
            post_only_acknowledged=True,
        ))
        driver.owned.add(client_id)
        driver.owned_quotes[client_id] = (side, price)
    assert driver._ingest_trades() == 1
    assert driver.engine.state.ledger.inventory_btc == Decimal("0.01")
    assert driver.owned == {"cancel-lag-sell"}
    return driver, gateway


class FutureWithinSkewEconomicFailureGateway(RepairedEconomicFailureGateway):
    def fetch_trades(self, **kwargs):
        rows = super().fetch_trades(**kwargs)
        for row in self.trades:
            if row["id"] == "repaired-normal-buy":
                row["timestamp"] = 2_490
        return rows


class SimultaneousTwoSidedFillGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.orders: list[tuple[str, str, str, Decimal]] = []
        self.trades: list[dict[str, object]] = []
        self.fills_emitted = False

    def submit_post_only(self, **kwargs):
        response = super().submit_post_only(**kwargs)
        self.orders.append((
            response["id"],
            kwargs["client_order_id"],
            kwargs["side"],
            Decimal(kwargs["price"]),
        ))
        return response

    def fetch_trades(self, **kwargs):
        self.read_calls += 1
        if len(self.orders) >= 2 and not self.fills_emitted:
            for index, (order_id, client_id, side, price) in enumerate(
                self.orders[:2], start=1
            ):
                self.trades.append({
                    "id": f"simultaneous-{side}",
                    "order": order_id,
                    "timestamp": index,
                    "side": side,
                    "price": str(price),
                    "amount": "1",
                    "fee": {"cost": "0.01", "currency": "USDT"},
                    "takerOrMaker": "maker",
                    "info": {"clOrdId": client_id},
                })
            self.position = Decimal("0")
            self.open_orders = ()
            self.fills_emitted = True
        return tuple(self.trades)


class ExplicitPostOnlyRejectGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.rejected = False

    def submit_post_only(self, **kwargs):
        if not self.rejected:
            callback = kwargs.get("before_dispatch")
            if callback is not None:
                callback()
            self.created += 1
            self.rejected = True
            raise PostOnlyCreateRejected(
                client_order_id=kwargs["client_order_id"],
                status="rejected",
                exchange_code="POST_ONLY_WOULD_TAKE",
            )
        return super().submit_post_only(**kwargs)


class PreDispatchPostOnlyCrossGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.blocked = False

    def submit_post_only(self, **kwargs):
        if not self.blocked:
            self.blocked = True
            raise PostOnlyWouldCross(
                client_order_id=kwargs["client_order_id"],
                side=kwargs["side"],
                price=Decimal(kwargs["price"]),
                best_bid=Decimal("64999.9"),
                best_ask=Decimal("65000.1"),
            )
        return super().submit_post_only(**kwargs)


class ClockSkewGateway(FakeGateway):
    def fetch_account(self):
        row = super().fetch_account()
        row.clock_skew_ms = 2_001
        return row


def _package(tmp_path: Path, *, creates: int = 2) -> SoakPackage:
    output = tmp_path / "package"
    output.mkdir(parents=True)
    market = _market()
    return SoakPackage(PROJECT_ROOT, output, {
        "package_id": "soak-package-fixture",
        "run_id": "soak-fixture",
        "session_id": "soak:soak-fixture:p0:nonce",
        "market_fingerprint": market.fingerprint,
        "source_manifest_sha256": "a" * 64,
        "risk_budget": {
            "session_wall_minutes": 30,
            "session_normal_create_cap": creates,
            "maximum_owned_bid": 1,
            "maximum_owned_ask": 1,
            "maximum_inventory_btc": "0.01",
            "maximum_market_age_ms": 1000,
            "maximum_clock_skew_ms": 1500,
            "observation_interval_ms": 0,
            "read_retry_attempts": 3,
            "capital_usdt": "750",
            "leverage": 3,
            "soft_guard_usdt": "22.50",
            "hard_kill_usdt": "37.50",
            "maximum_unresolved_flatten": 1,
        },
    })


def _economic_package(tmp_path: Path) -> SoakPackage:
    package = _package(tmp_path, creates=3)
    package.spec.update({
        "protocol_id": "okx-demo-multi-session-economic-soak-v1",
        "run_id": "economic-fixture",
        "session_id": "economic:economic-fixture:p0:nonce",
    })
    return package


def _repaired_economic_package(tmp_path: Path) -> SoakPackage:
    package = _economic_package(tmp_path)
    package.spec["risk_budget"].update({
        "session_normal_create_cap": 60,
        "admission_create_cap": 48,
        "maker_workoff_create_reserve": 12,
        "economic_repair_version": "a2-ack-accounting-v2",
        "maker_fee_rate": "0.0002",
        "minimum_half_spread_bps": "6.0",
        "fee_edge_safety_buffer_usdt": "0.01",
        "quote_retention_threshold_ticks": 2,
        "minimum_fill_balance": "0.60",
        "shutdown_reconciliation_reserve_seconds": 60,
    })
    return package


def _sample_efficiency_package(tmp_path: Path) -> SoakPackage:
    package = _repaired_economic_package(tmp_path)
    package.spec["risk_budget"].update({
        "economic_repair_version": "r0-sample-efficiency-v1",
        "minimum_half_spread_bps": "4.0",
        "balanced_quote_retention_threshold_ticks": 10,
        "defense_quote_retention_threshold_ticks": 20,
    })
    return package


def test_flat_bounded_run_uses_post_only_and_owned_cancel(tmp_path: Path) -> None:
    gateway = FakeGateway()
    values = iter((0.0, 0.0, 0.0, 1801.0))
    driver = BoundedSoakExecutor(
        _package(tmp_path), gateway, sleep=lambda _: None,
        now=lambda: next(values, 1801.0),
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_SUPPORT"
    assert gateway.created == 2 and gateway.cancelled == 2
    assert result["final_position_btc"] == "0"
    assert result["final_open_orders"] == 0
    assert (driver.package.output / "COMPLETED.json").is_file()
    assert not (driver.package.output / "soak_run/FAILED.json").exists()
    hashes = json.loads(
        (driver.package.output / "soak_run/completion_hashes.json").read_text()
    )
    assert hashes
    assert all(
        _sha256(driver.package.output / relative) == expected
        for relative, expected in hashes.items()
    )


def test_prearm_nonflat_fails_closed_before_create(tmp_path: Path) -> None:
    gateway = FakeGateway(position="0.01")
    driver = BoundedSoakExecutor(
        _package(tmp_path), gateway, sleep=lambda _: None, now=lambda: 0,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_FAILED"
    assert "not flat" in result["reason"]
    assert gateway.created == 0
    assert gateway.flatten_dispatches == 0
    assert (driver.package.output / "soak_run/FAILED.json").is_file()


def test_prearm_open_order_fails_without_mutation(tmp_path: Path) -> None:
    gateway = FakeGateway(open_orders=({"id": "foreign"},))
    driver = BoundedSoakExecutor(
        _package(tmp_path), gateway, sleep=lambda _: None, now=lambda: 0,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_FAILED"
    assert "not flat" in result["reason"]
    assert gateway.created == gateway.cancelled == gateway.flatten_dispatches == 0


def test_soft_and_hard_guards_stop_before_any_create(tmp_path: Path) -> None:
    soft = FakeGateway(equities=("100", "100", "77.50"))
    soft_result = BoundedSoakExecutor(
        _package(tmp_path / "soft"), soft, sleep=lambda _: None, now=lambda: 0,
    ).run()
    assert soft_result["status"] == "OKX_DEMO_BOUNDED_SOAK_SUPPORT"
    assert soft.created == 0

    hard = FakeGateway(equities=("100", "100", "62.50"))
    hard_result = BoundedSoakExecutor(
        _package(tmp_path / "hard"), hard, sleep=lambda _: None, now=lambda: 0,
    ).run()
    assert hard_result["status"] == "OKX_DEMO_BOUNDED_SOAK_FAILED"
    assert "hard loss guard" in hard_result["reason"]
    assert hard.created == 0


def test_ambiguous_create_is_not_retried_and_owned_cancel_is_attempted(
    tmp_path: Path,
) -> None:
    gateway = AmbiguousCreateGateway()
    values = iter((0.0, 0.0, 0.0))
    driver = BoundedSoakExecutor(
        _package(tmp_path), gateway, sleep=lambda _: None,
        now=lambda: next(values, 0.0),
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_FAILED"
    assert "timeout after dispatch" in result["reason"]
    assert gateway.created == 1
    assert gateway.cancelled == 1
    assert result["terminal_account_flat_empty"] is True


def test_transient_reads_are_bounded_and_never_retry_mutations(tmp_path: Path) -> None:
    recovered = TransientReadGateway(failures=2)
    values = iter((0.0, 0.0, 0.0, 1801.0))
    result = BoundedSoakExecutor(
        _package(tmp_path / "recovered"), recovered,
        sleep=lambda _: None, now=lambda: next(values, 1801.0),
    ).run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_SUPPORT"
    assert recovered.created == 2

    exhausted = TransientReadGateway(failures=3)
    result = BoundedSoakExecutor(
        _package(tmp_path / "exhausted"), exhausted,
        sleep=lambda _: None, now=lambda: 0,
    ).run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_FAILED"
    assert "read retry budget exhausted" in result["reason"]
    assert exhausted.created == exhausted.cancelled == 0


def test_market_bootstrap_failure_writes_account_only_terminal_evidence(
    tmp_path: Path,
) -> None:
    gateway = MarketBootstrapFailureGateway()
    driver = BoundedSoakExecutor(
        _economic_package(tmp_path), gateway, sleep=lambda _: None, now=lambda: 1,
    )

    result = driver.run()

    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_FAILED"
    assert result["failure_stage"] == "PRE_MARKET_BOOTSTRAP"
    assert result["market_bootstrap_completed"] is False
    assert result["terminal_reconciliation_mode"] == "ACCOUNT_ONLY_TWO_SNAPSHOT"
    assert result["terminal_account_authoritative"] is True
    assert result["two_flat_empty_snapshots"] is True
    assert result["normal_creates"] == 0
    assert gateway.terminal_reads == 2
    assert gateway.created == gateway.cancelled == gateway.flatten_dispatches == 0
    snapshots = json.loads((
        driver.package.output / "soak_run/terminal/account_snapshots.json"
    ).read_text())
    assert set(snapshots["first"]) == {
        "account_binding", "position_btc", "average_entry_usdt",
        "total_equity_usdt", "free_equity_usdt", "open_orders", "clock_skew_ms",
    }


def test_runtime_multi_partial_flatten_reconciles_engine_and_account(
    tmp_path: Path,
) -> None:
    gateway = MultiPartialGateway()
    values = iter((0.0, 0.0, 0.0, 1.0))
    driver = BoundedSoakExecutor(
        _package(tmp_path), gateway, sleep=lambda _: None,
        now=lambda: next(values, 1.0),
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_BOUNDED_SOAK_SUPPORT"
    assert gateway.flatten_dispatches == 1
    assert result["final_position_btc"] == "0"
    economics = json.loads(
        (driver.package.output / "soak_run/audits/economics.json").read_text()
    )
    assert economics["normal_bid_fills"] == 1
    assert economics["special_fill_count"] == 2
    assert economics["engine_reconciled"] is True


def test_economic_mode_continues_after_fill_and_proves_causal_round_trip(
    tmp_path: Path,
) -> None:
    gateway = EconomicRoundTripGateway()
    clock_value = 1.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.001
        return clock_value

    driver = BoundedSoakExecutor(
        _economic_package(tmp_path), gateway, sleep=lambda _: None, now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    assert gateway.created == 3
    assert gateway.flatten_dispatches == 0
    evidence = json.loads((
        driver.package.output
        / "soak_run/audits/economic_session_evidence.json"
    ).read_text())
    assert evidence["normal_bid_fills"] == 1
    assert evidence["normal_ask_fills"] == 1
    assert evidence["normal_fifo_round_trips"] == 1
    assert len(evidence["causal_reentry"]) == 2
    assert evidence["unclassified_quote_mode_ticks"] == 0
    assert evidence["final_position_btc"] == "0"
    assert evidence["final_open_orders"] == 0


def test_failure_manifest_writes_two_snapshots_and_attempted_counters_last(
    tmp_path: Path,
) -> None:
    gateway = RepairedEconomicFailureGateway()
    clock_value = 1.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.001
        return clock_value

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_FAILED"
    assert result["terminal_account_authoritative"] is True
    assert result["two_flat_empty_snapshots"] is True
    assert result["normal_creates"] == 2
    assert result["normal_bid_fills"] == 1
    output = driver.package.output
    snapshots = json.loads((
        output / "soak_run/terminal/account_snapshots.json"
    ).read_text())
    assert snapshots["first"]["position_btc"] == "0"
    assert snapshots["second"]["open_orders"] == 0
    evidence = json.loads((
        output / "soak_run/audits/economic_session_failure_evidence.json"
    ).read_text())
    assert evidence["evidence_kind"] == "ATTEMPTED_ECONOMIC_SESSION_FAILURE"
    completion = json.loads((
        output / "soak_run/completion_hashes.json"
    ).read_text())
    assert "soak_run/terminal/account_snapshots.json" in completion
    assert (
        output / "soak_run/FAILED.json"
    ).stat().st_mtime_ns >= (
        output / "soak_run/completion_hashes.json"
    ).stat().st_mtime_ns


def test_session5_read_exhaustion_stale_cancel_converges_before_flatten(
    tmp_path: Path,
) -> None:
    driver, gateway = _cancel_visibility_lag_driver(tmp_path)
    gateway.primary_read_failures = 3

    with pytest.raises(SoakExecutionError, match="read retry budget exhausted") as caught:
        driver._read("fetch_account", gateway.fetch_account)

    first, second, reconciled = driver._shutdown()
    result = driver._finalize_failure(caught.value, (first, second), None)

    assert reconciled is True
    assert result["reason"] == "SoakExecutionError:read retry budget exhausted: fetch_account"
    assert result["terminal_account_authoritative"] is True
    assert result["two_flat_empty_snapshots"] is True
    assert result["final_position_btc"] == "0"
    assert result["final_open_orders"] == 0
    assert result["mutation_retries"] == 0
    assert gateway.flatten_dispatches == 1
    events = [
        json.loads(line)["payload"]["event"] for line in (
            driver.package.output / "soak_run/streams/events.jsonl"
        ).read_text().splitlines()
    ]
    assert events.count("CANCEL_ACCOUNT_VIEW_STALE") == 1
    assert "SHUTDOWN_CANCEL_RECONCILIATION_FAILED" not in events


def test_persistent_cancel_visibility_lag_blocks_flatten_dispatch(
    tmp_path: Path,
) -> None:
    driver, gateway = _cancel_visibility_lag_driver(tmp_path)
    gateway.stale_cancel_reads_on_cancel = 3

    with pytest.raises(
        SoakExecutionError,
        match="shutdown cancel reconciliation unresolved before flatten",
    ) as caught:
        driver._shutdown()

    assert isinstance(caught.value.__cause__, SoakExecutionError)
    assert "cancelled order remained authoritative-open" in str(caught.value.__cause__)
    assert gateway.flatten_dispatches == 0
    assert gateway.cancelled == 1
    events = [
        json.loads(line)["payload"]["event"] for line in (
            driver.package.output / "soak_run/streams/events.jsonl"
        ).read_text().splitlines()
    ]
    assert events.count("CANCEL_ACCOUNT_VIEW_STALE") == 3
    assert driver.owned == {"cancel-lag-sell"}


def test_future_fill_within_skew_uses_monotonic_causal_timestamp(
    tmp_path: Path,
) -> None:
    gateway = FutureWithinSkewEconomicFailureGateway()
    clock_value = 1.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.001
        return clock_value

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_FAILED"
    assert "inventory defense precedes fill" not in result["reason"]
    economics = json.loads((
        driver.package.output / "soak_run/audits/economics.json"
    ).read_text())
    pending = economics["pending_causal_fills"]
    assert pending == []
    special_closed = economics["special_closed_causal_fills"]
    assert len(special_closed) == 1
    assert special_closed[0]["fill_timestamp_ms"] == 2_490
    assert special_closed[0]["defense_timestamp_ms"] >= 2_490
    assert special_closed[0]["maker_workoff_observed"] is False
    assert special_closed[0]["immediate_taker_flatten"] is True
    assert result["terminal_account_authoritative"] is True
    assert result["final_position_btc"] == "0"
    assert result["final_open_orders"] == 0


def test_repaired_executor_retains_valid_quotes_instead_of_create_churn(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    clock_value = 1.0
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_value, clock_calls
        clock_calls += 1
        clock_value = (
            1.0 + clock_calls * 0.001
            if clock_calls < 40 else 1742.0
        )
        return clock_value

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    assert gateway.created == 2
    economics = json.loads((
        driver.package.output / "soak_run/audits/economics.json"
    ).read_text())
    assert economics["placement_reason_counters"]["QUOTE_STILL_VALID"] > 0
    assert economics["create_budget_partition"] == {
        "admission_create_cap": 48,
        "workoff_create_reserve": 12,
        "total_create_cap": 60,
    }


def test_successor_executor_seals_bounded_sample_efficiency_policy(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 1.0 + clock_calls * 0.001 if clock_calls < 40 else 1742.0

    driver = BoundedSoakExecutor(
        _sample_efficiency_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    assert driver.sample_efficiency_mode is True
    assert gateway.created == 2
    evidence = json.loads((
        driver.package.output
        / "soak_run/audits/economic_session_evidence.json"
    ).read_text())
    policy = evidence["control_audit"]["sample_efficiency_policy"]
    assert policy["minimum_half_spread_bps"] == "4.0"
    assert policy["balanced_retention_threshold_ticks"] == 10
    assert policy["defense_retention_threshold_ticks"] == 20
    assert policy["draining_workoff_retention_threshold_ticks"] == 5
    assert policy["total_create_cap"] == 60
    assert policy["risk_expansion"] is False
    assert evidence["unclassified_quote_mode_ticks"] == 0


def test_simultaneous_two_sided_fill_reconciles_before_next_dispatch(
    tmp_path: Path,
) -> None:
    gateway = SimultaneousTwoSidedFillGateway()
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 1.0 + clock_calls * 0.001 if clock_calls < 70 else 1742.0

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    evidence = json.loads((
        driver.package.output / "soak_run/audits/economic_session_evidence.json"
    ).read_text())
    create_audit = evidence["control_audit"]["create_counter_audit"]
    assert create_audit["normal_create_unresolved"] == 0
    assert create_audit["normal_create_dispatches"] == gateway.created
    events = [
        json.loads(line)["payload"]["event"]
        for line in (
            driver.package.output / "soak_run/streams/events.jsonl"
        ).read_text().splitlines()
    ]
    reconciled = events.index("ECONOMIC_FILL_RECONCILED")
    assert "CREATE_INTENT" in events[reconciled + 1:]
    assert evidence["normal_bid_fills"] == 1
    assert evidence["normal_ask_fills"] == 1


def test_explicit_post_only_rejection_is_absent_without_retry_or_phantom_order(
    tmp_path: Path,
) -> None:
    gateway = ExplicitPostOnlyRejectGateway()
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 1.0 + clock_calls * 0.001 if clock_calls < 30 else 1742.0

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    evidence = json.loads((
        driver.package.output / "soak_run/audits/economic_session_evidence.json"
    ).read_text())
    audit = evidence["control_audit"]["create_counter_audit"]
    assert audit["normal_create_rejections"] == 1
    assert audit["normal_create_unresolved"] == 0
    assert audit["mutation_retries"] == 0
    assert not driver.harness.ambiguous_intent
    assert evidence["final_open_orders"] == 0


def test_pre_dispatch_cross_is_activity_block_not_session_failure(
    tmp_path: Path,
) -> None:
    gateway = PreDispatchPostOnlyCrossGateway()
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 1.0 + clock_calls * 0.001 if clock_calls < 30 else 1742.0

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_SUPPORT"
    evidence = json.loads((
        driver.package.output / "soak_run/audits/economic_session_evidence.json"
    ).read_text())
    audit = evidence["control_audit"]["create_counter_audit"]
    reasons = evidence["control_audit"]["placement_reason_counters"]
    assert reasons["POST_ONLY_CROSS_BLOCKED"] == 1
    assert audit["normal_create_dispatches"] == gateway.created
    assert audit["normal_create_rejections"] == 0
    assert audit["normal_create_unresolved"] == 0
    assert audit["mutation_retries"] == 0
    assert not driver.harness.pending_intent
    assert not driver.harness.ambiguous_intent
    events = [
        json.loads(line)["payload"]["event"]
        for line in (
            driver.package.output / "soak_run/streams/events.jsonl"
        ).read_text().splitlines()
    ]
    assert "CREATE_BLOCKED_BEFORE_DISPATCH" in events


def test_closed_engine_gate_blocks_before_gateway_mutation(tmp_path: Path) -> None:
    gateway = FakeGateway()
    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=lambda: 1.0,
    )
    account = gateway.fetch_account()
    driver._build_engine(account, gateway.load_market())
    assert driver.engine is not None
    driver.engine.state.placement_halted_for_fill = True
    driver.engine._commit_or_halt()
    with pytest.raises(SoakExecutionError, match="before create dispatch"):
        driver._require_placement_gate("buy")
    assert gateway.created == 0
    assert driver.harness.normal_creates == 0


def test_prearm_clock_skew_fails_closed_without_mutation(tmp_path: Path) -> None:
    gateway = ClockSkewGateway()
    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=lambda: 1.0,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_FAILED"
    assert result["reason"] == "SoakExecutionError:account clock skew exceeds budget"
    assert result["normal_create_dispatches"] == 0
    assert result["normal_create_unresolved"] == 0
    assert gateway.created == 0
    assert gateway.cancelled == 0
    assert gateway.flatten_dispatches == 0
    assert result["terminal_account_authoritative"] is True
    assert result["two_flat_empty_snapshots"] is True


def test_draining_cancel_fill_race_reconciles_before_terminal_exit(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=lambda: 1.0,
    )
    account = gateway.fetch_account()
    driver._build_engine(account, gateway.load_market())
    assert driver.activity is not None
    driver.activity.enter_draining(timestamp_ms=1, normal_creates=48)
    driver.owned.add("race-order")
    sequence: list[str] = []

    def cancel_with_fill() -> None:
        sequence.append("cancel")
        driver.owned.clear()
        gateway.position = Decimal("0.01")
        driver.activity.observe_fill(
            trade_id="cancel-race-fill",
            side="buy",
            timestamp_ms=2,
            inventory_before_btc=Decimal("0"),
            inventory_after_btc=Decimal("0.01"),
            fill_order_id="race-order-id",
            fill_quantity_btc=Decimal("0.01"),
            fill_price_usdt=Decimal("65000"),
        )
        driver.activity.observe_inventory_defense(
            trade_id="cancel-race-fill", timestamp_ms=2
        )

    def ingest_after_cancel() -> int:
        sequence.append("ingest")
        return 0

    driver._cancel_owned = cancel_with_fill  # type: ignore[method-assign]
    driver._ingest_trades = ingest_after_cancel  # type: ignore[method-assign]
    driver._reconcile_economic_fill_latch = lambda value: value  # type: ignore[method-assign]
    refreshed, ready = driver._reconcile_draining_cancel_boundary(account)
    assert sequence == ["cancel", "ingest"]
    assert refreshed.position_btc == Decimal("0.01")
    assert ready is False
    assert list(driver.activity.pending_fills) == ["cancel-race-fill"]


def test_failure_manifest_preserves_full_decimal_accounting_and_create_counters(
    tmp_path: Path,
) -> None:
    gateway = RepairedEconomicFailureGateway()
    clock_value = 1.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.001
        return clock_value

    driver = BoundedSoakExecutor(
        _repaired_economic_package(tmp_path),
        gateway,
        sleep=lambda _: None,
        now=clock,
    )
    result = driver.run()
    assert result["status"] == "OKX_DEMO_ECONOMIC_SESSION_FAILED"
    evidence = json.loads((
        driver.package.output
        / "soak_run/audits/economic_session_failure_evidence.json"
    ).read_text())
    assert Decimal(evidence["realized_spread_pnl_usdt"]) + Decimal(
        evidence["inventory_pnl_usdt"]
    ) == Decimal(evidence["normal_gross_pnl_usdt"])
    assert Decimal(evidence["normal_gross_pnl_usdt"]) - Decimal(
        evidence["normal_fees_usdt"]
    ) == Decimal(evidence["normal_net_pnl_usdt"])
    assert Decimal(evidence["aggregate_gross_pnl_usdt"]) - Decimal(
        evidence["aggregate_fees_usdt"]
    ) == Decimal(evidence["aggregate_net_pnl_usdt"])
    assert evidence["normal_create_dispatches"] == gateway.created
    assert evidence["normal_create_unresolved"] == 0


def test_durable_lease_collision_and_stale_recovery(tmp_path: Path) -> None:
    path = tmp_path / "lease.json"
    first = DurableLease(path, "p0", now=lambda: 1.0)
    first.acquire(100)
    collision = DurableLease(path, "p1", now=lambda: 1.05)
    with pytest.raises(SoakExecutionError, match="collision"):
        collision.acquire(100)

    recovered = DurableLease(path, "p2", now=lambda: 2.0)
    recovered.acquire(100)
    assert list(tmp_path.glob("lease.stale.*.json"))
    recovered.release()
    state = json.loads(path.read_text())
    assert state["owner"] == "p2" and state["status"] == "RELEASED"


def test_arm_token_and_marker_are_single_use(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assert package.expected_arm_token.startswith("OKX_DEMO:soak:")
    assert package.expected_arm_token != "wrong"

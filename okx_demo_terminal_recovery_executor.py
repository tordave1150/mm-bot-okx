"""Successor executor primitives for ambiguous flatten terminal recovery."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from okx_demo_soak_executor import BoundedSoakExecutor, SoakExecutionError
from okx_fill_restart_gateway import FormalDemoGateway, FormalGatewayError


class TerminalRecoveryGateway(FormalDemoGateway):
    """Resolve one dispatched flatten by immutable identity without retry."""

    def _identity_rows(self, client_order_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for method_name in ("fetch_open_orders", "fetch_closed_orders", "fetch_orders"):
            if not hasattr(self.exchange, method_name):
                continue
            try:
                values = self._read(method_name, "BTC/USDT:USDT")
            except Exception:
                continue
            rows.extend(
                row for row in values
                if isinstance(row, dict)
                and self.client_order_id(row) == client_order_id
            )
        return rows

    def _identity_trades(self, client_order_id: str) -> list[dict[str, Any]]:
        try:
            rows = self._read("fetch_my_trades", "BTC/USDT:USDT")
        except Exception:
            return []
        return [
            row for row in rows
            if isinstance(row, dict)
            and str(
                row.get("clientOrderId")
                or (row.get("info") or {}).get("clOrdId")
                or ""
            ) == client_order_id
        ]

    def resolve_flatten_identity(self, client_order_id: str) -> dict[str, Any]:
        candidates = self._identity_rows(client_order_id)
        trades = self._identity_trades(client_order_id)
        order_ids = {
            str(row.get("id") or "") for row in candidates if row.get("id")
        }
        order_ids.update(
            str(row.get("order") or row.get("orderId") or "")
            for row in trades
            if row.get("order") or row.get("orderId")
        )
        order_ids.discard("")
        if len(order_ids) > 1:
            raise FormalGatewayError("flatten identity resolves to multiple orders")
        if candidates:
            return candidates[-1]
        if len(order_ids) == 1 and trades:
            return {
                "id": next(iter(order_ids)),
                "clientOrderId": client_order_id,
                "status": "closed",
                "reduceOnly": True,
                "reconciledFromTrades": True,
            }
        raise FormalGatewayError(
            "flatten identity absent from open/closed/history/recent-tail reads"
        )

    def submit_reduce_only_flatten(
        self, *, client_order_id: str, position_btc: Decimal
    ) -> dict[str, Any]:
        if self.market_spec is None or position_btc == 0:
            raise FormalGatewayError("flatten requires a known nonzero position")
        if self.flatten_dispatches >= 1:
            raise FormalGatewayError("single-flight flatten dispatch is exhausted")
        side = "sell" if position_btc > 0 else "buy"
        contracts = self.market_spec.base_to_contracts(abs(position_btc), exact=True)
        self.flatten_dispatches += 1
        try:
            response = self._mutate(
                "create_order",
                "BTC/USDT:USDT",
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
            try:
                return self.resolve_flatten_identity(client_order_id)
            except Exception as resolution_exc:
                raise FormalGatewayError(
                    "flatten dispatch ambiguous after bounded history/tail reconciliation; "
                    "no retry allowed"
                ) from resolution_exc
        if not isinstance(response, dict) or not response.get("id"):
            return self.resolve_flatten_identity(client_order_id)
        return response


class TerminalRecoveryExecutor(BoundedSoakExecutor):
    """Preserve two account-only snapshots after any flatten ambiguity."""

    def _shutdown(self) -> tuple[Any, Any, bool]:
        engine_reconciled = True
        if self.owned:
            try:
                self._cancel_owned()
            except Exception:
                engine_reconciled = False
                self.owned.clear()
                self.owned_quotes.clear()
        account = self._read("fetch_account", self.gateway.fetch_account)
        flatten_failure = ""
        if account.position_btc != 0:
            try:
                engine_reconciled = self._flatten(account) and engine_reconciled
            except Exception as exc:
                flatten_failure = f"{type(exc).__name__}:{exc}"
                engine_reconciled = False
        if flatten_failure:
            first, second = self._account_only_pair()
        else:
            first, second = self._account_pair()
        flat_empty = all(
            item.position_btc == 0 and not item.open_orders for item in (first, second)
        )
        if self.engine is None:
            engine_reconciled = False
        elif engine_reconciled:
            self._ingest_trades()
            for item in (first, second):
                self.engine.record_authoritative_snapshot(self._snapshot(item))
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
            flatten_failure=flatten_failure,
            flatten_dispatches=int(getattr(self.gateway, "flatten_dispatches", 0)),
            mutation_retry=False,
        )
        if flatten_failure and not flat_empty:
            # The caller will finalize a durable failure with the account pair;
            # this never authorizes another flatten dispatch.
            return first, second, False
        return first, second, engine_reconciled


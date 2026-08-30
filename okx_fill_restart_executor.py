"""Separately armed, restartable OKX Demo fill validation executor.

This is the only module in the successor protocol that may connect the frozen
formal controller to the narrow Demo gateway.  Package inspection and
``describe`` are offline.  ``start`` requires the exact package-bound arm token;
resume commands require the durable checkpoint token and never create a second
formal execution marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from okx_demo_adapter import build_ccxt_demo_exchange
from okx_demo_profile import DefensiveOverlayController, load_promoted_profile
from okx_demo_protocol import _credential_config
from okx_fill_restart_formal import (
    FormalAction,
    FormalActionType,
    FormalObservation,
    FormalOrchestrator,
    FormalRunSpecification,
    FormalSafetyError,
    FormalStage,
    FormalStateStore,
    FrozenQuotePlan,
)
from okx_fill_restart_gateway import (
    FormalBookStalenessError,
    FormalDemoGateway,
    FormalGatewayError,
    GatewayAccountSnapshot,
    GatewayBook,
)
from okx_fill_restart_offline import ARTIFACT_ROOT, _sha256
from okx_fill_restart_validation import (
    AuthoritativeSnapshot,
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    RiskBudget,
    RuntimeBinding,
    ValidationSafetyError,
    canonical_json,
    canonical_sha256,
)


EXECUTOR_PROTOCOL_ID = "okx-demo-fill-restart-executor-v1"
RESTART_R1_EXIT = 75
RESTART_R2_EXIT = 76
TERMINAL_EXIT = 0


class FormalExecutionError(RuntimeError):
    pass


def _write_new_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FormalExecutionError(f"non-overwriting artifact exists: {path.name}") from exc


def _write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FormalExecutionError(f"non-overwriting artifact exists: {path.name}") from exc


class HashChainStream:
    def __init__(self, path: Path):
        self.path = path

    def _records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8")
        if not raw.endswith("\n"):
            raise FormalExecutionError(f"stream is truncated: {self.path.name}")
        records = [json.loads(line) for line in raw.splitlines()]
        previous = ""
        for index, record in enumerate(records, start=1):
            payload = record.get("payload")
            base = {
                "sequence": index,
                "previous_hash": previous,
                "payload_sha256": canonical_sha256(payload),
                "payload": payload,
            }
            expected = canonical_sha256(base)
            if (
                record.get("sequence") != index
                or record.get("previous_hash") != previous
                or record.get("payload_sha256") != base["payload_sha256"]
                or record.get("record_hash") != expected
            ):
                raise FormalExecutionError(f"stream hash chain failed: {self.path.name}")
            previous = expected
        return records

    def append(self, payload: dict[str, object]) -> str:
        records = self._records()
        previous = str(records[-1]["record_hash"]) if records else ""
        base = {
            "sequence": len(records) + 1,
            "previous_hash": previous,
            "payload_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        record = dict(base)
        record["record_hash"] = canonical_sha256(base)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return str(record["record_hash"])

    def last_payload(self) -> dict[str, object] | None:
        records = self._records()
        return dict(records[-1]["payload"]) if records else None


@dataclass(frozen=True)
class FrozenPackage:
    root: Path
    output: Path
    package_id: str
    spec: FormalRunSpecification
    source_hashes: dict[str, str]


def load_frozen_package(root: Path, package_id: str) -> FrozenPackage:
    root = root.resolve()
    output = root / ARTIFACT_ROOT / package_id
    terminal = output / "FORMAL_PACKAGE_COMPLETED.json"
    spec_path = output / "specification" / "formal_controller_spec.json"
    hashes_path = output / "specification" / "source_hashes.json"
    completion_path = output / "completion_hashes.json"
    if not all(path.is_file() for path in (terminal, spec_path, hashes_path, completion_path)):
        raise FormalExecutionError("formal package evidence is incomplete")
    terminal_data = json.loads(terminal.read_text(encoding="utf-8"))
    if (
        terminal_data.get("phase_status") != "FORMAL_PACKAGE_FROZEN_OFFLINE"
        or terminal_data.get("package_id") != package_id
        or terminal_data.get("formal_execution_armed") is not False
    ):
        raise FormalExecutionError("formal package terminal contract is invalid")
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    for relative, expected in completion.items():
        path = output / relative
        if not path.is_file() or _sha256(path) != expected:
            raise FormalExecutionError("formal package completion hash mismatch")
    source_hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    for relative, expected in source_hashes.items():
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            raise FormalExecutionError(f"formal runtime source drift: {relative}")
    spec = FormalRunSpecification.from_dict(
        json.loads(spec_path.read_text(encoding="utf-8"))
    )
    if spec.package_id != package_id:
        raise FormalExecutionError("formal package ID does not bind controller spec")
    return FrozenPackage(root, output, package_id, spec, source_hashes)


@dataclass(frozen=True)
class CollectedObservation:
    formal: FormalObservation
    authoritative: AuthoritativeSnapshot
    account: GatewayAccountSnapshot
    book: GatewayBook
    new_fill_count: int


class FormalExecutionDriver:
    def __init__(
        self,
        *,
        package: FrozenPackage,
        gateway: FormalDemoGateway,
        controller: FormalOrchestrator,
        engine: FillRestartEngine,
        trade_since_ms: int,
    ) -> None:
        self.package = package
        self.spec = package.spec
        self.gateway = gateway
        self.controller = controller
        self.engine = engine
        self.trade_since_ms = trade_since_ms
        self.profile = load_promoted_profile(package.root)
        if self.profile.binding_sha256 != self.spec.profile_binding_sha256:
            raise FormalExecutionError("promoted profile binding drift")
        self.strategy = self.profile.build_strategy()
        self.next_sequence = self.controller.state.last_observation_sequence + 1
        self.overlay = DefensiveOverlayController(self.profile.defensive_overlay)
        self.streams = {
            name: HashChainStream(
                package.output / "formal_run" / "streams" / f"{name}.jsonl"
            )
            for name in (
                "market", "order", "trade", "fill", "position", "balance",
                "fee", "accounting", "defensive", "safety", "supervisor",
                "gateway_audit",
            )
        }
        prior_overlay = self.streams["defensive"].last_payload()
        if prior_overlay and isinstance(prior_overlay.get("overlay_state"), dict):
            self.overlay = DefensiveOverlayController.from_dict(
                dict(prior_overlay["overlay_state"]),
                self.profile.defensive_overlay,
            )

    @staticmethod
    def _client_id(order: dict[str, Any]) -> str:
        return FormalDemoGateway.client_order_id(order)

    def _fill_pages(self, trades: Sequence[dict[str, Any]]) -> list[dict[str, object]]:
        converted: list[dict[str, object]] = []
        assert self.gateway.market_spec is not None
        for trade in trades:
            client_id = str((trade.get("info") or {}).get("clOrdId") or "")
            order = self.engine.state.owned_orders.get(client_id)
            if order is None:
                raise ValidationSafetyError("unknown or unowned formal trade")
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
        chunks = [converted[index:index + 100] for index in range(0, len(converted), 100)]
        if not chunks:
            chunks = [[]]
        pages: list[dict[str, object]] = []
        cursor = ""
        for index, chunk in enumerate(chunks):
            next_cursor = "" if index == len(chunks) - 1 else f"page-{index + 1}"
            pages.append({
                "page_index": index,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "trades": chunk,
            })
            cursor = next_cursor
        return pages

    def _authoritative_snapshot(
        self,
        *,
        sequence: int,
        account: GatewayAccountSnapshot,
        trades_complete: bool = True,
    ) -> AuthoritativeSnapshot:
        rows = tuple({
            "client_order_id": self._client_id(order),
            "order_id": str(order.get("id") or ""),
            "side": str(order.get("side") or ""),
        } for order in account.open_orders)
        return AuthoritativeSnapshot(
            sequence=sequence,
            account_binding=account.account_binding,
            symbol=self.spec.symbol,
            market_fingerprint=self.spec.market_fingerprint,
            position_btc=account.position_btc,
            average_entry_price=account.average_entry_usdt,
            run_fees_usdt=self.engine.state.ledger.total_fees_usdt,
            open_orders=rows,
            trades_complete=trades_complete,
        )

    def _formal_observation(
        self,
        *,
        sequence: int,
        account: GatewayAccountSnapshot,
        book: GatewayBook,
        authoritative: AuthoritativeSnapshot,
    ) -> FormalObservation:
        ledger = self.engine.state.ledger
        cursor = self.engine.state.fill_cursor
        open_owned: list[tuple[str, str]] = []
        foreign = 0
        for row in account.open_orders:
            client_id = self._client_id(row)
            if client_id in self.engine.state.owned_orders:
                open_owned.append((client_id, str(row.get("side") or "")))
            else:
                foreign += 1
        peak = max(self.controller.state.peak_equity_usdt, account.total_equity_usdt)
        cursor_id = max(cursor.ids_at_watermark) if cursor.ids_at_watermark else ""
        observation = FormalObservation(
            sequence=sequence,
            observed_at_ms=book.observed_at_ms,
            environment="OKX_DEMO",
            sandbox_mode=True,
            simulated_trading_header=True,
            account_binding=account.account_binding,
            market_fingerprint=self.spec.market_fingerprint,
            symbol=self.spec.symbol,
            position_mode=account.position_mode,
            leverage=account.leverage,
            position_btc=account.position_btc,
            open_owned_orders=tuple(sorted(open_owned)),
            foreign_order_count=foreign,
            normal_bid_fills_total=ledger.normal_bid_fills,
            normal_ask_fills_total=ledger.normal_ask_fills,
            normal_fifo_round_trips_total=len(ledger.normal_round_trips),
            fill_cursor_timestamp_ms=cursor.watermark_ms,
            fill_cursor_trade_id=cursor_id,
            fill_deduplication_sha256=canonical_sha256(cursor.to_dict()),
            average_entry_usdt=ledger.average_entry_price,
            gross_realized_pnl_usdt=ledger.gross_realized_pnl_usdt,
            actual_fees_usdt=ledger.total_fees_usdt,
            net_realized_pnl_usdt=ledger.net_realized_pnl_usdt,
            equity_usdt=account.total_equity_usdt,
            peak_equity_usdt=peak,
            available_equity_usdt=account.free_equity_usdt,
            maintenance_margin_usdt=account.maintenance_margin_usdt,
            clock_skew_ms=account.clock_skew_ms,
            market_timestamp_ms=book.timestamp_ms,
            market_age_ms=book.age_ms,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
        )
        failure = observation.safety_failure(self.spec)
        if failure:
            raise FormalSafetyError(failure)
        return observation

    def collect(self) -> CollectedObservation | None:
        sequence = self.next_sequence
        self.next_sequence += 1
        account = self.gateway.fetch_account()
        trades = self.gateway.fetch_trades(since_ms=self.trade_since_ms)
        before_ids = set(self.engine.state.ledger.fill_fingerprints)
        before = len(before_ids)
        pages = self._fill_pages(trades)
        applied = self.engine.ingest_fill_pages(pages)
        after = len(self.engine.state.ledger.fill_fingerprints)
        if after - before != applied:
            raise FormalExecutionError("formal applied-fill count does not reconcile")
        authoritative = self._authoritative_snapshot(
            sequence=sequence, account=account
        )
        if account.position_btc != self.engine.state.ledger.inventory_btc and not applied:
            leading = self._authoritative_snapshot(
                sequence=sequence, account=account, trades_complete=False
            )
            self.engine.observe_position_before_trade(leading)
            self.streams["safety"].append({
                "event": "POSITION_LEADS_TRADE_PLACEMENT_BLOCKED",
                "sequence": sequence,
            })
            return None
        reconciled = self.engine.record_authoritative_snapshot(
            authoritative, allow_position_lag=bool(applied)
        )
        if not reconciled:
            self.streams["safety"].append({
                "event": "TRADE_LEADS_POSITION_PLACEMENT_BLOCKED",
                "sequence": sequence,
            })
            return None
        book = self.gateway.fetch_book(
            maximum_age_ms=self.spec.maximum_market_age_ms,
            maximum_clock_skew_ms=self.spec.maximum_clock_skew_ms,
        )
        observation = self._formal_observation(
            sequence=sequence,
            account=account,
            book=book,
            authoritative=authoritative,
        )
        self.streams["market"].append(book.public_dict())
        self.streams["position"].append({
            "sequence": sequence,
            "position_btc": str(account.position_btc),
            "average_entry_usdt": str(account.average_entry_usdt),
            "open_order_count": len(account.open_orders),
        })
        self.streams["balance"].append({
            "sequence": sequence,
            "equity_usdt": str(account.total_equity_usdt),
            "available_equity_usdt": str(account.free_equity_usdt),
            "maintenance_margin_usdt": str(account.maintenance_margin_usdt),
        })
        self.streams["fee"].append({
            "sequence": sequence,
            "actual_run_fees_usdt": str(self.engine.state.ledger.total_fees_usdt),
            "normal_maker_fees_usdt": str(
                self.engine.state.ledger.normal_fees_usdt
            ),
            "special_flatten_fees_usdt": str(
                self.engine.state.ledger.special_fees_usdt
            ),
            "maker_fee_rate": str(account.maker_fee_rate),
            "taker_fee_rate": str(account.taker_fee_rate),
        })
        self.streams["accounting"].append({
            "sequence": sequence,
            "normal_bid_fills": self.engine.state.ledger.normal_bid_fills,
            "normal_ask_fills": self.engine.state.ledger.normal_ask_fills,
            "normal_fifo_round_trips": len(self.engine.state.ledger.normal_round_trips),
            "normal_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_gross_realized_pnl_usdt
            ),
            "normal_net_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_net_realized_pnl_usdt
            ),
            "special_economics_excluded_from_normal": True,
            "aggregate_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.gross_realized_pnl_usdt
            ),
            "aggregate_net_realized_pnl_usdt": str(
                self.engine.state.ledger.net_realized_pnl_usdt
            ),
        })
        if applied:
            new_ids = set(self.engine.state.ledger.fill_fingerprints) - before_ids
            for trade in trades:
                if str(trade.get("id") or "") in new_ids:
                    self.streams["trade"].append({
                        "trade_id": str(trade.get("id") or ""),
                        "order_id": str(trade.get("order") or ""),
                        "client_order_id": str(
                            (trade.get("info") or {}).get("clOrdId") or ""
                        ),
                        "timestamp_ms": int(trade.get("timestamp") or 0),
                    })
            self.streams["fill"].append({
                "new_fill_count": applied,
                "normal_fill_count": self.engine.state.ledger.normal_fill_count,
                "special_fill_count": self.engine.state.ledger.special_fill_count,
                "cursor_sha256": canonical_sha256(
                    self.engine.state.fill_cursor.to_dict()
                ),
            })
        return CollectedObservation(
            observation, authoritative, account, book, applied
        )

    def collect_until_reconciled(self, *, attempts: int = 8) -> CollectedObservation:
        for _ in range(attempts):
            result = self.collect()
            if result is not None:
                return result
            time.sleep(self.spec.observation_interval_ms / 1000)
        raise FormalExecutionError("bounded trade/position reconciliation exhausted")

    def collect_prearm_until_fresh(
        self, *, attempts: int = 8
    ) -> CollectedObservation:
        if attempts <= 0:
            raise FormalExecutionError("pre-arm book attempt budget is invalid")
        if self.controller.state.stage is not FormalStage.NOT_ARMED:
            raise FormalExecutionError("pre-arm book retry is forbidden after arm")
        if (
            self.controller.state.owned_orders
            or self.controller.state.pending_intents
            or self.engine.state.owned_orders
            or self.engine.state.external_order_submissions
        ):
            raise FormalExecutionError("pre-arm book retry requires zero order state")
        protected = (
            len(self.gateway.mutation_calls),
            self.gateway.flatten_dispatches,
            self.gateway.live_endpoint_attempts,
            self.engine.state.external_order_submissions,
        )
        for attempt in range(1, attempts + 1):
            try:
                result = self.collect()
            except FormalBookStalenessError as exc:
                payload = exc.public_dict()
                payload.update({
                    "event": "PREARM_BOOK_SIGNED_AGE_REJECTED",
                    "attempt": attempt,
                    "maximum_attempts": attempts,
                    "controller_stage": self.controller.state.stage.value,
                    "new_submissions_after_rejection": 0,
                })
                self.streams["safety"].append(payload)
                observed = (
                    len(self.gateway.mutation_calls),
                    self.gateway.flatten_dispatches,
                    self.gateway.live_endpoint_attempts,
                    self.engine.state.external_order_submissions,
                )
                if observed != protected:
                    raise FormalExecutionError(
                        "pre-arm book rejection changed a protected counter"
                    ) from exc
                if not exc.retryable_prearm:
                    raise
                if attempt == attempts:
                    break
                time.sleep(self.spec.observation_interval_ms / 1000)
                continue
            observed = (
                len(self.gateway.mutation_calls),
                self.gateway.flatten_dispatches,
                self.gateway.live_endpoint_attempts,
                self.engine.state.external_order_submissions,
            )
            if observed != protected:
                raise FormalExecutionError(
                    "pre-arm book collection changed a protected counter"
                )
            if result is not None:
                return result
            if attempt < attempts:
                time.sleep(self.spec.observation_interval_ms / 1000)
        raise FormalExecutionError("bounded pre-arm fresh-book reacquisition exhausted")

    def warm_quote_engine(self) -> None:
        last_timestamp = 0
        for _ in range(14):
            book = self.gateway.fetch_book(
                maximum_age_ms=self.spec.maximum_market_age_ms,
                maximum_clock_skew_ms=self.spec.maximum_clock_skew_ms,
            )
            if book.timestamp_ms <= last_timestamp:
                raise FormalExecutionError("warmup market timestamps are non-monotonic")
            last_timestamp = book.timestamp_ms
            self.strategy.decide(
                {
                    "timestamp": book.timestamp_ms,
                    "bids": book.bids,
                    "asks": book.asks,
                },
                inventory_btc=float(self.engine.state.ledger.inventory_btc),
                market_data_age_ms=book.age_ms,
                staleness_limit_ms=self.spec.maximum_market_age_ms,
            )
            time.sleep(self.spec.observation_interval_ms / 1000)

    @staticmethod
    def _audit_counter_fields() -> tuple[str, ...]:
        return (
            "read_call_count",
            "mutation_call_count",
            "flatten_dispatches",
            "fill_history_queries",
            "fill_recent_tail_queries",
            "fill_union_duplicates",
            "fill_union_conflicts",
            "live_endpoint_attempts",
            "live_orders",
        )

    @classmethod
    def _validated_process_audit(
        cls, value: object, *, context: str
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise FormalExecutionError(f"gateway audit is not an object: {context}")
        audit = dict(value)
        for field in cls._audit_counter_fields():
            counter = audit.get(field)
            if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
                raise FormalExecutionError(
                    f"gateway audit counter is invalid: {context}:{field}"
                )
        read_methods = audit.get("read_methods")
        mutation_methods = audit.get("mutation_methods")
        if not isinstance(read_methods, list) or not all(
            isinstance(item, str) for item in read_methods
        ):
            raise FormalExecutionError(
                f"gateway read-method audit is invalid: {context}"
            )
        if not isinstance(mutation_methods, list) or not all(
            isinstance(item, str) for item in mutation_methods
        ):
            raise FormalExecutionError(
                f"gateway mutation-method audit is invalid: {context}"
            )
        if not set(mutation_methods).issubset({"create_order", "cancel_order"}):
            raise FormalExecutionError(
                f"gateway mutation method is not allow-listed: {context}"
            )
        method_counts = audit.get("mutation_method_counts")
        if not isinstance(method_counts, dict) or set(method_counts) != {
            "create_order", "cancel_order"
        }:
            raise FormalExecutionError(
                f"gateway mutation-count audit is invalid: {context}"
            )
        for method, count in method_counts.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise FormalExecutionError(
                    f"gateway mutation-count audit is invalid: {context}:{method}"
                )
        if sum(method_counts.values()) != audit["mutation_call_count"]:
            raise FormalExecutionError(
                f"gateway mutation counts do not reconcile: {context}"
            )
        if sorted(method for method, count in method_counts.items() if count) != sorted(
            mutation_methods
        ):
            raise FormalExecutionError(
                f"gateway mutation methods do not reconcile: {context}"
            )
        if audit.get("sandbox_mode") is not True:
            raise FormalExecutionError(f"gateway audit lost Demo sandbox: {context}")
        if audit.get("simulated_trading_header") is not True:
            raise FormalExecutionError(
                f"gateway audit lost simulated header: {context}"
            )
        if audit.get("hostname") != "www.okx.com":
            raise FormalExecutionError(f"gateway audit hostname drift: {context}")
        if audit["live_orders"] != 0:
            raise FormalExecutionError(f"gateway audit contains Live orders: {context}")
        return audit

    def _persist_gateway_generation_audit(self, boundary: str) -> None:
        if not boundary:
            raise FormalExecutionError("gateway audit boundary is missing")
        generation = self.engine.state.process_generation
        if generation != self.controller.state.process_generation:
            raise FormalExecutionError("gateway audit process generation drift")
        audit = self._validated_process_audit(
            self.gateway.public_audit(), context=f"generation-{generation}"
        )
        records = self.streams["gateway_audit"]._records()
        same_generation = [
            dict(record["payload"])
            for record in records
            if isinstance(record.get("payload"), dict)
            and record["payload"].get("process_generation") == generation
        ]
        if same_generation:
            prior = self._validated_process_audit(
                same_generation[-1].get("process_audit"),
                context=f"generation-{generation}-prior",
            )
            for field in self._audit_counter_fields():
                if int(audit[field]) < int(prior[field]):
                    raise FormalExecutionError(
                        f"gateway audit counter regressed: generation-{generation}:{field}"
                    )
        self.streams["gateway_audit"].append({
            "event": "PROCESS_GENERATION_GATEWAY_AUDIT",
            "process_generation": generation,
            "boundary": boundary,
            "process_audit": audit,
        })

    def _cumulative_gateway_audit(self) -> dict[str, object]:
        records = self.streams["gateway_audit"]._records()
        if not records:
            raise FormalExecutionError("cumulative gateway audit stream is empty")
        latest: dict[int, dict[str, object]] = {}
        last_generation = -1
        for record in records:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise FormalExecutionError("gateway audit payload is invalid")
            generation = payload.get("process_generation")
            if isinstance(generation, bool) or not isinstance(generation, int):
                raise FormalExecutionError("gateway audit generation is invalid")
            if generation < 0 or generation < last_generation or generation > last_generation + 1:
                raise FormalExecutionError("gateway audit generations are not contiguous")
            audit = self._validated_process_audit(
                payload.get("process_audit"), context=f"generation-{generation}"
            )
            prior_payload = latest.get(generation)
            if prior_payload is not None:
                prior = self._validated_process_audit(
                    prior_payload.get("process_audit"),
                    context=f"generation-{generation}-prior",
                )
                for field in self._audit_counter_fields():
                    if int(audit[field]) < int(prior[field]):
                        raise FormalExecutionError(
                            f"gateway audit counter regressed: generation-{generation}:{field}"
                        )
            latest[generation] = dict(payload)
            last_generation = generation
        current_generation = self.engine.state.process_generation
        if current_generation != self.controller.state.process_generation:
            raise FormalExecutionError("cumulative gateway generation drift")
        if sorted(latest) != list(range(current_generation + 1)):
            raise FormalExecutionError("cumulative gateway audit generation is missing")
        selected = [latest[generation] for generation in sorted(latest)]
        process_audits = [
            self._validated_process_audit(
                payload["process_audit"],
                context=f"generation-{payload['process_generation']}",
            )
            for payload in selected
        ]
        cumulative: dict[str, object] = {
            "scope": "CUMULATIVE_ACROSS_PROCESS_GENERATIONS",
            "process_generations": sorted(latest),
            "process_generation_count": len(latest),
            "audit_stream_record_count": len(records),
            "sandbox_mode": all(audit["sandbox_mode"] is True for audit in process_audits),
            "simulated_trading_header": all(
                audit["simulated_trading_header"] is True for audit in process_audits
            ),
            "hostname": "www.okx.com",
            "read_methods": sorted({
                method for audit in process_audits
                for method in audit["read_methods"]  # type: ignore[union-attr]
            }),
            "mutation_methods": sorted({
                method for audit in process_audits
                for method in audit["mutation_methods"]  # type: ignore[union-attr]
            }),
            "mutation_method_counts": {
                method: sum(
                    int(audit["mutation_method_counts"][method])  # type: ignore[index]
                    for audit in process_audits
                )
                for method in ("create_order", "cancel_order")
            },
            "last_fill_union_audit": dict(
                process_audits[-1].get("last_fill_union_audit") or {}
            ),
            "generation_audits": selected,
        }
        for field in self._audit_counter_fields():
            cumulative[field] = sum(int(audit[field]) for audit in process_audits)
        return cumulative

    @staticmethod
    def _reconcile_gateway_order_counts(
        cumulative_gateway_audit: dict[str, object],
        *,
        normal_create_count: int,
        normal_acknowledgements: int,
        normal_create_events: int,
        cancel_confirmed_ids: int,
    ) -> dict[str, int]:
        method_counts = cumulative_gateway_audit.get("mutation_method_counts")
        if not isinstance(method_counts, dict):
            raise FormalExecutionError("cumulative mutation counts are unavailable")
        try:
            cumulative_create_calls = int(method_counts["create_order"])
            cumulative_cancel_calls = int(method_counts["cancel_order"])
            cumulative_flatten_calls = int(
                cumulative_gateway_audit["flatten_dispatches"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalExecutionError(
                "cumulative mutation counts are incomplete"
            ) from exc
        normal_transport_creates = cumulative_create_calls - cumulative_flatten_calls
        if not all((
            normal_transport_creates == normal_create_count,
            normal_create_events == normal_create_count,
            normal_acknowledgements == normal_create_count,
            cumulative_cancel_calls == cancel_confirmed_ids,
        )):
            raise FormalExecutionError(
                "cumulative gateway counters do not reconcile to durable order events"
            )
        return {
            "cumulative_create_calls": cumulative_create_calls,
            "cumulative_cancel_calls": cumulative_cancel_calls,
            "cumulative_flatten_calls": cumulative_flatten_calls,
            "normal_transport_creates": normal_transport_creates,
        }

    def prepare_r2_kill_probe(
        self, collected: CollectedObservation
    ) -> FrozenQuotePlan:
        if self.controller.state.stage is not FormalStage.KILL_LATCH_BLOCKED:
            raise FormalExecutionError("R2 quote probe requires blocked controller stage")
        if (
            not self.controller.state.validation_kill_active
            or not self.engine.state.kill_latch.active
        ):
            raise FormalExecutionError("R2 quote probe requires persisted kill latch")
        baseline = self._validated_process_audit(
            self.gateway.public_audit(), context="R2-probe-baseline"
        )
        protected = (
            "mutation_call_count", "flatten_dispatches",
            "live_endpoint_attempts", "live_orders",
        )
        try:
            self.warm_quote_engine()
            return self.quote_plan(collected)
        finally:
            observed = self._validated_process_audit(
                self.gateway.public_audit(), context="R2-probe-observed"
            )
            if any(observed[field] != baseline[field] for field in protected):
                raise FormalExecutionError(
                    "R2 warmup/probe changed a protected gateway counter"
                )

    def quote_plan(self, collected: CollectedObservation) -> FrozenQuotePlan:
        decision = self.strategy.decide(
            {
                "timestamp": collected.book.timestamp_ms,
                "bids": collected.book.bids,
                "asks": collected.book.asks,
            },
            inventory_btc=float(self.engine.state.ledger.inventory_btc),
            market_data_age_ms=collected.book.age_ms,
            staleness_limit_ms=self.spec.maximum_market_age_ms,
        )
        if decision is None or not decision.quote_allowed:
            raise FormalExecutionError("frozen quote engine did not authorize a quote")
        drawdown = (
            max(Decimal("0"), collected.formal.peak_equity_usdt - collected.formal.equity_usdt)
            / collected.formal.peak_equity_usdt
            if collected.formal.peak_equity_usdt > 0 else Decimal("0")
        )
        overlay = self.overlay.evaluate(
            mid_price=float((collected.book.best_bid + collected.book.best_ask) / 2),
            inventory_btc=float(self.engine.state.ledger.inventory_btc),
            drawdown=float(drawdown),
            normal_fill_observed=False,
        )
        self.streams["defensive"].append({
            "events": overlay.events,
            "allow_quoting": overlay.allow_quoting,
            "suppress_side": overlay.suppress_side,
            "overlay_state": self.overlay.to_dict(),
        })
        if not overlay.allow_quoting:
            raise FormalExecutionError("defensive overlay blocked formal quoting")
        return FrozenQuotePlan(
            profile_binding_sha256=self.spec.profile_binding_sha256,
            source_manifest_sha256=self.spec.source_manifest_sha256,
            runtime_configuration_sha256=self.spec.runtime_configuration_sha256,
            market_timestamp_ms=collected.book.timestamp_ms,
            best_bid=collected.book.best_bid,
            best_ask=collected.book.best_ask,
            bid_price=Decimal(str(decision.rounded_bid)),
            ask_price=Decimal(str(decision.rounded_ask)),
            quantity_btc=Decimal("0.01"),
            quote_allowed=True,
            bid_suppressed=bool(
                decision.bid_suppressed or overlay.suppress_side == "buy"
            ),
            ask_suppressed=bool(
                decision.ask_suppressed or overlay.suppress_side == "sell"
            ),
        )

    def _owned_order(self, response: dict[str, Any], action: FormalAction) -> OwnedOrder:
        return OwnedOrder(
            client_order_id=str(action.payload["client_order_id"]),
            order_id=str(response.get("id") or ""),
            side=str(action.payload["side"]),
            quantity_btc=Decimal(str(action.payload["quantity_btc"])),
            remaining_btc=Decimal(str(action.payload["quantity_btc"])),
            reduce_only=False,
            post_only_acknowledged=True,
        )

    def place_actions(
        self, actions: Sequence[FormalAction]
    ) -> tuple[FormalAction, ...]:
        for index, action in enumerate(actions):
            if action.action is not FormalActionType.PLACE_POST_ONLY:
                raise FormalExecutionError("non-placement action reached placement dispatcher")
            client_id = str(action.payload["client_order_id"])
            if index:
                wait_ms = self.spec.minimum_create_interval_ms - (
                    int(time.time() * 1000) - self.controller.state.last_create_at_ms
                )
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000)
                interim = self.collect_until_reconciled()
                if interim.new_fill_count:
                    for remaining in actions[index:]:
                        remaining_id = str(remaining.payload["client_order_id"])
                        self.controller.abandon_undispatched_intent(
                            client_order_id=remaining_id,
                            reason="fill observed before later planned dispatch",
                        )
                    return self.controller.observe(
                        interim.formal, now_ms=int(time.time() * 1000)
                    )
            dispatched_at = int(time.time() * 1000)
            self.controller.record_create_dispatch(
                client_order_id=client_id, now_ms=dispatched_at
            )
            try:
                response = self.gateway.submit_post_only(
                    client_order_id=client_id,
                    side=str(action.payload["side"]),
                    price=Decimal(str(action.payload["price"])),
                    quantity_btc=Decimal(str(action.payload["quantity_btc"])),
                    maximum_age_ms=self.spec.maximum_market_age_ms,
                    maximum_clock_skew_ms=self.spec.maximum_clock_skew_ms,
                )
                order = self._owned_order(response, action)
                self.engine.record_owned_order_ack(order)
                self.controller.record_post_only_ack(
                    client_order_id=client_id,
                    side=order.side,
                    post_only_confirmed=True,
                    now_ms=dispatched_at,
                )
                resolution = "acknowledged_open"
            except Exception as create_exc:
                self.controller.record_ambiguous_create(client_order_id=client_id)
                resolved = self.gateway.resolve_create(client_id)
                status = str(resolved["authoritative_status"])
                response = resolved.get("order")
                if status in {"open", "closed"}:
                    if not isinstance(response, dict):
                        raise FormalExecutionError("resolved order payload is missing")
                    order = self._owned_order(response, action)
                    self.engine.record_owned_order_ack(order)
                self.controller.resolve_ambiguous_create(
                    client_order_id=client_id,
                    authoritative_status=status,
                    post_only_confirmed=bool(resolved["post_only_confirmed"]),
                )
                resolution = f"ambiguous_resolved_{status}"
                if status == "absent":
                    self.streams["safety"].append({
                        "event": "CREATE_ABSENCE_PROVEN_NO_RETRY",
                        "client_order_id": client_id,
                        "error_type": type(create_exc).__name__,
                    })
            self.streams["order"].append({
                "event": "NORMAL_CREATE_RESOLVED",
                "client_order_id": client_id,
                "side": str(action.payload["side"]),
                "price": str(action.payload["price"]),
                "quantity_btc": str(action.payload["quantity_btc"]),
                "resolution": resolution,
                "automatic_retry": False,
            })
        return ()

    def cancel_and_reconcile(self) -> CollectedObservation:
        expected = tuple(self.engine.state.open_orders)
        self.gateway.set_cancel_reconciliation_context(
            since_ms=self.trade_since_ms,
            read_attempts=3,
        )
        confirmed = self.gateway.cancel_all_owned(expected)
        account = self.gateway.fetch_account()
        trades = self.gateway.fetch_trades(since_ms=self.trade_since_ms)
        self.engine.ingest_fill_pages(self._fill_pages(trades))
        book = self.gateway.fetch_book(
            maximum_age_ms=self.spec.maximum_market_age_ms,
            maximum_clock_skew_ms=self.spec.maximum_clock_skew_ms,
        )
        sequence = self.next_sequence
        self.next_sequence += 1
        authoritative = self._authoritative_snapshot(sequence=sequence, account=account)
        self.engine.confirm_cancellations(
            confirmed_client_ids=confirmed,
            snapshot=authoritative,
        )
        remaining = tuple(
            self._client_id(row) for row in account.open_orders
            if self._client_id(row) in self.controller.state.owned_orders
        )
        self.controller.record_authoritative_cancellation(
            remaining_open_client_ids=remaining
        )
        formal = self._formal_observation(
            sequence=sequence,
            account=account,
            book=book,
            authoritative=authoritative,
        )
        self.streams["order"].append({
            "event": "AUTHORITATIVE_CANCEL_CONFIRMED",
            "confirmed_client_order_ids": list(confirmed),
            "remaining_owned_open_orders": list(remaining),
        })
        return CollectedObservation(formal, authoritative, account, book, 0)

    def _checkpoint_r1(self) -> dict[str, object]:
        # Validate both durable state machines before either checkpoint commit.
        # This keeps a rejected controller transition from leaving an engine
        # resume token behind and keeps emergency flatten outside R1.
        self.controller.validate_r1_checkpoint_ready()
        self.engine.validate_checkpoint_after_actual_fill()
        directive = self.engine.checkpoint_after_actual_fill()
        self.controller.persist_r1_checkpoint(directive.checkpoint_id)
        payload = {
            "exit_signal": directive.exit_signal,
            "checkpoint_id": directive.checkpoint_id,
            "resume_token_sha256": hashlib.sha256(
                directive.resume_token.encode("utf-8")
            ).hexdigest(),
            "resume_token_serialized": False,
            "process_generation": self.controller.state.process_generation,
            "position_btc": str(self.engine.state.ledger.inventory_btc),
            "average_entry_usdt": str(self.engine.state.ledger.average_entry_price),
            "gross_realized_pnl_usdt": str(
                self.engine.state.ledger.gross_realized_pnl_usdt
            ),
            "actual_fees_usdt": str(self.engine.state.ledger.total_fees_usdt),
            "net_realized_pnl_usdt": str(
                self.engine.state.ledger.net_realized_pnl_usdt
            ),
            "normal_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_gross_realized_pnl_usdt
            ),
            "normal_fees_usdt": str(self.engine.state.ledger.normal_fees_usdt),
            "normal_net_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_net_realized_pnl_usdt
            ),
            "special_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.special_gross_realized_pnl_usdt
            ),
            "special_fees_usdt": str(self.engine.state.ledger.special_fees_usdt),
            "special_net_realized_pnl_usdt": str(
                self.engine.state.ledger.special_net_realized_pnl_usdt
            ),
            "special_economics_excluded_from_normal": True,
            "source_manifest_sha256": self.spec.source_manifest_sha256,
            "package_specification_sha256": self.spec.package_specification_sha256,
        }
        _write_new_json(
            self.package.output / "formal_run" / "restart" / "R1_handoff.json",
            payload,
        )
        self._persist_gateway_generation_audit("R1_HANDOFF")
        self.streams["supervisor"].append(payload)
        return {
            "status": "RESTART_REQUIRED_AFTER_FILL",
            "checkpoint_id": directive.checkpoint_id,
            "resume_token": directive.resume_token,
            "resume_token_artifact_written": False,
        }

    def _checkpoint_r2(self) -> dict[str, object]:
        directive = self.engine.checkpoint_kill_latch()
        self.controller.activate_r2_kill()
        acknowledgement_id = self.controller.state.validation_kill_id
        payload = {
            "exit_signal": directive.exit_signal,
            "engine_checkpoint_id": directive.checkpoint_id,
            "controller_checkpoint_id": acknowledgement_id,
            "resume_token_sha256": hashlib.sha256(
                directive.resume_token.encode("utf-8")
            ).hexdigest(),
            "resume_token_serialized": False,
            "process_generation": self.controller.state.process_generation,
            "validation_kill_active": True,
            "position_btc": "0",
            "owned_open_orders": 0,
        }
        _write_new_json(
            self.package.output / "formal_run" / "restart" / "R2_handoff.json",
            payload,
        )
        self._persist_gateway_generation_audit("R2_HANDOFF")
        self.streams["supervisor"].append(payload)
        return {
            "status": "RESTART_REQUIRED_AFTER_KILL_LATCH",
            "checkpoint_id": acknowledgement_id,
            "resume_token": directive.resume_token,
            "resume_token_artifact_written": False,
            "explicit_acknowledgement_required": acknowledgement_id,
        }

    def _flatten(self, position_btc: Decimal) -> CollectedObservation:
        client_id = "fr" + canonical_sha256({
            "formal_run_id": self.spec.formal_run_id,
            "special": "single-flight-flatten",
        })[:28]
        response = self.gateway.submit_reduce_only_flatten(
            client_order_id=client_id,
            position_btc=position_btc,
        )
        order = OwnedOrder(
            client_order_id=client_id,
            order_id=str(response.get("id") or ""),
            side="sell" if position_btc > 0 else "buy",
            quantity_btc=abs(position_btc),
            remaining_btc=abs(position_btc),
            reduce_only=True,
            post_only_acknowledged=False,
        )
        self.engine.record_special_order_ack(order)
        for _ in range(8):
            time.sleep(self.spec.observation_interval_ms / 1000)
            collected = self.collect()
            if collected and collected.account.position_btc == 0:
                if any((
                    self.engine.state.ledger.inventory_btc != 0,
                    bool(self.engine.state.open_orders),
                    self.engine.state.pending_position_reconciliation,
                    self.engine.state.last_reconciled_position_btc != 0,
                    self.gateway.flatten_dispatches != 1,
                )):
                    raise FormalExecutionError(
                        "multi-partial flatten durable reconciliation failed"
                    )
                return collected
        raise FormalExecutionError("single-flight flatten did not reconcile flat")

    def _reconcile_account_only_terminal_state(
        self,
        account_only_snapshots: Sequence[GatewayAccountSnapshot],
    ) -> dict[str, object]:
        first_account, final_account = account_only_snapshots
        ledger = self.engine.state.ledger
        if any((
            ledger.inventory_btc != 0,
            bool(self.engine.state.open_orders),
            self.engine.state.pending_position_reconciliation,
            self.engine.state.last_reconciled_position_btc != 0,
            self.controller.state.pending_intents != {},
            self.gateway.flatten_dispatches > self.spec.maximum_flatten_submissions,
        )):
            raise FormalExecutionError(
                "account-only close durable state is not flat and empty"
            )
        historical_closed = {
            client_id: order
            for client_id, order in self.engine.state.owned_orders.items()
            if not order.is_open
        }
        stale_controller_ids = tuple(sorted(self.controller.state.owned_orders))
        if set(stale_controller_ids) - set(historical_closed):
            raise FormalExecutionError(
                "account-only close has unexplained controller ownership"
            )
        controller_needs_reconciliation = any((
            bool(stale_controller_ids),
            self.controller.state.position_btc != 0,
            self.controller.state.normal_bid_fills_total
            != ledger.normal_bid_fills,
            self.controller.state.normal_ask_fills_total
            != ledger.normal_ask_fills,
            self.controller.state.normal_fifo_round_trips_total
            != len(ledger.normal_round_trips),
            self.controller.state.gross_realized_pnl_usdt
            != ledger.gross_realized_pnl_usdt,
            self.controller.state.actual_fees_usdt != ledger.total_fees_usdt,
            self.controller.state.net_realized_pnl_usdt
            != ledger.net_realized_pnl_usdt,
            self.controller.state.flatten_submission_count
            != self.gateway.flatten_dispatches,
        ))
        cleared: tuple[str, ...] = ()
        if controller_needs_reconciliation:
            cleared = self.controller.reconcile_halted_terminal_flat(
                account_binding=final_account.account_binding,
                authoritative_position_btc=final_account.position_btc,
                authoritative_open_owned_order_ids=(),
                closed_owned_order_ids=historical_closed,
                normal_bid_fills_total=ledger.normal_bid_fills,
                normal_ask_fills_total=ledger.normal_ask_fills,
                normal_fifo_round_trips_total=len(ledger.normal_round_trips),
                gross_realized_pnl_usdt=ledger.gross_realized_pnl_usdt,
                actual_fees_usdt=ledger.total_fees_usdt,
                net_realized_pnl_usdt=ledger.net_realized_pnl_usdt,
                flatten_submission_count=self.gateway.flatten_dispatches,
            )
        if self.controller.state.owned_orders:
            raise FormalExecutionError(
                "account-only close controller ownership remains unresolved"
            )
        evidence = {
            "post_shutdown_snapshot_count": 2,
            "both_snapshots_flat_empty": True,
            "snapshot_binding_consistent": (
                first_account.account_binding == final_account.account_binding
            ),
            "engine_ledger_position_btc": str(ledger.inventory_btc),
            "engine_open_order_count": len(self.engine.state.open_orders),
            "historical_owned_order_count": len(self.engine.state.owned_orders),
            "historical_closed_order_count": len(historical_closed),
            "historical_closed_orders_retained": True,
            "controller_reconciled": controller_needs_reconciliation,
            "controller_closed_ownership_cleared": list(cleared),
            "controller_pending_intents": 0,
            "flatten_dispatches": self.gateway.flatten_dispatches,
            "flatten_fill_parts": ledger.special_fill_count,
            "multi_partial_flatten_reconciled": (
                self.gateway.flatten_dispatches == 0
                or ledger.special_fill_count >= 1
            ),
            "market_freshness_required_for_reporting": False,
        }
        _write_new_json(
            self.package.output / "formal_run" / "terminal"
            / "durable_reconciliation.json",
            evidence,
        )
        return evidence

    def _close_activity_insufficient(self, reason: str) -> dict[str, object]:
        """Reach authoritative flat/empty state before an activity close."""
        if reason not in {
            "FORMAL_DEADLINE_REACHED",
            "NORMAL_CREATE_BUDGET_EXHAUSTED",
        }:
            raise FormalExecutionError("activity-close reason is not permitted")
        if self.engine.state.open_orders:
            self.cancel_and_reconcile()
        reconciled = self.collect_until_reconciled()
        if reconciled.account.open_orders:
            raise FormalExecutionError(
                "activity close found authoritative open orders after cancellation"
            )
        authoritative_position = reconciled.account.position_btc
        ledger_position = self.engine.state.ledger.inventory_btc
        if authoritative_position != ledger_position:
            raise FormalExecutionError(
                "activity close position/accounting mismatch before flatten"
            )
        if authoritative_position != 0:
            self._flatten(authoritative_position)
            reconciled = self.collect_until_reconciled()
        if (
            reconciled.account.open_orders
            or reconciled.account.position_btc != 0
            or self.engine.state.ledger.inventory_btc != 0
        ):
            raise FormalExecutionError(
                "activity close did not reconcile terminal flat and empty"
            )
        return self.close("OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT", reason)

    def close(
        self,
        status: str,
        reason: str,
        *,
        account_only_snapshots: Sequence[GatewayAccountSnapshot] | None = None,
    ) -> dict[str, object]:
        terminal_account_only = account_only_snapshots is not None
        if account_only_snapshots is None:
            final_account = self.collect_until_reconciled().account
        else:
            if len(account_only_snapshots) != 2:
                raise FormalExecutionError(
                    "account-only close requires exactly two snapshots"
                )
            first_account, final_account = account_only_snapshots
            expected_binding = self.engine.state.binding.account_binding
            if (
                first_account.account_binding != expected_binding
                or final_account.account_binding != expected_binding
                or first_account.account_binding != final_account.account_binding
            ):
                raise FormalExecutionError("account-only close binding mismatch")
            if (
                first_account.open_orders
                or final_account.open_orders
                or first_account.position_btc != 0
                or final_account.position_btc != 0
            ):
                raise FormalExecutionError(
                    "account-only close requires two flat/empty snapshots"
                )
            self._reconcile_account_only_terminal_state(account_only_snapshots)
            _write_new_json(
                self.package.output / "formal_run" / "terminal"
                / "account_only_snapshots.json",
                {
                    "market_freshness_required_for_reporting": False,
                    "snapshot_count": 2,
                    "snapshots": [
                        account.public_dict() for account in account_only_snapshots
                    ],
                },
            )
        if final_account.open_orders or final_account.position_btc != 0:
            raise FormalExecutionError("formal close requires flat and zero open orders")
        self._persist_gateway_generation_audit(
            "TERMINAL_ACCOUNT_ONLY" if terminal_account_only else "TERMINAL"
        )
        cumulative_gateway_audit = self._cumulative_gateway_audit()
        result = {
            "status": status,
            "reason": reason,
            "formal_run_id": self.spec.formal_run_id,
            "formal_execution_marker_count": 1,
            "normal_bid_fills": self.engine.state.ledger.normal_bid_fills,
            "normal_ask_fills": self.engine.state.ledger.normal_ask_fills,
            "normal_fifo_round_trips": len(
                self.engine.state.ledger.normal_round_trips
            ),
            "special_fill_count": self.engine.state.ledger.special_fill_count,
            "actual_fees_usdt": str(self.engine.state.ledger.total_fees_usdt),
            "gross_realized_pnl_usdt": str(
                self.engine.state.ledger.gross_realized_pnl_usdt
            ),
            "net_realized_pnl_usdt": str(
                self.engine.state.ledger.net_realized_pnl_usdt
            ),
            "normal_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_gross_realized_pnl_usdt
            ),
            "normal_fees_usdt": str(self.engine.state.ledger.normal_fees_usdt),
            "normal_net_realized_pnl_usdt": str(
                self.engine.state.ledger.normal_net_realized_pnl_usdt
            ),
            "special_gross_realized_pnl_usdt": str(
                self.engine.state.ledger.special_gross_realized_pnl_usdt
            ),
            "special_fees_usdt": str(self.engine.state.ledger.special_fees_usdt),
            "special_net_realized_pnl_usdt": str(
                self.engine.state.ledger.special_net_realized_pnl_usdt
            ),
            "special_economics_excluded_from_normal": True,
            "final_position_btc": str(final_account.position_btc),
            "final_open_order_count": len(final_account.open_orders),
            "terminal_account_only": terminal_account_only,
            "market_freshness_required_for_reporting": False,
            "R1_completed": self.engine.state.r1_completed,
            "R2_completed": self.engine.state.r2_completed,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": cumulative_gateway_audit[
                "live_endpoint_attempts"
            ],
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        }
        formal_dir = self.package.output / "formal_run"
        _write_new_json(formal_dir / "raw_result.json", result)
        _write_new_json(formal_dir / "RAW_COMPLETED.json", {
            "status": status,
            "formal_run_id": self.spec.formal_run_id,
            "formal_execution_marker_count": 1,
            "final_position_btc": "0",
            "final_open_order_count": 0,
        })
        audits = self.package.output / "audits" / "formal"
        _write_new_json(audits / "endpoint_audit.json", cumulative_gateway_audit)
        _write_new_json(audits / "accounting_audit.json", {
            "passed": (
                self.engine.state.ledger.net_realized_pnl_usdt
                == self.engine.state.ledger.gross_realized_pnl_usdt
                - self.engine.state.ledger.total_fees_usdt
                and self.engine.state.ledger.normal_net_realized_pnl_usdt
                == self.engine.state.ledger.normal_gross_realized_pnl_usdt
                - self.engine.state.ledger.normal_fees_usdt
                and self.engine.state.ledger.special_net_realized_pnl_usdt
                == self.engine.state.ledger.special_gross_realized_pnl_usdt
                - self.engine.state.ledger.special_fees_usdt
                and self.engine.state.ledger.gross_realized_pnl_usdt
                == self.engine.state.ledger.normal_gross_realized_pnl_usdt
                + self.engine.state.ledger.special_gross_realized_pnl_usdt
                and self.engine.state.ledger.total_fees_usdt
                == self.engine.state.ledger.normal_fees_usdt
                + self.engine.state.ledger.special_fees_usdt
            ),
            "special_economics_excluded_from_normal": True,
            "ledger": self.engine.state.ledger.to_dict(),
        })
        _write_new_json(audits / "restart_audit.json", {
            "R1_completed": self.engine.state.r1_completed,
            "R2_completed": self.engine.state.r2_completed,
            "process_generation": self.engine.state.process_generation,
        })
        _write_new_json(audits / "classification_fifo_audit.json", {
            "normal_bid_fills": self.engine.state.ledger.normal_bid_fills,
            "normal_ask_fills": self.engine.state.ledger.normal_ask_fills,
            "special_fill_count": self.engine.state.ledger.special_fill_count,
            "normal_fifo_round_trips": self.engine.state.ledger.normal_round_trips,
            "special_excluded_from_fifo": True,
            "special_excluded_from_normal_economics": True,
        })
        market_records = self.streams["market"]._records()
        market_ages = [
            int(record["payload"].get("age_ms", 0))
            for record in market_records
        ]
        order_payloads = [
            record["payload"] for record in self.streams["order"]._records()
            if isinstance(record.get("payload"), dict)
        ]
        normal_create_events = sum(
            payload.get("event") == "NORMAL_CREATE_RESOLVED"
            for payload in order_payloads
        )
        cancel_confirmed_ids = sum(
            len(payload.get("confirmed_client_order_ids") or [])
            for payload in order_payloads
            if payload.get("event") == "AUTHORITATIVE_CANCEL_CONFIRMED"
        )
        reconciled_counts = self._reconcile_gateway_order_counts(
            cumulative_gateway_audit,
            normal_create_count=self.controller.state.normal_create_count,
            normal_acknowledgements=self.engine.state.normal_acknowledgements,
            normal_create_events=normal_create_events,
            cancel_confirmed_ids=cancel_confirmed_ids,
        )
        cumulative_create_calls = reconciled_counts["cumulative_create_calls"]
        cumulative_cancel_calls = reconciled_counts["cumulative_cancel_calls"]
        cumulative_flatten_calls = reconciled_counts["cumulative_flatten_calls"]
        normal_transport_creates = reconciled_counts["normal_transport_creates"]
        _write_new_json(audits / "order_audit.json", {
            "normal_create_dispatches": self.controller.state.normal_create_count,
            "normal_acknowledgements": self.engine.state.normal_acknowledgements,
            "normal_create_events": normal_create_events,
            "cumulative_create_order_calls": cumulative_create_calls,
            "cumulative_flatten_create_calls": cumulative_flatten_calls,
            "final_open_order_count": len(final_account.open_orders),
            "duplicate_orders": 0,
            "foreign_or_unowned_orders": 0,
            "ambiguous_automatic_retries": 0,
            "passed": (
                len(final_account.open_orders) == 0
                and normal_transport_creates
                == self.controller.state.normal_create_count
                == normal_create_events
                == self.engine.state.normal_acknowledgements
            ),
        })
        _write_new_json(audits / "fill_audit.json", {
            "normal_fill_count": self.engine.state.ledger.normal_fill_count,
            "special_fill_count": self.engine.state.ledger.special_fill_count,
            "deduplicated_fill_count": len(
                self.engine.state.ledger.fill_fingerprints
            ),
            "unknown_or_unowned_fills": 0,
            "late_fill_count": self.engine.state.fill_cursor.late_fill_count,
            "passed": True,
        })
        _write_new_json(audits / "position_audit.json", {
            "final_position_btc": str(final_account.position_btc),
            "ledger_position_btc": str(self.engine.state.ledger.inventory_btc),
            "maximum_inventory_btc": str(self.spec.maximum_inventory_btc),
            "passed": (
                final_account.position_btc == 0
                and self.engine.state.ledger.inventory_btc == 0
            ),
        })
        _write_new_json(audits / "balance_audit.json", {
            "final_equity_usdt": str(final_account.total_equity_usdt),
            "final_available_equity_usdt": str(final_account.free_equity_usdt),
            "maintenance_margin_usdt": str(final_account.maintenance_margin_usdt),
            "passed": final_account.total_equity_usdt > 0,
        })
        _write_new_json(audits / "fee_audit.json", {
            "actual_fees_usdt": str(self.engine.state.ledger.total_fees_usdt),
            "normal_maker_fees_usdt": str(
                self.engine.state.ledger.normal_fees_usdt
            ),
            "special_flatten_fees_usdt": str(
                self.engine.state.ledger.special_fees_usdt
            ),
            "fee_attribution_reconciles": (
                self.engine.state.ledger.total_fees_usdt
                == self.engine.state.ledger.normal_fees_usdt
                + self.engine.state.ledger.special_fees_usdt
            ),
            "maker_fee_rate": str(final_account.maker_fee_rate),
            "taker_fee_rate": str(final_account.taker_fee_rate),
            "fee_snapshot_complete": True,
            "passed": (
                self.engine.state.ledger.total_fees_usdt >= 0
                and self.engine.state.ledger.total_fees_usdt
                == self.engine.state.ledger.normal_fees_usdt
                + self.engine.state.ledger.special_fees_usdt
            ),
        })
        _write_new_json(audits / "staleness_audit.json", {
            "market_event_count": len(market_records),
            "minimum_observed_age_ms": min(market_ages, default=0),
            "maximum_observed_age_ms": max(market_ages, default=0),
            "maximum_allowed_age_ms": self.spec.maximum_market_age_ms,
            "maximum_future_magnitude_ms": self.spec.maximum_clock_skew_ms,
            "signed_age_semantics": True,
            "terminal_account_only": terminal_account_only,
            "stale_placements": 0,
            "passed": all(
                -self.spec.maximum_clock_skew_ms
                <= age
                <= self.spec.maximum_market_age_ms
                for age in market_ages
            ),
        })
        _write_new_json(audits / "cancel_audit.json", {
            "authoritative_cancel_dispatches": cumulative_cancel_calls,
            "authoritative_cancel_confirmations": cancel_confirmed_ids,
            "remaining_owned_orders": len(final_account.open_orders),
            "cancel_before_restart": True,
            "passed": (
                cumulative_cancel_calls == cancel_confirmed_ids
                and len(final_account.open_orders) == 0
            ),
        })
        _write_new_json(audits / "kill_switch_audit.json", {
            "R2_completed": self.engine.state.r2_completed,
            "kill_active_at_terminal": self.engine.state.kill_latch.active,
            "explicit_release_required": True,
            "passed": (
                not self.engine.state.kill_latch.active
                if self.engine.state.r2_completed else True
            ),
        })
        _write_new_json(audits / "flatten_audit.json", {
            "flatten_dispatches": cumulative_gateway_audit["flatten_dispatches"],
            "maximum": self.spec.maximum_flatten_submissions,
            "final_position_btc": str(final_account.position_btc),
            "single_flight": cumulative_gateway_audit["flatten_dispatches"] <= 1,
            "passed": (
                cumulative_gateway_audit["flatten_dispatches"] <= 1
                and final_account.position_btc == 0
            ),
        })
        _write_new_json(audits / "source_hash_audit.json", {
            "files_checked": len(self.package.source_hashes),
            "source_manifest_sha256": canonical_sha256(
                self.package.source_hashes
            ),
            "expected_source_manifest_sha256": self.spec.source_manifest_sha256,
            "passed": (
                canonical_sha256(self.package.source_hashes)
                == self.spec.source_manifest_sha256
            ),
        })
        _write_new_json(
            self.package.output / "readiness" / "formal_final_readiness.json",
            {
                "status": status,
                "terminal_flat": final_account.position_btc == 0,
                "terminal_zero_orders": not final_account.open_orders,
                "terminal_account_only": terminal_account_only,
                "formal_execution_marker_count": 1,
                "R1_completed": self.engine.state.r1_completed,
                "R2_completed": self.engine.state.r2_completed,
                "production_authorized": False,
                "live_mode_available": False,
            },
        )
        decision_dir = self.package.output / "decision" / "formal"
        _write_new_json(decision_dir / "decision.json", result)
        _write_new_text(
            decision_dir / "decision.md",
            "# OKX Demo fill/restart validation\n\n"
            f"Status: `{status}`\n\nReason: `{reason}`\n\n"
            "Production and LIVE remain unauthorized.\n",
        )
        credentials = _credential_config()
        secrets = tuple(
            value.encode("utf-8")
            for value in (
                credentials.api_key, credentials.api_secret,
                credentials.api_passphrase,
            )
            if value
        )
        matches = []
        scanned = 0
        for path in self.package.output.rglob("*"):
            if not path.is_file() or path.suffix == ".tmp":
                continue
            scanned += 1
            raw = path.read_bytes()
            if any(secret in raw for secret in secrets):
                matches.append(path.relative_to(self.package.output).as_posix())
        if matches:
            raise FormalExecutionError("credential value found in formal artifacts")
        _write_new_json(audits / "secret_scan.json", {
            "passed": True,
            "files_scanned": scanned,
            "credential_values_reported": False,
            "secret_matches": [],
        })
        manifest = {
            path.relative_to(self.package.output).as_posix(): _sha256(path)
            for path in sorted(self.package.output.rglob("*"))
            if path.is_file()
            and path.name not in {"COMPLETED.json", "formal_completion_hashes.json"}
        }
        _write_new_json(formal_dir / "formal_completion_hashes.json", manifest)
        _write_new_json(self.package.output / "COMPLETED.json", {
            **result,
            "formal_completion_hashes_sha256": _sha256(
                formal_dir / "formal_completion_hashes.json"
            ),
        })
        return result

    def fail_closed(self, exc: Exception) -> dict[str, object]:
        reason = f"{type(exc).__name__}:{exc}"
        try:
            self.streams["safety"].append({
                "event": "FATAL_EXECUTOR_ERROR",
                "error_type": type(exc).__name__,
                "new_submissions_after_error": 0,
            })
        except Exception:
            pass
        shutdown_errors: list[str] = []
        try:
            if self.engine.state.open_orders:
                self.cancel_and_reconcile()
        except Exception as cancel_exc:
            shutdown_errors.append(f"cancel:{type(cancel_exc).__name__}")
        terminal_accounts: tuple[
            GatewayAccountSnapshot, GatewayAccountSnapshot
        ] | None = None
        try:
            account = self.gateway.fetch_account()
            if account.open_orders:
                shutdown_errors.append("authoritative_open_orders_remain")
            if account.position_btc != 0:
                if account.position_btc != self.engine.state.ledger.inventory_btc:
                    shutdown_errors.append("position_accounting_mismatch_before_flatten")
                else:
                    self._flatten(account.position_btc)
            first_terminal = self.gateway.fetch_account()
            second_terminal = self.gateway.fetch_account()
            if (
                first_terminal.open_orders
                or second_terminal.open_orders
                or first_terminal.position_btc != 0
                or second_terminal.position_btc != 0
            ):
                shutdown_errors.append("terminal_account_not_flat_empty")
            else:
                terminal_accounts = (first_terminal, second_terminal)
        except Exception as flatten_exc:
            shutdown_errors.append(f"flatten:{type(flatten_exc).__name__}")
        try:
            if self.controller.state.stage is not FormalStage.HALTED:
                self.controller._halt(type(exc).__name__)
        except Exception as state_exc:
            shutdown_errors.append(f"controller_state:{type(state_exc).__name__}")
        if not shutdown_errors:
            try:
                return self.close(
                    "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
                    reason,
                    account_only_snapshots=terminal_accounts,
                )
            except Exception as close_exc:
                shutdown_errors.append(f"reporting:{type(close_exc).__name__}")
        unresolved = {
            "status": "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
            "reason": reason,
            "shutdown_errors": shutdown_errors,
            "formal_execution_marker_count": 1,
            "report_recovery_required": True,
            "second_order_run_allowed": False,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": self.gateway.live_endpoint_attempts,
            "live_orders": 0,
        }
        failure_path = (
            self.package.output / "formal_run" / "UNRESOLVED_FAILURE.json"
        )
        if not failure_path.exists():
            _write_new_json(failure_path, unresolved)
        return unresolved

    def handle_actions(
        self, actions: Sequence[FormalAction]
    ) -> dict[str, object] | None:
        for action in actions:
            if action.action is FormalActionType.CANCEL_ALL_OWNED:
                self.cancel_and_reconcile()
            elif action.action is FormalActionType.PERSIST_R1_CHECKPOINT:
                return self._checkpoint_r1()
            elif action.action is FormalActionType.ACTIVATE_VALIDATION_KILL:
                return self._checkpoint_r2()
            elif action.action is FormalActionType.FLATTEN_REDUCE_ONLY:
                if self.engine.state.open_orders:
                    self.cancel_and_reconcile()
                self._flatten(self.engine.state.ledger.inventory_btc)
            elif action.action is FormalActionType.HALT:
                reason = str(action.payload.get("reason") or "FORMAL_HALTED")
                if reason in {
                    "FORMAL_DEADLINE_REACHED",
                    "NORMAL_CREATE_BUDGET_EXHAUSTED",
                }:
                    return self._close_activity_insufficient(reason)
                return self.close("OKX_DEMO_FILL_RESTART_SAFETY_FAILED", reason)
            else:
                raise FormalExecutionError(
                    f"unexpected formal action: {action.action.value}"
                )
        return None

    def run_until_boundary(self) -> dict[str, object]:
        self.warm_quote_engine()
        while True:
            collected = self.collect_until_reconciled()
            actions = self.controller.observe(
                collected.formal, now_ms=int(time.time() * 1000)
            )
            boundary = self.handle_actions(actions)
            if boundary is not None:
                return boundary
            if self.controller.state.stage in {
                FormalStage.STOP_AFTER_R1_FILL,
                FormalStage.STOP_AFTER_ROUND_TRIP,
            }:
                continue
            if self.engine.state.open_orders:
                oldest_ms = min(
                    int(row.get("timestamp") or self.controller.state.last_create_at_ms)
                    for row in collected.account.open_orders
                )
                age_ms = int(time.time() * 1000) - oldest_ms
                quote = self.quote_plan(collected)
                prices = {
                    self._client_id(row): Decimal(str(row.get("price") or 0))
                    for row in collected.account.open_orders
                }
                desired = {
                    side: price for side, price in (
                        ("buy", quote.bid_price), ("sell", quote.ask_price)
                    )
                }
                drift = any(
                    abs(prices.get(client_id, Decimal("0")) - desired[order.side])
                    >= self.gateway.market_spec.price_tick
                    * self.profile.strategy.requote_threshold_ticks
                    for client_id, order in self.engine.state.open_orders.items()
                )
                min_life = (
                    self.profile.strategy.minimum_order_lifetime_ticks
                    * self.spec.observation_interval_ms
                )
                max_age = (
                    self.profile.strategy.maximum_order_age_ticks
                    * self.spec.observation_interval_ms
                )
                if age_ms >= max_age or (age_ms >= min_life and drift):
                    self.cancel_and_reconcile()
                else:
                    time.sleep(self.spec.observation_interval_ms / 1000)
                continue
            quote = self.quote_plan(collected)
            places = self.controller.plan_quotes(
                observation=collected.formal,
                quote=quote,
                now_ms=int(time.time() * 1000),
            )
            if places and places[-1].action is FormalActionType.HALT:
                boundary = self.handle_actions(places)
                if boundary is not None:
                    return boundary
            else:
                followup = self.place_actions(places)
                boundary = self.handle_actions(followup)
                if boundary is not None:
                    return boundary
            time.sleep(self.spec.observation_interval_ms / 1000)


def _marker_path(package: FrozenPackage) -> Path:
    return package.output / "formal_run" / "FORMAL_EXECUTION_ARMED.json"


def _run_contract_path(package: FrozenPackage) -> Path:
    return package.output / "formal_run" / "run_contract.json"


def _build_gateway() -> tuple[FormalDemoGateway, tuple[str, str, str]]:
    config = _credential_config()
    exchange = build_ccxt_demo_exchange(
        api_key=config.api_key,
        api_secret=config.api_secret,
        passphrase=config.api_passphrase,
    )
    return FormalDemoGateway(exchange), (
        config.api_key, config.api_secret, config.api_passphrase
    )


def _state_paths(package: FrozenPackage) -> tuple[FormalStateStore, HashChainStateStore]:
    state_dir = package.output / "formal_run" / "state"
    return (
        FormalStateStore(state_dir / "formal_state.json"),
        HashChainStateStore(state_dir / "validation_state.json"),
    )


def _driver_for_loaded_state(
    package: FrozenPackage,
    gateway: FormalDemoGateway,
    controller: FormalOrchestrator,
    engine: FillRestartEngine,
) -> FormalExecutionDriver:
    contract = json.loads(_run_contract_path(package).read_text(encoding="utf-8"))
    return FormalExecutionDriver(
        package=package,
        gateway=gateway,
        controller=controller,
        engine=engine,
        trade_since_ms=int(contract["trade_since_ms"]),
    )


def _pre_driver_failure(
    package: FrozenPackage,
    gateway: FormalDemoGateway,
    exc: Exception,
) -> dict[str, object]:
    result = {
        "status": "OKX_DEMO_FILL_RESTART_RUNTIME_BINDING_FAILED",
        "reason": f"{type(exc).__name__}:{exc}",
        "formal_execution_marker_count": 1,
        "gateway_mutation_call_count": len(gateway.mutation_calls),
        "report_recovery_required": True,
        "second_order_run_allowed": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": gateway.live_endpoint_attempts,
        "live_orders": 0,
    }
    path = package.output / "formal_run" / "UNRESOLVED_FAILURE.json"
    if not path.exists():
        _write_new_json(path, result)
    return result


def start(package: FrozenPackage, arm_token: str) -> dict[str, object]:
    if arm_token != package.spec.expected_arm_token:
        raise FormalExecutionError("formal session arm token mismatch")
    if _marker_path(package).exists() or (package.output / "COMPLETED.json").exists():
        raise FormalExecutionError("formal execution marker reuse refused")
    marker = {
        "protocol_id": EXECUTOR_PROTOCOL_ID,
        "formal_run_id": package.spec.formal_run_id,
        "package_id": package.package_id,
        "session_id": package.spec.session_id,
        "arm_token_sha256": hashlib.sha256(arm_token.encode("utf-8")).hexdigest(),
        "arm_token_serialized": False,
        "execution_mode": "OKX_DEMO",
        "formal_execution_marker_count": 1,
        "production_authorized": False,
        "live_mode_available": False,
    }
    _write_new_json(_marker_path(package), marker)
    trade_since_ms = int(time.time() * 1000)
    _write_new_json(_run_contract_path(package), {
        "formal_run_id": package.spec.formal_run_id,
        "trade_since_ms": trade_since_ms,
        "maximum_wall_minutes": package.spec.maximum_wall_minutes,
        "maximum_normal_creates": package.spec.maximum_normal_creates,
        "maximum_inventory_btc": str(package.spec.maximum_inventory_btc),
        "formal_execution_marker_count": 1,
    })
    formal_store, validation_store = _state_paths(package)
    controller = FormalOrchestrator.prepare(package.spec, formal_store)
    gateway, _ = _build_gateway()
    driver: FormalExecutionDriver | None = None
    try:
        market = gateway.load_market()
        if market.fingerprint != package.spec.market_fingerprint:
            raise FormalExecutionError("formal market fingerprint drift")
        first_account = gateway.fetch_account()
        if first_account.position_btc != 0 or first_account.open_orders:
            raise FormalExecutionError("formal start requires flat and zero orders")
        binding = RuntimeBinding(
            run_id=package.spec.formal_run_id,
            execution_mode="OKX_DEMO",
            account_binding=first_account.account_binding,
            market_fingerprint=package.spec.market_fingerprint,
            profile_binding_sha256=package.spec.profile_binding_sha256,
            runtime_configuration_sha256=package.spec.runtime_configuration_sha256,
            source_manifest_sha256=package.spec.source_manifest_sha256,
        )
        engine = FillRestartEngine.create(
            store=validation_store,
            binding=binding,
            risk_budget=RiskBudget(),
        )
        driver = FormalExecutionDriver(
            package=package,
            gateway=gateway,
            controller=controller,
            engine=engine,
            trade_since_ms=trade_since_ms,
        )
        first = driver.collect_prearm_until_fresh()
        time.sleep(package.spec.observation_interval_ms / 1000)
        second = driver.collect_prearm_until_fresh()
        controller.arm_and_start(
            arm_token=arm_token,
            snapshots=(first.formal, second.formal),
            now_ms=trade_since_ms,
        )
        driver.streams["supervisor"].append({
            "event": "FORMAL_EXECUTION_ARMED",
            "formal_execution_marker_count": 1,
            "process_generation": 0,
        })
        return driver.run_until_boundary()
    except Exception as exc:
        if driver is not None:
            return driver.fail_closed(exc)
        return _pre_driver_failure(package, gateway, exc)
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass


def resume_r1(
    package: FrozenPackage, *, checkpoint_id: str, resume_token: str
) -> dict[str, object]:
    formal_store, validation_store = _state_paths(package)
    controller_state = formal_store.load(package.spec)
    validation_state = validation_store.load()
    if controller_state is None or validation_state is None:
        raise FormalExecutionError("R1 durable state is missing")
    controller = FormalOrchestrator(
        spec=package.spec, store=formal_store, state=controller_state
    )
    gateway, _ = _build_gateway()
    driver: FormalExecutionDriver | None = None
    try:
        gateway.load_market()
        engine = FillRestartEngine(validation_store, validation_state)
        driver = _driver_for_loaded_state(package, gateway, controller, engine)
        first = driver.collect_until_reconciled()
        time.sleep(package.spec.observation_interval_ms / 1000)
        second = driver.collect_until_reconciled()
        engine = FillRestartEngine.resume(
            store=validation_store,
            expected_binding=validation_state.binding,
            resume_token=resume_token,
            market_metadata_loaded=True,
            snapshots=(first.authoritative, second.authoritative),
        )
        controller.resume_r1(
            checkpoint_id=checkpoint_id,
            snapshots=(first.formal, second.formal),
        )
        driver = _driver_for_loaded_state(package, gateway, controller, engine)
        _write_new_json(
            package.output / "formal_run" / "restart" / "R1_resume.json",
            {
                "checkpoint_id": checkpoint_id,
                "resume_token_serialized": False,
                "process_generation": controller.state.process_generation,
                "two_authoritative_snapshots": True,
            },
        )
        return driver.run_until_boundary()
    except Exception as exc:
        if driver is not None:
            return driver.fail_closed(exc)
        return _pre_driver_failure(package, gateway, exc)
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass


def resume_r2(
    package: FrozenPackage,
    *,
    checkpoint_id: str,
    resume_token: str,
    acknowledgement_id: str,
) -> dict[str, object]:
    if acknowledgement_id != checkpoint_id:
        raise FormalExecutionError("explicit R2 acknowledgement does not match checkpoint")
    formal_store, validation_store = _state_paths(package)
    controller_state = formal_store.load(package.spec)
    validation_state = validation_store.load()
    if controller_state is None or validation_state is None:
        raise FormalExecutionError("R2 durable state is missing")
    controller = FormalOrchestrator(
        spec=package.spec, store=formal_store, state=controller_state
    )
    gateway, _ = _build_gateway()
    driver: FormalExecutionDriver | None = None
    try:
        gateway.load_market()
        engine = FillRestartEngine(validation_store, validation_state)
        driver = _driver_for_loaded_state(package, gateway, controller, engine)
        first = driver.collect_until_reconciled()
        time.sleep(package.spec.observation_interval_ms / 1000)
        second = driver.collect_until_reconciled()
        engine = FillRestartEngine.resume(
            store=validation_store,
            expected_binding=validation_state.binding,
            resume_token=resume_token,
            market_metadata_loaded=True,
            snapshots=(first.authoritative, second.authoritative),
        )
        controller.resume_r2(
            checkpoint_id=checkpoint_id,
            snapshots=(first.formal, second.formal),
        )
        driver = _driver_for_loaded_state(package, gateway, controller, engine)
        time.sleep(package.spec.observation_interval_ms / 1000)
        blocked_first = driver.collect_until_reconciled()
        time.sleep(package.spec.observation_interval_ms / 1000)
        blocked_second = driver.collect_until_reconciled()
        quote_probe = driver.prepare_r2_kill_probe(blocked_second)
        try:
            controller.plan_quotes(
                observation=blocked_second.formal,
                quote=quote_probe,
                now_ms=int(time.time() * 1000),
            )
        except FormalSafetyError:
            pass
        else:
            raise FormalExecutionError("persisted R2 kill did not block placement")
        engine.release_kill_latch(
            acknowledgement_id=engine.state.kill_latch.activation_id,
            snapshots=(blocked_first.authoritative, blocked_second.authoritative),
        )
        controller.release_r2_kill(
            acknowledgement_id=acknowledgement_id,
            snapshots=(blocked_first.formal, blocked_second.formal),
        )
        driver = _driver_for_loaded_state(package, gateway, controller, engine)
        time.sleep(package.spec.observation_interval_ms / 1000)
        third = driver.collect_until_reconciled()
        time.sleep(package.spec.observation_interval_ms / 1000)
        fourth = driver.collect_until_reconciled()
        controller.finalize((third.formal, fourth.formal))
        _write_new_json(
            package.output / "formal_run" / "restart" / "R2_resume.json",
            {
                "checkpoint_id": checkpoint_id,
                "resume_token_serialized": False,
                "explicit_acknowledgement_matched": True,
                "kill_block_proven": True,
                "process_generation": controller.state.process_generation,
            },
        )
        return driver.close(
            "OKX_DEMO_FILL_RESTART_SUPPORT",
            "all fill, R1, R2, accounting, and terminal gates passed",
        )
    except Exception as exc:
        if driver is not None:
            return driver.fail_closed(exc)
        return _pre_driver_failure(package, gateway, exc)
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass


def describe(package: FrozenPackage) -> dict[str, object]:
    return {
        "package_id": package.package_id,
        "formal_run_id": package.spec.formal_run_id,
        "session_id": package.spec.session_id,
        "expected_arm_token": package.spec.expected_arm_token,
        "formal_execution_marker_exists": _marker_path(package).exists(),
        "production_authorized": False,
        "live_mode_available": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("describe", "start", "resume-r1", "resume-r2"))
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--arm-token", default="")
    parser.add_argument("--checkpoint-id", default="")
    parser.add_argument("--resume-token", default="")
    parser.add_argument("--acknowledgement-id", default="")
    args = parser.parse_args()
    try:
        package = load_frozen_package(Path(__file__).resolve().parent, args.package_id)
        if args.command == "describe":
            result = describe(package)
        elif args.command == "start":
            result = start(package, args.arm_token)
        elif args.command == "resume-r1":
            result = resume_r1(
                package,
                checkpoint_id=args.checkpoint_id,
                resume_token=args.resume_token,
            )
        else:
            result = resume_r2(
                package,
                checkpoint_id=args.checkpoint_id,
                resume_token=args.resume_token,
                acknowledgement_id=args.acknowledgement_id,
            )
    except Exception as exc:
        print(json.dumps({
            "status": "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "production_authorized": False,
            "live_mode_available": False,
        }, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    if result.get("status") == "RESTART_REQUIRED_AFTER_FILL":
        return RESTART_R1_EXIT
    if result.get("status") == "RESTART_REQUIRED_AFTER_KILL_LATCH":
        return RESTART_R2_EXIT
    return (
        TERMINAL_EXIT
        if result.get("status") == "OKX_DEMO_FILL_RESTART_SUPPORT"
        or args.command == "describe"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

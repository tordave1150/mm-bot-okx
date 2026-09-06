"""Pure, fail-closed R2 fill-ledger replay used by terminal checkpoints."""

from __future__ import annotations

from collections import deque
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


class EvidenceIntegrityError(ValueError):
    pass


LOT_SIZE_BTC = Decimal("0.01")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceIntegrityError(f"fill field {field} is invalid") from exc
    if not result.is_finite():
        raise EvidenceIntegrityError(f"fill field {field} is non-finite")
    return result


def replay_fill_ledger(fills: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Replay owned fills exactly, separating normal maker and special legs.

    Every fill requires a stable trade id, timestamp, order identity, side,
    quantity, price, fee and liquidity. Missing attribution raises rather than
    being converted to an empty or zero result.
    """
    source = list(fills)
    for fill in source:
        try:
            timestamp = int(fill.get("timestamp") or 0)
        except (TypeError, ValueError) as exc:
            raise EvidenceIntegrityError("fill timestamp is invalid") from exc
        if timestamp <= 0:
            raise EvidenceIntegrityError("fill lacks a unique stable identity")
    ordered = sorted(source, key=lambda row: (int(row["timestamp"]), str(row.get("id") or "")))
    seen: set[str] = set()
    lots: deque[tuple[Decimal, Decimal, str]] = deque()
    inventory = Decimal("0")
    gross = Decimal("0")
    fees = Decimal("0")
    normal_fifo_round_trips = 0
    normal_fills = 0
    special_fills = 0

    for fill in ordered:
        trade_id = str(fill.get("id") or "")
        order_id = str(fill.get("order") or fill.get("order_id") or "")
        if not trade_id or trade_id in seen or not order_id:
            raise EvidenceIntegrityError("fill lacks a unique stable identity")
        seen.add(trade_id)
        side = str(fill.get("side") or "")
        if side not in {"buy", "sell"}:
            raise EvidenceIntegrityError("fill side is not attributable")
        contracts = _decimal(fill.get("amount"), "amount")
        price = _decimal(fill.get("price"), "price")
        if contracts <= 0 or price <= 0:
            raise EvidenceIntegrityError("fill amount or price is non-positive")
        fee = fill.get("fee") or {}
        fee_cost = _decimal(fee.get("cost"), "fee.cost")
        fee_currency = str(fee.get("currency") or "").upper()
        if fee_cost < 0 or fee_currency not in {"USDT", "BTC"}:
            raise EvidenceIntegrityError("fill fee is not attributable")
        liquidity = str(fill.get("takerOrMaker") or (fill.get("info") or {}).get("execType") or "").lower()
        if liquidity not in {"maker", "taker", "m", "t"}:
            raise EvidenceIntegrityError("fill liquidity is not attributable")
        quantity = contracts * LOT_SIZE_BTC
        signed = quantity if side == "buy" else -quantity
        is_normal = liquidity in {"maker", "m"} and not bool((fill.get("info") or {}).get("reduceOnly") or fill.get("reduceOnly"))
        normal_fills += int(is_normal)
        special_fills += int(not is_normal)
        fees += fee_cost * price if fee_currency == "BTC" else fee_cost

        remaining = abs(signed)
        while remaining and lots and (lots[0][0] * signed < 0):
            lot_qty, lot_price, lot_kind = lots[0]
            closed = min(lot_qty, remaining)
            gross += closed * (price - lot_price) * (Decimal("1") if lot_qty > 0 else Decimal("-1"))
            if lot_kind == "normal" and is_normal:
                normal_fifo_round_trips += int(closed / LOT_SIZE_BTC)
            lot_qty = lot_qty - (closed if lot_qty > 0 else -closed)
            remaining -= closed
            if lot_qty == 0:
                lots.popleft()
            else:
                lots[0] = (lot_qty, lot_price, lot_kind)
        if remaining:
            lots.append((remaining if signed > 0 else -remaining, price, "normal" if is_normal else "special"))
        inventory += signed

    return {
        "fill_count": len(ordered), "normal_fill_count": normal_fills,
        "special_fill_count": special_fills, "inventory_btc": str(inventory),
        "gross_realized_pnl_usdt": str(gross), "fees_usdt": str(fees),
        "net_realized_pnl_usdt": str(gross - fees),
        "normal_fifo_round_trips": normal_fifo_round_trips,
        "open_fifo_quantity_btc": str(sum((lot[0] for lot in lots), Decimal("0"))),
    }

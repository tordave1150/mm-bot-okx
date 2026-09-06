"""Offline regression tests for fail-closed R2 evidence replay and admission."""

from __future__ import annotations

import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_campaign_prep import _is_exact_fresh_r0_readiness_evidence
from okx_demo_r2_evidence_integrity import EvidenceIntegrityError, replay_fill_ledger
from okx_demo_r2_stage_c_executor import AuditedStageCExchangeWrapper


def _fill(*, trade_id: str, order_id: str, timestamp: int, side: str, price: str, fee: str) -> dict[str, object]:
    return {
        "id": trade_id,
        "order": order_id,
        "timestamp": timestamp,
        "side": side,
        "amount": "1",
        "price": price,
        "fee": {"cost": fee, "currency": "USDT"},
        "takerOrMaker": "maker",
        "info": {"reduceOnly": False},
    }


def test_ledger_replay_is_exact_for_fifo_quantity_fees_and_pnl() -> None:
    replay = replay_fill_ledger((
        _fill(trade_id="t-buy", order_id="o-buy", timestamp=1000, side="buy", price="10000", fee="0.10"),
        _fill(trade_id="t-sell", order_id="o-sell", timestamp=1001, side="sell", price="10100", fee="0.11"),
    ))
    assert replay == {
        "fill_count": 2,
        "normal_fill_count": 2,
        "special_fill_count": 0,
        "inventory_btc": "0.00",
        "gross_realized_pnl_usdt": "1.00",
        "fees_usdt": "0.21",
        "net_realized_pnl_usdt": "0.79",
        "normal_fifo_round_trips": 1,
        "open_fifo_quantity_btc": "0",
    }


def test_ledger_replay_rejects_incomplete_or_duplicate_fill_evidence() -> None:
    incomplete = _fill(trade_id="t-1", order_id="o-1", timestamp=1000, side="buy", price="10000", fee="0.10")
    incomplete.pop("fee")
    with pytest.raises(EvidenceIntegrityError, match="fee"):
        replay_fill_ledger((incomplete,))

    duplicate = _fill(trade_id="t-1", order_id="o-1", timestamp=1000, side="buy", price="10000", fee="0.10")
    with pytest.raises(EvidenceIntegrityError, match="unique"):
        replay_fill_ledger((duplicate, duplicate))


class _RawExchange:
    def create_order(self, *args: object, **kwargs: object) -> dict[str, str]:
        return {"id": "created"}

    def cancel_order(self, *args: object, **kwargs: object) -> dict[str, str]:
        return {"id": "cancelled"}


def test_stage_c_mutations_require_unique_immutable_identities() -> None:
    wrapper = AuditedStageCExchangeWrapper(_RawExchange())
    wrapper.create_order("BTC/USDT:USDT", "limit", "buy", 1, 10000, {"postOnly": True, "clOrdId": "create-1"})
    wrapper.cancel_order("exchange-1", "BTC/USDT:USDT")
    with pytest.raises(DemoAdapterError, match="create identity"):
        wrapper.create_order("BTC/USDT:USDT", "limit", "buy", 1, 10000, {"postOnly": True, "clOrdId": "create-1"})
    with pytest.raises(DemoAdapterError, match="cancel retry"):
        wrapper.cancel_order("exchange-1", "BTC/USDT:USDT")
    assert wrapper.mutation_retry_attempts == 2


def test_fresh_r0_admission_rejects_missing_or_default_zero_fields() -> None:
    baseline = {
        "status": "R0_OFFLINE_REPAIR_PASSED",
        "decision": "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION",
        "offline_verification": {
            "network_access": False,
            "order_mutations": 0,
            "orders_created": 0,
            "orders_cancelled": 0,
            "flatten_attempts": 0,
            "mutation_retries": 0,
        },
        "boundaries": {"r1_prepared": False, "r2_prepared": False, "r2_executed": False},
    }
    assert _is_exact_fresh_r0_readiness_evidence(baseline)
    baseline["offline_verification"].pop("mutation_retries")
    assert not _is_exact_fresh_r0_readiness_evidence(baseline)


def test_post_clearance_r0_admission_requires_exact_zero_zero_and_disposition() -> None:
    baseline = {
        "status": "R0_OFFLINE_READINESS_PASSED",
        "decision": "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION",
        "clearance_observation": {
            "execution_mode": "OKX_DEMO", "instrument": "BTC-USDT-SWAP",
            "nonzero_position_count": 0, "open_order_count": 0, "clearance_passed": True,
        },
        "offline_boundary": {
            "credentials_read": 0, "network_attempts": 0, "orders_or_mutations": 0,
            "mutation_retries": 0, "live_endpoint_attempts": 0,
            "r1_prepared_or_run": False, "r2_or_r3_prepared_or_run": False,
        },
        "failed_identity_disposition": {
            "reuse_authorized": False, "retry_authorized": False, "resume_authorized": False,
        },
    }
    assert _is_exact_fresh_r0_readiness_evidence(baseline)
    baseline["clearance_observation"]["open_order_count"] = 1
    assert not _is_exact_fresh_r0_readiness_evidence(baseline)


def test_repaired_r0_admission_requires_exact_repair_proofs_and_closed_boundaries() -> None:
    baseline = {
        "status": "R0_OFFLINE_REPAIR_PASSED",
        "decision": "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION",
        "repaired_r0_evidence_id": "r0-post-clearance-readiness-1",
        "candidate_fingerprint": "a" * 64,
        "repair_results": {
            "accepts_exact_post_clearance_zero_zero_schema": True,
            "rejects_missing_or_nonzero_clearance_fields": True,
            "rejects_stale_r1_candidate_fingerprint": True,
        },
        "offline_verification": {
            "credentials_read": 0, "network_attempts": 0, "orders_or_mutations": 0,
            "mutation_retries": 0, "live_endpoint_attempts": 0,
        },
        "boundaries": {
            "r1_prepared": False, "r2_prepared": False, "r2_executed": False,
            "r3_prepared_or_run": False, "live_or_production": False,
        },
    }
    assert _is_exact_fresh_r0_readiness_evidence(baseline)
    baseline["repair_results"]["rejects_stale_r1_candidate_fingerprint"] = False
    assert not _is_exact_fresh_r0_readiness_evidence(baseline)

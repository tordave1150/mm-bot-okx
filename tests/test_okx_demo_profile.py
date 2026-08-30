import json
from dataclasses import asdict
from pathlib import Path

import pytest

from okx_demo_profile import (
    DefensiveOverlayController,
    PROFILE_FINGERPRINT,
    PROFILE_ID,
    PROFILE_NAME,
    ProfilePromotionError,
    load_promoted_profile,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_profile_maps_every_runtime_field_exactly() -> None:
    promoted = load_promoted_profile(ROOT)
    spec = json.loads(
        (ROOT / promoted.source_path).read_text(encoding="utf-8")
    )
    frozen = next(row for row in spec["profiles"] if row["profile_id"] == PROFILE_ID)
    assert promoted.profile_name == PROFILE_NAME
    assert promoted.profile_fingerprint == PROFILE_FINGERPRINT
    assert promoted.strategy_fingerprint == promoted.strategy.fingerprint
    assert promoted.strategy_fingerprint != promoted.profile_fingerprint
    assert asdict(promoted.strategy) == frozen["parameters"]
    assert promoted.defensive_overlay == frozen["defensive_overlay"]
    assert len(promoted.binding_sha256) == 64


def test_frozen_profile_rejects_parameter_drift(tmp_path: Path) -> None:
    promoted = load_promoted_profile(ROOT)
    spec = json.loads((ROOT / promoted.source_path).read_text(encoding="utf-8"))
    profile = next(row for row in spec["profiles"] if row["profile_id"] == PROFILE_ID)
    profile["parameters"]["minimum_half_spread_bps"] = 5.9
    target = (
        tmp_path / "artifacts/mm_v1_6_economic_viability/"
        "specification_20260801T070658Z/protocol_spec.json"
    )
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ProfilePromotionError, match="fingerprint"):
        load_promoted_profile(tmp_path)


def test_drawdown_guard_latches_and_survives_restore() -> None:
    promoted = load_promoted_profile(ROOT)
    controller = DefensiveOverlayController(promoted.defensive_overlay)
    decision = controller.evaluate(
        mid_price=50_000.0,
        inventory_btc=0.01,
        drawdown=0.03,
        normal_fill_observed=False,
    )
    assert decision.allow_quoting is False
    assert decision.drawdown_guard_latched is True
    restored = DefensiveOverlayController.from_dict(
        controller.to_dict(), promoted.defensive_overlay
    )
    assert restored.drawdown_guard_latched is True


def test_timeboxed_one_sided_requires_causal_confirmation_and_reentry() -> None:
    promoted = load_promoted_profile(ROOT)
    controller = DefensiveOverlayController(promoted.defensive_overlay)
    controller.evaluate(
        mid_price=50_000.0, inventory_btc=0.0, drawdown=0.0,
        normal_fill_observed=False,
    )
    entry = controller.evaluate(
        mid_price=50_025.0, inventory_btc=0.01, drawdown=0.0,
        normal_fill_observed=False,
    )
    assert entry.one_sided_active is True
    assert entry.suppress_side == "sell"
    controller.evaluate(
        mid_price=50_026.0, inventory_btc=0.01, drawdown=0.0,
        normal_fill_observed=False,
    )
    controller.evaluate(
        mid_price=50_027.0, inventory_btc=0.01, drawdown=0.0,
        normal_fill_observed=False,
    )
    exit_decision = controller.evaluate(
        mid_price=50_028.0, inventory_btc=0.01, drawdown=0.0,
        normal_fill_observed=False,
    )
    assert exit_decision.completed_reentry is True
    assert exit_decision.suppress_side == "buy"

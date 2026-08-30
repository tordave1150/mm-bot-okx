"""Regression gates for MM v1.3C semantic evidence repair."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from backtest.matching_engine import MatchingEngine
from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3b_protocol import (
    SEVERITY as V13B_SEVERITY,
    paths as v13b_paths,
    profiles as v13b_profiles,
)
from backtest.mm_v1_3c_evidence import (
    _fixture_fill,
    activity_counts,
    classification_fixtures,
    normal_fifo_evidence,
    reconcile_fill_stream,
    validate_fill_record,
)
from backtest.mm_v1_3c_protocol import (
    SEVERITY,
    build_spec,
    path_disjointness,
    paths,
    profile_carry_forward_exact,
    profiles,
    ticks,
)
from backtest.mm_v1_3c_repair import _profile_activity, _reentry
from config import Config
from fill_tracker import FillTracker
from market_maker.as_config import MarketMakerV1Config
from fill_classification import (
    CanonicalFillClassification,
    FillTrigger,
    LiquidityRole,
    passive_classification,
    special_exit_classification,
)


ROOT = Path(__file__).resolve().parents[2]


def _normal(
    fill_id: str,
    side: str = "buy",
    before: str = "0",
    trigger: str = "STRICT_TRADE_THROUGH",
) -> dict:
    return _fixture_fill(
        fill_id,
        side=side,
        quantity="0.01",
        price="100",
        fee="0.001",
        role="maker",
        trigger=trigger,
        before=before,
        tick=int(fill_id.removeprefix("f") or "1"),
    )


def test_01_canonical_trigger_enum():
    assert [item.value for item in FillTrigger] == [
        "STRICT_TRADE_THROUGH",
        "AGGRESSOR_TRADE_AT_QUOTE",
        "TERMINAL_EXECUTION",
        "HARD_KILL_EXECUTION",
        "EMERGENCY_EXECUTION",
    ]


def test_02_strict_maker_classification():
    item = passive_classification()
    assert item.maker_or_taker is LiquidityRole.MAKER
    assert item.fill_trigger is FillTrigger.STRICT_TRADE_THROUGH
    assert item.normal_activity_eligible


def test_03_aggressor_at_quote_maker_classification():
    item = passive_classification("AGGRESSOR_TRADE_AT_QUOTE")
    assert item.normal_round_trip_eligible and not item.special_exit


def test_04_terminal_taker_classification():
    item = special_exit_classification("mm-terminal")
    assert item.fill_trigger is FillTrigger.TERMINAL_EXECUTION
    assert item.maker_or_taker is LiquidityRole.TAKER


def test_05_hard_kill_taker_classification():
    item = special_exit_classification("mm-hard-kill")
    assert item.fill_trigger is FillTrigger.HARD_KILL_EXECUTION
    assert item.special_exit


def test_06_emergency_taker_classification():
    item = special_exit_classification("mm-emergency")
    assert item.fill_trigger is FillTrigger.EMERGENCY_EXECUTION
    assert not item.normal_activity_eligible


def test_07_invalid_maker_terminal_rejection():
    with pytest.raises(ValueError):
        CanonicalFillClassification.create(
            maker_or_taker="maker", fill_trigger="TERMINAL_EXECUTION"
        )


def test_08_invalid_maker_hard_kill_rejection():
    with pytest.raises(ValueError):
        CanonicalFillClassification.create(
            maker_or_taker="maker", fill_trigger="HARD_KILL_EXECUTION"
        )


def test_09_invalid_maker_emergency_rejection():
    with pytest.raises(ValueError):
        CanonicalFillClassification.create(
            maker_or_taker="maker", fill_trigger="EMERGENCY_EXECUTION"
        )


def test_10_invalid_taker_strict_rejection():
    with pytest.raises(ValueError):
        CanonicalFillClassification.create(
            maker_or_taker="taker", fill_trigger="STRICT_TRADE_THROUGH"
        )


def test_11_missing_trigger_rejection():
    row = _normal("f1")
    del row["fill_trigger"]
    with pytest.raises(ValueError):
        validate_fill_record(row)


def test_12_unknown_trigger_rejection():
    row = _normal("f1")
    row["fill_trigger"] = "UNKNOWN"
    with pytest.raises(ValueError):
        validate_fill_record(row)


def test_13_multiply_classified_fill_rejection():
    row = _normal("f1")
    row["special_exit"] = True
    with pytest.raises(ValueError):
        validate_fill_record(row)


def test_14_trigger_assigned_at_fill_creation():
    tracker = FillTracker(Config(initial_capital=750.0))
    engine = MatchingEngine(
        fill_mode="conservative",
        canonical_fill_classification=True,
        passive_fill_trigger="STRICT_TRADE_THROUGH",
    )
    engine.place_order("buy", 100.0, 0.01)
    fills = engine.check_fills(
        {"bids": [[99.0, 1.0]], "asks": [[99.5, 1.0]], "timestamp": 1},
        tracker,
    )
    assert fills[0].classification == passive_classification()


def test_15_summary_cannot_overwrite_classification():
    rows = [_normal("f1")]
    before = deepcopy(rows)
    activity_counts(rows)
    assert rows == before


def test_16_fill_partition_identity():
    fixtures, mixed = classification_fixtures()
    assert len(fixtures) == 16 and mixed["fill_partition_reconciles"]


def test_17_activity_identity():
    rows = [_normal("f1")]
    result = reconcile_fill_stream(rows)
    assert result["normal_activity_fills"] == 1
    assert result["activity_identity_reconciles"]


def test_18_special_exit_activity_exclusion():
    normal = _normal("f1")
    terminal = _fixture_fill(
        "f2", side="sell", quantity="0.01", price="99", fee="0.00495",
        role="taker", trigger="TERMINAL_EXECUTION", before="0.01", tick=2,
    )
    result = activity_counts([normal, terminal])
    assert result["normal_activity_fills"] == 1
    assert result["excluded_special_exit_fills"] == 1


def test_19_normal_fifo_subset_identity():
    rows = [_normal("f1"), _normal("f2", "sell", "0.01")]
    _, _, result = normal_fifo_evidence(rows)
    assert result["normal_round_trip_fill_ids_subset_of_activity"]


def test_20_special_exit_fifo_exclusion():
    normal = _normal("f1")
    terminal = _fixture_fill(
        "f2", side="sell", quantity="0.01", price="99", fee="0.00495",
        role="taker", trigger="TERMINAL_EXECUTION", before="0.01", tick=2,
    )
    trips, special, result = normal_fifo_evidence([normal, terminal])
    assert not trips and len(special) == 1
    assert not result["special_exit_in_normal_round_trips"]


def test_21_partial_fill_classification():
    rows = [
        _fixture_fill(
            "f1", side="buy", quantity="0.004", price="100",
            fee="0.0004", role="maker", trigger="STRICT_TRADE_THROUGH",
            before="0", tick=1, order_id="partial-order",
        ),
        _fixture_fill(
            "f2", side="buy", quantity="0.006", price="100",
            fee="0.0006", role="maker", trigger="STRICT_TRADE_THROUGH",
            before="0.004", tick=2, order_id="partial-order",
        ),
    ]
    assert all(row["normal_activity_eligible"] for row in rows)
    assert reconcile_fill_stream(rows)["passed"]


def test_22_quantity_reconciliation():
    fixtures, mixed = classification_fixtures()
    assert all(item["passed"] for item in fixtures)
    assert mixed["quantity_identity_reconciles"]


def test_23_fee_reconciliation():
    _, mixed = classification_fixtures()
    assert mixed["fee_identity_reconciles"]


def test_24_normal_plus_terminal_fixture():
    fixtures, _ = classification_fixtures()
    assert next(
        row for row in fixtures
        if row["fixture"] == "08_normal_fill_plus_terminal_close"
    )["passed"]


def test_25_normal_plus_hard_kill_fixture():
    fixtures, _ = classification_fixtures()
    assert next(
        row for row in fixtures
        if row["fixture"] == "09_normal_fill_plus_hard_kill_close"
    )["passed"]


def test_26_mixed_stream_fixture():
    fixtures, mixed = classification_fixtures()
    assert next(
        row for row in fixtures
        if row["fixture"] == "10_mixed_execution_stream"
    )["passed"]
    assert mixed["passed"]


def test_27_v13a_writer_regression_preserved():
    source = (ROOT / "backtest/mm_v1_3a_diagnostic.py").read_text()
    assert "def _normalize(" in source
    assert "validate_references" in source and "validate_schema" in source


def test_28_quote_mode_reconciliation():
    profile = profiles()[0]
    path = paths()[0]
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id="MM_V1_3C_QUOTE_MODE_FIXTURE",
        scenario=path["scenario"],
        source_block="v1-3c-quote-mode-fixture",
        fill_seed=path["fill_seed"],
        cancel_latency_ticks=path["parameters"]["cancel_delay"],
        initial_capital_usdt=750,
        leverage=3,
        defensive_overlay=profile["defensive_overlay"],
        evidence_namespace="v13c-quote-mode-fixture",
    ).run(ticks(path, count=30))
    assert (
        result.two_sided_quote_decisions
        + result.one_sided_quote_decisions
        + result.no_quote_decisions
        == result.total_ticks
    )


def test_29_profile_carry_forward_exactness():
    assert profile_carry_forward_exact()
    assert all(
        new["parameters"] == old["parameters"]
        and new["defensive_overlay"] == old["defensive_overlay"]
        for old, new in zip(v13b_profiles(), profiles(), strict=True)
    )


def test_30_no_parameter_mutation():
    before = deepcopy(v13b_profiles())
    profiles()
    assert v13b_profiles() == before


def test_31_path_disjointness():
    result = path_disjointness()
    assert result["passed"]
    assert not (
        {row["path_id"] for row in paths()}
        & {row["path_id"] for row in v13b_paths()}
    )


def test_32_exact_four_paths_per_severity():
    assert len(paths()) == 16
    assert all(
        sum(row["severity"] == severity for row in paths()) == 4
        for severity in SEVERITY
    )


def test_33_activity_floor_calculation_rejects_no_trade():
    profile_id = profiles()[2]["profile_id"]
    metas = [{
        "profile_id": profile_id,
        "path_id": "p1",
        "severity": "S2_5",
        "quote_eligible_ticks": 1,
    }]
    rows = {
        "fills.jsonl": [],
        "round_trips.jsonl": [],
        "quote_events.jsonl": [{
            "profile_id": profile_id,
            "path_id": "p1",
            "decision": "QUOTE",
            "quote_mode": "TWO_SIDED",
        }],
        "defensive_events.jsonl": [],
    }
    assert not _profile_activity(profile_id, metas, rows)["passed"]


def test_34_bid_ask_representation_required():
    floor = build_spec(ROOT)["activity_floor"]
    assert floor["both_normal_fill_sides_required"]


def test_35_scenario_coverage_required():
    floor = build_spec(ROOT)["activity_floor"]
    assert floor["represented_scenario_families_min"] == 3


def test_36_defensive_reentry_requirement():
    profile = profiles()[2]
    activity = {
        "pause_ticks": 0,
        "no_quote_rate": 0.1,
        "one_sided_ticks": 0,
        "two_sided_quote_rate": 0.5,
        "bid_normal_fills": 1,
        "ask_normal_fills": 1,
        "passed": True,
        "represented_scenario_families": 3,
        "normal_fills_by_severity": {"S2_5": 1, "S3_LOW": 1},
        "normal_fifo_round_trips": 2,
    }
    fills = [
        {**_normal("f1"), "profile_id": profile["profile_id"],
         "path_id": "p", "severity": "S2_5", "tick": 1},
        {**_normal("f4", "sell", "0.01"), "profile_id": profile["profile_id"],
         "path_id": "p", "severity": "S2_5", "tick": 4},
    ]
    events = [{
        "profile_id": profile["profile_id"], "path_id": "p",
        "severity": "S2_5", "event": "VOLATILITY_SPREAD_GUARD", "tick": 2,
    }]
    result = _reentry(
        profile,
        activity,
        {"fills.jsonl": fills, "defensive_events.jsonl": events},
    )
    assert result["passed"]


def test_37_composite_lower_severity_activity_requirement():
    spec = build_spec(ROOT)
    requirement = spec["reentry_requirements"]["composite_additional"]
    assert requirement["normal_fills_at_S2_5_gt"] == 0
    assert requirement["normal_fills_at_S3_LOW_gt"] == 0


def test_38_no_trade_rejection():
    assert build_spec(ROOT)["activity_floor"]["normal_strict_maker_fills_min"] > 0


def test_39_gate_ordering():
    source = (ROOT / "backtest/mm_v1_3c_repair.py").read_text()
    gates = [
        "GATE_0_SEMANTIC_EVIDENCE_INTEGRITY",
        "GATE_1_SAFETY_INFRASTRUCTURE",
        "GATE_2_ACTIVITY",
        "GATE_3_DEFENSIVE_IMPROVEMENT",
        "GATE_4_RESILIENCE_BUDGET",
    ]
    positions = [source.index(f'"{gate}"') for gate in gates]
    assert positions == sorted(positions)


def test_40_no_prior_matrix_rerun():
    spec = build_spec(ROOT)
    assert not spec["balanced_v1_3_matrix_rerun"]
    assert not spec["stress_v1_3a_matrix_rerun"]
    assert not spec["repair_v1_3b_matrix_rerun"]


def test_41_no_optuna():
    assert not build_spec(ROOT)["optimization"]


def test_42_no_validation_or_holdout():
    spec = build_spec(ROOT)
    assert not spec["validation_opened"] and not spec["holdout_opened"]


def test_43_no_git_or_external_access():
    spec = build_spec(ROOT)
    assert not spec["git_write_operation"] and not spec["external_access"]
    for name in (
        "backtest/mm_v1_3c_protocol.py",
        "backtest/mm_v1_3c_repair.py",
    ):
        tree = ast.parse((ROOT / name).read_text())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        assert not any(
            token in imported.lower()
            for imported in imports
            for token in ("optuna", "requests", "github")
        )


def test_44_deterministic_replay():
    profile = profiles()[0]
    path = paths()[0]
    kwargs = {
        "profile": MarketMakerV1Config(**profile["parameters"]),
        "protocol_id": "MM_V1_3C_DETERMINISM_FIXTURE",
        "scenario": path["scenario"],
        "source_block": "v1-3c-determinism-fixture",
        "fill_seed": path["fill_seed"],
        "cancel_latency_ticks": path["parameters"]["cancel_delay"],
        "initial_capital_usdt": 750,
        "leverage": 3,
        "defensive_overlay": profile["defensive_overlay"],
        "evidence_namespace": "v13c-determinism-fixture",
    }
    first = MarketMakerBacktestRunner(**kwargs).run(ticks(path, count=40))
    second = MarketMakerBacktestRunner(**kwargs).run(ticks(path, count=40))
    assert first.fills == second.fills
    assert first.economics == second.economics


def test_severity_ladder_is_exact_v13b_copy():
    assert SEVERITY == V13B_SEVERITY

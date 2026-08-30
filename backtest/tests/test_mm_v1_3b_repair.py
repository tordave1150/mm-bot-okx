"""Fixed-design and causal overlay tests for MM v1.3B."""

from __future__ import annotations

import ast
from pathlib import Path

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3b_protocol import SEVERITY, build_spec, paths, profiles, ticks
from market_maker.as_config import MarketMakerV1Config


ROOT=Path(__file__).resolve().parents[2]


def test_exact_eight_profiles_and_single_change_isolation():
    ps=profiles();assert len(ps)==8
    singles=ps[2:7]
    assert all(1<=len(p["changed_fields"])<=3 for p in singles)
    assert ps[-1]["profile_name"]=="COMPOSITE_DEFENSIVE"


def test_refined_ladder_and_exact_four_paths_each():
    assert list(SEVERITY)==["S2_5","S3_LOW","S3_MID","S3_HIGH"]
    matrix=paths();assert len(matrix)==16
    assert all(sum(p["severity"]==s for p in matrix)==4 for s in SEVERITY)


def test_profiles_paths_and_fingerprints_are_deterministic():
    assert profiles()==profiles();assert paths()==paths()
    assert len({p["profile_fingerprint"] for p in profiles()})==8
    assert len({p["path_hash"] for p in paths()})==16


def test_causal_overlay_uses_no_path_or_severity_labels():
    source=(ROOT/"backtest/mm_runner.py").read_text()
    assert 'path["severity"]' not in source
    assert 'path["scenario"]' not in source


def test_volatility_guard_pause_fast_cancel_and_reentry_telemetry():
    profile=profiles()[-1];path=paths()[0]
    run=MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id="MM_V1_3B_OVERLAY_ENGINEERING",scenario="causal_fixture",
        source_block="v1-3b-regression-only",fill_seed=209999,
        cancel_latency_ticks=1,initial_capital_usdt=750,leverage=3,
        defensive_overlay=profile["defensive_overlay"]).run(ticks(path))
    events={x["event"] for x in run.defensive_events}
    assert "VOLATILITY_SPREAD_GUARD" in events
    assert "FAST_CANCEL_ON_VOLATILITY" in events
    assert run.two_sided_quote_decisions+run.one_sided_quote_decisions+run.no_quote_decisions==run.total_ticks


def test_activity_floor_rejects_no_trade_and_special_exits():
    floor=build_spec(ROOT)["activity_floor"]
    assert floor["fills_min"]==8 and floor["normal_round_trips_min"]==2
    assert floor["two_sided_rate_min"]==.05
    assert floor["no_quote_rate_max"]==.85


def test_no_extensions_prior_reruns_or_forbidden_access():
    spec=build_spec(ROOT)
    assert spec["profile_extension"] is None and spec["path_extension"] is None
    assert spec["balanced_matrix_rerun"] is False
    assert spec["v13a_matrix_rerun"] is False
    assert not any((spec["optimization"],spec["validation_opened"],
                    spec["holdout_opened"],spec["external_access"],
                    spec["git_write_operation"]))


def test_no_optuna_network_or_future_signal_imports():
    for name in ("backtest/mm_v1_3b_protocol.py","backtest/mm_v1_3b_repair.py"):
        tree=ast.parse((ROOT/name).read_text())
        imports=[a.name for n in ast.walk(tree)
                 if isinstance(n,(ast.Import,ast.ImportFrom)) for a in n.names]
        assert not any(any(x in item.lower() for x in ("optuna","requests","github"))
                       for item in imports)

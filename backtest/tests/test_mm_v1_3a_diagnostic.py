"""Stress-writer and frozen-design tests for MM v1.3A."""

from __future__ import annotations

import ast
from pathlib import Path

from backtest.mm_v1_3_protocol import fixed_profiles as prior_profiles
from backtest.mm_v1_3a_diagnostic import SCHEMAS, validate_references, validate_schema
from backtest.mm_v1_3a_protocol import (
    FILL_MODEL, MANDATORY_STREAMS, SEVERITY, build_spec, carried_profiles,
    stress_paths,
)


ROOT = Path(__file__).resolve().parents[2]


def test_all_mandatory_stream_schemas_exist_and_reject_missing_fields():
    assert set(SCHEMAS) == set(MANDATORY_STREAMS)
    for stream, fields in SCHEMAS.items():
        assert validate_schema(stream, {field: 0 for field in fields})
        assert not validate_schema(stream, {})


def test_empty_trade_stream_is_mandatory_and_manifestable():
    assert "trade_events.jsonl" in MANDATORY_STREAMS


def test_broken_cross_reference_fails():
    rows = {name: [] for name in MANDATORY_STREAMS}
    rows["fills.jsonl"] = [{"fill_id": "f", "order_id": "missing",
        "quote_event_id": "missing", "trigger_event_id": "missing"}]
    assert not validate_references(rows, set(), set())["passed"]


def test_severity_ladder_is_exactly_frozen_s1_to_s4():
    assert list(SEVERITY) == ["S1", "S2", "S3", "S4"]
    assert [SEVERITY[x]["rank"] for x in SEVERITY] == [1, 2, 3, 4]


def test_exactly_three_fresh_paths_per_severity():
    paths = stress_paths()
    assert len(paths) == 12
    assert all(sum(path["severity"] == level for path in paths) == 3
               for level in SEVERITY)
    assert len({path["path_id"] for path in paths}) == 12
    assert len({path["path_hash"] for path in paths}) == 12


def test_six_profiles_carry_forward_without_parameter_mutation():
    prior, current = prior_profiles(), carried_profiles()
    assert len(current) == 6
    assert [x["parameters"] for x in current] == [
        x["parameters"] for x in prior
    ]
    assert all(x["profile_id"].startswith("mm-v1-3a-") for x in current)


def test_strict_only_model_and_no_balanced_rerun():
    spec = build_spec(ROOT)
    assert spec["fill_model"] == FILL_MODEL == "ADVERSE_SELECTION_STRESS_MODEL"
    assert spec["balanced_matrix_rerun"] is False


def test_budget_and_design_have_no_extensions():
    spec = build_spec(ROOT)
    assert spec["profile_extension"] is None
    assert spec["path_extension"] is None
    assert not any((spec["optimization"], spec["validation_opened"],
                    spec["holdout_opened"], spec["external_access"],
                    spec["git_write_operation"]))


def test_no_optuna_or_network_imports():
    for name in ("backtest/mm_v1_3a_protocol.py",
                 "backtest/mm_v1_3a_diagnostic.py"):
        tree = ast.parse((ROOT / name).read_text())
        imports = [alias.name for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names]
        assert not any(any(term in imported.lower()
                           for term in ("optuna", "requests", "github"))
                       for imported in imports)

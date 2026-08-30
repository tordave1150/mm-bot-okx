import json
from pathlib import Path

import pytest

import okx_demo_post_campaign_offline as offline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_predecessor_hash_chain_and_terminal_decision_are_immutable() -> None:
    audit = offline.verify_predecessor(
        PROJECT_ROOT, offline.PREDECESSOR_PACKAGE_ID
    )
    assert audit["immutable"] is True
    assert audit["terminal_decision"] == "INSUFFICIENT_EVIDENCE"
    assert audit["sessions"] == 12
    assert audit["unsafe_sessions"] == 0
    assert audit["live_endpoint_attempts"] == 0
    assert audit["live_orders"] == 0
    assert audit["registry_tail_sha256"] == (
        "90916abd153f7d31096b559ce871656ad1d8eab98fe3033c90115414dd7ff138"
    )


def test_diagnostic_reconciles_observed_cohorts_and_gate_deficits() -> None:
    diagnostic = offline.diagnose(
        PROJECT_ROOT, offline.PREDECESSOR_PACKAGE_ID
    )
    assert diagnostic["terminal_decision"] == "INSUFFICIENT_EVIDENCE"
    assert diagnostic["decision_reasons"] == [
        "NORMAL_FILL_FLOOR",
        "BID_FILL_FLOOR",
        "FIFO_ROUND_TRIP_FLOOR",
    ]
    assert diagnostic["cohorts"] == {
        "no_fill_slots": [3, 4, 5, 7],
        "productive_fifo_slots": [6, 8, 11],
        "special_flatten_slots": [1, 2, 9, 10, 12],
        "one_fill_then_flatten_slots": [1, 2, 9, 10, 12],
    }
    assert diagnostic["gate_deficits"] == {
        "normal_fills": 9,
        "bid_fills": 1,
        "ask_fills": 0,
        "fifo_round_trips": 3,
        "special_flatten_sessions_excess": 3,
    }
    assert diagnostic["maximum_allowed_special_flatten_sessions"] == 2
    assert diagnostic["aggregate"]["normal_creates"] == 599
    assert diagnostic["aggregate"]["normal_fill_count"] == 15
    assert diagnostic["aggregate"]["normal_net_pnl_usdt"] == "0.3052058"
    assert diagnostic["aggregate"]["aggregate_net_pnl_usdt"] == "-2.044501705"
    assert diagnostic["risk_expansion_recommended"] is False
    assert diagnostic["optuna_recommended"] is False


def test_every_session_remains_terminally_reconciled_in_diagnostic() -> None:
    diagnostic = offline.diagnose(
        PROJECT_ROOT, offline.PREDECESSOR_PACKAGE_ID
    )
    assert len(diagnostic["session_rows"]) == 12
    assert all(row["terminal_reconciled"] for row in diagnostic["session_rows"])
    assert all(row["final_position_btc"] == "0" for row in diagnostic["session_rows"])
    assert all(row["final_open_orders"] == 0 for row in diagnostic["session_rows"])
    assert sum(row["normal_fills"] for row in diagnostic["session_rows"]) == 15
    assert sum(row["normal_fifo_round_trips"] for row in diagnostic["session_rows"]) == 5
    assert sum(row["flatten_dispatches"] for row in diagnostic["session_rows"]) == 5


def test_offline_builder_writes_marker_last_and_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(offline, "ARTIFACT_ROOT", tmp_path)

    def fake_suites(root: Path, output: Path):
        result = {
            "post_campaign_targeted": {
                "returncode": 0,
                "passed": 1,
                "network_attempts": 0,
                "live_endpoint_attempts": 0,
                "optuna_imported": False,
                "passed_gate": True,
            }
        }
        offline._write_json(output / "tests" / "test_summary.json", result)
        return result

    monkeypatch.setattr(offline, "_run_suites", fake_suites)
    evidence_id = "economic-repair-offline-20990101T000000Z"
    output = offline.run_offline(
        PROJECT_ROOT, evidence_id, offline.PREDECESSOR_PACKAGE_ID
    )
    marker = output / "R0_OFFLINE_REPAIR_COMPLETED.json"
    assert marker.is_file()
    assert json.loads(marker.read_text())["orders_submitted"] == 0
    prior_times = [
        path.stat().st_mtime_ns
        for path in output.rglob("*")
        if path.is_file() and path != marker
    ]
    assert marker.stat().st_mtime_ns >= max(prior_times)
    with pytest.raises(offline.PostCampaignOfflineError, match="reuse"):
        offline.run_offline(
            PROJECT_ROOT, evidence_id, offline.PREDECESSOR_PACKAGE_ID
        )


def test_wrong_predecessor_and_bad_evidence_identity_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(offline.PostCampaignOfflineError, match="identity"):
        offline.verify_predecessor(PROJECT_ROOT, "economic-package-reused")
    with pytest.raises(offline.PostCampaignOfflineError, match="identity"):
        offline.run_offline(PROJECT_ROOT, "bad-id")
    assert not list(tmp_path.iterdir())


def test_successor_source_does_not_activate_external_authority() -> None:
    risk = offline.frozen_risk_specification()
    assert risk["risk_expansion"] is False
    assert risk["OKX_connection_authorized"] is False
    assert risk["orders_authorized"] is False
    assert risk["production_authorized"] is False
    assert offline.SUCCESSOR_SOURCE != "AGENTS.md"

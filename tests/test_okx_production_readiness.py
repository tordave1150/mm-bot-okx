import json
from pathlib import Path

from config import Config
from okx_production_readiness import audit_project


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_gate_is_offline_secret_free_and_fail_closed() -> None:
    marker_key = "marker-key-must-not-escape"
    marker_secret = "marker-secret-must-not-escape"
    marker_passphrase = "marker-passphrase-must-not-escape"
    report = audit_project(
        ROOT,
        Config(
            api_key=marker_key,
            api_secret=marker_secret,
            api_passphrase=marker_passphrase,
        ),
    )
    serialized = json.dumps(report, sort_keys=True)
    assert report["production_ready"] is False
    assert report["live_trading_authorized"] is False
    assert report["network_calls"] == 0
    assert report["exchange_api_calls"] == 0
    assert report["credentials_serialized"] is False
    assert marker_key not in serialized
    assert marker_secret not in serialized
    assert marker_passphrase not in serialized


def test_readiness_gate_preserves_v1_6_closure_and_names_runtime_blockers() -> None:
    report = audit_project(ROOT)
    assert report["closed_evidence"]["decision_status"] == (
        "MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT"
    )
    assert report["closed_evidence"]["best_profile"] == "FEE_AWARE_SPREAD_6"
    first = (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0]
    mismatches = report["closed_evidence"]["source_hash_mismatches"]
    if first == "# AGENTS.md - OKX Demo Fill and Restart Validation":
        # The activated successor freezes every v1.6 source and explicitly
        # permits only the root authority transition.
        assert mismatches == ["AGENTS.md"]
        assert (ROOT / "AGENTS.md").read_bytes() == (
            ROOT / "AGENTS_OKX_DEMO_FILL_RESTART_VALIDATION.md"
        ).read_bytes()
        # The frozen predecessor readiness module must fail closed because it
        # deliberately recognizes only its own successor generation.  The new
        # protocol performs its separate hash/transition audit.
        assert report["closed_evidence"]["successor_protocol_transition"] is False
        blockers = set(report["blocker_codes"])
        assert "V1_6_SOURCE_CLOSURE" in blockers
        assert "ACTIVE_PROTOCOL_AUTHORIZATION" in blockers
        assert "RUNTIME_CREDENTIAL_PREFLIGHT" in blockers
    else:
        assert mismatches == []
        assert report["closed_evidence"]["successor_protocol_transition"] is True
        blockers = set(report["blocker_codes"])
        assert "RUNTIME_CREDENTIAL_PREFLIGHT" in blockers
        assert "SIGNED_PROMOTION_MANIFEST" in blockers
        assert "ACTIVE_PROTOCOL_AUTHORIZATION" not in blockers
        assert "RESEARCH_TO_RUNTIME_PROFILE_BINDING" not in blockers
        assert "ORDER_IDEMPOTENCY_INTEGRATION" not in blockers

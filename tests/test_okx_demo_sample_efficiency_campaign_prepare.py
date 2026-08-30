from __future__ import annotations

import json
from pathlib import Path

import pytest

import okx_demo_sample_efficiency_campaign_prepare as successor


def test_successor_r1_requires_exact_r0_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "preflight-fixture"
    output = tmp_path / "artifacts/okx_demo_fill_restart_validation" / run_id
    payloads = {
        "preflight/preflight_result.json": {
            "passed": True,
            "snapshots_consistent": True,
            "state_resolved": True,
            "transport_audit": {"endpoint_hosts": ["www.okx.com"]},
            "mutation_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "initial_snapshot": {"position_btc": 0.0, "open_orders": 0},
            "verified_snapshot": {"position_btc": 0.0, "open_orders": 0},
        },
        "specification/preflight_spec.json": {
            "protocol_id": "okx-demo-multi-session-a1-read-only-preflight-v1"
        },
        "predecessor/offline_evidence_audit.json": {
            "evidence_kind": successor.R0_EVIDENCE_KIND,
            "repair_id": "wrong-r0",
        },
    }
    for relative, payload in payloads.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        successor,
        "verify_preflight_evidence",
        lambda root, requested: {"passed": True, "run_id": requested},
    )
    with pytest.raises(successor.base.A2PackageError, match="not eligible"):
        successor._verify_r1(
            tmp_path,
            preflight_run_id=run_id,
            r0_evidence_id="expected-r0",
        )


def test_successor_source_extension_does_not_modify_r0_bound_preparer() -> None:
    root = Path(__file__).resolve().parents[1]
    r0_sources = json.loads(
        (
            root
            / "artifacts/okx_demo_post_campaign_repair"
            / "economic-repair-offline-20260820T132835Z"
            / "specification/source_hashes.json"
        ).read_text(encoding="utf-8")
    )
    assert successor.base._sha256(root / "okx_demo_multi_session_prepare.py") == (
        r0_sources["okx_demo_multi_session_prepare.py"]
    )
    sources = successor._source_hashes(root)
    assert "okx_demo_sample_efficiency_campaign_prepare.py" in sources
    assert "tests/test_okx_demo_sample_efficiency_campaign_prepare.py" in sources

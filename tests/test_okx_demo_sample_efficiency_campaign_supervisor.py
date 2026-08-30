from __future__ import annotations

import json
from pathlib import Path

import pytest

import okx_demo_sample_efficiency_campaign_supervisor as successor


def test_rejected_prearm_package_remains_unarmed_and_immutable() -> None:
    root = Path(__file__).resolve().parents[1]
    package = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages"
        / "economic-package-20260820T135400Z"
    )
    terminal = json.loads(
        (package / "A2_PACKAGE_COMPLETED.json").read_text(encoding="utf-8")
    )
    assert terminal["campaign_executed"] is False
    assert not (package / "campaign_run/A2_CAMPAIGN_ARMED.json").exists()


def test_legacy_supervisor_rejects_successor_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(
        successor.legacy.CampaignSupervisorError, match="boundary"
    ):
        successor.legacy.load_campaign_package(
            root, "economic-package-20260820T135400Z"
        )


def test_successor_loader_rejects_rejected_package_without_binding_audit() -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(
        successor.legacy.CampaignSupervisorError, match="incomplete"
    ):
        successor.load_campaign_package(
            root, "economic-package-20260820T135400Z"
        )


def test_post_start_child_runtime_additions_preserve_immutable_package_audit() -> None:
    root = Path(__file__).resolve().parents[1]
    package = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages"
        / "economic-package-20260820T141231Z"
    )
    spec = json.loads(
        (package / "specification/campaign_package_spec.json").read_text(
            encoding="utf-8"
        )
    )
    audits = json.loads(
        (package / "specification/session_package_audits.json").read_text(
            encoding="utf-8"
        )
    )["packages"]
    successor._verify_child(root, spec, spec["session_slots"][0], audits[0])


def test_external_confirmation_cannot_promote_or_resume_campaign() -> None:
    package = successor.legacy.A2CampaignPackage(
        Path("."), Path("."), {"campaign_id": "campaign"}, ()
    )
    with pytest.raises(
        successor.legacy.CampaignSupervisorError, match="confirmation"
    ):
        successor.fail_closed_from_external_flat_confirmation(
            package,
            owner="owner",
            now_ms=1,
            confirmation={
                "campaign_id": "campaign",
                "position_btc": "0",
                "open_orders": 0,
                "reported_by": "user",
                "authoritative_exchange_snapshot": False,
                "economic_evidence": True,
                "resume_authorized": False,
                "confirmation_sha256": "invalid",
            },
        )

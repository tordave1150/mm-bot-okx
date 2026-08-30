from __future__ import annotations

import json
from pathlib import Path

import pytest

from okx_demo_multi_session_campaign import CampaignError
from okx_demo_operational_failure_campaign import (
    EXPECTED_DECISIONS,
    SCENARIO_GROUPS,
    FailureCampaignManifest,
    FailureCampaignRegistry,
    FailureMatrixStatus,
    FailureRunEvidence,
    seal_failure_run,
    verify_failure_registry,
)


SOURCE_SHA256 = "a" * 64


def _manifest(name: str = "failure-campaign-fixture") -> FailureCampaignManifest:
    return FailureCampaignManifest(
        campaign_id=name,
        source_sha256=SOURCE_SHA256,
        created_at_ms=1,
        economic_campaign_id="economic-campaign-fixture",
    )


def _case(case: str) -> dict[str, object]:
    expected = EXPECTED_DECISIONS[case]
    maximum = 1 if case in {
        "timeout_after_create_dispatch",
        "duplicate_create_ack",
        "multi_partial_flatten",
    } else 0
    return {
        "case": case,
        "injection_point": f"fixture:{case}",
        "expected_decision": expected,
        "observed_decision": expected,
        "expected_durable_state": f"durable:{case}",
        "observed_durable_state": f"durable:{case}",
        "mutation_delta": maximum,
        "maximum_mutation_delta": maximum,
        "read_retries": 3 if case == "read_retry_exhaustion" else 0,
        "mutation_retries": 0,
        "passed": True,
    }


def _run_payload(
    index: int,
    group: str,
    *,
    terminal_mode: str = "FLAT_EMPTY",
    unresolved_reason: str = "",
    **overrides: object,
) -> dict[str, object]:
    start = 1_000_000 + index * 1_000_000
    payload: dict[str, object] = {
        "run_id": f"failure:{index}:{group}",
        "group": group,
        "source_sha256": SOURCE_SHA256,
        "started_at_ms": start,
        "ended_at_ms": start + 1_000,
        "normal_creates": 1 if group == "dispatch_ambiguity" else 0,
        "cancels": 1 if group == "shutdown_flatten" else 0,
        "flatten_dispatches": 1 if group == "shutdown_flatten" else 0,
        "mutation_retries": 0,
        "cases": [_case(case) for case in SCENARIO_GROUPS[group]],
        "terminal_mode": terminal_mode,
        "final_position_btc": "0",
        "final_open_orders": 0,
        "pending_intent": terminal_mode == "UNRESOLVED_FAIL_CLOSED",
        "ambiguous_intent": False,
        "unresolved_reason": unresolved_reason,
        "account_configuration_mutations": 0,
        "credential_accesses": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_imported": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
    }
    payload.update(overrides)
    return payload


def _run(index: int, group: str, **overrides: object) -> FailureRunEvidence:
    return FailureRunEvidence.from_dict(seal_failure_run(
        _run_payload(index, group, **overrides)
    ))


def test_exact_ten_group_matrix_passes_and_reverifies(tmp_path: Path) -> None:
    assert len(SCENARIO_GROUPS) == 10
    registry = FailureCampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    groups = tuple(SCENARIO_GROUPS)
    for index, group in enumerate(groups[:-1]):
        assert registry.register_run(_run(index, group)) is FailureMatrixStatus.IN_PROGRESS
    assert registry.register_run(_run(9, groups[-1])) is FailureMatrixStatus.PASSED
    verified = verify_failure_registry(tmp_path / "campaign")
    assert verified["matrix_status"] == "PASSED"
    assert verified["aggregate"]["run_count"] == 10
    assert verified["aggregate"]["case_count"] == len(EXPECTED_DECISIONS)
    assert verified["aggregate"]["mutation_retries"] == 0
    assert verified["aggregate"]["live_endpoint_attempts"] == 0


def test_unresolved_fail_closed_run_is_explicit_and_never_retries_mutation() -> None:
    run = _run(
        0,
        "dispatch_ambiguity",
        terminal_mode="UNRESOLVED_FAIL_CLOSED",
        unresolved_reason="AMBIGUOUS_CREATE_REQUIRES_AUTHORITATIVE_RECONCILIATION",
    )
    assert run.pending_intent is True
    assert run.mutation_retries == 0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"normal_creates": 5}, "normal_creates"),
        ({"flatten_dispatches": 2}, "flatten_dispatches"),
        ({"mutation_retries": 1}, "mutation_retries"),
        ({"credential_accesses": 1}, "credential_accesses"),
        ({"live_endpoint_attempts": 1}, "live_endpoint_attempts"),
        ({"account_configuration_mutations": 1}, "account_configuration_mutations"),
        ({"optuna_imported": True}, "prohibited"),
        ({"git_write_operation": True}, "prohibited"),
    ],
)
def test_run_risk_and_prohibited_boundaries_are_fail_closed(
    overrides: dict[str, object], match: str
) -> None:
    payload = _run_payload(0, "dispatch_ambiguity", **overrides)
    with pytest.raises(CampaignError, match=match):
        FailureRunEvidence.from_dict(seal_failure_run(payload))


def test_case_decision_durable_state_and_exact_coverage_are_mandatory() -> None:
    decision = _run_payload(0, "read_retry")
    decision["cases"][0]["observed_decision"] = "CONTINUE"
    with pytest.raises(CampaignError, match="decision mismatch"):
        FailureRunEvidence.from_dict(seal_failure_run(decision))

    durable = _run_payload(0, "read_retry")
    durable["cases"][0]["observed_durable_state"] = "drift"
    with pytest.raises(CampaignError, match="durable state"):
        FailureRunEvidence.from_dict(seal_failure_run(durable))

    missing = _run_payload(0, "read_retry")
    missing["cases"] = missing["cases"][:-1]
    with pytest.raises(CampaignError, match="coverage"):
        FailureRunEvidence.from_dict(seal_failure_run(missing))

    retry = _run_payload(0, "dispatch_ambiguity")
    retry["cases"][1]["mutation_retries"] = 1
    with pytest.raises(CampaignError, match="retried a mutation"):
        FailureRunEvidence.from_dict(seal_failure_run(retry))


def test_group_and_run_identity_reuse_are_refused_without_append(tmp_path: Path) -> None:
    registry = FailureCampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    first = _run(0, "read_retry")
    registry.register_run(first)
    before = len(registry.records())
    with pytest.raises(CampaignError, match="reuse"):
        registry.register_run(first)
    assert len(registry.records()) == before
    second_same_group = _run(1, "read_retry")
    with pytest.raises(CampaignError, match="reuse"):
        registry.register_run(second_same_group)
    assert len(registry.records()) == before


def test_source_binding_and_evidence_seal_are_refused(tmp_path: Path) -> None:
    registry = FailureCampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    payload = _run_payload(0, "read_retry")
    payload["source_sha256"] = "b" * 64
    with pytest.raises(CampaignError, match="source binding"):
        registry.register_run(FailureRunEvidence.from_dict(seal_failure_run(payload)))

    sealed = seal_failure_run(_run_payload(0, "read_retry"))
    sealed["normal_creates"] = 4
    with pytest.raises(CampaignError, match="seal mismatch"):
        FailureRunEvidence.from_dict(sealed)


def test_registry_hash_corruption_and_manifest_matrix_drift_are_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "campaign"
    registry = FailureCampaignRegistry.initialize(root, _manifest())
    registry.register_run(_run(0, "read_retry"))
    lines = registry.registry_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["payload"]["run"]["normal_creates"] = 4
    lines[-1] = json.dumps(record, sort_keys=True)
    registry.registry_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="record hash"):
        FailureCampaignRegistry.load(root)

    manifest_root = tmp_path / "manifest"
    FailureCampaignRegistry.initialize(
        manifest_root, _manifest("failure-campaign-manifest")
    )
    path = manifest_root / "failure_campaign_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["scenario_groups"]["read_retry"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(CampaignError, match="seal mismatch"):
        FailureCampaignRegistry.load(manifest_root)


def test_registry_is_terminal_after_passing_matrix(tmp_path: Path) -> None:
    registry = FailureCampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    for index, group in enumerate(SCENARIO_GROUPS):
        registry.register_run(_run(index, group))
    with pytest.raises(CampaignError, match="terminal"):
        registry.register_run(_run(10, "read_retry"))

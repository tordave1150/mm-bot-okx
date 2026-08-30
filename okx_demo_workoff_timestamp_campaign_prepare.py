"""Prepare a fresh work-off timestamp successor R2 package offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import okx_demo_multi_session_prepare as campaign_base
import okx_demo_terminal_causal_cli_campaign_prepare as base
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_validation import canonical_sha256


EVIDENCE_KIND = "workoff_timestamp_r0_offline_repair"
PROTOCOL_ID = "okx-demo-workoff-timestamp-economic-campaign-v1"
EXECUTION_SOURCE = "okx_demo_workoff_timestamp_campaign_supervisor.py"
PREPARATION_SOURCE = "okx_demo_workoff_timestamp_campaign_prepare.py"
FAILED_PACKAGE_ID = "economic-package-20260821T173615Z"
FAILED_RUN_ID = "economic-campaign-run-20260821T173615Z"
FAILED_SESSION_ID = "soak-package-20260821T173615Z-s12-870c0378a3"
SOURCE_FILES = (
    PREPARATION_SOURCE,
    EXECUTION_SOURCE,
    "okx_demo_unobserved_workoff_timestamp_repair_offline.py",
    "tests/test_okx_demo_unobserved_workoff_timestamp_repair.py",
    "tests/test_okx_demo_workoff_timestamp_campaign.py",
)
TARGETED_TESTS = (
    *base.TARGETED_TESTS,
    "tests/test_okx_demo_unobserved_workoff_timestamp_repair.py",
    "tests/test_okx_demo_workoff_timestamp_campaign.py",
)


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = base._source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise campaign_base.A2PackageError(
                f"work-off timestamp R2 source missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _failed_predecessor_audit(root: Path, evidence_id: str) -> dict[str, object]:
    evidence_root = (
        root / "artifacts/okx_demo_unobserved_workoff_timestamp_repair" / evidence_id
    )
    frozen = json.loads(
        (evidence_root / "predecessor/failed_campaign_audit.json").read_text(
            encoding="utf-8"
        )
    )
    package = (
        root / campaign_base.CAMPAIGN_ARTIFACT_ROOT / FAILED_PACKAGE_ID
    )
    campaign = package / "campaign_run"
    session = (
        root / campaign_base.SESSION_ARTIFACT_ROOT / FAILED_SESSION_ID / "soak_run"
    )
    paths = {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "slot_12_attempt": campaign / "attempted_sessions/slot-12.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_state": session / "state/validation_state.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }
    expected = dict(frozen.get("hashes") or {})
    failures = [
        name for name, path in paths.items() if expected.get(name) != _sha256(path)
    ]
    if any((
        failures,
        frozen.get("immutable") is not True,
        frozen.get("package_id") != FAILED_PACKAGE_ID,
        frozen.get("campaign_run_id") != FAILED_RUN_ID,
        frozen.get("failed_session_package_id") != FAILED_SESSION_ID,
        frozen.get("terminal_decision") != "NOT_READY",
        frozen.get("resume_authorized") is not False,
        frozen.get("rerun_authorized") is not False,
        frozen.get("terminal_position_btc") != "0",
        frozen.get("terminal_open_orders") != 0,
    )):
        raise campaign_base.A2PackageError(
            "failed work-off timestamp predecessor binding drifted"
        )
    return {
        "package_id": FAILED_PACKAGE_ID,
        "campaign_run_id": FAILED_RUN_ID,
        "session_package_id": FAILED_SESSION_ID,
        "hashes": expected,
        "immutable_failed_post_start": True,
        "campaign_decision": "NOT_READY",
        "resume_authorized": False,
        "rerun_authorized": False,
        "all_slots_attempted": True,
        "completed_slots": list(range(1, 12)),
        "failed_slots": [12],
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
    }


def _write_session_package(**kwargs: object) -> dict[str, object]:
    audit = base._write_session_package(**kwargs)
    root = Path(kwargs["root"])
    output = root / campaign_base.SESSION_ARTIFACT_ROOT / str(audit["package_id"])
    spec_path = output / "specification/soak_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["workoff_completion_timestamp_repair_required"] = True
    spec["partial_workoff_restart_reconciliation_required"] = True
    canonical = dict(spec)
    canonical.pop("specification_sha256", None)
    spec["specification_sha256"] = canonical_sha256(canonical)
    _write_json(spec_path, spec)
    completion = campaign_base._completion_hashes(output, "SOAK_PACKAGE_COMPLETED.json")
    _write_json(output / "completion_hashes.json", completion)
    terminal_path = output / "SOAK_PACKAGE_COMPLETED.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["completion_files_checked"] = len(completion)
    terminal["completion_hashes_sha256"] = _sha256(output / "completion_hashes.json")
    _write_json(terminal_path, terminal)
    return {
        **audit,
        "completion_hashes_sha256": terminal["completion_hashes_sha256"],
        "terminal_sha256": _sha256(terminal_path),
    }


def prepare(
    root: Path, *, evidence_id: str, preflight_run_id: str
) -> tuple[Path, dict[str, object]]:
    return base.prepare(
        root,
        evidence_id=evidence_id,
        preflight_run_id=preflight_run_id,
        evidence_kind=EVIDENCE_KIND,
        protocol_id=PROTOCOL_ID,
        execution_source=EXECUTION_SOURCE,
        preparation_source=PREPARATION_SOURCE,
        source_hashes_fn=_source_hashes,
        failed_audit_fn=_failed_predecessor_audit,
        session_writer=_write_session_package,
        targeted_tests=TARGETED_TESTS,
        failed_audit_name="failed_workoff_timestamp_package_audit.json",
        extra_spec={
            "workoff_completion_timestamp_repair_required": True,
            "partial_workoff_restart_reconciliation_required": True,
        },
        extra_decision={
            "workoff_completion_timestamp_repair_bound": True,
            "partial_workoff_restart_reconciliation_bound": True,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-evidence-id", required=True)
    parser.add_argument("--preflight-run-id", required=True)
    args = parser.parse_args()
    try:
        output, identifiers = prepare(
            args.root,
            evidence_id=args.repair_evidence_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"WORKOFF_TIMESTAMP_R2_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Prepare a fresh post-wall interruption successor R2 package offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import okx_demo_multi_session_prepare as campaign_base
import okx_demo_owned_cancel_reconciliation_campaign_prepare as base
import okx_demo_terminal_causal_cli_campaign_prepare as generic_base
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_validation import canonical_sha256


EVIDENCE_KIND = "post_wall_interruption_r0_offline_audit"
PROTOCOL_ID = "okx-demo-post-wall-interruption-economic-campaign-v1"
EXECUTION_SOURCE = "okx_demo_post_wall_interruption_campaign_supervisor.py"
PREPARATION_SOURCE = "okx_demo_post_wall_interruption_campaign_prepare.py"
FAILED_PACKAGE_ID = "economic-package-20260826T160834Z"
FAILED_RUN_ID = "economic-campaign-run-20260826T160834Z"
TERMINAL_SESSION_ID = "soak-package-20260826T160834Z-s05-0d028be127"
SOURCE_FILES = (
    PREPARATION_SOURCE,
    EXECUTION_SOURCE,
    "okx_demo_post_wall_interruption_audit_offline.py",
    "okx_demo_terminal_causal_cli_campaign_supervisor.py",
    "tests/test_okx_demo_post_wall_interruption_audit.py",
    "tests/test_okx_demo_post_wall_interruption_campaign.py",
)
TARGETED_TESTS = (
    *base.TARGETED_TESTS,
    "tests/test_okx_demo_post_wall_interruption_audit.py",
    "tests/test_okx_demo_post_wall_interruption_campaign.py",
)


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = base._source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise campaign_base.A2PackageError(
                f"post-wall R2 source missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _failed_predecessor_audit(root: Path, evidence_id: str) -> dict[str, object]:
    evidence_root = root / "artifacts/okx_demo_post_wall_interruption_audit" / evidence_id
    frozen = json.loads(
        (evidence_root / "predecessor/failed_campaign_audit.json").read_text(
            encoding="utf-8"
        )
    )
    package = root / campaign_base.CAMPAIGN_ARTIFACT_ROOT / FAILED_PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / campaign_base.SESSION_ARTIFACT_ROOT / TERMINAL_SESSION_ID
    paths = {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_completion": package / "completion_hashes.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "package_sources": package / "specification/source_hashes.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_lease": campaign / "state/campaign_lease.json",
        "campaign_events": campaign / "streams/supervisor_events.jsonl",
        "slot5_terminal": session / "COMPLETED.json",
        "slot5_completion": session / "soak_run/completion_hashes.json",
        "slot5_evidence": session / "soak_run/audits/economic_session_evidence.json",
        "slot5_economics": session / "soak_run/audits/economics.json",
        "slot5_gateway": session / "soak_run/audits/gateway_audit.json",
        "slot5_snapshots": session / "soak_run/terminal/account_snapshots.json",
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
        frozen.get("terminal_decision") != "NOT_READY",
        frozen.get("terminal_reason") != "CAMPAIGN_WALL_BUDGET",
        frozen.get("resume_authorized") is not False,
        frozen.get("rerun_authorized") is not False,
        frozen.get("completed_slots") != [1, 2, 3, 4, 5],
        frozen.get("failed_slots") != [],
        frozen.get("slots_6_through_12_started") is not False,
        frozen.get("stale_lease_recovered") is not True,
        frozen.get("completed_active_slot_ingested") is not True,
        frozen.get("terminal_account_authoritative") is not True,
        frozen.get("terminal_position_btc") != "0",
        frozen.get("terminal_open_orders") != 0,
        frozen.get("normal_fill_count") != 43,
        frozen.get("normal_fifo_round_trips") != 21,
        frozen.get("normal_net_pnl_usdt") != "7.2687144",
        frozen.get("special_flatten_sessions") != 1,
        frozen.get("unclassified_quote_mode_ticks") != 0,
        frozen.get("unsafe_sessions") != 0,
    )):
        raise campaign_base.A2PackageError("failed post-wall predecessor binding drifted")
    return {
        "package_id": FAILED_PACKAGE_ID,
        "campaign_run_id": FAILED_RUN_ID,
        "session_package_id": TERMINAL_SESSION_ID,
        "hashes": expected,
        "immutable_failed_post_start": True,
        "campaign_decision": "NOT_READY",
        "terminal_reason": "CAMPAIGN_WALL_BUDGET",
        "resume_authorized": False,
        "rerun_authorized": False,
        "completed_slots": [1, 2, 3, 4, 5],
        "failed_slots": [],
        "slots_6_through_12_started": False,
        "stale_lease_recovered": True,
        "completed_active_slot_ingested": True,
        "terminal_account_authoritative": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "normal_fill_count": 43,
        "normal_fifo_round_trips": 21,
        "normal_net_pnl_usdt": "7.2687144",
        "special_flatten_sessions": 1,
        "unclassified_quote_mode_ticks": 0,
        "unsafe_sessions": 0,
    }


def _write_session_package(**kwargs: object) -> dict[str, object]:
    audit = base._write_session_package(**kwargs)
    root = Path(kwargs["root"])
    output = root / campaign_base.SESSION_ARTIFACT_ROOT / str(audit["package_id"])
    spec_path = output / "specification/soak_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.update({
        "post_wall_interruption_audit_required": True,
        "completed_active_session_ingestion_required": True,
        "stale_lease_recovery_required": True,
        "campaign_wall_fail_closed_required": True,
        "resume_after_campaign_wall_authorized": False,
    })
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
    return generic_base.prepare(
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
        failed_audit_name="failed_post_wall_package_audit.json",
        extra_spec={
            "post_wall_interruption_audit_required": True,
            "completed_active_session_ingestion_required": True,
            "stale_lease_recovery_required": True,
            "campaign_wall_fail_closed_required": True,
            "resume_after_campaign_wall_authorized": False,
            "authoritative_owned_cancel_reconciliation_required": True,
            "cancel_fill_cursor_union_required": True,
            "cancel_reconciliation_read_attempts": 3,
            "cancel_reconciliation_interval_seconds": 2,
            "cancel_mutation_retry_attempts": 0,
            "special_flatten_session_limit": 2,
        },
        extra_decision={
            "post_wall_interruption_audit_bound": True,
            "completed_active_session_ingestion_bound": True,
            "campaign_wall_fail_closed_bound": True,
            "resume_after_campaign_wall_authorized": False,
            "authoritative_owned_cancel_reconciliation_bound": True,
            "special_flatten_session_limit": 2,
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
        print(f"POST_WALL_R2_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

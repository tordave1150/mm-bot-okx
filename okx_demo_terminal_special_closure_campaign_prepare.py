"""Prepare a fresh terminal special-closure successor R2 package offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import okx_demo_multi_session_prepare as campaign_base
import okx_demo_terminal_causal_cli_campaign_prepare as base
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_validation import canonical_sha256


EVIDENCE_KIND = "terminal_special_closure_r0_offline_repair"
PROTOCOL_ID = "okx-demo-terminal-special-closure-economic-campaign-v1"
EXECUTION_SOURCE = "okx_demo_terminal_special_closure_campaign_supervisor.py"
PREPARATION_SOURCE = "okx_demo_terminal_special_closure_campaign_prepare.py"
FAILED_PACKAGE_ID = "economic-package-20260822T040609Z"
FAILED_RUN_ID = "economic-campaign-run-20260822T040609Z"
FAILED_SESSION_ID = "soak-package-20260822T040609Z-s11-7ab96d4c71"
SOURCE_FILES = (
    PREPARATION_SOURCE,
    EXECUTION_SOURCE,
    "okx_demo_terminal_special_closure_repair_offline.py",
    "tests/test_okx_demo_terminal_special_closure_repair.py",
    "tests/test_okx_demo_terminal_special_closure_campaign.py",
)
TARGETED_TESTS = (
    *base.TARGETED_TESTS,
    "tests/test_okx_demo_terminal_special_closure_repair.py",
    "tests/test_okx_demo_terminal_special_closure_campaign.py",
)


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = base._source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise campaign_base.A2PackageError(
                f"terminal special-closure R2 source missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _failed_predecessor_audit(root: Path, evidence_id: str) -> dict[str, object]:
    evidence_root = (
        root / "artifacts/okx_demo_terminal_special_closure_repair" / evidence_id
    )
    frozen = json.loads(
        (evidence_root / "predecessor/failed_campaign_audit.json").read_text(
            encoding="utf-8"
        )
    )
    package = root / campaign_base.CAMPAIGN_ARTIFACT_ROOT / FAILED_PACKAGE_ID
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
        "slot_11_attempt": campaign / "attempted_sessions/slot-11.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_economics": session / "audits/economics.json",
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
        frozen.get("completed_slots") != list(range(1, 11)),
        frozen.get("failed_slots") != [11],
        frozen.get("slot_12_started") is not False,
        frozen.get("terminal_position_btc") != "0",
        frozen.get("terminal_open_orders") != 0,
        frozen.get("special_flatten_sessions") != 4,
    )):
        raise campaign_base.A2PackageError(
            "failed terminal special-closure predecessor binding drifted"
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
        "completed_slots": list(range(1, 11)),
        "failed_slots": [11],
        "slot_12_started": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "special_flatten_sessions": 4,
    }


def _write_session_package(**kwargs: object) -> dict[str, object]:
    audit = base._write_session_package(**kwargs)
    root = Path(kwargs["root"])
    output = root / campaign_base.SESSION_ARTIFACT_ROOT / str(audit["package_id"])
    spec_path = output / "specification/soak_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["terminal_special_fill_closure_required"] = True
    spec["controller_engine_account_multi_partial_reconciliation_required"] = True
    spec["bounded_timeboxed_workoff_required"] = True
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
        failed_audit_name="failed_terminal_special_closure_package_audit.json",
        extra_spec={
            "terminal_special_fill_closure_required": True,
            "controller_engine_account_multi_partial_reconciliation_required": True,
            "bounded_timeboxed_workoff_required": True,
            "special_flatten_session_limit": 2,
        },
        extra_decision={
            "terminal_special_fill_closure_bound": True,
            "controller_engine_account_multi_partial_reconciliation_bound": True,
            "bounded_timeboxed_workoff_bound": True,
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
        print(f"TERMINAL_SPECIAL_R2_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

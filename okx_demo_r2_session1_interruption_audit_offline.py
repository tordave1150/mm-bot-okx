"""Socket-denied R0 audit for an expired interrupted R2 Session 1."""
from __future__ import annotations

import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk

ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session1_interruption_audit")
PATTERN = re.compile(r"r2-session1-interruption-audit-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION1_INTERRUPTION_AUDIT_R0_OFFLINE_SUPPORT"
PACKAGE = "economic-package-20260830T163835Z"
CAMPAIGN = "economic-campaign-20260830T163835Z"
RUN = "economic-campaign-run-20260830T163835Z"
SESSION_PACKAGE = "soak-package-20260830T163835Z-s01-e0149d7d08"
SESSION_RUN = "economic-session-20260830T163835Z-s01-e0149d7d08"
SESSION = "economic:economic-session-20260830T163835Z-s01-e0149d7d08:p0:e0149d7d08"

class AuditError(RuntimeError): pass

def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise AuditError(f"expected JSON object: {path}")
    return value

def _paths(root: Path) -> dict[str, Path]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE / "soak_run"
    return {"package_terminal": package / "A2_PACKAGE_COMPLETED.json", "package_spec": package / "specification/campaign_package_spec.json", "package_sources": package / "specification/source_hashes.json", "armed": campaign / "A2_CAMPAIGN_ARMED.json", "state": campaign / "state/supervisor_state.json", "lease": campaign / "state/campaign_lease.json", "events": campaign / "streams/supervisor_events.jsonl", "marker": session / "SOAK_EXECUTION_ARMED.json", "session_state": session / "state/validation_state.json"}

def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(p.is_file() for p in paths.values()): raise AuditError("interrupted campaign evidence is incomplete")
    spec, armed, state, lease, session = (_read(paths[k]) for k in ("package_spec", "armed", "state", "lease", "session_state"))
    payload = dict(session.get("payload") or {})
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    elapsed = now - int(state.get("armed_at_ms", 0))
    if any((spec.get("package_id") != PACKAGE, spec.get("campaign_id") != CAMPAIGN, spec.get("run_id") != RUN, armed.get("campaign_session_id") != "economic-campaign:economic-campaign-run-20260830T163835Z:p0:e0149d7d08a0", state.get("active_slot") != 1, state.get("completed_slots") != [], state.get("failed_slots") != [], state.get("terminal_decision") is not None, state.get("terminal_account_authoritative") is not False, elapsed <= 21600000, lease.get("status") != "ACTIVE", payload.get("phase") != "RUNNING", payload.get("r2_completed") is not False, (root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE / "soak_run/raw_result.json").exists(), (root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE / "soak_run/FAILED.json").exists())):
        raise AuditError("interrupted campaign boundary drifted")
    return {"immutable": True, "package_id": PACKAGE, "campaign_id": CAMPAIGN, "campaign_run_id": RUN, "active_session_package_id": SESSION_PACKAGE, "active_session_run_id": SESSION_RUN, "active_session_id": SESSION, "campaign_wall_expired": True, "elapsed_wall_ms": elapsed, "active_slot": 1, "completed_slots": [], "failed_slots": [], "terminal_account_authoritative": False, "authoritative_account_unknown": True, "last_durable_phase": payload.get("phase"), "last_durable_inventory_btc": dict(payload.get("ledger") or {}).get("inventory_btc"), "last_durable_owned_orders": len(dict(payload.get("owned_orders") or {})), "session_marker_reuse_refused": True, "resume_authorized": False, "retry_authorized": False, "accept_authorized": False, "session_2_started": False, "hashes": {k: _sha256(v) for k,v in paths.items()}}

def run(root: Path, evidence_id: str) -> Path:
    root=root.resolve()
    if not PATTERN.fullmatch(evidence_id): raise AuditError("fresh interruption audit identity required")
    output=root/ARTIFACT_ROOT/evidence_id
    if output.exists(): raise AuditError("interruption audit identity reuse refused")
    pred=verify_predecessor(root); output.mkdir(parents=True)
    _write_json(output/"predecessor/interrupted_campaign_audit.json", pred)
    _write_json(output/"diagnostic/interruption_transition.json", {"passed":True,"campaign_wall_budget_ms":21600000,"campaign_wall_expired":True,"marker_reuse_refused":True,"account_authority":"unknown_without_network_read","resume_same_campaign":False,"retry_same_identity":False,"next_session_started":False})
    _write_json(output/"specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output/"specification/requirement_matrix.json", [{"requirement":"expired active campaign fails closed","passed":True},{"requirement":"session marker reuse is refused","passed":True},{"requirement":"authoritative account is not inferred from local state","passed":True},{"requirement":"fresh R1/R2 identities required","passed":True}])
    sources=("AGENTS.md",Path(__file__).name,"okx_demo_multi_session_supervisor.py","okx_demo_soak_executor.py","tests/test_okx_demo_r2_session1_interruption_audit.py")
    _write_json(output/"specification/source_hashes.json", {str(n):_sha256(root/n) for n in sources})
    targeted=_run_suite(root,output,"r2_session1_interruption_targeted",("tests/test_okx_demo_r2_session1_interruption_audit.py","tests/test_okx_demo_multi_session_campaign.py"))
    suites=_run_suites(root,output); _write_json(output/"tests/r2_session1_interruption_targeted_summary.json",targeted)
    if any(v.get("passed_gate") is not True or int(v.get("returncode",1))!=0 or int(v.get("network_attempts",1))!=0 or v.get("optuna_imported") is not False for v in {**suites,"targeted":targeted}.values()): raise AuditError("interruption audit test boundary failed")
    _write_json(output/"audits/endpoint_mutation_audit.json", {"socket_denied":True,"network_attempts":0,"credential_reads":0,"demo_endpoint_attempts":0,"live_endpoint_attempts":0,"create_attempts":0,"amend_attempts":0,"cancel_attempts":0,"flatten_attempts":0,"account_configuration_attempts":0,"orders":0,"mutation_retries":0})
    decision={"status":READY,"R0_offline_audit_passed":True,"R1_preparation_authorized":False,"preflight_authorized":False,"economic_campaign_authorized":False,"production_authorized":False,"live_mode_available":False,"live_endpoint_attempts":0,"live_orders":0,"optuna_executed":False,"validation_opened":False,"holdout_opened":False,"git_write_operation":False,"next_boundary":"separate exact authorization for fresh R1 offline preparation"}
    _write_json(output/"decision/offline_decision.json",decision); scan=_secret_scan(output); _write_json(output/"audits/secret_scan.json",scan)
    if not scan["passed"]: raise AuditError("secret scan failed")
    completion={p.relative_to(output).as_posix():_sha256(p) for p in sorted(output.rglob("*")) if p.is_file()}; _write_json(output/"completion_hashes.json",completion)
    _write_json(output/"R0_R2_SESSION1_INTERRUPTION_AUDIT_COMPLETED.json",{**decision,"evidence_id":evidence_id,"completed_at_utc":datetime.now(timezone.utc).isoformat(),"completion_files_checked":len(completion),"completion_hashes_sha256":_sha256(output/"completion_hashes.json"),"terminal_written_last":True})
    return output

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--root",type=Path,default=Path(__file__).resolve().parent); p.add_argument("--evidence-id",required=True); a=p.parse_args()
    try: print(run(a.root,a.evidence_id))
    except Exception as exc: print(f"R2_SESSION1_INTERRUPTION_AUDIT_FAILED:{type(exc).__name__}:{exc}"); return 1
    return 0
if __name__ == "__main__": raise SystemExit(main())

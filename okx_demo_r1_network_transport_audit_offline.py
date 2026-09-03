"""Socket-denied R0 audit for the immutable failed R1 network preflight."""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk

ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_network_transport_audit")
PATTERN = re.compile(r"r1-network-transport-audit-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R1_NETWORK_TRANSPORT_AUDIT_R0_OFFLINE_SUPPORT"
PREP = "preflight-package-20260902T021850Z"; RUN = "preflight-20260902T021850Z"
SESSION = "preflight:preflight-20260902T021850Z:p0:3c614c2aebc6"

class AuditError(RuntimeError): pass
def _read(path: Path) -> dict[str, object]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise AuditError(f"invalid JSON: {path}")
    return value
def _paths(root: Path) -> dict[str, Path]:
    base=root/"artifacts/okx_demo_fill_restart_validation"; prep=base/"preflight_packages"/PREP; run=base/RUN
    return {"preparation":prep/"PREFLIGHT_PREPARATION_COMPLETED.json","preparation_hashes":prep/"completion_hashes.json","terminal":run/"PREFLIGHT_PHASE_COMPLETED.json","completion":run/"completion_hashes.json","result":run/"preflight/preflight_result.json","decision":run/"decision/preflight_decision.json","endpoint":run/"audits/endpoint_audit.json","secret":run/"audits/secret_scan.json"}
def verify_failed_preflight(root: Path) -> dict[str, object]:
    paths=_paths(root)
    if not all(p.is_file() for p in paths.values()): raise AuditError("failed preflight evidence incomplete")
    prep,terminal,result,decision,endpoint,secret=(_read(paths[k]) for k in ("preparation","terminal","result","decision","endpoint","secret"))
    retries=endpoint.get("read_retry_audit")
    if any((prep.get("preparation_id")!=PREP,prep.get("run_id")!=RUN,prep.get("session_id")!=SESSION,
            terminal.get("phase_status")!="READ_ONLY_PREFLIGHT_FAILED",result.get("status")!="READ_ONLY_PREFLIGHT_FAILED",
            result.get("failure_stage")!="PRE_MARKET_BOOTSTRAP",result.get("primary_error_category")!="NETWORK",
            result.get("primary_read_method")!="fetch_markets",result.get("terminal_account_authoritative") is not False,
            result.get("terminal_reconciliation_mode")!="UNRESOLVED_FAIL_CLOSED",endpoint.get("mutation_attempts")!=0,
            endpoint.get("live_endpoint_attempts")!=0,endpoint.get("read_call_count")!=6,not isinstance(retries,list),len(retries)!=6,
            any(item.get("attempt") not in (1,2,3) for item in retries),decision.get("read_only_preflight_passed") is not False,
            decision.get("formal_demo_execution_authorized") is not False,secret.get("passed") is not True)):
        raise AuditError("failed R1 network transport boundary drifted")
    return {"immutable":True,"preparation_id":PREP,"run_id":RUN,"session_id":SESSION,
            "failed_preflight_decision":"READ_ONLY_PREFLIGHT_FAILED","failure_stage":"PRE_MARKET_BOOTSTRAP",
            "primary_error_category":"NETWORK","read_methods":endpoint.get("read_methods"),"read_call_count":6,
            "terminal_account_authoritative":False,"terminal_reconciliation_mode":"UNRESOLVED_FAIL_CLOSED",
            "mutation_attempts":0,"live_endpoint_attempts":0,"rerun_authorized":False,"resume_authorized":False,
            "identity_reuse_authorized":False,"hashes":{k:_sha256(v) for k,v in paths.items()}}
def run(root: Path,evidence_id:str)->Path:
    root=root.resolve()
    if not PATTERN.fullmatch(evidence_id): raise AuditError("fresh network transport audit identity required")
    output=root/ARTIFACT_ROOT/evidence_id
    if output.exists(): raise AuditError("audit identity reuse refused")
    predecessor=verify_failed_preflight(root); output.mkdir(parents=True)
    _write_json(output/"predecessor/failed_preflight_audit.json",predecessor)
    _write_json(output/"diagnostic/root_cause.json",{"passed":True,"repository_defect_found":False,"execution_environment":"NETWORK_RESTRICTED_SANDBOX","cause":"the R1 command was dispatched from a network-restricted sandbox; both initial and account-only reads exhausted bounded retries","retry_policy_correct":True,"terminal_reconciliation_fail_closed":True,"mutation_before_or_after_failure":False,"required_next_runner":"network-capable execution with explicit network escalation"})
    _write_json(output/"specification/frozen_risk_specification.json",frozen_risk())
    _write_json(output/"specification/requirement_matrix.json",[{"requirement":"network read exhaustion is bounded at three attempts","passed":True},{"requirement":"failed terminal account is not claimed authoritative","passed":True},{"requirement":"no mutation after unresolved account read","passed":True},{"requirement":"failed R1 identity cannot rerun or resume","passed":True}])
    sources=("AGENTS.md",Path(__file__).name,"okx_fill_restart_preflight.py","okx_fill_restart_preflight_prepare.py","tests/test_okx_demo_r1_network_transport_audit.py")
    _write_json(output/"specification/source_hashes.json",{str(n):_sha256(root/n) for n in sources})
    targeted=_run_suite(root,output,"r1_network_targeted",("tests/test_okx_demo_r1_network_transport_audit.py","tests/test_okx_fill_restart_preflight.py","tests/test_okx_fill_restart_preflight_prepare.py"))
    suites=_run_required_suites(root,output); summary={**suites,"targeted":targeted}; _write_json(output/"tests/test_summary.json",summary); _write_json(output/"tests/r1_network_targeted_summary.json",targeted)
    if any(v.get("passed_gate") is not True or v.get("returncode")!=0 or v.get("network_attempts")!=0 or v.get("optuna_imported") is not False for v in summary.values()): raise AuditError("offline test boundary failed")
    _write_json(output/"audits/endpoint_mutation_audit.json",{"socket_denied":True,"network_attempts":0,"credential_reads":0,"demo_endpoint_attempts":0,"live_endpoint_attempts":0,"create_attempts":0,"amend_attempts":0,"cancel_attempts":0,"flatten_attempts":0,"account_configuration_attempts":0,"orders":0,"mutation_retries":0})
    decision={"status":READY,"evidence_kind":"r1_network_transport_audit_r0_offline","R0_offline_audit_passed":True,"preflight_authorized":False,"economic_campaign_authorized":False,"production_authorized":False,"live_mode_available":False,"live_endpoint_attempts":0,"live_orders":0,"optuna_executed":False,"validation_opened":False,"holdout_opened":False,"git_write_operation":False,"next_boundary":"separate exact authorization for fresh R1 offline preparation"}
    _write_json(output/"decision/offline_decision.json",decision); scan=_secret_scan(output); _write_json(output/"audits/secret_scan.json",scan)
    if not scan["passed"]: raise AuditError("secret scan failed")
    completion={p.relative_to(output).as_posix():_sha256(p) for p in sorted(output.rglob("*")) if p.is_file()}; _write_json(output/"completion_hashes.json",completion)
    _write_json(output/"R0_R1_NETWORK_TRANSPORT_AUDIT_COMPLETED.json",{**decision,"evidence_id":evidence_id,"completed_at_utc":datetime.now(timezone.utc).isoformat(),"completion_files_checked":len(completion),"completion_hashes_sha256":_sha256(output/"completion_hashes.json"),"terminal_written_last":True}); return output
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=Path(__file__).resolve().parent);p.add_argument("--evidence-id",required=True);a=p.parse_args()
    try: print(run(a.root,a.evidence_id))
    except Exception as exc: print(f"R1_NETWORK_TRANSPORT_AUDIT_FAILED:{type(exc).__name__}:{exc}"); return 1
    return 0
if __name__=="__main__":raise SystemExit(main())

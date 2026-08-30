"""Offline-only package preparer for the execution-environment successor."""
from __future__ import annotations

import argparse, hashlib, json, socket
from datetime import datetime, timezone
from pathlib import Path

import okx_demo_multi_session_prepare as base
from okx_fill_restart_formal_prepare import _OfflineSocketGuard, verify_preflight_evidence
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_preflight import verify_offline_evidence
from okx_fill_restart_validation import canonical_sha256
from okx_demo_execution_environment_successor_supervisor import (
    PROTOCOL_ID,
    REPAIR_KIND,
    R0_EVIDENCE_KINDS,
)


class PrepareError(RuntimeError): pass

def prepare(root: Path, repair_id: str, preflight_run_id: str) -> tuple[Path, dict[str, object]]:
    """Prepare a sealed successor package while socket dispatch is denied."""
    with _OfflineSocketGuard():
        return _prepare(root, repair_id, preflight_run_id)


def _prepare(root: Path, repair_id: str, preflight_run_id: str) -> tuple[Path, dict[str, object]]:
    root=root.resolve(); repair=verify_offline_evidence(root, repair_id)
    preflight=verify_preflight_evidence(root, preflight_run_id)
    result=json.loads((root/'artifacts/okx_demo_fill_restart_validation'/preflight_run_id/'preflight/preflight_result.json').read_text())
    predecessor=json.loads((root/'artifacts/okx_demo_fill_restart_validation'/preflight_run_id/'predecessor/offline_evidence_audit.json').read_text())
    if any((repair.get('evidence_kind') not in R0_EVIDENCE_KINDS, repair.get('passed') is not True,
            preflight.get('passed') is not True, result.get('passed') is not True,
            predecessor.get('repair_id')!=repair_id, result.get('mutation_attempts')!=0,
            result.get('initial_snapshot',{}).get('position_btc')!=0.0,
            result.get('initial_snapshot',{}).get('open_orders')!=0)):
        raise PrepareError('R0/R1 binding is not eligible')
    sources = base._source_hashes(root)
    sources.update({
        name: _sha256(root / name)
        for name in (
            'okx_demo_execution_environment_successor_prepare.py',
            'okx_demo_execution_environment_successor_supervisor.py',
            'tests/test_okx_demo_execution_environment_successor.py',
        )
    })
    sources = dict(sorted(sources.items()))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); package_id=f'economic-package-{stamp}'; campaign_id=f'economic-campaign-{stamp}'; run_id=f'economic-campaign-run-{stamp}'
    nonce=hashlib.sha256(f'{repair_id}|{preflight_run_id}|{package_id}'.encode()).hexdigest()[:12]
    session_id=f'economic-campaign:{run_id}:p0:{nonce}'; token=f'OKX_DEMO:{session_id}'
    output=root/base.CAMPAIGN_ARTIFACT_ROOT/package_id
    if output.exists(): raise PrepareError('identity collision')
    output.mkdir(parents=True)
    slots = []
    for slot in range(1, 13):
        session_run_id = f'economic-session-{stamp}-s{slot:02d}-{nonce[:10]}'
        session_id_value = f'economic:{session_run_id}:p0:{nonce[:10]}'
        session_token = f'OKX_DEMO:{session_id_value}'
        slots.append({
            'slot': slot,
            'package_id': f'soak-package-{stamp}-s{slot:02d}-{nonce[:10]}',
            'run_id': session_run_id,
            'session_id': session_id_value,
            'arm_token_sha256': hashlib.sha256(session_token.encode()).hexdigest(),
            'arm_token_serialized': False,
        })
    spec={'schema_version':1,'protocol_id':PROTOCOL_ID,'execution_source':'okx_demo_execution_environment_successor_supervisor.py','child_execution_source':'okx_demo_soak_executor.py','package_id':package_id,'campaign_id':campaign_id,'run_id':run_id,'campaign_session_id':session_id,'campaign_arm_token_sha256':hashlib.sha256(token.encode()).hexdigest(),'campaign_arm_token_serialized':False,'r0_evidence_id':repair_id,'r0_evidence_kind':repair['evidence_kind'],'a0_evidence_id':repair_id,'preflight_run_id':preflight_run_id,'source_manifest_sha256':canonical_sha256(sources),'session_count':12,'session_slots':slots,'campaign_limits':base.CampaignLimits().to_dict(),'session_risk_budget':base._risk_budget(),'campaign_authorized':False,'campaign_executed':False,'execution_marker_created':False,'network_authorized_during_freeze':False,'orders_authorized_during_freeze':False,'production_authorized':False,'created_at_utc':datetime.now(timezone.utc).isoformat()}
    spec['specification_sha256'] = canonical_sha256(spec)
    _write_json(output/'predecessor/r0_evidence_audit.json',repair); _write_json(output/'predecessor/preflight_evidence_audit.json',preflight); _write_json(output/'specification/source_hashes.json',sources); _write_json(output/'specification/campaign_package_spec.json',spec)
    child=[base._write_session_package(root=root,campaign_output=output,campaign_spec=spec,slot=slot,sources=sources,a0=repair,preflight=preflight) for slot in slots]
    _write_json(output/'specification/session_package_audits.json',{'count':len(child),'packages':child})
    decision={'status':'R2_SUCCESSOR_PACKAGE_READY','package_id':package_id,'campaign_id':campaign_id,'run_id':run_id,'campaign_session_id':session_id,'session_packages_prepared':len(child),'campaign_authorized':False,'campaign_executed':False,'network_attempts':0,'orders_submitted':0,'live_endpoint_attempts':0,'production_authorized':False,'live_mode_available':False,'live_orders':0,'next_boundary':'separate exact R2 campaign authorization'}
    _write_json(output/'decision/a2_package_decision.json',decision)
    completion=base._completion_hashes(output,'A2_PACKAGE_COMPLETED.json'); _write_json(output/'completion_hashes.json',completion); _write_json(output/'A2_PACKAGE_COMPLETED.json',{**decision,'completion_files_checked':len(completion),'completion_hashes_sha256':_sha256(output/'completion_hashes.json'),'terminal_written_last':True})
    return output, {'package_id':package_id,'campaign_id':campaign_id,'run_id':run_id,'campaign_session_id':session_id,'campaign_arm_token':token}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=Path('.')); p.add_argument('--repair-id',required=True); p.add_argument('--preflight-run-id',required=True); a=p.parse_args(); o,v=prepare(a.root,a.repair_id,a.preflight_run_id); print(json.dumps({'output':str(o),**v}))

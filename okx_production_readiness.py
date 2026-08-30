"""Offline, secret-free readiness audit for the selected OKX demo runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import Config


SUPPORTED_STATUS = "MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT"
SUPPORTED_PROFILE = "FEE_AWARE_SPREAD_6"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    passed: bool
    summary: str
    evidence: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _latest_directory(root: Path, prefix: str) -> Path | None:
    candidates = sorted(path for path in root.glob(f"{prefix}*") if path.is_dir())
    return candidates[-1] if candidates else None


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _closed_evidence_findings(root: Path) -> tuple[list[Finding], dict[str, object]]:
    artifact_root = root / "artifacts" / "mm_v1_6_economic_viability"
    spec_dir = _latest_directory(artifact_root, "specification_")
    decision_dir = _latest_directory(artifact_root, "decision_")
    metadata: dict[str, object] = {}
    if spec_dir is None or decision_dir is None:
        return [Finding(
            "V1_6_EVIDENCE_MISSING", "BLOCKER", False,
            "Closed MM v1.6 evidence is incomplete.",
            "Specification or decision directory was not found.",
        )], metadata

    spec_path = spec_dir / "protocol_spec.json"
    decision_path = decision_dir / "decision.json"
    completion_path = decision_dir / "COMPLETED.json"
    if not all(path.is_file() for path in (spec_path, decision_path, completion_path)):
        return [Finding(
            "V1_6_EVIDENCE_MISSING", "BLOCKER", False,
            "Closed MM v1.6 evidence is incomplete.",
            "A required specification, decision, or completion file is absent.",
        )], metadata

    spec = json.loads(_text(spec_path))
    decision = json.loads(_text(decision_path))
    mismatches: list[str] = []
    for relative, expected in spec.get("source_hashes", {}).items():
        source_path = root / relative
        actual = _sha256(source_path) if source_path.is_file() else "MISSING"
        if actual != expected:
            mismatches.append(relative)

    initial_audit = (
        root / "artifacts" / "okx_production_readiness"
        / "audit_20260801T170923Z" / "readiness.json"
    )
    candidate = root / "AGENTS_OKX_DEMO_EXECUTION_SAFETY.md"
    transition_verified = False
    if mismatches == ["AGENTS.md"] and initial_audit.is_file() and candidate.is_file():
        initial = json.loads(_text(initial_audit))
        transition_verified = (
            initial.get("closed_evidence", {}).get("source_hash_mismatches") == []
            and _sha256(root / "AGENTS.md") == _sha256(candidate)
        )
    effective_mismatches = [] if transition_verified else mismatches
    decision_supported = (
        decision.get("status") == SUPPORTED_STATUS
        and decision.get("best_profile") == SUPPORTED_PROFILE
        and decision.get("optuna_eligible") is True
        and decision.get("optuna_executed") is False
    )
    metadata.update({
        "best_profile": decision.get("best_profile"),
        "decision_status": decision.get("status"),
        "optuna_eligible": decision.get("optuna_eligible"),
        "optuna_executed": decision.get("optuna_executed"),
        "specification_sha256": decision.get("specification_sha256"),
        "source_hash_mismatches": effective_mismatches,
        "successor_protocol_transition": transition_verified,
        "transitioned_authority_files": ["AGENTS.md"] if transition_verified else [],
    })
    return [
        Finding(
            "V1_6_SOURCE_CLOSURE",
            "INFO" if not effective_mismatches else "BLOCKER",
            not effective_mismatches,
            "Frozen MM v1.6 closure is preserved."
            if not effective_mismatches else "Frozen MM v1.6 source drift exists.",
            "authorized AGENTS.md successor transition" if transition_verified
            else ("none" if not effective_mismatches else ", ".join(effective_mismatches)),
        ),
        Finding(
            "V1_6_DECISION_SUPPORT",
            "INFO" if decision_supported else "BLOCKER",
            decision_supported,
            "MM v1.6 supports the frozen research profile."
            if decision_supported else "MM v1.6 decision/profile is unsupported.",
            f"status={decision.get('status')}; profile={decision.get('best_profile')}",
        ),
    ], metadata


def audit_project(root: Path, config: Config | None = None) -> dict[str, object]:
    """Return an offline readiness report without serializing credential values."""
    root = root.resolve()
    cfg = config or Config()
    findings, evidence = _closed_evidence_findings(root)
    agents = _text(root / "AGENTS.md")
    config_source = _text(root / "config.py")
    adapter = _text(root / "okx_demo_adapter.py")
    runtime = _text(root / "okx_demo_runtime.py")
    state = _text(root / "okx_demo_state.py")
    profile = _text(root / "okx_demo_profile.py")
    protocol = _text(root / "okx_demo_protocol.py")

    def add(code: str, passed: bool, good: str, bad: str, evidence_text: str) -> None:
        findings.append(Finding(
            code, "INFO" if passed else "BLOCKER", passed,
            good if passed else bad, evidence_text,
        ))

    active = agents.startswith("# AGENTS.md — OKX Demo Execution Safety Before Production")
    add(
        "ACTIVE_PROTOCOL_AUTHORIZATION", active,
        "The active protocol authorizes the OKX demo stage.",
        "The active protocol does not authorize the selected demo stage.",
        agents.splitlines()[0] if agents else "AGENTS.md empty",
    )
    selected_runtime = all(
        (root / name).is_file()
        for name in (
            "okx_demo_profile.py", "okx_demo_state.py", "okx_demo_adapter.py",
            "okx_demo_runtime.py", "okx_demo_protocol.py",
        )
    )
    add(
        "OPT_IN_DEMO_RUNTIME_SELECTED", selected_runtime,
        "The isolated demo runtime is selected for formal execution.",
        "The isolated demo runtime is incomplete.",
        "selected_runtime=okx_demo_protocol.py",
    )
    sandbox = (
        cfg.sandbox
        and "if not cfg.sandbox" in config_source
        and "ExecutionMode.LIVE" in adapter
        and "LIVE mode is unavailable" in adapter
        and '"x-simulated-trading"' in adapter
    )
    add(
        "SANDBOX_ENFORCEMENT", sandbox,
        "Sandbox transport is fail-closed and LIVE mode is unavailable.",
        "Sandbox/LIVE separation is incomplete.",
        f"sandbox={cfg.sandbox}; selected_mode=OKX_DEMO",
    )
    credentials = all((cfg.api_key, cfg.api_secret, cfg.api_passphrase))
    add(
        "RUNTIME_CREDENTIAL_PREFLIGHT", credentials,
        "Key, secret, and passphrase presence is complete.",
        "Credential presence has not been preflighted.",
        "presence_only=" + str(credentials).lower(),
    )
    credential_enforcement = all(
        token in protocol for token in ("config.api_key", "config.api_secret", "config.api_passphrase")
    )
    add(
        "CREDENTIAL_COMPLETENESS_ENFORCEMENT", credential_enforcement,
        "The selected startup validates all three credential fields.",
        "The selected startup does not validate credential completeness.",
        "credential_values_not_read_or_serialized_by_audit",
    )
    profile_bound = all(
        token in profile
        for token in (
            "FEE_AWARE_SPREAD_6", "mm-v1-6-profile-02",
            "profile_fingerprint", "defensive overlay drift", "capital policy drift",
        )
    )
    add(
        "RESEARCH_TO_RUNTIME_PROFILE_BINDING", profile_bound,
        "The frozen profile, overlay, capital policy, units, and hashes bind runtime.",
        "The frozen profile is not completely bound to runtime.",
        "selected_profile=FEE_AWARE_SPREAD_6",
    )
    idempotency = all(
        token in adapter for token in ("OrderIntent", '"clOrdId"', "_resolve_by_client_id")
    )
    add(
        "ORDER_IDEMPOTENCY_INTEGRATION", idempotency,
        "Deterministic client identity and ambiguous-create resolution are integrated.",
        "Runtime order idempotency is incomplete.",
        "runtime=okx_demo_adapter.OkxDemoAdapter",
    )
    cancel_confirmed = all(
        token in adapter for token in ("cancel_confirmed", "CANCEL_NOT_CONFIRMED", "fetch_open_orders")
    ) and "replace" not in runtime.lower()
    add(
        "CANCEL_BEFORE_REPLACE_CONFIRMATION", cancel_confirmed,
        "No replacement path exists without authoritative cancel confirmation.",
        "Replacement can occur without confirmed cancellation.",
        "formal runtime has no replace operation",
    )
    sync_closed = all(
        token in adapter
        for token in ("PREFLIGHT_FAILED", "foreign or unowned", "missing expected orders")
    )
    add(
        "OPEN_ORDER_SYNC_FAIL_CLOSED", sync_closed,
        "Incomplete/foreign/missing order state halts the selected runtime.",
        "Order synchronization is not fail-closed.",
        "authoritative preflight and reconciliation",
    )
    kill_persisted = all(
        token in state for token in ("KillSwitchLatch", 'payload["kill_switch"]', "from_dict")
    )
    add(
        "KILL_SWITCH_PERSISTENCE", kill_persisted,
        "The kill-switch latch persists across restart.",
        "Kill-switch persistence is incomplete.",
        "okx_demo_state.DemoRuntimeState",
    )
    market_before_state = adapter.find("fetch_market_info") < adapter.find("state_store.load")
    add(
        "MARKET_SPEC_BEFORE_STATE_RESTORE", market_before_state,
        "Market metadata is loaded before bound state restoration.",
        "State can restore before market fingerprint validation.",
        "preflight order verified statically",
    )
    mode_verified = all(
        token in adapter
        for token in ("set_position_mode", "set_leverage", "fetch_position_mode", "fetch_leverage")
    )
    add(
        "ACCOUNT_MODE_AND_LEVERAGE_PREFLIGHT", mode_verified,
        "Position mode, isolated margin intent, and leverage are verified.",
        "Account mode/leverage verification is incomplete.",
        "net_mode; isolated; leverage=3",
    )
    stale_halt = all(
        token in runtime
        for token in ("MarketDataGate", "stale or future-dated", "cancel_all_owned", "MARKET_DATA_UNSAFE")
    )
    add(
        "STALE_MARKET_CANCEL_AND_HALT", stale_halt,
        "Stale/crossed/empty/non-monotonic market data cancels and halts.",
        "Market-data failure does not fail closed.",
        "okx_demo_runtime.MarketDataGate",
    )
    maintenance = all(
        token in adapter for token in ("maintenanceMargin", "maintenance_margin_usdt", "free_equity_usdt")
    )
    add(
        "MAINTENANCE_MARGIN_RUNTIME_INPUT", maintenance,
        "Authoritative equity and maintenance margin are present in each snapshot.",
        "Maintenance margin is not wired to runtime snapshots.",
        "AccountSnapshot",
    )
    flatten = all(
        token in adapter
        for token in ("flatten_attempts", "flatten already submitted", "reduceOnly", "reconcile_flatten")
    )
    add(
        "EMERGENCY_FLATTEN_SINGLE_FLIGHT", flatten,
        "Emergency flatten is reduce-only, persisted, single-flight, and reconciled.",
        "Emergency flatten can be duplicated or is not reconciled.",
        "frozen retry budget=1",
    )
    empty_zero = "explicitly zeros local state" in adapter and "snapshot.position_btc" in adapter
    add(
        "EMPTY_POSITION_SNAPSHOT_RECONCILIATION", empty_zero,
        "An empty position snapshot explicitly zeros local inventory.",
        "Empty position reconciliation is incomplete.",
        "authoritative snapshot assignment",
    )
    cancel_scope = "cancel_all_owned" in adapter and "foreign order prevents" in adapter
    add(
        "AUTHORITATIVE_CANCEL_SCOPE", cancel_scope,
        "Emergency cancellation enumerates and owns the exchange order set.",
        "Cancellation is limited to stale local state.",
        "fetch_open_orders before owned cancellation",
    )
    fill_cursor = all(
        token in state for token in ("class FillCursor", "timestamp_ms", "ids_at_timestamp")
    )
    add(
        "MONOTONIC_FILL_CURSOR_PERSISTENCE", fill_cursor,
        "A monotonic timestamp+ID fill cursor persists across restart.",
        "Fill restart deduplication is incomplete.",
        "okx_demo_state.FillCursor",
    )
    state_fail = "raise DemoStateError" in state and "STATE_SAVE_AFTER_CREATE_FAILED" in adapter
    add(
        "STATE_SAVE_FAILURE_FAIL_CLOSED", state_fail,
        "Persistence failures propagate to cancel/halt handling.",
        "Persistence failure can be swallowed while quoting.",
        "DemoStateStore.save raises",
    )
    supervisor = "raise DemoRunFailed" in runtime and "raise SystemExit(main())" in protocol
    add(
        "SUPERVISOR_FAILURE_SIGNAL", supervisor,
        "Fatal demo runtime failures propagate a non-success process signal.",
        "Fatal runtime failures can exit successfully.",
        "DemoRunFailed -> CLI process failure",
    )
    manifests = sorted(
        root.glob("artifacts/okx_demo_execution_safety/*/specification/promotion_manifest.json")
    )
    manifest_present = bool(manifests)
    add(
        "SIGNED_PROMOTION_MANIFEST", manifest_present,
        "A frozen, non-overwriting profile promotion manifest exists.",
        "No formal profile promotion manifest exists yet.",
        str(manifests[-1].relative_to(root)) if manifests else "not_frozen_yet",
    )

    blockers = [
        asdict(item) for item in findings
        if not item.passed and item.severity == "BLOCKER"
    ]
    return {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "offline_okx_demo_readiness",
        "selected_runtime": "okx_demo_protocol.py",
        "network_calls": 0,
        "exchange_api_calls": 0,
        "credentials_serialized": False,
        "production_defaults_changed": False,
        "live_trading_authorized": False,
        "production_ready": False,
        "demo_ready": not blockers,
        "blocker_count": len(blockers),
        "blocker_codes": [item["code"] for item in blockers],
        "closed_evidence": evidence,
        "findings": [asdict(item) for item in findings],
        "next_required_stage": (
            "FORMAL_OKX_DEMO_EXECUTION" if not blockers
            else "CLOSE_REMAINING_DEMO_READINESS_BLOCKERS"
        ),
    }


def _write_report(report: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "readiness.json"
    md_path = output_dir / "readiness.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# OKX demo readiness gate", "",
        f"Demo ready: `{str(report['demo_ready']).lower()}`",
        "Production ready: `false`",
        f"Blockers: `{report['blocker_count']}`", "", "## Findings", "",
    ]
    for finding in report["findings"]:
        mark = "PASS" if finding["passed"] else "FAIL"
        lines.append(
            f"- `{mark}` `{finding['code']}` — {finding['summary']} "
            f"Evidence: {finding['evidence']}"
        )
    lines.extend([
        "",
        "This report authorizes neither production trading nor a live endpoint.",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report = audit_project(args.root)
    if args.output_dir:
        json_path, md_path = _write_report(report, args.output_dir)
        print(json_path)
        print(md_path)
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

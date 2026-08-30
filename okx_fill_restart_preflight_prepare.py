"""Prepare a fresh read-only OKX Demo preflight package without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_preflight import (
    ARTIFACT_ROOT,
    MULTI_SESSION_A1_R0_EVIDENCE_KINDS,
    PREPARATION_ARTIFACT_ROOT,
    expected_arm_token,
    preflight_source_hashes,
    verify_offline_evidence,
)
from okx_fill_restart_validation import canonical_sha256


class PreflightPreparationError(RuntimeError):
    pass


class _OfflineSocketGuard:
    """Block and count socket dispatch while the package is prepared."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create_connection = socket.create_connection

    def __enter__(self) -> "_OfflineSocketGuard":
        guard = self

        def blocked_connect(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise PreflightPreparationError(
                "network access is prohibited during preflight preparation"
            )

        def blocked_connect_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise PreflightPreparationError(
                "network access is prohibited during preflight preparation"
            )

        def blocked_create_connection(
            address: object, *args: object, **kwargs: object
        ) -> None:
            guard.attempts.append(type(address).__name__)
            raise PreflightPreparationError(
                "network access is prohibited during preflight preparation"
            )

        socket.socket.connect = blocked_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = blocked_connect_ex  # type: ignore[method-assign]
        socket.create_connection = blocked_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, *args: object) -> None:
        socket.socket.connect = self._connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._create_connection


def make_identifiers(
    *, repair_id: str, source_manifest_sha256: str, now: datetime | None = None
) -> dict[str, str]:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise PreflightPreparationError("identifier timestamp must be timezone aware")
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"preflight-{stamp}"
    preparation_id = f"preflight-package-{stamp}"
    nonce = hashlib.sha256(
        f"{repair_id}|{run_id}|{source_manifest_sha256}".encode("utf-8")
    ).hexdigest()[:12]
    session_id = f"preflight:{run_id}:p0:{nonce}"
    return {
        "preparation_id": preparation_id,
        "run_id": run_id,
        "session_id": session_id,
        "arm_token": expected_arm_token(session_id),
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "PREFLIGHT_PREPARATION_COMPLETED.json"}
    result: dict[str, str] = {}
    for path in output.rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.name in ignored
            or path.suffix == ".tmp"
        ):
            continue
        result[path.relative_to(output).as_posix()] = _sha256(path)
    return dict(sorted(result.items()))


def prepare_preflight_package(root: Path, repair_id: str) -> tuple[Path, dict[str, str]]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        repair = verify_offline_evidence(root, repair_id)
        if repair.get("evidence_kind") not in {
            "fill_cursor_repair",
            "activity_budget_shutdown_repair",
            "future_book_timestamp_repair",
            "r2_warmup_audit_repair",
            "signed_age_prearm_terminal_repair",
            "r1_terminal_reconciliation_repair",
            "soak_failure_injection_readiness",
            "multi_session_a0_offline_build",
            "sample_efficiency_r0_offline_repair",
            "r2_post_start_terminal_recovery_offline_repair",
            "terminal_causal_cli_r0_offline_repair",
            "fifo_attribution_r0_offline_repair",
            "workoff_timestamp_r0_offline_repair",
            "terminal_special_closure_r0_offline_repair",
            "markout_special_closure_r0_offline_repair",
            "owned_cancel_reconciliation_r0_offline_repair",
            "post_wall_interruption_r0_offline_audit",
            "market_bootstrap_terminal_reconciliation_r0_offline_repair",
            "preflight_market_bootstrap_terminal_reconciliation_r0_offline_repair",
            "transport_resilience_r0_offline_repair",
            "execution_environment_transport_r0_offline_repair",
            "r2_session5_terminal_reconciliation_r0_offline_repair",
            "r2_session1_cancel_fill_reconciliation_r0_offline_repair",
        }:
            raise PreflightPreparationError(
                "supported successor repair evidence is required"
            )
        sources = preflight_source_hashes(root)
        source_manifest_sha256 = canonical_sha256(sources)
        identifiers = make_identifiers(
            repair_id=repair_id,
            source_manifest_sha256=source_manifest_sha256,
        )
        output = root / PREPARATION_ARTIFACT_ROOT / identifiers["preparation_id"]
        preflight_output = root / ARTIFACT_ROOT / identifiers["run_id"]
        if output.exists() or preflight_output.exists():
            raise PreflightPreparationError("fresh preflight identity collision")
        output.mkdir(parents=True)
        token_hash = hashlib.sha256(
            identifiers["arm_token"].encode("utf-8")
        ).hexdigest()
        protocol_id = (
            "okx-demo-multi-session-a1-preflight-preparation-v1"
            if repair.get("evidence_kind") in MULTI_SESSION_A1_R0_EVIDENCE_KINDS
            else "okx-demo-fill-cursor-repair-preflight-preparation-v1"
        )
        spec = {
            "protocol_id": protocol_id,
            "preparation_id": identifiers["preparation_id"],
            "run_id": identifiers["run_id"],
            "repair_id": repair_id,
            "predecessor_evidence_kind": repair["evidence_kind"],
            "session_id": identifiers["session_id"],
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
            "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "FUTURE_READ_ONLY_OKX_DEMO_PREFLIGHT",
            "preflight_authorized": False,
            "preflight_executed": False,
            "network_authorized": False,
            "orders_authorized": False,
            "account_configuration_mutation_authorized": False,
            "repair_completion_sha256": repair["completion_hashes_sha256"],
            "repair_terminal_sha256": repair["terminal_sha256"],
            "repair_decision_sha256": repair["decision_sha256"],
            "source_manifest_sha256": source_manifest_sha256,
            "required_next_boundary": (
                "separate explicit user confirmation quoting the exact fresh "
                "run ID, repair ID, preparation ID, session ID, and arm token"
            ),
        }
        spec["specification_sha256"] = canonical_sha256(spec)
        _write_json(output / "predecessor" / "repair_evidence_audit.json", repair)
        _write_json(output / "specification" / "source_hashes.json", sources)
        _write_json(output / "specification" / "preflight_preparation.json", spec)
        _write_json(output / "audits" / "network_audit.json", {
            "socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
            "live_endpoint_attempts": 0,
        })
        _write_json(output / "audits" / "mutation_audit.json", {
            "preflight_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "account_configuration_mutations": 0,
            "credentials_loaded": False,
        })
        decision = {
            "status": "READ_ONLY_PREFLIGHT_PACKAGE_READY",
            "preparation_id": identifiers["preparation_id"],
            "run_id": identifiers["run_id"],
            "repair_id": repair_id,
            "session_id": identifiers["session_id"],
            "preflight_authorized": False,
            "preflight_executed": False,
            "network_attempts": 0,
            "orders_submitted": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        }
        _write_json(output / "decision" / "preflight_preparation_decision.json", decision)
        _write_text(
            output / "decision" / "preflight_preparation_decision.md",
            "# Read-only OKX Demo preflight preparation\n\n"
            f"- Preparation: `{identifiers['preparation_id']}`\n"
            f"- Repair evidence: `{repair_id}`\n"
            f"- Reserved preflight run: `{identifiers['run_id']}`\n"
            f"- Reserved session: `{identifiers['session_id']}`\n"
            "- Preflight executed: `false`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n\n"
            "A separate exact user confirmation is required before the read-only preflight.\n",
        )
        manifest = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", manifest)
        terminal = {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(manifest),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": source_manifest_sha256,
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
        }
        _write_json(output / "PREFLIGHT_PREPARATION_COMPLETED.json", terminal)
    if guard.attempts:
        raise PreflightPreparationError(
            "network attempt was blocked during preflight preparation"
        )
    return output, identifiers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id", required=True)
    args = parser.parse_args()
    try:
        output, identifiers = prepare_preflight_package(args.root, args.repair_id)
    except Exception as exc:
        print(f"PREFLIGHT_PREPARATION_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

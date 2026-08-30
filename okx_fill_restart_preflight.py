"""Separately armed, read-only OKX Demo preflight for the successor protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from okx_demo_adapter import build_ccxt_demo_exchange
from okx_demo_protocol import _adapter, _credential_config
from okx_demo_runtime import MarketDataGate
from okx_fill_restart_offline import (
    ARTIFACT_ROOT,
    _completion_hashes,
    _sha256,
    _write_json,
    source_hashes,
    verify_predecessors,
)
from okx_fill_restart_gateway import FormalDemoGateway
from okx_fill_restart_validation import RiskBudget, canonical_sha256


PREFLIGHT_PROTOCOL_ID = "okx-demo-fill-restart-read-only-preflight-v1"
MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID = (
    "okx-demo-multi-session-a1-read-only-preflight-v1"
)
MULTI_SESSION_A1_R0_EVIDENCE_KINDS = frozenset(
    {
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
    }
)


def preflight_protocol_id_for_evidence(evidence_kind: object) -> str:
    """Select the immutable read-only preflight protocol for an R0 evidence kind."""
    if evidence_kind in MULTI_SESSION_A1_R0_EVIDENCE_KINDS:
        return MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID
    return PREFLIGHT_PROTOCOL_ID


EXPECTED_OKX_DEMO_HOSTNAME = "www.okx.com"
MAXIMUM_CLOCK_SKEW_MS = 1_500
MAXIMUM_READ_ATTEMPTS = 3
OFFLINE_READY_STATUS = "OFFLINE_IMPLEMENTATION_READY"
REPAIR_READY_STATUS = "OKX_DEMO_FILL_CURSOR_REPAIR_OFFLINE_SUPPORT"
REPAIR_ARTIFACT_ROOT = Path("artifacts") / "okx_demo_fill_cursor_repair"
SHUTDOWN_REPAIR_READY_STATUS = (
    "OKX_DEMO_ACTIVITY_BUDGET_SHUTDOWN_REPAIR_OFFLINE_SUPPORT"
)
SHUTDOWN_REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_activity_budget_shutdown_repair"
)
FUTURE_BOOK_REPAIR_READY_STATUS = (
    "OKX_DEMO_FUTURE_BOOK_TIMESTAMP_REPAIR_OFFLINE_SUPPORT"
)
FUTURE_BOOK_REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_future_book_timestamp_repair"
)
R2_REPAIR_READY_STATUS = "OKX_DEMO_R2_WARMUP_AUDIT_REPAIR_OFFLINE_SUPPORT"
R2_REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r2_warmup_audit_repair"
)
SIGNED_AGE_REPAIR_READY_STATUS = (
    "OKX_DEMO_SIGNED_AGE_PREARM_TERMINAL_REPAIR_OFFLINE_SUPPORT"
)
SIGNED_AGE_REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_signed_age_prearm_terminal_repair"
)
R1_TERMINAL_REPAIR_READY_STATUS = (
    "OKX_DEMO_R1_TERMINAL_RECONCILIATION_REPAIR_OFFLINE_SUPPORT"
)
R1_TERMINAL_REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r1_terminal_reconciliation_repair"
)
SOAK_FAILURE_READY_STATUS = (
    "OKX_DEMO_SOAK_FAILURE_INJECTION_OFFLINE_SUPPORT"
)
SOAK_FAILURE_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_soak_failure_injection"
)
MULTI_SESSION_A0_READY_STATUS = "OKX_DEMO_MULTI_SESSION_A0_OFFLINE_SUPPORT"
MULTI_SESSION_A0_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_multi_session_economic_soak"
)
SAMPLE_EFFICIENCY_R0_READY_STATUS = (
    "OKX_DEMO_SAMPLE_EFFICIENCY_R0_OFFLINE_SUPPORT"
)
SAMPLE_EFFICIENCY_R0_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_post_campaign_repair"
)
TERMINAL_RECOVERY_READY_STATUS = (
    "OKX_DEMO_R2_TERMINAL_RECOVERY_OFFLINE_SUPPORT"
)
TERMINAL_RECOVERY_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_terminal_recovery_repair"
)
TERMINAL_CAUSAL_CLI_READY_STATUS = (
    "OKX_DEMO_TERMINAL_CAUSAL_CLI_R0_OFFLINE_SUPPORT"
)
TERMINAL_CAUSAL_CLI_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_terminal_causal_cli_repair"
)
FIFO_ATTRIBUTION_READY_STATUS = (
    "OKX_DEMO_FIFO_ATTRIBUTION_R0_OFFLINE_SUPPORT"
)
FIFO_ATTRIBUTION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_fifo_attribution_repair"
)
WORKOFF_TIMESTAMP_READY_STATUS = (
    "OKX_DEMO_UNOBSERVED_WORKOFF_TIMESTAMP_R0_OFFLINE_SUPPORT"
)
WORKOFF_TIMESTAMP_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_unobserved_workoff_timestamp_repair"
)
TERMINAL_SPECIAL_CLOSURE_READY_STATUS = (
    "OKX_DEMO_TERMINAL_SPECIAL_CLOSURE_R0_OFFLINE_SUPPORT"
)
TERMINAL_SPECIAL_CLOSURE_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_terminal_special_closure_repair"
)
MARKOUT_SPECIAL_CLOSURE_READY_STATUS = (
    "OKX_DEMO_MARKOUT_SPECIAL_CLOSURE_R0_OFFLINE_SUPPORT"
)
MARKOUT_SPECIAL_CLOSURE_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_markout_special_closure_repair"
)
OWNED_CANCEL_RECONCILIATION_READY_STATUS = (
    "OKX_DEMO_OWNED_CANCEL_RECONCILIATION_R0_OFFLINE_SUPPORT"
)
OWNED_CANCEL_RECONCILIATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_owned_cancel_reconciliation_repair"
)
POST_WALL_INTERRUPTION_READY_STATUS = (
    "OKX_DEMO_POST_WALL_INTERRUPTION_R0_OFFLINE_SUPPORT"
)
POST_WALL_INTERRUPTION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_post_wall_interruption_audit"
)
MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS = (
    "OKX_DEMO_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_R0_OFFLINE_SUPPORT"
)
MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_market_bootstrap_terminal_reconciliation"
)
PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS = (
    "OKX_DEMO_PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_"
    "R0_OFFLINE_SUPPORT"
)
PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_preflight_market_bootstrap_terminal_reconciliation"
)
TRANSPORT_RESILIENCE_READY_STATUS = "OKX_DEMO_TRANSPORT_RESILIENCE_R0_OFFLINE_SUPPORT"
TRANSPORT_RESILIENCE_ARTIFACT_ROOT = Path("artifacts") / "okx_demo_transport_resilience_repair"
EXECUTION_ENVIRONMENT_TRANSPORT_READY_STATUS = (
    "OKX_DEMO_EXECUTION_ENVIRONMENT_TRANSPORT_R0_OFFLINE_SUPPORT"
)
EXECUTION_ENVIRONMENT_TRANSPORT_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_execution_environment_transport_repair"
)
R2_SESSION5_TERMINAL_RECONCILIATION_READY_STATUS = (
    "OKX_DEMO_R2_SESSION5_TERMINAL_RECONCILIATION_R0_OFFLINE_SUPPORT"
)
R2_SESSION5_TERMINAL_RECONCILIATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r2_session5_terminal_reconciliation_repair"
)
R2_SESSION1_CANCEL_FILL_RECONCILIATION_READY_STATUS = (
    "OKX_DEMO_R2_SESSION1_CANCEL_FILL_RECONCILIATION_R0_OFFLINE_SUPPORT"
)
R2_SESSION1_CANCEL_FILL_RECONCILIATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r2_session1_cancel_fill_reconciliation_repair"
)
PREPARATION_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_fill_restart_validation" / "preflight_packages"
)
MUTATING_METHODS = {
    "create_order",
    "create_orders",
    "edit_order",
    "cancel_order",
    "cancel_orders",
    "cancel_all_orders",
    "set_leverage",
    "set_position_mode",
    "set_margin_mode",
    "transfer",
    "withdraw",
    "borrow_margin",
    "repay_margin",
}
LOCAL_ONLY_METHODS = {"set_markets", "close"}
PREFLIGHT_SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md",
    "AGENTS_OKX_DEMO_ACTIVITY_BUDGET_SHUTDOWN_REPAIR.md",
    "AGENTS_OKX_DEMO_FUTURE_BOOK_TIMESTAMP_REPAIR.md",
    "AGENTS_OKX_DEMO_R2_WARMUP_AUDIT_COUNTER_REPAIR.md",
    "AGENTS_OKX_DEMO_SIGNED_AGE_PREARM_TERMINAL_REPAIR.md",
    "AGENTS_OKX_DEMO_R1_TERMINAL_RECONCILIATION_REPAIR.md",
    "AGENTS_OKX_DEMO_SOAK_FAILURE_INJECTION.md",
    "AGENTS_OKX_DEMO_BOUNDED_SOAK_EXECUTION.md",
    "AGENTS_OKX_DEMO_MULTI_SESSION_ECONOMIC_SOAK_FAILURE_INJECTION.md",
    "AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md",
    "okx_demo_multi_session_campaign.py",
    "okx_demo_multi_session_prepare.py",
    "okx_demo_multi_session_supervisor.py",
    "okx_demo_operational_failure_campaign.py",
    "okx_demo_economic_session_controller.py",
    "okx_demo_economic_fill_engine.py",
    "okx_demo_multi_session_a0_offline.py",
    "okx_demo_post_campaign_offline.py",
    "okx_demo_terminal_recovery_executor.py",
    "okx_demo_r2_terminal_recovery_offline.py",
    "okx_demo_terminal_causal_cli_repair_offline.py",
    "okx_demo_fifo_attribution_repair_offline.py",
    "okx_demo_unobserved_workoff_timestamp_repair_offline.py",
    "okx_demo_terminal_special_closure_repair_offline.py",
    "okx_demo_markout_special_closure_repair_offline.py",
    "okx_demo_owned_cancel_reconciliation_repair_offline.py",
    "okx_demo_post_wall_interruption_audit_offline.py",
    "okx_demo_market_bootstrap_terminal_reconciliation_repair_offline.py",
    "okx_demo_preflight_market_bootstrap_terminal_reconciliation_repair_offline.py",
    "okx_demo_transport_resilience_repair_offline.py",
    "okx_demo_execution_environment_transport_repair_offline.py",
    "okx_demo_execution_environment_successor_prepare.py",
    "okx_demo_execution_environment_successor_supervisor.py",
    "okx_demo_sample_efficiency_campaign_supervisor.py",
    "okx_activity_budget_shutdown_repair_offline.py",
    "okx_future_book_timestamp_repair_offline.py",
    "okx_r2_warmup_audit_repair_offline.py",
    "okx_signed_age_prearm_terminal_repair_offline.py",
    "okx_r1_terminal_reconciliation_repair_offline.py",
    "okx_demo_soak_failure_injection.py",
    "okx_demo_soak_failure_injection_offline.py",
    "okx_demo_soak_executor.py",
    "okx_demo_soak_prepare.py",
    "okx_demo_runtime.py",
    "okx_fill_restart_formal.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_preflight.py",
    "okx_fill_restart_preflight_prepare.py",
    "tests/test_okx_activity_budget_shutdown_repair.py",
    "tests/test_okx_future_book_timestamp_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_demo_soak_failure_injection.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_multi_session_campaign.py",
    "tests/test_okx_demo_fifo_attribution_repair.py",
    "tests/test_okx_demo_unobserved_workoff_timestamp_repair.py",
    "tests/test_okx_demo_terminal_special_closure_repair.py",
    "tests/test_okx_demo_markout_special_closure_repair.py",
    "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
    "tests/test_okx_demo_post_wall_interruption_audit.py",
    "tests/test_okx_demo_multi_session_prepare.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_operational_failure_campaign.py",
    "tests/test_okx_demo_economic_session_controller.py",
    "tests/test_okx_demo_economic_fill_engine.py",
    "tests/test_okx_demo_multi_session_a0_offline.py",
    "tests/test_okx_demo_post_campaign_offline.py",
    "tests/test_okx_demo_sample_efficiency_repair.py",
    "tests/test_okx_demo_terminal_recovery.py",
    "tests/test_okx_demo_terminal_recovery_campaign.py",
    "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_fill_restart_preflight_prepare.py",
    "tests/test_okx_demo_execution_environment_successor.py",
)


class ReadOnlyPreflightError(RuntimeError):
    pass


def _read_error_detail(error: BaseException) -> dict[str, object]:
    """Classify read errors without persisting messages, URLs, or secrets."""
    chain: list[BaseException] = []
    cursor: BaseException | None = error
    while cursor is not None and len(chain) < 3:
        chain.append(cursor)
        # Exception context can be the earlier primary market failure while a
        # terminal fallback is running; only an explicit cause is causal.
        next_error = cursor.__cause__
        cursor = next_error if isinstance(next_error, BaseException) else None
    names = " ".join(type(item).__name__.lower() for item in chain)
    status: int | None = None
    os_error: int | None = None
    for item in chain:
        for name in ("http_status", "status_code", "status"):
            value = getattr(item, name, None)
            if isinstance(value, int) and 100 <= value <= 599:
                status = value
                break
        value = getattr(item, "errno", None)
        if isinstance(value, int):
            os_error = value
        if status is not None:
            break
    if status == 429 or "ratelimit" in names or "ddos" in names:
        category, retryable = "RATE_LIMIT", True
    elif status is not None and 500 <= status <= 599:
        category, retryable = "SERVER", True
    elif status is not None and 400 <= status <= 499:
        category, retryable = "HTTP_CLIENT", False
    elif "auth" in names or "permission" in names or "credential" in names:
        category, retryable = "AUTHORIZATION", False
    elif "malformed" in names or "parse" in names or "decode" in names:
        category, retryable = "MALFORMED_RESPONSE", False
    elif "certificate" in names or "ssl" in names or "tls" in names:
        category, retryable = "TLS", False
    elif "gaierror" in names or "dns" in names or "name_resolution" in names:
        category, retryable = "DNS", True
    elif "timeout" in names:
        category, retryable = "TIMEOUT", True
    elif "network" in names or "connection" in names or "oserror" in names:
        category, retryable = "NETWORK", True
    else:
        category, retryable = "READ_FAILURE", False
    return {
        "category": category,
        "retryable": retryable,
        "http_status": status,
        "os_error_code": os_error,
    }


def _hash_matches(path: Path, expected: object) -> bool:
    try:
        return path.is_file() and _sha256(path) == expected
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReadOnlyPreflightError(f"JSON object required: {path.name}")
    return value


class ReadOnlyExchangeProxy:
    """Allow read dispatch only after proving CCXT Demo transport each time."""

    def __init__(self, exchange: Any, *, sleep: Callable[[float], None] = time.sleep):
        self._exchange = exchange
        self._sleep = sleep
        self.read_calls: list[str] = []
        self.local_calls: list[str] = []
        self.mutation_attempts = 0
        self.live_endpoint_attempts = 0
        self.permission_snapshots: list[tuple[str, ...]] = []
        self.read_retry_audit: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._exchange, name)
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            if self._is_mutating(name):
                self.mutation_attempts += 1
                raise ReadOnlyPreflightError(
                    f"exchange mutation is prohibited during preflight: {name}"
                )
            if name in LOCAL_ONLY_METHODS:
                self.local_calls.append(name)
                return attribute(*args, **kwargs)
            for attempt in range(1, MAXIMUM_READ_ATTEMPTS + 1):
                self._verify_demo_transport()
                self.read_calls.append(name)
                try:
                    result = attribute(*args, **kwargs)
                except Exception as exc:
                    detail = _read_error_detail(exc)
                    retry = bool(detail["retryable"]) and attempt < MAXIMUM_READ_ATTEMPTS
                    self.read_retry_audit.append({
                        "method": name,
                        "attempt": attempt,
                        "retry": retry,
                        **detail,
                    })
                    if not retry:
                        raise
                    self._sleep(0.05 * (2 ** (attempt - 1)))
                    continue
                if name == "privateGetAccountConfig":
                    self._capture_permissions(result)
                self.read_retry_audit.append({
                    "method": name,
                    "attempt": attempt,
                    "retry": False,
                    "category": "SUCCESS",
                    "retryable": False,
                    "http_status": None,
                    "os_error_code": None,
                })
                return result
            raise ReadOnlyPreflightError("read retry loop exhausted unexpectedly")

        return guarded

    @staticmethod
    def _is_mutating(name: str) -> bool:
        lowered = name.lower()
        if name in MUTATING_METHODS:
            return True
        return (
            lowered.startswith("privatepost")
            or lowered.startswith("privatedelete")
            or lowered.startswith("publicpost")
            or lowered.startswith("publicdelete")
        )

    def _verify_demo_transport(self) -> None:
        sandbox = self._exchange.options.get("sandboxMode") is True
        simulated = str(self._exchange.headers.get("x-simulated-trading", "")) == "1"
        configured_hostname = str(
            getattr(self._exchange, "hostname", "") or ""
        ).lower()
        expected_hostname = configured_hostname == EXPECTED_OKX_DEMO_HOSTNAME
        expected_endpoints = self.endpoint_hosts == [EXPECTED_OKX_DEMO_HOSTNAME]
        if not sandbox or not simulated or not expected_hostname or not expected_endpoints:
            self.live_endpoint_attempts += 1
            raise ReadOnlyPreflightError(
                "read dispatch refused because Demo transport is not proven"
            )

    def _capture_permissions(self, response: object) -> None:
        if not isinstance(response, dict):
            raise ReadOnlyPreflightError("account configuration response is malformed")
        rows = response.get("data") or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ReadOnlyPreflightError("account configuration permission row is unknown")
        raw = str(rows[0].get("perm") or "")
        permissions = tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))
        if not permissions:
            raise ReadOnlyPreflightError("API-key permissions are unavailable")
        self.permission_snapshots.append(permissions)

    @property
    def endpoint_hosts(self) -> list[str]:
        values: list[str] = []
        configured_hostname = str(
            getattr(self._exchange, "hostname", "") or ""
        ).lower()

        def visit(value: object) -> None:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                resolved = value.replace("{hostname}", configured_hostname)
                host = urlparse(resolved).hostname
                if host:
                    values.append(host)
            elif isinstance(value, dict):
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(self._exchange.urls.get("api", {}))
        return sorted(set(values))

    def public_audit(self) -> dict[str, object]:
        permissions = (
            list(self.permission_snapshots[-1])
            if self.permission_snapshots else []
        )
        return {
            "sandbox_mode": self._exchange.options.get("sandboxMode") is True,
            "simulated_trading_header": (
                str(self._exchange.headers.get("x-simulated-trading", "")) == "1"
            ),
            "endpoint_hosts": self.endpoint_hosts,
            "read_call_count": len(self.read_calls),
            "maximum_read_attempts": MAXIMUM_READ_ATTEMPTS,
            "read_methods": sorted(set(self.read_calls)),
            "read_retry_audit": list(self.read_retry_audit),
            "local_only_methods": sorted(set(self.local_calls)),
            "mutation_attempts": self.mutation_attempts,
            "live_endpoint_attempts": self.live_endpoint_attempts,
            "permission_snapshot_count": len(self.permission_snapshots),
            "permissions": permissions,
            "non_withdrawal_permissions": bool(permissions) and "withdraw" not in permissions,
            "read_permission_present": "read_only" in permissions,
            "trade_permission_present": "trade" in permissions,
        }


def expected_arm_token(session_id: str) -> str:
    if not session_id.startswith("preflight:"):
        raise ReadOnlyPreflightError("preflight session identity is invalid")
    return f"OKX_DEMO:{session_id}"


def verify_offline_evidence(root: Path, offline_run_id: str) -> dict[str, object]:
    if offline_run_id.startswith("r2-session1-cancel-fill-repair-offline-"):
        return _verify_r2_session1_cancel_fill_reconciliation_evidence(
            root, offline_run_id
        )
    if offline_run_id.startswith("r2-session5-terminal-repair-offline-"):
        return _verify_r2_session5_terminal_reconciliation_evidence(
            root, offline_run_id
        )
    if offline_run_id.startswith("execution-environment-transport-repair-offline-"):
        return _verify_execution_environment_transport_evidence(root, offline_run_id)
    if offline_run_id.startswith("transport-resilience-repair-offline-"):
        return _verify_transport_resilience_evidence(root, offline_run_id)
    if offline_run_id.startswith("preflight-market-bootstrap-repair-offline-"):
        return _verify_preflight_market_bootstrap_terminal_reconciliation_evidence(
            root, offline_run_id
        )
    if offline_run_id.startswith("market-bootstrap-terminal-repair-offline-"):
        return _verify_market_bootstrap_terminal_reconciliation_evidence(
            root, offline_run_id
        )
    if offline_run_id.startswith("post-wall-audit-offline-"):
        return _verify_post_wall_interruption_evidence(root, offline_run_id)
    if offline_run_id.startswith("owned-cancel-repair-offline-"):
        return _verify_owned_cancel_reconciliation_evidence(root, offline_run_id)
    if offline_run_id.startswith("markout-special-repair-offline-"):
        return _verify_markout_special_closure_evidence(root, offline_run_id)
    if offline_run_id.startswith("terminal-special-repair-offline-"):
        return _verify_terminal_special_closure_evidence(root, offline_run_id)
    if offline_run_id.startswith("workoff-timestamp-repair-offline-"):
        return _verify_workoff_timestamp_evidence(root, offline_run_id)
    if offline_run_id.startswith("fifo-attribution-repair-offline-"):
        return _verify_fifo_attribution_evidence(root, offline_run_id)
    if offline_run_id.startswith("terminal-causal-repair-offline-"):
        return _verify_terminal_causal_cli_evidence(root, offline_run_id)
    if offline_run_id.startswith("terminal-recovery-offline-"):
        return _verify_terminal_recovery_evidence(root, offline_run_id)
    if offline_run_id.startswith("economic-repair-offline-"):
        return _verify_sample_efficiency_r0_evidence(root, offline_run_id)
    if offline_run_id.startswith("multi-session-a0-offline-"):
        return _verify_multi_session_a0_evidence(root, offline_run_id)
    if offline_run_id.startswith("soak-failure-offline-"):
        return _verify_soak_failure_evidence(root, offline_run_id)
    if offline_run_id.startswith("r1-terminal-repair-offline-"):
        return _verify_r1_terminal_repair_evidence(root, offline_run_id)
    if offline_run_id.startswith("signed-age-repair-offline-"):
        return _verify_signed_age_repair_evidence(root, offline_run_id)
    if offline_run_id.startswith("r2-repair-offline-"):
        return _verify_r2_repair_evidence(root, offline_run_id)
    if offline_run_id.startswith("future-book-repair-offline-"):
        return _verify_future_book_repair_evidence(root, offline_run_id)
    if offline_run_id.startswith("shutdown-repair-offline-"):
        return _verify_shutdown_repair_evidence(root, offline_run_id)
    if offline_run_id.startswith("repair-offline-"):
        return _verify_repair_evidence(root, offline_run_id)
    output = root / ARTIFACT_ROOT / offline_run_id
    terminal_path = output / "OFFLINE_PHASE_COMPLETED.json"
    decision_path = output / "decision" / "offline_decision.json"
    completion_path = output / "completion_hashes.json"
    if not all(path.is_file() for path in (terminal_path, decision_path, completion_path)):
        raise ReadOnlyPreflightError("offline completion evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if terminal.get("phase_status") != OFFLINE_READY_STATUS:
        raise ReadOnlyPreflightError("offline phase is not ready")
    if decision.get("phase_status") != OFFLINE_READY_STATUS:
        raise ReadOnlyPreflightError("offline decision is not ready")
    if any((
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("preflight_executed") is not False,
        terminal.get("formal_execution_armed") is not False,
    )):
        raise ReadOnlyPreflightError("offline completion boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("offline completion manifest hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for relative, expected in manifest.items():
        path = output / relative
        if not _hash_matches(path, expected):
            failures.append(relative)
    source_manifest = json.loads(
        (output / "specification" / "source_hashes.json").read_text(encoding="utf-8")
    )
    source_failures: list[str] = []
    for relative, expected in source_manifest.items():
        path = root / relative
        if not _hash_matches(path, expected):
            source_failures.append(relative)
    if failures or source_failures:
        raise ReadOnlyPreflightError("offline evidence or source hash mismatch")
    return {
        "passed": True,
        "evidence_kind": "legacy_offline_validation",
        "offline_run_id": offline_run_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
    }


def _verify_sample_efficiency_r0_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / SAMPLE_EFFICIENCY_R0_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_OFFLINE_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    tests_path = output / "tests" / "test_summary.json"
    endpoint_path = output / "audits" / "endpoint_mutation_audit.json"
    secret_path = output / "audits" / "secret_scan.json"
    predecessor_path = output / "predecessor" / "a2_campaign_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        endpoint_path, secret_path, predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("sample-efficiency R0 evidence is incomplete")

    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    if any((
        terminal.get("status") != SAMPLE_EFFICIENCY_R0_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("network_attempts") != 0,
        terminal.get("credential_reads") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("git_write_operation") is not False,
        decision.get("status") != SAMPLE_EFFICIENCY_R0_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("successor_protocol_active") is not True,
        decision.get("R1_preparation_authorized") is not False,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "INSUFFICIENT_EVIDENCE",
        predecessor.get("unsafe_sessions") != 0,
        predecessor.get("live_endpoint_attempts") != 0,
        predecessor.get("live_orders") != 0,
    )):
        raise ReadOnlyPreflightError("sample-efficiency R0 boundary is invalid")
    required_suites = (
        "post_campaign_targeted", "root_non_optuna", "backtest_non_optuna"
    )
    if any(
        tests.get(name, {}).get("passed_gate") is not True
        or tests.get(name, {}).get("returncode") != 0
        or tests.get(name, {}).get("network_attempts") != 0
        or tests.get(name, {}).get("live_endpoint_attempts") != 0
        or tests.get(name, {}).get("optuna_imported") is not False
        for name in required_suites
    ):
        raise ReadOnlyPreflightError("sample-efficiency R0 tests did not pass")

    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "sample-efficiency R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "sample_efficiency_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "successor_protocol_active": True,
        "economic_predecessor_verified": True,
        "economic_predecessor_package_id": predecessor.get("package_id"),
        "economic_predecessor_campaign_id": predecessor.get("campaign_id"),
    }


def _verify_terminal_recovery_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / TERMINAL_RECOVERY_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R2_TERMINAL_RECOVERY_OFFLINE_COMPLETED.json"
    decision_path = output / "decision" / "offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    tests_path = output / "tests" / "test_summary.json"
    endpoint_path = output / "audits" / "endpoint_mutation_audit.json"
    secret_path = output / "audits" / "secret_scan.json"
    failed_path = output / "predecessor" / "failed_execution_audit.json"
    confirmation_path = output / "recovery" / "user_flat_empty_confirmation.json"
    recovery_path = output / "recovery" / "campaign_fail_closed_recovery_manifest.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        endpoint_path, secret_path, failed_path, confirmation_path, recovery_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("terminal-recovery evidence is incomplete")

    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    targeted = tests.get("terminal_recovery_targeted", {})
    if any((
        terminal.get("status") != TERMINAL_RECOVERY_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        decision.get("status") != TERMINAL_RECOVERY_READY_STATUS,
        decision.get("offline_repair_passed") is not True,
        decision.get("failed_campaign_decision") != "NOT_READY",
        decision.get("failed_campaign_resume_authorized") is not False,
        decision.get("preflight_authorized") is not False,
        decision.get("package_preparation_authorized") is not False,
        decision.get("campaign_authorized") is not False,
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "okx_requests",
            "live_endpoint_attempts", "live_orders", "create_attempts",
            "amend_attempts", "cancel_attempts", "flatten_attempts",
            "account_configuration_attempts",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        failed.get("immutable") is not True,
        failed.get("resume_authorized") is not False,
        failed.get("flatten_retry_authorized") is not False,
        failed.get("slot_2_through_12_started") is not False,
        confirmation.get("position_btc") != "0",
        confirmation.get("open_orders") != 0,
        confirmation.get("authoritative_exchange_snapshot") is not False,
        confirmation.get("economic_evidence") is not False,
        confirmation.get("resume_authorized") is not False,
        recovery.get("decision") != "NOT_READY",
        recovery.get("campaign_resume_authorized") is not False,
        recovery.get("fresh_preflight_and_package_required") is not True,
    )):
        raise ReadOnlyPreflightError("terminal-recovery boundary is invalid")

    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "terminal-recovery evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "r2_post_start_terminal_recovery_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": failed.get("package_id"),
        "failed_campaign_run_id": failed.get("campaign_run_id"),
        "failed_session_package_id": failed.get("session_package_id"),
        "failed_campaign_decision": "NOT_READY",
        "account_confirmation_is_exchange_authoritative": False,
    }


def _verify_terminal_causal_cli_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / TERMINAL_CAUSAL_CLI_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_TERMINAL_CAUSAL_CLI_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        endpoint_path, secret_path, predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("terminal causal/CLI R0 evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != TERMINAL_CAUSAL_CLI_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        decision.get("status") != TERMINAL_CAUSAL_CLI_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("R1_preparation_authorized") is not False,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("failed_slots") != [1],
        predecessor.get("slot_2_through_12_started") is not False,
        predecessor.get("resume_authorized") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("terminal_account_snapshots") != 2,
        predecessor.get("live_endpoint_attempts") != 0,
        predecessor.get("live_orders") != 0,
    )):
        raise ReadOnlyPreflightError("terminal causal/CLI R0 boundary is invalid")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [relative for relative, expected in manifest.items() if not _hash_matches(output / relative, expected)]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [relative for relative, expected in sources.items() if not _hash_matches(root / relative, expected)]
    if failures or source_failures:
        raise ReadOnlyPreflightError("terminal causal/CLI R0 evidence or source hash mismatch")
    return {
        "passed": True,
        "evidence_kind": "terminal_causal_cli_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_fifo_attribution_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / FIFO_ATTRIBUTION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_FIFO_ATTRIBUTION_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    replay_path = output / "diagnostic/fifo_attribution_replay.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        endpoint_path, secret_path, predecessor_path, replay_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("FIFO attribution R0 evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != FIFO_ATTRIBUTION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        decision.get("status") != FIFO_ATTRIBUTION_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("R1_preparation_authorized") is not False,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("failed_slots") != [2],
        predecessor.get("slot_3_through_12_started") is not False,
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("terminal_account_snapshots") != 2,
        predecessor.get("live_endpoint_attempts") != 0,
        predecessor.get("live_orders") != 0,
        replay.get("passed") is not True,
        replay.get("normal_fill_count") != 20,
        replay.get("normal_fifo_round_trips") != 11,
        replay.get("legacy_pair_bound") != 10,
        replay.get("quantity_conservation_passed") is not True,
        replay.get("identity_reuse_validated") is not True,
        replay.get("projection_promotable") is not False,
        replay.get("source_artifacts_modified") is not False,
    )):
        raise ReadOnlyPreflightError("FIFO attribution R0 boundary is invalid")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "FIFO attribution R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "fifo_attribution_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_workoff_timestamp_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / WORKOFF_TIMESTAMP_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_WORKOFF_TIMESTAMP_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/workoff_timestamp_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    diagnostic_path = output / "diagnostic/root_cause.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
        diagnostic_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("work-off timestamp R0 evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != WORKOFF_TIMESTAMP_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        decision.get("status") != WORKOFF_TIMESTAMP_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("R1_preparation_authorized") is not False,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("normal_fill_count") != 118,
        predecessor.get("normal_fifo_round_trips") != 59,
        diagnostic.get("failed_invariant")
        != "unobserved maker work-off has a timestamp",
        diagnostic.get("risk_expansion") is not False,
    )):
        raise ReadOnlyPreflightError("work-off timestamp R0 boundary is invalid")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "work-off timestamp R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "workoff_timestamp_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("failed_session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_terminal_special_closure_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / TERMINAL_SPECIAL_CLOSURE_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_TERMINAL_SPECIAL_CLOSURE_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/terminal_special_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    replay_path = output / "diagnostic/slot_11_special_closure_replay.json"
    workoff_path = output / "diagnostic/workoff_opportunity_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
        replay_path, workoff_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "terminal special-closure R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    workoff = json.loads(workoff_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != TERMINAL_SPECIAL_CLOSURE_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        decision.get("status") != TERMINAL_SPECIAL_CLOSURE_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("special_flatten_sessions") != 4,
        replay.get("passed") is not True,
        replay.get("pending_after") != 0,
        replay.get("special_closed_count") != 1,
        replay.get("causal_reentry_credit_after") != 0,
        replay.get("maker_workoff_credit") is not False,
        replay.get("terminal_inventory_btc") != "0",
        replay.get("projection_promotable") is not False,
        workoff.get("risk_expansion") is not False,
        workoff.get("flatten_reduction_claimed_without_demo_evidence") is not False,
        workoff.get("total_create_cap") != 60,
        workoff.get("workoff_create_reserve") != 12,
    )):
        raise ReadOnlyPreflightError(
            "terminal special-closure R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "terminal special-closure R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "terminal_special_closure_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("failed_session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_markout_special_closure_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / MARKOUT_SPECIAL_CLOSURE_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_MARKOUT_SPECIAL_CLOSURE_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/markout_special_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    replay_path = output / "diagnostic/split_markout_attribution_replay.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path, replay_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "markout/special-closure R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != MARKOUT_SPECIAL_CLOSURE_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        decision.get("status") != MARKOUT_SPECIAL_CLOSURE_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("completed_slots") != [],
        predecessor.get("failed_slots") != [1],
        predecessor.get("slots_2_through_12_started") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("normal_fill_count") != 3,
        predecessor.get("causal_maker_fill_count") != 2,
        predecessor.get("terminal_special_closed_fill_count") != 1,
        replay.get("passed") is not True,
        replay.get("normal_fill_count") != 3,
        replay.get("causal_maker_fill_count") != 2,
        replay.get("causal_markout_count") != 2,
        replay.get("terminal_special_closed_fill_count") != 1,
        replay.get("terminal_special_closed_markout_count") != 1,
        replay.get("normal_markout_attribution_reconciles") is not True,
        replay.get("terminal_position_btc") != "0",
        replay.get("terminal_open_orders") != 0,
        replay.get("projection_promotable") is not False,
    )):
        raise ReadOnlyPreflightError(
            "markout/special-closure R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "markout/special-closure R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "markout_special_closure_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("failed_session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_owned_cancel_reconciliation_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / OWNED_CANCEL_RECONCILIATION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_OWNED_CANCEL_RECONCILIATION_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/owned_cancel_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    replay_path = output / "diagnostic/cancel_outcome_replay.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path, replay_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "owned-cancel reconciliation R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    scenarios = list(replay.get("scenarios") or [])
    if any((
        terminal.get("status") != OWNED_CANCEL_RECONCILIATION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        decision.get("status") != OWNED_CANCEL_RECONCILIATION_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders", "mutation_retries",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("completed_slots") != [1, 2, 3, 4],
        predecessor.get("failed_slots") != [5],
        predecessor.get("slots_6_through_12_started") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("terminal_account_snapshots") != 2,
        predecessor.get("attempted_normal_fills") != 60,
        predecessor.get("attempted_fifo_round_trips") != 29,
        predecessor.get("attempted_special_flatten_sessions") != 2,
        predecessor.get("mutation_retries") != 0,
        replay.get("passed") is not True,
        replay.get("maximum_cancel_dispatches_per_owned_order") != 1,
        replay.get("maximum_read_attempts") != 3,
        replay.get("mutation_retries") != 0,
        replay.get("source_predecessor_modified") is not False,
        replay.get("projection_promotable") is not False,
        len(scenarios) != 6,
        any(item.get("passed") is not True for item in scenarios),
    )):
        raise ReadOnlyPreflightError(
            "owned-cancel reconciliation R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "owned-cancel reconciliation R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "owned_cancel_reconciliation_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("failed_session_package_id"),
        "failed_campaign_decision": "NOT_READY",
    }


def _verify_r2_session1_cancel_fill_reconciliation_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / R2_SESSION1_CANCEL_FILL_RECONCILIATION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_R2_SESSION1_CANCEL_FILL_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    targeted_path = output / "tests/r2_session1_cancel_fill_targeted_summary.json"
    tests_path = output / "tests/test_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_session_audit.json"
    diagnostic_path = output / "diagnostic/root_cause.json"
    rehearsal_path = output / "diagnostic/promotion_rehearsal.json"
    required = (terminal_path, decision_path, completion_path, source_path, targeted_path,
                tests_path, endpoint_path, secret_path, predecessor_path, diagnostic_path, rehearsal_path)
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("R2 Session 1 cancel/fill R0 evidence is incomplete")
    terminal, decision = json.loads(terminal_path.read_text(encoding="utf-8")), json.loads(decision_path.read_text(encoding="utf-8"))
    targeted, tests = json.loads(targeted_path.read_text(encoding="utf-8")), json.loads(tests_path.read_text(encoding="utf-8"))
    endpoint, secret = json.loads(endpoint_path.read_text(encoding="utf-8")), json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor, diagnostic = json.loads(predecessor_path.read_text(encoding="utf-8")), json.loads(diagnostic_path.read_text(encoding="utf-8"))
    rehearsal = json.loads(rehearsal_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != R2_SESSION1_CANCEL_FILL_RECONCILIATION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id, terminal.get("terminal_written_last") is not True,
        decision.get("R0_offline_repair_passed") is not True,
        decision.get("R1_preparation_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(tests.get(name, {}).get("passed_gate") is not True or tests.get(name, {}).get("returncode") != 0 or tests.get(name, {}).get("network_attempts") != 0 or tests.get(name, {}).get("optuna_imported") is not False for name in suites),
        targeted.get("passed_gate") is not True or targeted.get("returncode") != 0 or targeted.get("network_attempts") != 0,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in ("network_attempts", "credential_reads", "demo_endpoint_attempts", "live_endpoint_attempts", "create_attempts", "amend_attempts", "cancel_attempts", "flatten_attempts", "account_configuration_attempts", "orders", "mutation_retries")),
        secret.get("passed") is not True or secret.get("credential_environment_accessed") is not False or secret.get("credentials_serialized") is not False,
        predecessor.get("immutable") is not True or predecessor.get("flatten_dispatches") != 0 or predecessor.get("session_2_authorized") is not False,
        diagnostic.get("repair", {}).get("filled_during_cancel_requires_owned_trade_quantity_proof") is not True,
        diagnostic.get("repair", {}).get("persistent_ambiguity_blocks_flatten_before_dispatch") is not True,
        rehearsal.get("passed") is not True or rehearsal.get("mutation_retries") != 0 or rehearsal.get("risk_limits_changed") is not False,
    )):
        raise ReadOnlyPreflightError("R2 Session 1 cancel/fill R0 boundary is invalid")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [relative for relative, expected in manifest.items() if not _hash_matches(output / relative, expected)]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [relative for relative, expected in sources.items() if not _hash_matches(root / relative, expected)]
    if failures or source_failures:
        raise ReadOnlyPreflightError("R2 Session 1 cancel/fill evidence or source hash mismatch")
    return {"passed": True, "evidence_kind": "r2_session1_cancel_fill_reconciliation_r0_offline_repair", "offline_run_id": evidence_id, "repair_id": evidence_id, "completion_files_checked": len(manifest), "source_files_checked": len(sources), "completion_hashes_sha256": _sha256(completion_path), "terminal_sha256": _sha256(terminal_path), "decision_sha256": _sha256(decision_path), "failed_package_id": predecessor.get("package_id"), "failed_campaign_run_id": predecessor.get("campaign_run_id"), "failed_session_package_id": predecessor.get("session_package_id"), "failed_campaign_decision": "UNRESOLVED_FAIL_CLOSED", "active_failed_slot": 1, "terminal_account_authoritative": False, "resume_authorized": False}


def _verify_r2_session5_terminal_reconciliation_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / R2_SESSION5_TERMINAL_RECONCILIATION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_R2_SESSION5_TERMINAL_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/r2_session5_terminal_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_session_audit.json"
    diagnostic_path = output / "diagnostic/root_cause.json"
    projection_path = output / "diagnostic/repair_projection.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
        diagnostic_path, projection_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "R2 Session 5 terminal reconciliation R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status")
        != R2_SESSION5_TERMINAL_RECONCILIATION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        terminal.get("production_authorized") is not False,
        decision.get("status")
        != R2_SESSION5_TERMINAL_RECONCILIATION_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders", "mutation_retries",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("completed_slots") != [1, 2, 3, 4],
        predecessor.get("active_failed_slot") != 5,
        predecessor.get("sessions_6_through_12_started") is not False,
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("terminal_account_authoritative") is not False,
        predecessor.get("mutation_retries") != 0,
        predecessor.get("live_endpoint_attempts") != 0,
        predecessor.get("live_orders") != 0,
        diagnostic.get("passed") is not True,
        diagnostic.get("primary_fetch_account_retry_rows") != 3,
        diagnostic.get("shutdown_fetch_account_retry_rows") != 1,
        diagnostic.get("flatten_intents") != 1,
        diagnostic.get("flatten_acknowledged_events") != 0,
        diagnostic.get("repair", {}).get(
            "flatten_requires_durable_cancel_convergence"
        ) is not True,
        diagnostic.get("risk_expansion") is not False,
        projection.get("passed") is not True,
        projection.get("read_attempts") != 3,
        projection.get("mutation_retries") != 0,
        projection.get("risk_limits_changed") is not False,
        len(projection.get("fixtures") or []) != 3,
    )):
        raise ReadOnlyPreflightError(
            "R2 Session 5 terminal reconciliation R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "R2 Session 5 terminal reconciliation evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": (
            "r2_session5_terminal_reconciliation_r0_offline_repair"
        ),
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("session_package_id"),
        "failed_campaign_decision": "UNRESOLVED_FAIL_CLOSED",
        "active_failed_slot": 5,
        "terminal_account_authoritative": False,
        "resume_authorized": False,
    }


def _verify_transport_resilience_evidence(root: Path, evidence_id: str) -> dict[str, object]:
    output = root / TRANSPORT_RESILIENCE_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_TRANSPORT_RESILIENCE_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/transport_r0_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_preflight_audit.json"
    required = (terminal_path, decision_path, completion_path, source_path, tests_path, targeted_path, endpoint_path, secret_path, predecessor_path)
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("transport-resilience R0 evidence is incomplete")
    terminal, decision = _read_json(terminal_path), _read_json(decision_path)
    tests, targeted = _read_json(tests_path), _read_json(targeted_path)
    endpoint, secret, predecessor = _read_json(endpoint_path), _read_json(secret_path), _read_json(predecessor_path)
    suites = ("root_non_optuna", "backtest_non_optuna")
    zero_keys = ("network_attempts", "credential_reads", "demo_endpoint_attempts", "live_endpoint_attempts", "create_attempts", "amend_attempts", "cancel_attempts", "flatten_attempts", "account_configuration_attempts", "orders", "mutation_retries")
    if any((
        terminal.get("status") != TRANSPORT_RESILIENCE_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        decision.get("status") != TRANSPORT_RESILIENCE_READY_STATUS,
        any(tests.get(name, {}).get("passed_gate") is not True or tests.get(name, {}).get("returncode") != 0 or tests.get(name, {}).get("network_attempts") != 0 or tests.get(name, {}).get("live_endpoint_attempts") != 0 or tests.get(name, {}).get("optuna_imported") is not False for name in suites),
        targeted.get("passed_gate") is not True, targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0, targeted.get("live_endpoint_attempts") != 0,
        endpoint.get("socket_denied") is not True, any(endpoint.get(key) != 0 for key in zero_keys),
        secret.get("passed") is not True, secret.get("credential_environment_accessed") is not False,
        predecessor.get("immutable") is not True,
        predecessor.get("run_id") != "preflight-20260827T141951Z",
        predecessor.get("terminal_decision") != "READ_ONLY_PREFLIGHT_FAILED",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("identity_reuse_authorized") is not False,
    )):
        raise ReadOnlyPreflightError("transport-resilience R0 boundary is invalid")
    manifest = _read_json(completion_path)
    sources = _read_json(source_path)
    if any(not _hash_matches(output / relative, expected) for relative, expected in manifest.items()) or any(not _hash_matches(root / relative, expected) for relative, expected in sources.items()):
        raise ReadOnlyPreflightError("transport-resilience R0 evidence or source hash mismatch")
    return {"passed": True, "evidence_kind": "transport_resilience_r0_offline_repair", "offline_run_id": evidence_id, "repair_id": evidence_id, "completion_files_checked": len(manifest), "source_files_checked": len(sources), "completion_hashes_sha256": _sha256(completion_path), "terminal_sha256": _sha256(terminal_path), "decision_sha256": _sha256(decision_path), "failed_preparation_id": predecessor.get("preparation_id"), "failed_run_id": predecessor.get("run_id"), "failed_campaign_decision": "NOT_READY", "resume_authorized": False}


def _verify_execution_environment_transport_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / EXECUTION_ENVIRONMENT_TRANSPORT_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_EXECUTION_ENVIRONMENT_TRANSPORT_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/execution_environment_transport_r0_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_preflight_audit.json"
    transport_path = output / "diagnostic/authorized_transport_probe_summary.json"
    required = (terminal_path, decision_path, completion_path, source_path, tests_path,
                targeted_path, endpoint_path, secret_path, predecessor_path, transport_path)
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("execution-environment transport R0 evidence is incomplete")
    terminal, decision = _read_json(terminal_path), _read_json(decision_path)
    tests, targeted = _read_json(tests_path), _read_json(targeted_path)
    endpoint, secret = _read_json(endpoint_path), _read_json(secret_path)
    predecessor, transport = _read_json(predecessor_path), _read_json(transport_path)
    suites = ("root_non_optuna", "backtest_non_optuna")
    zero_keys = ("network_attempts", "credential_reads", "demo_endpoint_attempts",
                 "live_endpoint_attempts", "create_attempts", "amend_attempts",
                 "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
                 "orders", "mutation_retries")
    if any((
        terminal.get("status") != EXECUTION_ENVIRONMENT_TRANSPORT_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        decision.get("status") != EXECUTION_ENVIRONMENT_TRANSPORT_READY_STATUS,
        any(tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False for name in suites),
        targeted.get("passed_gate") is not True or targeted.get("returncode") != 0
            or targeted.get("network_attempts") != 0
            or targeted.get("live_endpoint_attempts") != 0,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in zero_keys),
        secret.get("passed") is not True or secret.get("credential_environment_accessed") is not False,
        predecessor.get("immutable") is not True,
        predecessor.get("run_id") != "preflight-20260828T140810Z",
        predecessor.get("terminal_decision") != "READ_ONLY_PREFLIGHT_FAILED",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("identity_reuse_authorized") is not False,
        transport.get("http_request_sent") is not False,
        transport.get("credentials_accessed") is not False,
        transport.get("account_api_called") is not False,
        transport.get("ipv6_tcp_tls_result") != "passed",
        transport.get("probe_replayed_during_R0") is not False,
    )):
        raise ReadOnlyPreflightError("execution-environment transport R0 boundary is invalid")
    manifest, sources = _read_json(completion_path), _read_json(source_path)
    if any(not _hash_matches(output / relative, expected) for relative, expected in manifest.items()) or any(not _hash_matches(root / relative, expected) for relative, expected in sources.items()):
        raise ReadOnlyPreflightError("execution-environment transport evidence or source hash mismatch")
    return {"passed": True, "evidence_kind": "execution_environment_transport_r0_offline_repair",
            "offline_run_id": evidence_id, "repair_id": evidence_id,
            "completion_files_checked": len(manifest), "source_files_checked": len(sources),
            "completion_hashes_sha256": _sha256(completion_path),
            "terminal_sha256": _sha256(terminal_path), "decision_sha256": _sha256(decision_path),
            "failed_preparation_id": predecessor.get("preparation_id"),
            "failed_run_id": predecessor.get("run_id"), "failed_campaign_decision": "NOT_READY",
            "resume_authorized": False}


def _verify_preflight_market_bootstrap_terminal_reconciliation_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = (
        root / PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_ARTIFACT_ROOT
        / evidence_id
    )
    terminal_path = output / "R0_PREFLIGHT_MARKET_BOOTSTRAP_REPAIR_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/preflight_market_bootstrap_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_preflight_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "preflight market-bootstrap terminal-reconciliation R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    zero_keys = (
        "network_attempts", "credential_reads", "demo_endpoint_attempts",
        "live_endpoint_attempts", "create_attempts", "amend_attempts",
        "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
        "orders", "mutation_retries",
    )
    if any((
        terminal.get("status") != PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        decision.get("status") != PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in ("root_non_optuna", "backtest_non_optuna")
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in zero_keys),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("preparation_id") != "preflight-package-20260827T140714Z",
        predecessor.get("run_id") != "preflight-20260827T140714Z",
        predecessor.get("terminal_decision") != "READ_ONLY_PREFLIGHT_FAILED",
        predecessor.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        predecessor.get("terminal_account_authoritative") is not False,
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("identity_reuse_authorized") is not False,
        predecessor.get("mutation_attempts") != 0,
        predecessor.get("live_endpoint_attempts") != 0,
    )):
        raise ReadOnlyPreflightError(
            "preflight market-bootstrap terminal-reconciliation R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "preflight market-bootstrap terminal-reconciliation R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "preflight_market_bootstrap_terminal_reconciliation_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_preparation_id": predecessor.get("preparation_id"),
        "failed_run_id": predecessor.get("run_id"),
        "failed_session_id": predecessor.get("session_id"),
        "failed_campaign_decision": "NOT_READY",
        "resume_authorized": False,
    }


def _verify_market_bootstrap_terminal_reconciliation_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/market_bootstrap_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError(
            "market-bootstrap terminal-reconciliation R0 evidence is incomplete"
        )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    suites = ("root_non_optuna", "backtest_non_optuna")
    zero_keys = (
        "network_attempts", "credential_reads", "demo_endpoint_attempts",
        "live_endpoint_attempts", "create_attempts", "amend_attempts",
        "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
        "orders", "mutation_retries",
    )
    if any((
        terminal.get("status") != MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_repair_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        decision.get("status") != MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_READY_STATUS,
        decision.get("R0_offline_repair_passed") is not True,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in zero_keys),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "economic-package-20260827T134515Z",
        predecessor.get("campaign_run_id") != "economic-campaign-run-20260827T134515Z",
        predecessor.get("session_package_id") != "soak-package-20260827T134515Z-s01-b14cb83a52",
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("slots_2_through_12_started") is not False,
        predecessor.get("market_bootstrap_failed_before_account_state") is not True,
        predecessor.get("normal_creates") != 0,
        predecessor.get("mutation_call_count") != 0,
        predecessor.get("live_endpoint_attempts") != 0,
        predecessor.get("live_orders") != 0,
    )):
        raise ReadOnlyPreflightError(
            "market-bootstrap terminal-reconciliation R0 boundary is invalid"
        )
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "market-bootstrap terminal-reconciliation R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "market_bootstrap_terminal_reconciliation_r0_offline_repair",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_session_package_id": predecessor.get("session_package_id"),
        "failed_campaign_decision": "NOT_READY",
        "resume_authorized": False,
    }


def _verify_post_wall_interruption_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / POST_WALL_INTERRUPTION_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "R0_POST_WALL_INTERRUPTION_AUDIT_COMPLETED.json"
    decision_path = output / "decision/offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification/source_hashes.json"
    tests_path = output / "tests/test_summary.json"
    targeted_path = output / "tests/post_wall_targeted_summary.json"
    endpoint_path = output / "audits/endpoint_mutation_audit.json"
    secret_path = output / "audits/secret_scan.json"
    predecessor_path = output / "predecessor/failed_campaign_audit.json"
    transition_path = output / "diagnostic/interruption_transition_audit.json"
    economic_path = output / "diagnostic/economic_cohort_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path, tests_path,
        targeted_path, endpoint_path, secret_path, predecessor_path,
        transition_path, economic_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("post-wall interruption R0 evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    targeted = json.loads(targeted_path.read_text(encoding="utf-8"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    transition = json.loads(transition_path.read_text(encoding="utf-8"))
    economic = json.loads(economic_path.read_text(encoding="utf-8"))
    suites = ("post_campaign_targeted", "root_non_optuna", "backtest_non_optuna")
    if any((
        terminal.get("status") != POST_WALL_INTERRUPTION_READY_STATUS,
        terminal.get("evidence_id") != evidence_id,
        terminal.get("terminal_written_last") is not True,
        terminal.get("R0_offline_audit_passed") is not True,
        terminal.get("R1_preparation_authorized") is not False,
        terminal.get("preflight_authorized") is not False,
        terminal.get("economic_campaign_authorized") is not False,
        decision.get("status") != POST_WALL_INTERRUPTION_READY_STATUS,
        decision.get("R0_offline_audit_passed") is not True,
        decision.get("preflight_authorized") is not False,
        decision.get("economic_campaign_authorized") is not False,
        any(
            tests.get(name, {}).get("passed_gate") is not True
            or tests.get(name, {}).get("returncode") != 0
            or tests.get(name, {}).get("network_attempts") != 0
            or tests.get(name, {}).get("live_endpoint_attempts") != 0
            or tests.get(name, {}).get("optuna_imported") is not False
            for name in suites
        ),
        targeted.get("passed_gate") is not True,
        targeted.get("returncode") != 0,
        targeted.get("network_attempts") != 0,
        targeted.get("live_endpoint_attempts") != 0,
        targeted.get("optuna_imported") is not False,
        endpoint.get("socket_denied") is not True,
        any(endpoint.get(key) != 0 for key in (
            "network_attempts", "credential_reads", "demo_endpoint_attempts",
            "live_endpoint_attempts", "create_attempts", "amend_attempts",
            "cancel_attempts", "flatten_attempts", "account_configuration_attempts",
            "orders", "mutation_retries",
        )),
        secret.get("passed") is not True,
        secret.get("credential_environment_accessed") is not False,
        secret.get("credentials_serialized") is not False,
        bool(secret.get("secret_pattern_matches")),
        predecessor.get("immutable") is not True,
        predecessor.get("terminal_decision") != "NOT_READY",
        predecessor.get("terminal_reason") != "CAMPAIGN_WALL_BUDGET",
        predecessor.get("resume_authorized") is not False,
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("completed_slots") != [1, 2, 3, 4, 5],
        predecessor.get("failed_slots") != [],
        predecessor.get("slots_6_through_12_started") is not False,
        predecessor.get("stale_lease_recovered") is not True,
        predecessor.get("completed_active_slot_ingested") is not True,
        predecessor.get("terminal_account_authoritative") is not True,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("normal_fill_count") != 43,
        predecessor.get("normal_fifo_round_trips") != 21,
        predecessor.get("normal_net_pnl_usdt") != "7.2687144",
        predecessor.get("special_flatten_sessions") != 1,
        predecessor.get("unclassified_quote_mode_ticks") != 0,
        predecessor.get("unsafe_sessions") != 0,
        transition.get("passed") is not True,
        transition.get("resume_same_campaign") is not False,
        transition.get("reuse_identities") is not False,
        transition.get("projection_promotable") is not False,
        len(list(transition.get("transitions") or [])) != 4,
        economic.get("sessions_observed") != 5,
        economic.get("normal_fill_count") != 43,
        economic.get("normal_fifo_round_trips") != 21,
        economic.get("economic_floors_observed") is not True,
        economic.get("twelve_session_campaign_complete") is not False,
        economic.get("promotable") is not False,
    )):
        raise ReadOnlyPreflightError("post-wall interruption R0 boundary is invalid")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in sources.items()
        if not _hash_matches(root / relative, expected)
    ]
    if failures or source_failures:
        raise ReadOnlyPreflightError(
            "post-wall interruption R0 evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "post_wall_interruption_r0_offline_audit",
        "offline_run_id": evidence_id,
        "repair_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "failed_package_id": predecessor.get("package_id"),
        "failed_campaign_run_id": predecessor.get("campaign_run_id"),
        "failed_campaign_decision": "NOT_READY",
        "terminal_reason": "CAMPAIGN_WALL_BUDGET",
        "completed_slots": [1, 2, 3, 4, 5],
        "resume_authorized": False,
    }


def _verify_multi_session_a0_evidence(
    root: Path, evidence_id: str
) -> dict[str, object]:
    output = root / MULTI_SESSION_A0_ARTIFACT_ROOT / evidence_id
    terminal_path = output / "A0_OFFLINE_BUILD_COMPLETED.json"
    decision_path = output / "decision" / "a0_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    formal_path = output / "predecessor" / "formal_audit.json"
    soak_path = output / "predecessor" / "bounded_soak_audit.json"
    failed_a2_path = output / "predecessor" / "failed_a2_audit.json"
    failed_a2_repair_path = (
        output / "predecessor" / "failed_a2_repair_audit.json"
    )
    failed_a2_clock_gate_path = (
        output / "predecessor" / "failed_a2_clock_gate_audit.json"
    )
    failed_a2_registry_projection_path = (
        output / "predecessor" / "failed_a2_registry_projection_audit.json"
    )
    failed_a2_causal_clock_path = (
        output / "predecessor" / "failed_a2_causal_clock_audit.json"
    )
    failed_a2_pre_dispatch_cross_path = (
        output / "predecessor" / "failed_a2_pre_dispatch_cross_audit.json"
    )
    interrupted_a2_path = (
        output / "predecessor" / "interrupted_a2_audit.json"
    )
    required = (
        terminal_path,
        decision_path,
        completion_path,
        source_path,
        formal_path,
        soak_path,
        failed_a2_path,
        failed_a2_repair_path,
        failed_a2_clock_gate_path,
        failed_a2_registry_projection_path,
        failed_a2_causal_clock_path,
        failed_a2_pre_dispatch_cross_path,
        interrupted_a2_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("multi-session A0 evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != MULTI_SESSION_A0_READY_STATUS
        or decision.get("status") != MULTI_SESSION_A0_READY_STATUS
    ):
        raise ReadOnlyPreflightError("multi-session A0 evidence is not ready")
    if any((
        terminal.get("evidence_id") != evidence_id,
        terminal.get("A0_passed") is not True,
        terminal.get("preflight_prepared") is not False,
        terminal.get("preflight_executed") is not False,
        terminal.get("economic_campaign_executed") is not False,
        terminal.get("operational_failure_campaign_executed") is not False,
        terminal.get("network_attempts") != 0,
        terminal.get("credential_accesses") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("orders_amended") != 0,
        terminal.get("orders_cancelled") != 0,
        terminal.get("production_authorized") is not False,
        terminal.get("live_mode_available") is not False,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
        terminal.get("failure_group_count") != 10,
    )):
        raise ReadOnlyPreflightError("multi-session A0 boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("multi-session A0 completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    successor = root / (
        "AGENTS_OKX_DEMO_MULTI_SESSION_ECONOMIC_SOAK_FAILURE_INJECTION.md"
    )
    if not (
        (root / "AGENTS.md").is_file()
        and successor.is_file()
        and _sha256(root / "AGENTS.md") == _sha256(successor)
    ):
        source_failures.append("AGENTS.md")
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    soak = json.loads(soak_path.read_text(encoding="utf-8"))
    failed_a2 = json.loads(failed_a2_path.read_text(encoding="utf-8"))
    failed_a2_repair = json.loads(
        failed_a2_repair_path.read_text(encoding="utf-8")
    )
    failed_a2_clock_gate = json.loads(
        failed_a2_clock_gate_path.read_text(encoding="utf-8")
    )
    failed_a2_registry_projection = json.loads(
        failed_a2_registry_projection_path.read_text(encoding="utf-8")
    )
    failed_a2_causal_clock = json.loads(
        failed_a2_causal_clock_path.read_text(encoding="utf-8")
    )
    failed_a2_pre_dispatch_cross = json.loads(
        failed_a2_pre_dispatch_cross_path.read_text(encoding="utf-8")
    )
    interrupted_a2 = json.loads(
        interrupted_a2_path.read_text(encoding="utf-8")
    )
    predecessor_failures: list[str] = []
    if any((
        formal.get("passed") is not True,
        formal.get("immutable") is not True,
        formal.get("package_id") != "formal-package-20260810T123953Z",
        formal.get("rerun_allowed") is not False,
        soak.get("passed") is not True,
        soak.get("immutable") is not True,
        soak.get("package_id") != "soak-package-20260811T140223Z",
        soak.get("economic_promotion_evidence") is not False,
        soak.get("rerun_allowed") is not False,
        failed_a2.get("passed") is not True,
        failed_a2.get("immutable") is not True,
        failed_a2.get("campaign_package_id")
        != "economic-package-20260811T164910Z",
        failed_a2.get("session_package_id")
        != "soak-package-20260811T164910Z-s01-1a69fe577c",
        failed_a2.get("historical_campaign_omitted_attempted_session")
        is not True,
        failed_a2.get("repair_requires_fresh_identifiers") is not True,
        failed_a2.get("rerun_or_resume_allowed") is not False,
        failed_a2_repair.get("passed") is not True,
        failed_a2_repair.get("immutable") is not True,
        failed_a2_repair.get("campaign_package_id")
        != "economic-package-20260812T082847Z",
        failed_a2_repair.get("session_package_id")
        != "soak-package-20260812T082847Z-s01-7a3c1b97ef",
        failed_a2_repair.get("historical_create_counter_mismatch") is not True,
        failed_a2_repair.get("historical_failed_accounting_components_omitted")
        is not True,
        failed_a2_repair.get("repair_requires_fresh_identifiers") is not True,
        failed_a2_repair.get("rerun_or_resume_allowed") is not False,
        failed_a2_clock_gate.get("passed") is not True,
        failed_a2_clock_gate.get("immutable") is not True,
        failed_a2_clock_gate.get("campaign_package_id")
        != "economic-package-20260812T103316Z",
        failed_a2_clock_gate.get("session_package_id")
        != "soak-package-20260812T103316Z-s01-671d71cd43",
        failed_a2_clock_gate.get("pre_mutation_failure") is not True,
        failed_a2_clock_gate.get("normal_create_dispatches") != 0,
        failed_a2_clock_gate.get("terminal_account_authoritative") is not True,
        failed_a2_clock_gate.get("repair_requires_fresh_identifiers") is not True,
        failed_a2_clock_gate.get("rerun_or_resume_allowed") is not False,
        failed_a2_registry_projection.get("passed") is not True,
        failed_a2_registry_projection.get("immutable") is not True,
        failed_a2_registry_projection.get("campaign_package_id")
        != "economic-package-20260812T144639Z",
        failed_a2_registry_projection.get("session_package_id")
        != "soak-package-20260812T144639Z-s01-0728ed7a96",
        failed_a2_registry_projection.get("source_evidence_seal_valid") is not True,
        failed_a2_registry_projection.get("registry_projection_seal_valid")
        is not False,
        failed_a2_registry_projection.get("session_two_never_authorized")
        is not True,
        failed_a2_registry_projection.get("session_two_never_started") is not True,
        failed_a2_registry_projection.get("terminal_account_authoritative")
        is not True,
        failed_a2_registry_projection.get("repair_requires_fresh_identifiers")
        is not True,
        failed_a2_registry_projection.get("rerun_or_resume_allowed") is not False,
        failed_a2_causal_clock.get("passed") is not True,
        failed_a2_causal_clock.get("immutable") is not True,
        failed_a2_causal_clock.get("campaign_package_id")
        != "economic-package-20260812T153839Z",
        failed_a2_causal_clock.get("session_package_id")
        != "soak-package-20260812T153839Z-s01-04d9bb3dce",
        failed_a2_causal_clock.get("future_fill_within_clock_skew_budget")
        is not True,
        failed_a2_causal_clock.get("clock_skew_ms") != [1307, 1311],
        failed_a2_causal_clock.get("maximum_clock_skew_ms") != 1500,
        failed_a2_causal_clock.get("historical_defense_timestamp_ms") != 0,
        failed_a2_causal_clock.get("session_two_never_authorized") is not True,
        failed_a2_causal_clock.get("session_two_never_started") is not True,
        failed_a2_causal_clock.get("normal_create_dispatches") != 2,
        failed_a2_causal_clock.get("normal_create_acknowledgements") != 2,
        failed_a2_causal_clock.get("normal_create_unresolved") != 0,
        failed_a2_causal_clock.get("mutation_retries") != 0,
        failed_a2_causal_clock.get("terminal_account_authoritative") is not True,
        failed_a2_causal_clock.get("terminal_position_btc") != "0",
        failed_a2_causal_clock.get("terminal_open_orders") != 0,
        failed_a2_causal_clock.get("two_flat_empty_snapshots") is not True,
        failed_a2_causal_clock.get("repair_requires_fresh_identifiers") is not True,
        failed_a2_causal_clock.get("rerun_or_resume_allowed") is not False,
        failed_a2_pre_dispatch_cross.get("passed") is not True,
        failed_a2_pre_dispatch_cross.get("immutable") is not True,
        failed_a2_pre_dispatch_cross.get("campaign_package_id")
        != "economic-package-20260813T133023Z",
        failed_a2_pre_dispatch_cross.get("session_package_id")
        != "soak-package-20260813T133023Z-s04-54411e2b57",
        failed_a2_pre_dispatch_cross.get(
            "historical_pre_dispatch_cross_treated_as_session_failure"
        ) is not True,
        failed_a2_pre_dispatch_cross.get("historical_pre_dispatch_mutation_delta")
        != 0,
        failed_a2_pre_dispatch_cross.get(
            "historical_attempted_aggregate_component_mismatch"
        ) is not True,
        failed_a2_pre_dispatch_cross.get(
            "historical_attempted_economic_attribution_reconciles"
        ) is not False,
        failed_a2_pre_dispatch_cross.get(
            "later_slots_never_authorized_or_started"
        ) is not True,
        failed_a2_pre_dispatch_cross.get("normal_create_dispatches") != 168,
        failed_a2_pre_dispatch_cross.get("normal_create_acknowledgements") != 168,
        failed_a2_pre_dispatch_cross.get("normal_create_unresolved") != 0,
        failed_a2_pre_dispatch_cross.get("mutation_retries") != 0,
        failed_a2_pre_dispatch_cross.get("terminal_account_authoritative")
        is not True,
        failed_a2_pre_dispatch_cross.get("terminal_position_btc") != "0",
        failed_a2_pre_dispatch_cross.get("terminal_open_orders") != 0,
        failed_a2_pre_dispatch_cross.get("two_flat_empty_snapshots") is not True,
        failed_a2_pre_dispatch_cross.get("repair_requires_fresh_identifiers")
        is not True,
        failed_a2_pre_dispatch_cross.get("rerun_or_resume_allowed") is not False,
        interrupted_a2.get("passed") is not True,
        interrupted_a2.get("immutable") is not True,
        interrupted_a2.get("interrupted") is not True,
        interrupted_a2.get("campaign_incomplete") is not True,
        interrupted_a2.get("campaign_package_id")
        != "economic-package-20260813T144426Z",
        interrupted_a2.get("session_package_id")
        != "soak-package-20260813T144426Z-s08-dd72c9e660",
        interrupted_a2.get("active_slot") != 8,
        interrupted_a2.get("completed_slots") != [1, 2, 3, 4, 5, 6, 7],
        interrupted_a2.get("failed_slots") != [],
        interrupted_a2.get("later_slots_never_authorized_or_started")
        is not True,
        interrupted_a2.get("authoritative_terminal_account_unknown")
        is not True,
        interrupted_a2.get("last_durable_local_inventory_btc") != "-0.010",
        interrupted_a2.get("last_durable_owned_orders") != 0,
        interrupted_a2.get("last_durable_pending_intent") is not False,
        interrupted_a2.get("normal_create_dispatches") != 31,
        interrupted_a2.get("normal_create_acknowledgements") != 31,
        interrupted_a2.get("normal_create_unresolved") != 0,
        interrupted_a2.get("completed_session_count") != 7,
        interrupted_a2.get("completed_normal_bid_fills") != 5,
        interrupted_a2.get("completed_normal_ask_fills") != 7,
        interrupted_a2.get("completed_normal_fifo_round_trips") != 4,
        interrupted_a2.get("completed_normal_net_pnl_usdt") != "0.4763120",
        interrupted_a2.get("completed_special_flatten_sessions") != 4,
        interrupted_a2.get(
            "same_generation_resume_rerun_or_recovery_allowed"
        ) is not False,
        interrupted_a2.get("fresh_read_only_preflight_required") is not True,
        interrupted_a2.get("fresh_identifiers_required") is not True,
    )):
        predecessor_failures.append("immutable_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "multi-session A0 evidence, source, or predecessor mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "multi_session_a0_offline_build",
        "offline_run_id": evidence_id,
        "evidence_id": evidence_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "soak_predecessor_verified": True,
        "failed_a2_predecessor_verified": True,
        "repair_failed_a2_predecessor_verified": True,
        "clock_gate_failed_a2_predecessor_verified": True,
        "registry_projection_failed_a2_predecessor_verified": True,
        "causal_clock_failed_a2_predecessor_verified": True,
        "pre_dispatch_cross_failed_a2_predecessor_verified": True,
        "interrupted_a2_predecessor_verified": True,
        "formal_predecessor_package_id": formal.get("package_id"),
        "formal_predecessor_run_id": formal.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": len(
            dict(formal.get("fixed_hashes") or {})
        ),
        "soak_predecessor_package_id": soak.get("package_id"),
        "soak_predecessor_run_id": soak.get("run_id"),
        "soak_predecessor_fixed_hashes_checked": len(
            dict(soak.get("fixed_hashes") or {})
        ),
        "failed_a2_campaign_package_id": failed_a2.get(
            "campaign_package_id"
        ),
        "failed_a2_fixed_hashes_checked": len(
            dict(failed_a2.get("fixed_hashes") or {})
        ),
        "repair_failed_a2_campaign_package_id": failed_a2_repair.get(
            "campaign_package_id"
        ),
        "repair_failed_a2_fixed_hashes_checked": len(
            dict(failed_a2_repair.get("fixed_hashes") or {})
        ),
        "clock_gate_failed_a2_campaign_package_id": failed_a2_clock_gate.get(
            "campaign_package_id"
        ),
        "clock_gate_failed_a2_fixed_hashes_checked": len(
            dict(failed_a2_clock_gate.get("fixed_hashes") or {})
        ),
        "registry_projection_failed_a2_campaign_package_id": (
            failed_a2_registry_projection.get("campaign_package_id")
        ),
        "registry_projection_failed_a2_fixed_hashes_checked": len(
            dict(failed_a2_registry_projection.get("fixed_hashes") or {})
        ),
        "causal_clock_failed_a2_campaign_package_id": (
            failed_a2_causal_clock.get("campaign_package_id")
        ),
        "causal_clock_failed_a2_fixed_hashes_checked": len(
            dict(failed_a2_causal_clock.get("fixed_hashes") or {})
        ),
        "pre_dispatch_cross_failed_a2_campaign_package_id": (
            failed_a2_pre_dispatch_cross.get("campaign_package_id")
        ),
        "pre_dispatch_cross_failed_a2_fixed_hashes_checked": len(
            dict(failed_a2_pre_dispatch_cross.get("fixed_hashes") or {})
        ),
        "interrupted_a2_campaign_package_id": interrupted_a2.get(
            "campaign_package_id"
        ),
        "interrupted_a2_session_package_id": interrupted_a2.get(
            "session_package_id"
        ),
        "interrupted_a2_fixed_hashes_checked": len(
            dict(interrupted_a2.get("fixed_hashes") or {})
        ),
        "interrupted_a2_authoritative_terminal_account_unknown": True,
        "interrupted_a2_last_durable_local_inventory_btc": "-0.010",
        "production_authorized": False,
    }


def _verify_shutdown_repair_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / SHUTDOWN_REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_SHUTDOWN_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "formal_run_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("shutdown repair evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != SHUTDOWN_REPAIR_READY_STATUS
        or decision.get("status") != SHUTDOWN_REPAIR_READY_STATUS
    ):
        raise ReadOnlyPreflightError("shutdown repair phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("shutdown repair boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("shutdown repair completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative
        for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures: list[str] = []
    for relative, expected in source_manifest.items():
        path = root / relative
        if relative == "AGENTS.md":
            successor = (
                root / "AGENTS_OKX_DEMO_ACTIVITY_BUDGET_SHUTDOWN_REPAIR.md"
            )
            try:
                active_successor = (
                    path.is_file()
                    and successor.is_file()
                    and _sha256(path) == _sha256(successor)
                )
            except OSError:
                active_successor = False
            if not active_successor:
                source_failures.append(relative)
        elif not _hash_matches(path, expected):
            source_failures.append(relative)
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = (
        root / ARTIFACT_ROOT / "formal-package-20260805T162735Z"
    )
    predecessor_failures = [
        relative
        for relative, expected in dict(predecessor.get("fixed_hashes") or {}).items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260805T162735Z",
        predecessor.get("formal_run_id") != "formal-20260805T162735Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not True,
        predecessor.get("R2_completed") is not False,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("rerun_allowed") is not False,
        len(dict(predecessor.get("fixed_hashes") or {})) != 4,
    )):
        predecessor_failures.append("formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "shutdown repair evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "activity_budget_shutdown_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "root_authority_transition_only": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 4,
    }


def _verify_future_book_repair_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / FUTURE_BOOK_REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_FUTURE_BOOK_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "formal_run_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("future-book repair evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != FUTURE_BOOK_REPAIR_READY_STATUS
        or decision.get("status") != FUTURE_BOOK_REPAIR_READY_STATUS
    ):
        raise ReadOnlyPreflightError("future-book repair phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("successor_protocol_active") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("future-book repair boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("future-book completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative
        for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative
        for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = (
        root / ARTIFACT_ROOT / "formal-package-20260806T152904Z"
    )
    fixed_hashes = dict(predecessor.get("fixed_hashes") or {})
    predecessor_failures = [
        relative
        for relative, expected in fixed_hashes.items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260806T152904Z",
        predecessor.get("formal_run_id") != "formal-20260806T152904Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not False,
        predecessor.get("R2_completed") is not False,
        predecessor.get("orders_submitted") != 0,
        predecessor.get("flatten_dispatches") != 0,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("rerun_allowed") is not False,
        len(fixed_hashes) != 5,
    )):
        predecessor_failures.append("formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "future-book repair evidence or source hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "future_book_timestamp_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 5,
    }


def _verify_r2_repair_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / R2_REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_R2_WARMUP_AUDIT_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "formal_run_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("R2 repair evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != R2_REPAIR_READY_STATUS
        or decision.get("status") != R2_REPAIR_READY_STATUS
    ):
        raise ReadOnlyPreflightError("R2 repair phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("successor_protocol_active") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("R2 repair boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("R2 repair completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = root / ARTIFACT_ROOT / "formal-package-20260807T130847Z"
    fixed_hashes = dict(predecessor.get("fixed_hashes") or {})
    predecessor_failures = [
        relative for relative, expected in fixed_hashes.items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260807T130847Z",
        predecessor.get("formal_run_id") != "formal-20260807T130847Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not True,
        predecessor.get("R2_completed") is not False,
        predecessor.get("normal_create_events") != 37,
        predecessor.get("authoritative_cancel_confirmations") != 35,
        predecessor.get("flatten_dispatches") != 0,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("kill_active_at_terminal") is not True,
        predecessor.get("rerun_allowed") is not False,
        len(fixed_hashes) != 5,
    )):
        predecessor_failures.append("formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "R2 repair evidence, source, or predecessor hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "r2_warmup_audit_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 5,
    }


def _verify_signed_age_repair_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / SIGNED_AGE_REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_SIGNED_AGE_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "formal_run_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("signed-age repair evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != SIGNED_AGE_REPAIR_READY_STATUS
        or decision.get("status") != SIGNED_AGE_REPAIR_READY_STATUS
    ):
        raise ReadOnlyPreflightError("signed-age repair phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("successor_protocol_active") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("signed-age repair boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("signed-age repair completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = root / ARTIFACT_ROOT / "formal-package-20260807T151814Z"
    fixed_hashes = dict(predecessor.get("fixed_hashes") or {})
    predecessor_failures = [
        relative for relative, expected in fixed_hashes.items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260807T151814Z",
        predecessor.get("formal_run_id") != "formal-20260807T151814Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        predecessor.get("reason") != "FormalSafetyError:MARKET_DATA_STALE",
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not False,
        predecessor.get("R2_completed") is not False,
        predecessor.get("normal_create_count") != 0,
        predecessor.get("external_order_submissions") != 0,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_owned_orders") != 0,
        predecessor.get("rerun_allowed") is not False,
        len(fixed_hashes) != 5,
    )):
        predecessor_failures.append("formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "signed-age repair evidence, source, or predecessor hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "signed_age_prearm_terminal_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 5,
    }


def _verify_r1_terminal_repair_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / R1_TERMINAL_REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_R1_TERMINAL_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "formal_run_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("R1/terminal repair evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != R1_TERMINAL_REPAIR_READY_STATUS
        or decision.get("status") != R1_TERMINAL_REPAIR_READY_STATUS
    ):
        raise ReadOnlyPreflightError("R1/terminal repair phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("successor_protocol_active") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("orders_amended") != 0,
        terminal.get("orders_cancelled") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("R1/terminal repair boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("R1/terminal completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = (
        root / ARTIFACT_ROOT / "formal-package-20260809T152113Z"
    )
    fixed_hashes = dict(predecessor.get("fixed_hashes") or {})
    predecessor_failures = [
        relative for relative, expected in fixed_hashes.items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260809T152113Z",
        predecessor.get("formal_run_id") != "formal-20260809T152113Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        predecessor.get("reason") != (
            "FormalSafetyError:R1 checkpoint requires no orders and nonzero position"
        ),
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not False,
        predecessor.get("R2_completed") is not False,
        predecessor.get("normal_create_count") != 49,
        predecessor.get("normal_cancel_confirmations") != 48,
        predecessor.get("normal_bid_fills") != 1,
        predecessor.get("normal_ask_fills") != 0,
        predecessor.get("flatten_dispatches") != 1,
        predecessor.get("flatten_fill_parts") != 2,
        predecessor.get("terminal_position_btc") != "0.0000",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("report_recovery_required") is not True,
        predecessor.get("rerun_allowed") is not False,
        len(fixed_hashes) != 5,
    )):
        predecessor_failures.append("formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "R1/terminal repair evidence, source, or predecessor hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "r1_terminal_reconciliation_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get(
            "source_manifest_sha256"
        ),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 5,
    }


def _verify_soak_failure_evidence(
    root: Path, repair_id: str
) -> dict[str, object]:
    output = root / SOAK_FAILURE_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_SOAK_FAILURE_COMPLETED.json"
    decision_path = output / "decision" / "offline_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_path = output / "predecessor" / "successful_formal_audit.json"
    required = (
        terminal_path, decision_path, completion_path, source_path,
        predecessor_path,
    )
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("soak failure-injection evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if (
        terminal.get("status") != SOAK_FAILURE_READY_STATUS
        or decision.get("status") != SOAK_FAILURE_READY_STATUS
    ):
        raise ReadOnlyPreflightError("soak failure-injection phase is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_passed") is not True,
        terminal.get("successor_protocol_active") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("preflight_prepared") is not False,
        terminal.get("preflight_executed") is not False,
        terminal.get("soak_executed") is not False,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("orders_amended") != 0,
        terminal.get("orders_cancelled") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("soak failure-injection boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("soak failure completion hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    completion_failures = [
        relative for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures = [
        relative for relative, expected in source_manifest.items()
        if not _hash_matches(root / relative, expected)
    ]
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor_package = (
        root / ARTIFACT_ROOT / "formal-package-20260810T123953Z"
    )
    fixed_hashes = dict(predecessor.get("fixed_hashes") or {})
    predecessor_failures = [
        relative for relative, expected in fixed_hashes.items()
        if not _hash_matches(predecessor_package / relative, expected)
    ]
    if any((
        predecessor.get("passed") is not True,
        predecessor.get("immutable") is not True,
        predecessor.get("package_id") != "formal-package-20260810T123953Z",
        predecessor.get("formal_run_id") != "formal-20260810T123953Z",
        predecessor.get("status") != "OKX_DEMO_FILL_RESTART_SUPPORT",
        predecessor.get("formal_execution_marker_count") != 1,
        predecessor.get("R1_completed") is not True,
        predecessor.get("R2_completed") is not True,
        predecessor.get("normal_create_count") != 44,
        predecessor.get("normal_cancel_count") != 42,
        predecessor.get("normal_bid_fills") != 1,
        predecessor.get("normal_ask_fills") != 1,
        predecessor.get("normal_fifo_round_trips") != 1,
        predecessor.get("flatten_dispatches") != 0,
        predecessor.get("terminal_position_btc") != "0",
        predecessor.get("terminal_open_orders") != 0,
        predecessor.get("rerun_allowed") is not False,
        len(fixed_hashes) != 6,
    )):
        predecessor_failures.append("successful_formal_predecessor_contract")
    if completion_failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError(
            "soak failure evidence, source, or predecessor hash mismatch"
        )
    return {
        "passed": True,
        "evidence_kind": "soak_failure_injection_readiness",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "formal_predecessor_verified": True,
        "formal_predecessor_package_id": predecessor.get("package_id"),
        "formal_predecessor_run_id": predecessor.get("formal_run_id"),
        "formal_predecessor_fixed_hashes_checked": 6,
    }


def _verify_repair_evidence(root: Path, repair_id: str) -> dict[str, object]:
    output = root / REPAIR_ARTIFACT_ROOT / repair_id
    terminal_path = output / "OFFLINE_REPAIR_COMPLETED.json"
    decision_path = output / "decision" / "offline_repair_decision.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    predecessor_audit_path = output / "predecessor" / "failed_formal_audit.json"
    required = (terminal_path, decision_path, completion_path, source_path)
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("repair completion evidence is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if terminal.get("status") != REPAIR_READY_STATUS:
        raise ReadOnlyPreflightError("repair phase is not ready")
    if decision.get("status") != REPAIR_READY_STATUS:
        raise ReadOnlyPreflightError("repair decision is not ready")
    if any((
        terminal.get("repair_id") != repair_id,
        terminal.get("offline_repair_passed") is not True,
        terminal.get("fresh_preflight_required") is not True,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        terminal.get("optuna_executed") is not False,
        terminal.get("validation_opened") is not False,
        terminal.get("holdout_opened") is not False,
        terminal.get("git_write_operation") is not False,
    )):
        raise ReadOnlyPreflightError("repair completion boundary is invalid")
    completion_sha256 = _sha256(completion_path)
    if completion_sha256 != terminal.get("completion_hashes_sha256"):
        raise ReadOnlyPreflightError("repair completion manifest hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative
        for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    source_failures: list[str] = []
    for relative, expected in source_manifest.items():
        path = root / relative
        if relative == "AGENTS.md":
            successor = root / "AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md"
            try:
                active_successor = (
                    path.is_file()
                    and successor.is_file()
                    and _sha256(path) == _sha256(successor)
                )
            except OSError:
                active_successor = False
            if not active_successor:
                source_failures.append(relative)
        elif not _hash_matches(path, expected):
            source_failures.append(relative)
    predecessor_failures: list[str] = []
    try:
        predecessor_audit = json.loads(
            predecessor_audit_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        predecessor_audit = {}
        predecessor_failures.append("failed_formal_audit.json")
    failed_package = (
        root / ARTIFACT_ROOT / "formal-package-20260805T151737Z"
    )
    for relative, expected in dict(predecessor_audit.get("fixed_hashes") or {}).items():
        if not _hash_matches(failed_package / relative, expected):
            predecessor_failures.append(relative)
    if any((
        predecessor_audit.get("passed") is not True,
        predecessor_audit.get("immutable") is not True,
        predecessor_audit.get("package_id") != "formal-package-20260805T151737Z",
        predecessor_audit.get("formal_run_id") != "formal-20260805T151737Z",
        predecessor_audit.get("status")
        != "OKX_DEMO_FILL_RESTART_RECONCILIATION_FAILED",
        predecessor_audit.get("formal_execution_marker_count") != 1,
        predecessor_audit.get("R1_completed") is not False,
        predecessor_audit.get("R2_completed") is not False,
        predecessor_audit.get("terminal_position_btc") != "0",
        predecessor_audit.get("terminal_open_orders") != 0,
        len(dict(predecessor_audit.get("fixed_hashes") or {})) != 4,
    )):
        predecessor_failures.append("failed_formal_contract")
    if failures or source_failures or predecessor_failures:
        raise ReadOnlyPreflightError("repair evidence or source hash mismatch")
    return {
        "passed": True,
        "evidence_kind": "fill_cursor_repair",
        "offline_run_id": repair_id,
        "repair_id": repair_id,
        "completion_files_checked": len(manifest),
        "source_files_checked": len(source_manifest),
        "completion_hashes_sha256": completion_sha256,
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "repair_source_manifest_sha256": terminal.get("source_manifest_sha256"),
        "successor_protocol_active": True,
        "root_authority_transition_only": True,
        "failed_formal_predecessor_verified": True,
        "failed_formal_fixed_hashes_checked": 4,
    }


def preflight_source_hashes(root: Path) -> dict[str, str]:
    hashes = source_hashes(root)
    for relative in PREFLIGHT_SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise ReadOnlyPreflightError(f"preflight source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def verify_preflight_preparation(
    root: Path,
    *,
    preparation_id: str,
    run_id: str,
    offline_run_id: str,
    session_id: str,
    arm_token: str,
) -> dict[str, object]:
    output = root / PREPARATION_ARTIFACT_ROOT / preparation_id
    terminal_path = output / "PREFLIGHT_PREPARATION_COMPLETED.json"
    spec_path = output / "specification" / "preflight_preparation.json"
    source_path = output / "specification" / "source_hashes.json"
    completion_path = output / "completion_hashes.json"
    required = (terminal_path, spec_path, source_path, completion_path)
    if not all(path.is_file() for path in required):
        raise ReadOnlyPreflightError("preflight preparation package is incomplete")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    expected_token_hash = hashlib.sha256(arm_token.encode("utf-8")).hexdigest()
    if any((
        terminal.get("status") != "READ_ONLY_PREFLIGHT_PACKAGE_READY",
        terminal.get("preparation_id") != preparation_id,
        terminal.get("run_id") != run_id,
        terminal.get("preflight_executed") is not False,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        spec.get("preparation_id") != preparation_id,
        spec.get("run_id") != run_id,
        spec.get("repair_id") != offline_run_id,
        spec.get("session_id") != session_id,
        spec.get("arm_token_sha256") != expected_token_hash,
        spec.get("arm_token_serialized") is not False,
    )):
        raise ReadOnlyPreflightError("preflight preparation binding is invalid")
    completion_sha256 = _sha256(completion_path)
    if terminal.get("completion_hashes_sha256") != completion_sha256:
        raise ReadOnlyPreflightError("preflight preparation manifest hash mismatch")
    manifest = json.loads(completion_path.read_text(encoding="utf-8"))
    failures = [
        relative
        for relative, expected in manifest.items()
        if not _hash_matches(output / relative, expected)
    ]
    frozen_sources = json.loads(source_path.read_text(encoding="utf-8"))
    current_sources = preflight_source_hashes(root)
    if failures or frozen_sources != current_sources:
        raise ReadOnlyPreflightError("preflight preparation source hash mismatch")
    repair = verify_offline_evidence(root, offline_run_id)
    if spec.get("repair_completion_sha256") != repair["completion_hashes_sha256"]:
        raise ReadOnlyPreflightError("preflight preparation repair binding drifted")
    if spec.get("predecessor_evidence_kind") != repair.get("evidence_kind"):
        raise ReadOnlyPreflightError("preflight preparation evidence kind drifted")
    if (
        repair.get("evidence_kind") in {
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
        }
        and spec.get("protocol_id")
        != "okx-demo-multi-session-a1-preflight-preparation-v1"
    ):
        raise ReadOnlyPreflightError("multi-session A1 preparation protocol mismatch")
    return {
        "passed": True,
        "preparation_id": preparation_id,
        "run_id": run_id,
        "session_id": session_id,
        "repair_id": offline_run_id,
        "completion_files_checked": len(manifest),
        "completion_hashes_sha256": completion_sha256,
        "source_manifest_sha256": canonical_sha256(current_sources),
        "arm_token_sha256": expected_token_hash,
        "arm_token_serialized": False,
    }


def _preflight_predecessor_audit(
    root: Path, offline: dict[str, object]
) -> dict[str, object]:
    evidence_kind = offline.get("evidence_kind")
    if evidence_kind == "multi_session_a0_offline_build":
        return {
            "passed": bool(
                offline.get("formal_predecessor_verified") is True
                and offline.get("soak_predecessor_verified") is True
                and offline.get("failed_a2_predecessor_verified") is True
                and offline.get("repair_failed_a2_predecessor_verified") is True
                and offline.get("clock_gate_failed_a2_predecessor_verified") is True
                and offline.get(
                    "registry_projection_failed_a2_predecessor_verified"
                ) is True
                and offline.get(
                    "causal_clock_failed_a2_predecessor_verified"
                ) is True
                and offline.get(
                    "pre_dispatch_cross_failed_a2_predecessor_verified"
                ) is True
                and offline.get("interrupted_a2_predecessor_verified") is True
            ),
            "authority": (
                "immutable_formal_bounded_soak_six_failed_and_one_interrupted_A2_generation_via_multi_session_A0_evidence"
            ),
            "formal_package_id": offline.get("formal_predecessor_package_id"),
            "formal_run_id": offline.get("formal_predecessor_run_id"),
            "formal_fixed_hashes_checked": offline.get(
                "formal_predecessor_fixed_hashes_checked"
            ),
            "soak_package_id": offline.get("soak_predecessor_package_id"),
            "soak_run_id": offline.get("soak_predecessor_run_id"),
            "soak_fixed_hashes_checked": offline.get(
                "soak_predecessor_fixed_hashes_checked"
            ),
            "failed_a2_package_id": offline.get(
                "failed_a2_campaign_package_id"
            ),
            "failed_a2_fixed_hashes_checked": offline.get(
                "failed_a2_fixed_hashes_checked"
            ),
            "repair_failed_a2_package_id": offline.get(
                "repair_failed_a2_campaign_package_id"
            ),
            "repair_failed_a2_fixed_hashes_checked": offline.get(
                "repair_failed_a2_fixed_hashes_checked"
            ),
            "clock_gate_failed_a2_package_id": offline.get(
                "clock_gate_failed_a2_campaign_package_id"
            ),
            "clock_gate_failed_a2_fixed_hashes_checked": offline.get(
                "clock_gate_failed_a2_fixed_hashes_checked"
            ),
            "registry_projection_failed_a2_package_id": offline.get(
                "registry_projection_failed_a2_campaign_package_id"
            ),
            "registry_projection_failed_a2_fixed_hashes_checked": offline.get(
                "registry_projection_failed_a2_fixed_hashes_checked"
            ),
            "causal_clock_failed_a2_package_id": offline.get(
                "causal_clock_failed_a2_campaign_package_id"
            ),
            "causal_clock_failed_a2_fixed_hashes_checked": offline.get(
                "causal_clock_failed_a2_fixed_hashes_checked"
            ),
            "pre_dispatch_cross_failed_a2_package_id": offline.get(
                "pre_dispatch_cross_failed_a2_campaign_package_id"
            ),
            "pre_dispatch_cross_failed_a2_fixed_hashes_checked": offline.get(
                "pre_dispatch_cross_failed_a2_fixed_hashes_checked"
            ),
            "interrupted_a2_package_id": offline.get(
                "interrupted_a2_campaign_package_id"
            ),
            "interrupted_a2_session_package_id": offline.get(
                "interrupted_a2_session_package_id"
            ),
            "interrupted_a2_fixed_hashes_checked": offline.get(
                "interrupted_a2_fixed_hashes_checked"
            ),
            "interrupted_a2_authoritative_terminal_account_unknown": (
                offline.get(
                    "interrupted_a2_authoritative_terminal_account_unknown"
                )
            ),
            "interrupted_a2_last_durable_local_inventory_btc": offline.get(
                "interrupted_a2_last_durable_local_inventory_btc"
            ),
        }
    if evidence_kind == "sample_efficiency_r0_offline_repair":
        return {
            "passed": offline.get("economic_predecessor_verified") is True,
            "authority": "immutable_insufficient_evidence_A2_via_sample_efficiency_R0",
            "package_id": offline.get("economic_predecessor_package_id"),
            "campaign_id": offline.get("economic_predecessor_campaign_id"),
        }
    if evidence_kind == "r2_post_start_terminal_recovery_offline_repair":
        return {
            "passed": bool(
                offline.get("failed_campaign_decision") == "NOT_READY"
                and offline.get("account_confirmation_is_exchange_authoritative")
                is False
            ),
            "authority": (
                "immutable_failed_post_start_campaign_via_terminal_recovery_evidence"
            ),
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "terminal_causal_cli_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_terminal_causal_cli_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "fifo_attribution_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_fifo_attribution_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "workoff_timestamp_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_workoff_timestamp_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "terminal_special_closure_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_terminal_special_closure_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "markout_special_closure_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_markout_special_closure_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "owned_cancel_reconciliation_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_campaign_via_owned_cancel_reconciliation_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "r2_session5_terminal_reconciliation_r0_offline_repair":
        return {
            "passed": bool(
                offline.get("failed_campaign_decision") == "UNRESOLVED_FAIL_CLOSED"
                and offline.get("active_failed_slot") == 5
                and offline.get("terminal_account_authoritative") is False
                and offline.get("resume_authorized") is False
            ),
            "authority": (
                "immutable_unresolved_R2_session5_via_terminal_reconciliation_R0"
            ),
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "r2_session1_cancel_fill_reconciliation_r0_offline_repair":
        return {
            "passed": bool(
                offline.get("failed_campaign_decision") == "UNRESOLVED_FAIL_CLOSED"
                and offline.get("active_failed_slot") == 1
                and offline.get("terminal_account_authoritative") is False
                and offline.get("resume_authorized") is False
            ),
            "authority": "immutable_unresolved_R2_session1_via_cancel_fill_reconciliation_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "post_wall_interruption_r0_offline_audit":
        return {
            "passed": bool(
                offline.get("failed_campaign_decision") == "NOT_READY"
                and offline.get("terminal_reason") == "CAMPAIGN_WALL_BUDGET"
                and offline.get("completed_slots") == [1, 2, 3, 4, 5]
                and offline.get("resume_authorized") is False
            ),
            "authority": "immutable_post_wall_campaign_via_interruption_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "market_bootstrap_terminal_reconciliation_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_pre_market_failed_campaign_via_terminal_reconciliation_R0",
            "package_id": offline.get("failed_package_id"),
            "campaign_run_id": offline.get("failed_campaign_run_id"),
            "session_package_id": offline.get("failed_session_package_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "preflight_market_bootstrap_terminal_reconciliation_r0_offline_repair":
        return {
            "passed": offline.get("failed_campaign_decision") == "NOT_READY",
            "authority": "immutable_failed_R1_preflight_via_market_bootstrap_terminal_reconciliation_R0",
            "preparation_id": offline.get("failed_preparation_id"),
            "run_id": offline.get("failed_run_id"),
            "session_id": offline.get("failed_session_id"),
            "resume_authorized": False,
        }
    if evidence_kind == "transport_resilience_r0_offline_repair":
        return {"passed": offline.get("failed_campaign_decision") == "NOT_READY", "authority": "immutable_failed_R1_preflight_via_transport_resilience_R0", "preparation_id": offline.get("failed_preparation_id"), "run_id": offline.get("failed_run_id"), "resume_authorized": False}
    if evidence_kind == "execution_environment_transport_r0_offline_repair":
        return {"passed": offline.get("failed_campaign_decision") == "NOT_READY", "authority": "immutable_sandbox_blocked_R1_preflight_via_execution_environment_transport_R0", "preparation_id": offline.get("failed_preparation_id"), "run_id": offline.get("failed_run_id"), "resume_authorized": False}
    if evidence_kind in {
        "activity_budget_shutdown_repair",
        "future_book_timestamp_repair",
        "r2_warmup_audit_repair",
        "signed_age_prearm_terminal_repair",
        "r1_terminal_reconciliation_repair",
        "soak_failure_injection_readiness",
    }:
        return {
            "passed": offline.get("formal_predecessor_verified") is True,
            "authority": "immutable_formal_via_successor_repair_evidence",
            "package_id": offline.get("formal_predecessor_package_id"),
            "formal_run_id": offline.get("formal_predecessor_run_id"),
            "fixed_hashes_checked": offline.get(
                "formal_predecessor_fixed_hashes_checked"
            ),
        }
    if evidence_kind == "fill_cursor_repair":
        return {
            "passed": offline.get("failed_formal_predecessor_verified") is True,
            "authority": "immutable_failed_formal_via_repair_evidence",
            "package_id": "formal-package-20260805T151737Z",
            "formal_run_id": "formal-20260805T151737Z",
            "fixed_hashes_checked": offline.get(
                "failed_formal_fixed_hashes_checked"
            ),
        }
    return verify_predecessors(root)


def _snapshot_key(snapshot: Any, account_binding: str) -> str:
    value = asdict(snapshot)
    value["account_uid"] = account_binding
    # Clock skew is a per-request health measurement; both observations must
    # stay within the frozen limit but need not be numerically identical.
    value.pop("clock_skew_ms", None)
    value["open_orders"] = canonical_sha256(list(snapshot.open_orders))
    value["recent_trades"] = canonical_sha256(list(snapshot.recent_trades))
    return canonical_sha256(value)


def _market_public(value: dict[str, Any]) -> dict[str, object]:
    return {
        "timestamp_ms": value["timestamp"],
        "best_bid": value["best_bid"],
        "best_ask": value["best_ask"],
        "mid_price": value["mid_price"],
        "age_ms": value["age_ms"],
    }


def _secret_scan(output: Path, secret_values: tuple[str, ...]) -> dict[str, object]:
    values = [value.encode("utf-8") for value in secret_values if len(value) >= 8]
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
            continue
        scanned += 1
        raw = path.read_bytes()
        if any(value in raw for value in values):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_matches": sorted(set(matches)),
        "credential_values_reported": False,
    }


ACCOUNT_ONLY_DIAGNOSTIC_KEYS = (
    "signed_position_btc",
    "position_row_count",
    "open_order_count",
    "account_binding_sha256",
    "position_identifiers_sha256",
    "open_order_identifiers_sha256",
)


def _sanitized_account_only_diagnostic(adapter: Any | None) -> dict[str, object] | None:
    raw = getattr(adapter, "last_account_only_diagnostic", None)
    if not isinstance(raw, dict) or any(key not in raw for key in ACCOUNT_ONLY_DIAGNOSTIC_KEYS):
        return None
    try:
        signed_position = format(Decimal(str(raw["signed_position_btc"])), "f")
    except (InvalidOperation, ValueError):
        return None
    counts = (raw["position_row_count"], raw["open_order_count"])
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    hashes = tuple(str(raw[key]) for key in ACCOUNT_ONLY_DIAGNOSTIC_KEYS[3:])
    if any(
        len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
        for value in hashes
    ):
        return None
    return {
        "signed_position_btc": signed_position,
        "position_row_count": counts[0],
        "open_order_count": counts[1],
        "account_binding_sha256": hashes[0],
        "position_identifiers_sha256": hashes[1],
        "open_order_identifiers_sha256": hashes[2],
    }


def _pre_market_terminal_account_pair(
    proxy: ReadOnlyExchangeProxy,
) -> tuple[dict[str, object], dict[str, object]]:
    """Prove terminal flat/empty state without CCXT market metadata."""
    if proxy.mutation_attempts != 0 or proxy.live_endpoint_attempts != 0:
        raise ReadOnlyPreflightError(
            "terminal reconciliation refused after unsafe transport activity"
        )
    gateway = FormalDemoGateway(proxy)
    try:
        first = gateway.fetch_terminal_account_only()
        second = gateway.fetch_terminal_account_only()
    except Exception as exc:
        partial = gateway.last_terminal_partial_diagnostic
        if partial is not None:
            setattr(exc, "terminal_account_partial_diagnostic", dict(partial))
        raise
    for snapshot in (first, second):
        if snapshot.position_btc != 0 or snapshot.open_orders:
            raise ReadOnlyPreflightError("terminal account is not flat and empty")
        if snapshot.clock_skew_ms > MAXIMUM_CLOCK_SKEW_MS:
            raise ReadOnlyPreflightError("terminal account clock skew exceeds budget")
    if first.account_binding != second.account_binding:
        raise ReadOnlyPreflightError("terminal account binding changed")
    return first.public_dict(), second.public_dict()


def _read_error_category(error: BaseException) -> str:
    """Return a stable, non-sensitive diagnostic category for read failures."""
    return str(_read_error_detail(error)["category"])


def run_read_only_preflight(
    *,
    root: Path,
    run_id: str,
    offline_run_id: str,
    preparation_id: str,
    session_id: str,
    arm_token: str,
) -> Path:
    root = root.resolve()
    if not run_id.startswith("preflight-") or any(ch.isspace() for ch in run_id):
        raise ReadOnlyPreflightError("preflight run_id is invalid")
    if arm_token != expected_arm_token(session_id):
        raise ReadOnlyPreflightError("session-scoped preflight arm token mismatch")
    output = root / ARTIFACT_ROOT / run_id
    if output.exists():
        raise ReadOnlyPreflightError("preflight run-ID reuse refused")

    offline = verify_offline_evidence(root, offline_run_id)
    predecessor = _preflight_predecessor_audit(root, offline)
    preparation = verify_preflight_preparation(
        root,
        preparation_id=preparation_id,
        run_id=run_id,
        offline_run_id=offline_run_id,
        session_id=session_id,
        arm_token=arm_token,
    )
    if not predecessor.get("passed") or not offline.get("passed"):
        raise ReadOnlyPreflightError("predecessor or offline evidence gate failed")
    hashes = preflight_source_hashes(root)
    output.mkdir(parents=True)
    protocol_id = preflight_protocol_id_for_evidence(offline.get("evidence_kind"))
    arm_marker = {
        "protocol_id": protocol_id,
        "run_id": run_id,
        "session_id": session_id,
        "armed_at_utc": datetime.now(timezone.utc).isoformat(),
        "arm_token_sha256": hashlib.sha256(arm_token.encode("utf-8")).hexdigest(),
        "arm_token_serialized": False,
        "scope": "READ_ONLY_OKX_DEMO_PREFLIGHT",
        "orders_authorized": False,
        "formal_execution_authorized": False,
    }
    _write_json(output / "READ_ONLY_PREFLIGHT_ARMED.json", arm_marker)
    _write_json(output / "predecessor" / "hash_audit.json", predecessor)
    _write_json(output / "predecessor" / "offline_evidence_audit.json", offline)
    _write_json(output / "predecessor" / "preflight_preparation_audit.json", preparation)
    _write_json(output / "specification" / "source_hashes.json", hashes)
    preflight_spec = {
        "protocol_id": protocol_id,
        "run_id": run_id,
        "preparation_id": preparation_id,
        "session_id": session_id,
        "execution_mode": "OKX_DEMO",
        "scope": "READ_ONLY_PREFLIGHT",
        "profile_id": "mm-v1-6-profile-02",
        "profile_name": "FEE_AWARE_SPREAD_6",
        "symbol": "BTC/USDT:USDT",
        "market_type": "linear_swap",
        "margin_mode": "isolated",
        "position_mode": "net_mode",
        "leverage": 3,
        "risk_budget": RiskBudget().to_dict(),
        "required_permissions": ["read_only", "trade"],
        "prohibited_permissions": ["withdraw"],
        "required_snapshots": 2,
        "mutations_allowed": False,
        "orders_allowed": False,
        "formal_execution_authorized": False,
        "source_manifest_sha256": canonical_sha256(hashes),
        "offline_evidence": offline,
        "preflight_preparation": preparation,
    }
    preflight_spec["specification_sha256"] = canonical_sha256(preflight_spec)
    _write_json(output / "specification" / "preflight_spec.json", preflight_spec)

    exchange: Any | None = None
    proxy: ReadOnlyExchangeProxy | None = None
    adapter: Any | None = None
    market_bootstrap_completed = False
    credentials: tuple[str, str, str] = ("", "", "")
    result: dict[str, object]
    try:
        config = _credential_config()
        credentials = (config.api_key, config.api_secret, config.api_passphrase)
        exchange = build_ccxt_demo_exchange(
            api_key=config.api_key,
            api_secret=config.api_secret,
            passphrase=config.api_passphrase,
        )
        proxy = ReadOnlyExchangeProxy(exchange)
        from okx_demo_profile import load_promoted_profile

        profile = load_promoted_profile(root)
        adapter = _adapter(
            exchange=proxy,
            profile=profile,
            session_id=session_id,
            state_path=output / "runtime" / "preflight_state.json",
            arm_token=arm_token,
        )
        market_gate = MarketDataGate()
        first = adapter.preflight()
        market_bootstrap_completed = True
        first_market = market_gate.validate(
            proxy.fetch_order_book("BTC/USDT:USDT"),
            now_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        )
        first_account_binding = str(adapter.state.account_uid if adapter.state else "")
        first_key = _snapshot_key(first, first_account_binding)

        second = adapter.preflight()
        second_market = market_gate.validate(
            proxy.fetch_order_book("BTC/USDT:USDT"),
            now_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        )
        second_account_binding = str(adapter.state.account_uid if adapter.state else "")
        second_key = _snapshot_key(second, second_account_binding)
        transport = proxy.public_audit()
        permissions = set(transport["permissions"])
        required_permissions = {"read_only", "trade"}
        permissions_passed = (
            required_permissions.issubset(permissions) and "withdraw" not in permissions
        )
        permission_snapshots_consistent = (
            len(proxy.permission_snapshots) == 2
            and proxy.permission_snapshots[0] == proxy.permission_snapshots[1]
        )
        snapshot_consistent = first_key == second_key
        state_resolved = bool(
            adapter.state
            and not adapter.state.owned_open_orders
            and not adapter.state.kill_switch.active
            and adapter.state.flatten_state in {"IDLE", "CONFIRMED"}
        )
        fee_matches = math.isclose(
            float(second.maker_fee_rate),
            float(profile.strategy.maker_fee_rate),
            rel_tol=0.0,
            abs_tol=1e-12,
        ) and math.isclose(
            float(second.taker_fee_rate),
            float(profile.strategy.taker_fee_rate),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        lot_btc = float(profile.strategy.fixed_lot_size_btc)
        one_order_margin = second_market["best_ask"] * lot_btc / 3.0
        two_sided_margin = one_order_margin * 2.0
        frozen_margin_limit = 750.0 * 0.80
        effective_margin_limit = min(frozen_margin_limit, second.free_equity_usdt)
        capacity_passed = two_sided_margin <= effective_margin_limit
        passed = all((
            snapshot_consistent,
            first.position_mode == "net_mode",
            second.position_mode == "net_mode",
            first.leverage == 3.0,
            second.leverage == 3.0,
            not first.open_orders,
            not second.open_orders,
            abs(first.position_btc) <= 1e-12,
            abs(second.position_btc) <= 1e-12,
            state_resolved,
            permissions_passed,
            permission_snapshots_consistent,
            fee_matches,
            capacity_passed,
            bool(transport["sandbox_mode"]),
            bool(transport["simulated_trading_header"]),
            transport["mutation_attempts"] == 0,
            transport["live_endpoint_attempts"] == 0,
        ))
        result = {
            "status": (
                "READ_ONLY_PREFLIGHT_PASSED" if passed
                else "READ_ONLY_PREFLIGHT_FAILED"
            ),
            "passed": passed,
            "run_id": run_id,
            "session_id": session_id,
            "execution_mode": "OKX_DEMO",
            "credentials_complete": True,
            "credentials_serialized": False,
            "credentials_non_withdrawal": permissions_passed,
            "transport_audit": transport,
            "snapshot_count": 2,
            "snapshots_consistent": snapshot_consistent,
            "snapshot_identity_sha256": second_key,
            "account_binding_sha256": second_account_binding,
            "initial_snapshot": first.public_dict(),
            "verified_snapshot": second.public_dict(),
            "permission_snapshots_consistent": permission_snapshots_consistent,
            "state_resolved": state_resolved,
            "market_spec": adapter.market_spec.to_dict() if adapter.market_spec else {},
            "market_fingerprint": (
                adapter.market_spec.fingerprint if adapter.market_spec else ""
            ),
            "profile_binding_sha256": profile.binding_sha256,
            "fee_schedule_matches_frozen_profile": fee_matches,
            "market_snapshots": [
                _market_public(first_market),
                _market_public(second_market),
            ],
            "market_timestamps_strictly_monotonic": (
                second_market["timestamp"] > first_market["timestamp"]
            ),
            "two_sided_capacity": {
                "fixed_lot_size_btc": lot_btc,
                "one_order_margin_usdt": one_order_margin,
                "two_sided_margin_usdt": two_sided_margin,
                "frozen_margin_limit_usdt": frozen_margin_limit,
                "authoritative_free_equity_usdt": second.free_equity_usdt,
                "effective_margin_limit_usdt": effective_margin_limit,
                "passed": capacity_passed,
            },
            "network_attempts": len(proxy.read_calls),
            "read_only_network_requests": len(proxy.read_calls),
            "mutation_attempts": proxy.mutation_attempts,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "account_setter_calls": 0,
            "formal_execution_authorized": False,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": proxy.live_endpoint_attempts,
            "live_orders": 0,
        }
        if not passed:
            raise ReadOnlyPreflightError("one or more read-only preflight gates failed")
    except Exception as exc:
        initial_read_methods = list(proxy.read_calls) if proxy is not None else []
        primary_read_method = (
            initial_read_methods[-1] if initial_read_methods else "UNAVAILABLE"
        )
        audit = proxy.public_audit() if proxy is not None else {
            "mutation_attempts": 0,
            "live_endpoint_attempts": 0,
            "read_call_count": 0,
        }
        result = {
            "status": "READ_ONLY_PREFLIGHT_FAILED",
            "passed": False,
            "run_id": run_id,
            "session_id": session_id,
            "execution_mode": "OKX_DEMO",
            "failure_type": type(exc).__name__,
            "failure_reason": str(exc),
            "primary_read_method": primary_read_method,
            "primary_error_category": _read_error_category(exc),
            "transport_audit": audit,
            "mutation_attempts": int(audit.get("mutation_attempts", 0)),
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "account_setter_calls": 0,
            "formal_execution_authorized": False,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": int(audit.get("live_endpoint_attempts", 0)),
            "live_orders": 0,
        }
        diagnostic = _sanitized_account_only_diagnostic(adapter)
        if diagnostic is not None:
            result["account_only_diagnostic"] = diagnostic
        pre_market_failure = bool(
            proxy is not None
            and not market_bootstrap_completed
            and "fetch_markets" in proxy.read_calls
            and proxy.mutation_attempts == 0
            and proxy.live_endpoint_attempts == 0
        )
        result["failure_stage"] = (
            "PRE_MARKET_BOOTSTRAP" if pre_market_failure else "POST_BOOTSTRAP"
        )
        result["market_bootstrap_completed"] = market_bootstrap_completed
        result["terminal_account_authoritative"] = False
        result["terminal_reconciliation_mode"] = "UNRESOLVED_FAIL_CLOSED"
        if pre_market_failure and proxy is not None:
            fallback_read_start = len(proxy.read_calls)
            try:
                terminal_first, terminal_second = _pre_market_terminal_account_pair(
                    proxy
                )
                result["terminal_account_authoritative"] = True
                result["terminal_reconciliation_mode"] = (
                    "ACCOUNT_ONLY_TWO_SNAPSHOT"
                )
                result["terminal_account_snapshots"] = [
                    terminal_first, terminal_second
                ]
            except Exception as terminal_exc:
                result["terminal_reconciliation_failure_type"] = type(
                    terminal_exc
                ).__name__
                result["terminal_reconciliation_error_category"] = (
                    _read_error_category(terminal_exc)
                )
                fallback_methods = proxy.read_calls[fallback_read_start:]
                result["terminal_reconciliation_read_method"] = (
                    fallback_methods[-1] if fallback_methods else "UNAVAILABLE"
                )
                result["terminal_reconciliation_read_methods"] = fallback_methods
                partial = getattr(
                    terminal_exc, "terminal_account_partial_diagnostic", None
                )
                if isinstance(partial, dict):
                    result["terminal_account_partial_diagnostic"] = partial
        if proxy is not None:
            # The fallback is itself read-only activity; record its final audit
            # rather than the stale snapshot captured at the primary failure.
            audit = proxy.public_audit()
            result["transport_audit"] = audit
            result["mutation_attempts"] = int(audit["mutation_attempts"])
            result["live_endpoint_attempts"] = int(audit["live_endpoint_attempts"])
    finally:
        if proxy is not None:
            try:
                proxy.close()
            except Exception:
                pass
        elif exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass

    _write_json(output / "preflight" / "preflight_result.json", result)
    if result.get("terminal_account_authoritative") is True:
        _write_json(
            output / "preflight" / "terminal_account_only_failure_manifest.json",
            {
                "status": "AUTHORITATIVE_PRE_MARKET_FAILURE",
                "run_id": run_id,
                "session_id": session_id,
                "failure_stage": "PRE_MARKET_BOOTSTRAP",
                "terminal_reconciliation_mode": "ACCOUNT_ONLY_TWO_SNAPSHOT",
                "terminal_account_authoritative": True,
                "terminal_account_snapshots": result["terminal_account_snapshots"],
                "mutation_retry": False,
                "orders_submitted": 0,
                "orders_amended": 0,
                "orders_cancelled": 0,
                "production_authorized": False,
                "live_mode_available": False,
                "live_endpoint_attempts": int(result["live_endpoint_attempts"]),
                "live_orders": 0,
            },
        )
    _write_json(output / "audits" / "endpoint_audit.json", result["transport_audit"])
    secret_scan = _secret_scan(output, credentials)
    _write_json(output / "audits" / "secret_scan.json", secret_scan)
    result_passed = bool(result["passed"] and secret_scan["passed"])
    decision = {
        "phase_status": (
            "READ_ONLY_PREFLIGHT_PASSED" if result_passed
            else "READ_ONLY_PREFLIGHT_FAILED"
        ),
        "preflight_executed": True,
        "read_only_preflight_passed": result_passed,
        "formal_demo_execution_authorized": False,
        "orders_submitted": 0,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": int(result["live_endpoint_attempts"]),
        "live_orders": 0,
        "optuna_executed": False,
        "v16_evidence_mutated": False,
        "prior_demo_evidence_mutated": False,
        "next_boundary": (
            "separate explicit formal Demo instruction and formal arm token"
            if result_passed else "stop; preflight failure must be resolved without orders"
        ),
    }
    _write_json(output / "decision" / "preflight_decision.json", decision)
    hashes_manifest = _completion_hashes(output)
    _write_json(output / "completion_hashes.json", hashes_manifest)
    terminal = {
        "phase_status": decision["phase_status"],
        "run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "preflight_executed": True,
        "read_only": True,
        "formal_execution_armed": False,
        "orders_submitted": 0,
        "live_endpoint_attempts": decision["live_endpoint_attempts"],
        "live_orders": 0,
        "optuna_executed": False,
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "completed_json_reserved_for_formal_close": True,
    }
    _write_json(output / "PREFLIGHT_PHASE_COMPLETED.json", terminal)
    if not result_passed:
        raise ReadOnlyPreflightError("read-only OKX Demo preflight failed")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--offline-run-id", required=True)
    parser.add_argument("--preparation-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--arm-token", required=True)
    args = parser.parse_args()
    try:
        output = run_read_only_preflight(
            root=args.root,
            run_id=args.run_id,
            offline_run_id=args.offline_run_id,
            preparation_id=args.preparation_id,
            session_id=args.session_id,
            arm_token=args.arm_token,
        )
    except Exception as exc:
        print(f"READ_ONLY_PREFLIGHT_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

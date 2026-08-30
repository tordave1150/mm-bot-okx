"""Source-bound start entrypoint for one terminal-recovery economic session."""

from __future__ import annotations

import argparse
import hashlib
import json

from okx_demo_soak_executor import (
    ROOT,
    DurableLease,
    SoakExecutionError,
    SoakPackage,
    _build_gateway,
    _start_failure,
    _verify_campaign_authorization,
    _write_new,
    load_package,
)
from okx_demo_terminal_recovery_executor import (
    TerminalRecoveryExecutor,
    TerminalRecoveryGateway,
)


def start(package: SoakPackage, arm_token: str) -> dict[str, object]:
    if arm_token != package.expected_arm_token:
        raise SoakExecutionError("terminal-recovery session arm token mismatch")
    _verify_campaign_authorization(package)
    marker = package.output / "soak_run/SOAK_EXECUTION_ARMED.json"
    _write_new(marker, {
        "package_id": package.spec["package_id"],
        "run_id": package.spec["run_id"],
        "session_id": package.spec["session_id"],
        "execution_source": __file__.split("\\")[-1].split("/")[-1],
        "recovery_executor": "okx_demo_terminal_recovery_executor.py",
        "arm_token_sha256": hashlib.sha256(arm_token.encode()).hexdigest(),
        "arm_token_serialized": False,
        "mutation_retry_attempts": 0,
        "production_authorized": False,
    })
    lease = DurableLease(
        package.output / "soak_run/state/lease.json",
        str(package.spec["session_id"]),
    )
    ttl_ms = (int(package.spec["risk_budget"]["session_wall_minutes"]) + 5) * 60 * 1000
    gateway: TerminalRecoveryGateway | None = None
    try:
        lease.acquire(ttl_ms)
        base_gateway, credentials = _build_gateway()
        gateway = TerminalRecoveryGateway(base_gateway.exchange)
    except Exception as exc:
        return _start_failure(package, exc, lease)
    try:
        return TerminalRecoveryExecutor(
            package, gateway, credentials=credentials, lease=lease
        ).run()
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("describe", "start"))
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--arm-token", default="")
    args = parser.parse_args()
    try:
        package = load_package(ROOT, args.package_id)
        if package.spec.get("execution_source") != __file__.split("\\")[-1].split("/")[-1]:
            raise SoakExecutionError("terminal-recovery session source binding mismatch")
        result = (
            {
                "package_id": package.spec["package_id"],
                "run_id": package.spec["run_id"],
                "session_id": package.spec["session_id"],
                "expected_arm_token": package.expected_arm_token,
                "execution_marker_exists": (
                    package.output / "soak_run/SOAK_EXECUTION_ARMED.json"
                ).exists(),
                "production_authorized": False,
            }
            if args.command == "describe" else start(package, args.arm_token)
        )
    except Exception as exc:
        print(json.dumps({"status": "TERMINAL_RECOVERY_SESSION_START_FAILED", "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if "FAILED" in str(result.get("status", "")) else 0


if __name__ == "__main__":
    raise SystemExit(main())

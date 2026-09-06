"""Durable local supervisor for a separately launched Stage C worker."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Sequence


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _redact(text: str) -> str:
    """Keep diagnostic output useful without persisting credential values."""
    patterns = (
        r"(?im)^(\s*(?:OKX_API_KEY|OKX_SECRET|OKX_PASSPHRASE|api[_-]?key|secret|passphrase|password)\s*[=:]\s*)\S+",
        r"(?im)^(\s*authorization\s*:\s*)\S+",
        r"(?im)^(\s*OK-ACCESS-[A-Z-]+\s*:\s*)\S+",
    )
    for pattern in patterns:
        text = re.sub(pattern, r"\1[REDACTED]", text)
    return text[:65536]


def _state_snapshot(run_dir: Path) -> dict[str, object]:
    """Return only local, non-authoritative state facts available to a parent."""
    state_files = sorted((run_dir / "state").glob("*_runtime_state.json"))
    if not state_files:
        return {"local_state_sha256": None, "local_mutation_counters": None}
    state_path = state_files[-1]
    raw = state_path.read_bytes()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        state = {}
    return {
        "local_state_path": str(state_path.relative_to(run_dir)),
        "local_state_sha256": hashlib.sha256(raw).hexdigest(),
        # These are local runtime observations only; they never make account state authoritative.
        "local_mutation_counters": {
            "client_order_generation": state.get("client_order_generation"),
            "flatten_attempts": state.get("flatten_attempts"),
            "owned_open_order_count": len(state.get("owned_open_orders", {})),
        },
    }


def supervise_worker(*, run_dir: Path, command: Sequence[str], campaign_id: str, session_id: str) -> int:
    """Capture child outcome and fail closed when it exits without terminal evidence."""
    # The worker owns initial run-directory creation. Creating it here would
    # make the executor reject the fresh run identity before it reaches its
    # interruption guard. The parent creates it only after the child exits.
    process = subprocess.run(list(command), text=True, capture_output=True, check=False)
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout = _redact(process.stdout)
    stderr = _redact(process.stderr)
    (run_dir / "worker_stdout.log").write_text(stdout, encoding="utf-8")
    (run_dir / "worker_stderr.log").write_text(stderr, encoding="utf-8")
    completed = run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json"
    failed = run_dir / "R2_STAGE_C_EXECUTION_FAILED.json"
    snapshot = _state_snapshot(run_dir)
    payload = {
        "status": "R2_STAGE_C_WORKER_SUPERVISION_COMPLETED",
        "campaign_id": campaign_id,
        "session_id": session_id,
        "exit_code": process.returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "terminal_marker_present": completed.is_file() or failed.is_file(),
        "terminal_account_authoritative": False,
        **snapshot,
    }
    _write(run_dir / "R2_STAGE_C_PROCESS_SUPERVISION.json", payload)
    if not payload["terminal_marker_present"]:
        _write(run_dir / "R2_STAGE_C_EXECUTION_FAILED.json", {
            "status": "R2_STAGE_C_EXECUTION_FAILED_NOT_ACCEPTABLE",
            "campaign_id": campaign_id,
            "session_id": session_id,
            "failure_reason_code": "WORKER_EXIT_WITHOUT_TERMINAL_MARKER",
            "worker_exit_code": process.returncode,
            "supervision_ref": "R2_STAGE_C_PROCESS_SUPERVISION.json",
            **snapshot,
            "mutation_counters_authoritative": False,
            "terminal_account_authoritative": False,
            "accept_authorized": False,
            "retry_authorized": False,
            "resume_authorized": False,
            "identity_reuse_authorized": False,
            "successor_session_authorized": False,
        })
    return process.returncode

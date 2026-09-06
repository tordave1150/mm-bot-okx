import json
import os
from pathlib import Path
import sys

from okx_demo_r2_stage_c_supervisor import supervise_worker


def test_supervisor_records_exit_without_terminal_marker(tmp_path: Path) -> None:
    state = tmp_path / "state" / "S03_runtime_state.json"
    state.parent.mkdir()
    state.write_text('{"client_order_generation": 3, "flatten_attempts": 0, "owned_open_orders": {}}')
    # os._exit bypasses Python's atexit and excepthook, modelling an abruptly lost worker.
    code = supervise_worker(run_dir=tmp_path, command=[sys.executable, "-c", "import os; os._exit(9)"], campaign_id="c", session_id="s")
    assert code != 0
    marker = json.loads((tmp_path / "R2_STAGE_C_EXECUTION_FAILED.json").read_text())
    assert marker["failure_reason_code"] == "WORKER_EXIT_WITHOUT_TERMINAL_MARKER"
    assert marker["local_state_sha256"]
    assert marker["terminal_account_authoritative"] is False
    assert (tmp_path / "worker_stderr.log").is_file()


def test_supervisor_preserves_existing_terminal_marker(tmp_path: Path) -> None:
    (tmp_path / "R2_STAGE_C_EXECUTION_COMPLETED.json").write_text('{"status":"passed"}')
    assert supervise_worker(
        run_dir=tmp_path, command=[sys.executable, "-c", "print('complete')"], campaign_id="c", session_id="s"
    ) == 0
    payload = json.loads((tmp_path / "R2_STAGE_C_PROCESS_SUPERVISION.json").read_text())
    assert payload["terminal_marker_present"] is True
    assert not (tmp_path / "R2_STAGE_C_EXECUTION_FAILED.json").exists()


def test_supervisor_allows_worker_to_create_fresh_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "fresh-run"
    code = supervise_worker(
        run_dir=run_dir,
        command=[sys.executable, "-c", "import os; os._exit(17)"], campaign_id="c", session_id="s",
    )
    assert code == 17
    assert (run_dir / "R2_STAGE_C_EXECUTION_FAILED.json").is_file()

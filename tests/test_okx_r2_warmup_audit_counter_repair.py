from pathlib import Path
from types import SimpleNamespace

import pytest

from okx_fill_restart_executor import (
    FormalExecutionDriver,
    FormalExecutionError,
    HashChainStream,
)
from okx_fill_restart_formal import FormalStage
from okx_r2_warmup_audit_repair_offline import (
    source_hashes as repair_source_hashes,
    verify_predecessor,
)


ROOT = Path(__file__).resolve().parents[1]


class AuditGateway:
    def __init__(
        self,
        *,
        reads: int = 0,
        creates: int = 0,
        cancels: int = 0,
        flatten: int = 0,
    ) -> None:
        self.reads = reads
        self.creates = creates
        self.cancels = cancels
        self.flatten = flatten
        self.live_attempts = 0
        self.live_orders = 0
        self.extra_mutation_method = ""

    def public_audit(self) -> dict[str, object]:
        methods = []
        if self.creates:
            methods.append("create_order")
        if self.cancels:
            methods.append("cancel_order")
        if self.extra_mutation_method:
            methods.append(self.extra_mutation_method)
        return {
            "sandbox_mode": True,
            "simulated_trading_header": True,
            "hostname": "www.okx.com",
            "read_call_count": self.reads,
            "read_methods": ["fetch_order_book"] if self.reads else [],
            "mutation_call_count": self.creates + self.cancels,
            "mutation_methods": sorted(methods),
            "mutation_method_counts": {
                "create_order": self.creates,
                "cancel_order": self.cancels,
            },
            "flatten_dispatches": self.flatten,
            "fill_history_queries": 0,
            "fill_recent_tail_queries": 0,
            "fill_union_duplicates": 0,
            "fill_union_conflicts": 0,
            "last_fill_union_audit": {},
            "live_endpoint_attempts": self.live_attempts,
            "live_orders": self.live_orders,
        }


def _probe_driver(gateway: AuditGateway) -> FormalExecutionDriver:
    driver = object.__new__(FormalExecutionDriver)
    driver.gateway = gateway
    driver.controller = SimpleNamespace(state=SimpleNamespace(
        stage=FormalStage.KILL_LATCH_BLOCKED,
        validation_kill_active=True,
    ))
    driver.engine = SimpleNamespace(state=SimpleNamespace(
        kill_latch=SimpleNamespace(active=True),
    ))
    return driver


def _audit_driver(tmp_path: Path, gateway: AuditGateway) -> FormalExecutionDriver:
    driver = object.__new__(FormalExecutionDriver)
    driver.gateway = gateway
    driver.controller = SimpleNamespace(state=SimpleNamespace(process_generation=0))
    driver.engine = SimpleNamespace(state=SimpleNamespace(process_generation=0))
    driver.streams = {
        "gateway_audit": HashChainStream(tmp_path / "gateway_audit.jsonl")
    }
    return driver


def test_r2_probe_warms_before_quote_and_changes_no_mutation_counter() -> None:
    gateway = AuditGateway(reads=2)
    driver = _probe_driver(gateway)
    events: list[str] = []
    quote = object()

    def warm() -> None:
        events.append("warm")
        gateway.reads += 14

    def plan(collected: object) -> object:
        events.append("quote")
        return quote

    driver.warm_quote_engine = warm
    driver.quote_plan = plan
    assert driver.prepare_r2_kill_probe(object()) is quote
    assert events == ["warm", "quote"]
    assert gateway.creates == gateway.cancels == gateway.flatten == 0


def test_r2_probe_quote_denial_fails_closed_with_zero_mutation() -> None:
    gateway = AuditGateway()
    driver = _probe_driver(gateway)
    driver.warm_quote_engine = lambda: setattr(gateway, "reads", 14)

    def denied(collected: object) -> object:
        raise FormalExecutionError("frozen quote engine did not authorize a quote")

    driver.quote_plan = denied
    with pytest.raises(FormalExecutionError, match="did not authorize"):
        driver.prepare_r2_kill_probe(object())
    assert gateway.creates == gateway.cancels == gateway.flatten == 0


def test_r2_probe_warmup_failure_preserves_zero_mutation() -> None:
    gateway = AuditGateway()
    driver = _probe_driver(gateway)

    def failed_warmup() -> None:
        gateway.reads += 1
        raise FormalExecutionError("warmup market timestamps are non-monotonic")

    driver.warm_quote_engine = failed_warmup
    driver.quote_plan = lambda collected: pytest.fail("quote must not run")
    with pytest.raises(FormalExecutionError, match="non-monotonic"):
        driver.prepare_r2_kill_probe(object())
    assert gateway.creates == gateway.cancels == gateway.flatten == 0


def test_r2_probe_rejects_protected_counter_drift() -> None:
    gateway = AuditGateway()
    driver = _probe_driver(gateway)

    def mutating_warmup() -> None:
        gateway.creates += 1

    driver.warm_quote_engine = mutating_warmup
    driver.quote_plan = lambda collected: object()
    with pytest.raises(FormalExecutionError, match="protected gateway counter"):
        driver.prepare_r2_kill_probe(object())


def test_r2_probe_requires_persisted_blocked_kill_state() -> None:
    gateway = AuditGateway()
    driver = _probe_driver(gateway)
    driver.controller.state.stage = FormalStage.RESTART_REQUIRED_R2
    driver.warm_quote_engine = lambda: pytest.fail("warmup must not run")
    with pytest.raises(FormalExecutionError, match="blocked controller stage"):
        driver.prepare_r2_kill_probe(object())
    assert gateway.creates == gateway.cancels == gateway.flatten == 0


def test_cumulative_gateway_audit_sums_contiguous_generations(tmp_path: Path) -> None:
    driver = _audit_driver(
        tmp_path, AuditGateway(reads=10, creates=3, cancels=2)
    )
    driver._persist_gateway_generation_audit("R1_HANDOFF")
    driver.engine.state.process_generation = 1
    driver.controller.state.process_generation = 1
    driver.gateway = AuditGateway(reads=20, creates=4, cancels=4)
    driver._persist_gateway_generation_audit("R2_HANDOFF")
    driver.engine.state.process_generation = 2
    driver.controller.state.process_generation = 2
    driver.gateway = AuditGateway(reads=8)
    driver._persist_gateway_generation_audit("TERMINAL")

    audit = driver._cumulative_gateway_audit()
    assert audit["scope"] == "CUMULATIVE_ACROSS_PROCESS_GENERATIONS"
    assert audit["process_generations"] == [0, 1, 2]
    assert audit["read_call_count"] == 38
    assert audit["mutation_call_count"] == 13
    assert audit["mutation_method_counts"] == {
        "create_order": 7,
        "cancel_order": 6,
    }
    assert audit["live_endpoint_attempts"] == 0
    assert audit["live_orders"] == 0


def test_terminal_gateway_counts_reconcile_create_cancel_and_flatten() -> None:
    counts = FormalExecutionDriver._reconcile_gateway_order_counts(
        {
            "mutation_method_counts": {"create_order": 8, "cancel_order": 6},
            "flatten_dispatches": 1,
        },
        normal_create_count=7,
        normal_acknowledgements=7,
        normal_create_events=7,
        cancel_confirmed_ids=6,
    )
    assert counts == {
        "cumulative_create_calls": 8,
        "cumulative_cancel_calls": 6,
        "cumulative_flatten_calls": 1,
        "normal_transport_creates": 7,
    }


def test_terminal_gateway_counts_fail_closed_on_event_mismatch() -> None:
    with pytest.raises(FormalExecutionError, match="do not reconcile"):
        FormalExecutionDriver._reconcile_gateway_order_counts(
            {
                "mutation_method_counts": {"create_order": 7, "cancel_order": 6},
                "flatten_dispatches": 0,
            },
            normal_create_count=7,
            normal_acknowledgements=7,
            normal_create_events=7,
            cancel_confirmed_ids=5,
        )


def test_same_generation_gateway_counters_must_be_monotonic(tmp_path: Path) -> None:
    driver = _audit_driver(tmp_path, AuditGateway(reads=5, creates=1))
    driver._persist_gateway_generation_audit("SNAPSHOT")
    driver.gateway = AuditGateway(reads=4, creates=1)
    with pytest.raises(FormalExecutionError, match="counter regressed"):
        driver._persist_gateway_generation_audit("TERMINAL")


def test_cumulative_gateway_audit_rejects_missing_generation(tmp_path: Path) -> None:
    driver = _audit_driver(tmp_path, AuditGateway(reads=1))
    driver._persist_gateway_generation_audit("R1_HANDOFF")
    driver.engine.state.process_generation = 2
    driver.controller.state.process_generation = 2
    driver.gateway = AuditGateway(reads=1)
    driver._persist_gateway_generation_audit("TERMINAL")
    with pytest.raises(FormalExecutionError, match="not contiguous|missing"):
        driver._cumulative_gateway_audit()


def test_cumulative_gateway_audit_rejects_truncated_hash_chain(tmp_path: Path) -> None:
    driver = _audit_driver(tmp_path, AuditGateway(reads=1))
    driver._persist_gateway_generation_audit("TERMINAL")
    path = driver.streams["gateway_audit"].path
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    with pytest.raises(FormalExecutionError, match="truncated"):
        driver._cumulative_gateway_audit()


def test_gateway_audit_rejects_non_allowlisted_mutation_method(tmp_path: Path) -> None:
    gateway = AuditGateway()
    gateway.extra_mutation_method = "edit_order"
    driver = _audit_driver(tmp_path, gateway)
    with pytest.raises(FormalExecutionError, match="not allow-listed"):
        driver._persist_gateway_generation_audit("TERMINAL")


def test_failed_formal_predecessor_is_frozen_and_not_resumable() -> None:
    predecessor = verify_predecessor(ROOT)
    assert predecessor["formal_run_id"] == "formal-20260807T130847Z"
    assert predecessor["R1_completed"] is True
    assert predecessor["R2_completed"] is False
    assert predecessor["normal_create_events"] == 37
    assert predecessor["authoritative_cancel_confirmations"] == 35
    assert predecessor["kill_active_at_terminal"] is True
    assert predecessor["rerun_allowed"] is False


def test_successor_root_protocol_is_byte_identical_to_source_copy() -> None:
    hashes = repair_source_hashes(ROOT)
    assert hashes["AGENTS.md"] == hashes[
        "AGENTS_OKX_DEMO_R2_WARMUP_AUDIT_COUNTER_REPAIR.md"
    ]

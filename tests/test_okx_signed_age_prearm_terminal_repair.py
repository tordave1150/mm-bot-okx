from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import okx_fill_restart_executor
import okx_fill_restart_gateway
from okx_fill_restart_executor import (
    FormalExecutionDriver,
    FormalExecutionError,
    FrozenPackage,
    HashChainStream,
)
from okx_fill_restart_formal import (
    FormalOrchestrator,
    FormalSafetyError,
    FormalStage,
    FormalStateStore,
    make_fixture_observation,
    make_fixture_spec,
)
from okx_fill_restart_gateway import (
    FormalBookStalenessError,
    FormalDemoGateway,
    FormalGatewayError,
    GatewayAccountSnapshot,
)
from okx_fill_restart_validation import (
    FillRestartEngine,
    HashChainStateStore,
    RiskBudget,
    RuntimeBinding,
)


@pytest.mark.parametrize("age", [0, 1_000, -1, -1_500])
def test_formal_observation_accepts_signed_age_boundaries(age: int) -> None:
    spec = make_fixture_spec()
    observation = make_fixture_observation(spec, market_age_ms=age)
    assert observation.safety_failure(spec) == ""


@pytest.mark.parametrize("age", [1_001, -1_501, True, 1.5, None])
def test_formal_observation_rejects_out_of_budget_or_invalid_age(
    age: object,
) -> None:
    spec = make_fixture_spec()
    observation = make_fixture_observation(spec, market_age_ms=age)
    assert observation.safety_failure(spec) == "MARKET_DATA_STALE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_market_age_ms", 0),
        ("maximum_market_age_ms", True),
        ("maximum_market_age_ms", 1_001),
        ("maximum_clock_skew_ms", 0),
        ("maximum_clock_skew_ms", True),
        ("maximum_clock_skew_ms", 1_501),
    ],
)
def test_formal_spec_rejects_invalid_signed_age_budgets(
    field: str, value: object
) -> None:
    spec = replace(make_fixture_spec(), **{field: value})
    with pytest.raises(FormalSafetyError):
        spec.validate()


class BookExchange:
    def __init__(self, timestamp_ms: int, *, crossed: bool = False) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.hostname = "www.okx.com"
        self.timestamp_ms = timestamp_ms
        self.crossed = crossed

    def fetch_order_book(self, symbol: str) -> dict[str, object]:
        return {
            "timestamp": self.timestamp_ms,
            "bids": [[50_001 if self.crossed else 49_999, 1]],
            "asks": [[50_000 if self.crossed else 50_001, 1]],
        }


def test_gateway_positive_stale_error_carries_retry_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(okx_fill_restart_gateway.time, "time", lambda: 1_000.0)
    gateway = FormalDemoGateway(BookExchange(998_999))
    with pytest.raises(FormalBookStalenessError) as raised:
        gateway.fetch_book(maximum_age_ms=1_000, maximum_clock_skew_ms=1_500)
    error = raised.value
    assert error.signed_age_ms == 1_001
    assert error.reason == "POSITIVE_STALE"
    assert error.retryable_prearm is True
    assert error.public_dict()["maximum_age_ms"] == 1_000
    assert gateway.mutation_calls == []


def test_gateway_future_beyond_budget_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(okx_fill_restart_gateway.time, "time", lambda: 1_000.0)
    gateway = FormalDemoGateway(BookExchange(1_001_501))
    with pytest.raises(FormalBookStalenessError) as raised:
        gateway.fetch_book(maximum_age_ms=1_000, maximum_clock_skew_ms=1_500)
    assert raised.value.signed_age_ms == -1_501
    assert raised.value.retryable_prearm is False
    assert gateway.mutation_calls == []


def test_gateway_crossed_book_fails_immediately_not_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(okx_fill_restart_gateway.time, "time", lambda: 1_000.0)
    gateway = FormalDemoGateway(BookExchange(1_000_000, crossed=True))
    with pytest.raises(FormalGatewayError) as raised:
        gateway.fetch_book(maximum_age_ms=1_000, maximum_clock_skew_ms=1_500)
    assert not isinstance(raised.value, FormalBookStalenessError)
    assert gateway.mutation_calls == []


class PrearmGateway:
    def __init__(self) -> None:
        self.mutation_calls: list[str] = []
        self.flatten_dispatches = 0
        self.live_endpoint_attempts = 0


def _prearm_driver(tmp_path: Path) -> FormalExecutionDriver:
    driver = object.__new__(FormalExecutionDriver)
    driver.spec = SimpleNamespace(observation_interval_ms=0)
    driver.gateway = PrearmGateway()
    driver.controller = SimpleNamespace(state=SimpleNamespace(
        stage=FormalStage.NOT_ARMED,
        owned_orders={},
        pending_intents={},
    ))
    driver.engine = SimpleNamespace(state=SimpleNamespace(
        owned_orders={},
        external_order_submissions=0,
    ))
    driver.streams = {
        "safety": HashChainStream(tmp_path / "safety.jsonl")
    }
    return driver


def _stale(age: int) -> FormalBookStalenessError:
    return FormalBookStalenessError(
        signed_age_ms=age,
        exchange_timestamp_ms=1_000,
        observed_at_ms=1_000 + age,
        maximum_age_ms=1_000,
        maximum_clock_skew_ms=1_500,
    )


def test_prearm_stale_then_fresh_retries_read_only_and_persists_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _prearm_driver(tmp_path)
    fresh = object()
    outcomes: list[object] = [_stale(1_001), fresh]
    driver.collect = lambda: (
        (_ for _ in ()).throw(outcomes.pop(0))
        if isinstance(outcomes[0], Exception) else outcomes.pop(0)
    )
    monkeypatch.setattr(okx_fill_restart_executor.time, "sleep", lambda value: None)
    assert driver.collect_prearm_until_fresh(attempts=2) is fresh
    diagnostic = driver.streams["safety"].last_payload()
    assert diagnostic["signed_age_ms"] == 1_001
    assert diagnostic["attempt"] == 1
    assert diagnostic["new_submissions_after_rejection"] == 0
    assert driver.gateway.mutation_calls == []


def test_prearm_positive_stale_exhaustion_is_bounded_and_zero_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = _prearm_driver(tmp_path)
    attempts = 0

    def collect() -> object:
        nonlocal attempts
        attempts += 1
        raise _stale(1_001 + attempts)

    driver.collect = collect
    monkeypatch.setattr(okx_fill_restart_executor.time, "sleep", lambda value: None)
    with pytest.raises(FormalExecutionError, match="reacquisition exhausted"):
        driver.collect_prearm_until_fresh(attempts=3)
    assert attempts == 3
    assert driver.gateway.mutation_calls == []


def test_prearm_future_beyond_budget_is_not_retried(tmp_path: Path) -> None:
    driver = _prearm_driver(tmp_path)
    attempts = 0

    def collect() -> object:
        nonlocal attempts
        attempts += 1
        raise _stale(-1_501)

    driver.collect = collect
    with pytest.raises(FormalBookStalenessError):
        driver.collect_prearm_until_fresh(attempts=8)
    assert attempts == 1
    assert driver.gateway.mutation_calls == []


def test_prearm_retry_is_forbidden_after_arm(tmp_path: Path) -> None:
    driver = _prearm_driver(tmp_path)
    driver.controller.state.stage = FormalStage.SEEK_R1_FILL
    driver.collect = lambda: pytest.fail("collection must not run")
    with pytest.raises(FormalExecutionError, match="forbidden after arm"):
        driver.collect_prearm_until_fresh()
    assert driver.gateway.mutation_calls == []


class AuditGateway:
    def __init__(self) -> None:
        self.live_endpoint_attempts = 0
        self.mutation_calls: list[str] = []
        self.flatten_dispatches = 0

    def public_audit(self) -> dict[str, object]:
        return {
            "sandbox_mode": True,
            "simulated_trading_header": True,
            "hostname": "www.okx.com",
            "read_call_count": 2,
            "read_methods": ["fetch_account"],
            "mutation_call_count": 0,
            "mutation_methods": [],
            "mutation_method_counts": {"create_order": 0, "cancel_order": 0},
            "flatten_dispatches": 0,
            "fill_history_queries": 0,
            "fill_recent_tail_queries": 0,
            "fill_union_duplicates": 0,
            "fill_union_conflicts": 0,
            "last_fill_union_audit": {},
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        }


def _account(binding: str, *, position: str = "0") -> GatewayAccountSnapshot:
    return GatewayAccountSnapshot(
        account_binding=binding,
        permissions=("read_only", "trade"),
        position_mode="net_mode",
        leverage=Decimal("3"),
        total_equity_usdt=Decimal("750"),
        free_equity_usdt=Decimal("750"),
        position_btc=Decimal(position),
        average_entry_usdt=Decimal("0") if position == "0" else Decimal("50000"),
        maintenance_margin_usdt=Decimal("0"),
        open_orders=(),
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.0005"),
        clock_skew_ms=25,
    )


def _account_close_driver(tmp_path: Path) -> tuple[FormalExecutionDriver, str]:
    spec = make_fixture_spec(
        formal_run_id="formal-account-close-fixture",
        package_id="formal-package-account-close-fixture",
    )
    binding_value = "fixture-account-binding"
    binding = RuntimeBinding(
        run_id=spec.formal_run_id,
        execution_mode="OKX_DEMO",
        account_binding=binding_value,
        market_fingerprint=spec.market_fingerprint,
        profile_binding_sha256=spec.profile_binding_sha256,
        runtime_configuration_sha256=spec.runtime_configuration_sha256,
        source_manifest_sha256=spec.source_manifest_sha256,
    )
    controller = FormalOrchestrator.prepare(
        spec, FormalStateStore(tmp_path / "controller.json")
    )
    engine = FillRestartEngine.create(
        store=HashChainStateStore(tmp_path / "validation.json"),
        binding=binding,
        risk_budget=RiskBudget(),
    )
    package = FrozenPackage(
        root=tmp_path,
        output=tmp_path / "package",
        package_id=spec.package_id,
        spec=spec,
        source_hashes={},
    )
    driver = object.__new__(FormalExecutionDriver)
    driver.package = package
    driver.spec = spec
    driver.gateway = AuditGateway()
    driver.controller = controller
    driver.engine = engine
    driver.streams = {
        name: HashChainStream(
            package.output / "formal_run" / "streams" / f"{name}.jsonl"
        )
        for name in ("market", "order", "gateway_audit")
    }
    return driver, binding_value


def test_account_only_terminal_close_writes_completed_without_market(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, binding = _account_close_driver(tmp_path)
    monkeypatch.setattr(
        okx_fill_restart_executor,
        "_credential_config",
        lambda: SimpleNamespace(api_key="", api_secret="", api_passphrase=""),
    )
    result = driver.close(
        "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        "FormalBookStalenessError:POSITIVE_STALE",
        account_only_snapshots=(_account(binding), _account(binding)),
    )
    assert result["terminal_account_only"] is True
    assert result["final_position_btc"] == "0"
    assert result["final_open_order_count"] == 0
    assert (driver.package.output / "COMPLETED.json").is_file()
    terminal = (
        driver.package.output / "formal_run" / "terminal"
        / "account_only_snapshots.json"
    )
    assert terminal.is_file()
    assert not driver.streams["market"].path.exists()
    assert driver.gateway.mutation_calls == []


def test_account_only_terminal_close_rejects_binding_or_nonflat_snapshot(
    tmp_path: Path,
) -> None:
    driver, binding = _account_close_driver(tmp_path)
    with pytest.raises(FormalExecutionError, match="binding mismatch"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account("wrong")),
        )
    driver, binding = _account_close_driver(tmp_path / "nonflat")
    with pytest.raises(FormalExecutionError, match="flat/empty"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account(binding, position="0.01")),
        )


def test_account_only_terminal_close_rejects_ledger_mismatch(tmp_path: Path) -> None:
    driver, binding = _account_close_driver(tmp_path)
    driver.engine.state.ledger.inventory_btc = Decimal("0.01")
    with pytest.raises(FormalExecutionError, match="durable state"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account(binding)),
        )


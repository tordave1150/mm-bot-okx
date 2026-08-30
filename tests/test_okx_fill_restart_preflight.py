import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import okx_fill_restart_preflight as preflight
from okx_demo_adapter import DemoAdapterError
from okx_fill_restart_preflight import (
    ReadOnlyExchangeProxy,
    ReadOnlyPreflightError,
    _preflight_predecessor_audit,
    _pre_market_terminal_account_pair,
    _sanitized_account_only_diagnostic,
    _snapshot_key,
    expected_arm_token,
    preflight_source_hashes,
    verify_offline_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


class FixtureExchange:
    def __init__(self) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.hostname = "www.okx.com"
        self.urls = {"api": {"rest": "https://www.okx.com"}}
        self.create_calls = 0

    def fetch_balance(self):
        return {"USDT": {"total": 750, "free": 750}}
    def privateGetAccountConfig(self):
        return {"data": [{
            "uid": "fixture-demo-account",
            "posMode": "net_mode",
            "perm": "read_only,trade",
        }]}

    def fetch_markets(self):
        raise TimeoutError("market metadata unavailable")

    def fetch_time(self):
        return int(time.time() * 1000)

    def privateGetAccountPositions(self, params):
        return {"data": []}

    def privateGetTradeOrdersPending(self, params):
        return {"data": []}

    def create_order(self, *args, **kwargs):
        self.create_calls += 1
        return {"id": "must-not-happen"}

    def set_markets(self, markets):
        return markets

    def close(self):
        return None


def test_terminal_recovery_predecessor_is_fail_closed_and_never_authorizes_resume() -> None:
    audit = _preflight_predecessor_audit(
        ROOT,
        {
            "evidence_kind": "r2_post_start_terminal_recovery_offline_repair",
            "failed_campaign_decision": "NOT_READY",
            "account_confirmation_is_exchange_authoritative": False,
            "failed_package_id": "economic-package-failed",
            "failed_campaign_run_id": "economic-campaign-run-failed",
            "failed_session_package_id": "soak-package-failed-s01",
        },
    )
    assert audit["passed"] is True
    assert audit["resume_authorized"] is False
    assert audit["package_id"] == "economic-package-failed"


def test_session_scoped_arm_token_is_exact() -> None:
    session_id = "preflight:preflight-fixture:p1"
    assert expected_arm_token(session_id) == f"OKX_DEMO:{session_id}"
    with pytest.raises(ReadOnlyPreflightError, match="session"):
        expected_arm_token("formal:fixture")


def test_execution_environment_transport_evidence_uses_multi_session_protocol() -> None:
    assert (
        preflight.preflight_protocol_id_for_evidence(
            "execution_environment_transport_r0_offline_repair"
        )
        == preflight.MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID
    )


def test_r2_session5_terminal_repair_uses_multi_session_protocol() -> None:
    evidence_kind = "r2_session5_terminal_reconciliation_r0_offline_repair"
    assert (
        preflight.preflight_protocol_id_for_evidence(evidence_kind)
        == preflight.MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID
    )
    audit = _preflight_predecessor_audit(
        ROOT,
        {
            "evidence_kind": evidence_kind,
            "failed_campaign_decision": "UNRESOLVED_FAIL_CLOSED",
            "active_failed_slot": 5,
            "terminal_account_authoritative": False,
            "resume_authorized": False,
            "failed_package_id": "economic-package-failed",
            "failed_campaign_run_id": "economic-campaign-run-failed",
            "failed_session_package_id": "soak-package-failed-s05",
        },
    )
    assert audit["passed"] is True
    assert audit["resume_authorized"] is False
    assert audit["session_package_id"] == "soak-package-failed-s05"


def test_unknown_evidence_uses_legacy_preflight_protocol() -> None:
    assert (
        preflight.preflight_protocol_id_for_evidence("unknown-r0-evidence")
        == preflight.PREFLIGHT_PROTOCOL_ID
    )


def test_execution_environment_evidence_arms_multi_session_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RejectingAdapter:
        last_account_only_diagnostic = None

        def preflight(self) -> None:
            raise DemoAdapterError("offline fixture stops before any read dispatch")

    monkeypatch.setattr(
        preflight,
        "verify_offline_evidence",
        lambda *args, **kwargs: {
            "passed": True,
            "evidence_kind": "execution_environment_transport_r0_offline_repair",
        },
    )
    monkeypatch.setattr(
        preflight, "_preflight_predecessor_audit", lambda *args, **kwargs: {"passed": True}
    )
    monkeypatch.setattr(
        preflight, "verify_preflight_preparation", lambda *args, **kwargs: {"passed": True}
    )
    monkeypatch.setattr(preflight, "preflight_source_hashes", lambda root: {})
    monkeypatch.setattr(
        preflight,
        "_credential_config",
        lambda: SimpleNamespace(
            api_key="fixture-api-key",
            api_secret="fixture-api-secret",
            api_passphrase="fixture-passphrase",
        ),
    )
    monkeypatch.setattr(
        preflight, "build_ccxt_demo_exchange", lambda **kwargs: FixtureExchange()
    )
    monkeypatch.setattr(preflight, "_adapter", lambda **kwargs: RejectingAdapter())
    monkeypatch.setattr(preflight, "ARTIFACT_ROOT", tmp_path / "preflight-artifacts")
    run_id = "preflight-execution-environment-fixture"
    session_id = f"preflight:{run_id}:p0:fixture"

    with pytest.raises(ReadOnlyPreflightError, match="preflight failed"):
        preflight.run_read_only_preflight(
            root=ROOT,
            run_id=run_id,
            offline_run_id="offline-fixture",
            preparation_id="preflight-package-fixture",
            session_id=session_id,
            arm_token=f"OKX_DEMO:{session_id}",
        )

    marker = json.loads(
        (
            tmp_path
            / "preflight-artifacts"
            / run_id
            / "READ_ONLY_PREFLIGHT_ARMED.json"
        ).read_text(encoding="utf-8")
    )
    assert marker["protocol_id"] == preflight.MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID


def test_proxy_allows_reads_captures_non_withdraw_permissions_and_blocks_mutation() -> None:
    exchange = FixtureExchange()
    proxy = ReadOnlyExchangeProxy(exchange)
    assert proxy.fetch_balance()["USDT"]["free"] == 750
    proxy.privateGetAccountConfig()
    proxy.set_markets([])
    with pytest.raises(ReadOnlyPreflightError, match="mutation"):
        proxy.create_order("BTC/USDT:USDT", "limit", "buy", 1, 50000)
    audit = proxy.public_audit()
    assert exchange.create_calls == 0
    assert audit["sandbox_mode"] is True
    assert audit["simulated_trading_header"] is True
    assert audit["mutation_attempts"] == 1
    assert audit["live_endpoint_attempts"] == 0
    assert audit["permissions"] == ["read_only", "trade"]
    assert audit["non_withdrawal_permissions"] is True
    assert audit["read_permission_present"] is True
    assert audit["trade_permission_present"] is True
    assert audit["endpoint_hosts"] == ["www.okx.com"]
    assert audit["local_only_methods"] == ["set_markets"]


def test_proxy_refuses_read_dispatch_without_demo_header() -> None:
    exchange = FixtureExchange()
    exchange.headers = {}
    proxy = ReadOnlyExchangeProxy(exchange)
    with pytest.raises(ReadOnlyPreflightError, match="Demo transport"):
        proxy.fetch_balance()
    assert proxy.live_endpoint_attempts == 1
    assert proxy.read_calls == []


def test_proxy_retries_only_retryable_reads_up_to_three_attempts() -> None:
    exchange = FixtureExchange()
    attempts = 0

    def flaky_balance():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("fixture timeout")
        return {"USDT": {"total": 750, "free": 750}}

    exchange.fetch_balance = flaky_balance
    sleeps: list[float] = []
    proxy = ReadOnlyExchangeProxy(exchange, sleep=sleeps.append)

    assert proxy.fetch_balance()["USDT"]["free"] == 750
    audit = proxy.public_audit()
    assert attempts == 3
    assert sleeps == [0.05, 0.1]
    assert audit["read_call_count"] == 3
    assert [entry["category"] for entry in audit["read_retry_audit"]] == [
        "TIMEOUT", "TIMEOUT", "SUCCESS",
    ]


@pytest.mark.parametrize(
    "error,expected_category,expected_attempts",
    [
        (TimeoutError("fixture"), "TIMEOUT", 3),
        (type("DnsError", (Exception,), {})("fixture"), "DNS", 3),
        (type("Http429", (Exception,), {"status": 429})("fixture"), "RATE_LIMIT", 3),
        (type("Http503", (Exception,), {"status": 503})("fixture"), "SERVER", 3),
        (type("TlsError", (Exception,), {})("fixture"), "TLS", 1),
        (PermissionError("fixture"), "AUTHORIZATION", 1),
        (ValueError("fixture"), "READ_FAILURE", 1),
    ],
)
def test_proxy_retry_taxonomy_never_retries_unsafe_categories(
    error: Exception, expected_category: str, expected_attempts: int
) -> None:
    exchange = FixtureExchange()
    attempts = 0

    def failing_balance():
        nonlocal attempts
        attempts += 1
        raise error

    exchange.fetch_balance = failing_balance
    proxy = ReadOnlyExchangeProxy(exchange, sleep=lambda delay: None)
    with pytest.raises(type(error)):
        proxy.fetch_balance()
    audit = proxy.public_audit()
    assert attempts == expected_attempts
    assert audit["read_retry_audit"][-1]["category"] == expected_category
    assert audit["read_retry_audit"][-1]["retry"] is False


def test_proxy_resolves_ccxt_hostname_template_and_refuses_unexpected_host() -> None:
    exchange = FixtureExchange()
    exchange.urls = {"api": {"rest": "https://{hostname}"}}
    proxy = ReadOnlyExchangeProxy(exchange)
    assert proxy.fetch_balance()["USDT"]["free"] == 750
    assert proxy.public_audit()["endpoint_hosts"] == ["www.okx.com"]

    exchange.hostname = "example.invalid"
    with pytest.raises(ReadOnlyPreflightError, match="Demo transport"):
        proxy.fetch_balance()
    assert proxy.live_endpoint_attempts == 1


def test_snapshot_identity_ignores_only_per_request_clock_skew() -> None:
    values = dict(
        account_uid="raw-account-never-serialized",
        position_mode="net_mode",
        leverage=3.0,
        total_equity_usdt=750.0,
        free_equity_usdt=750.0,
        position_btc=0.0,
        average_entry_price=0.0,
        maintenance_margin_usdt=0.0,
        open_orders=(),
        recent_trades=(),
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
    )
    first = SimpleNamespace(**values, clock_skew_ms=10)
    second = SimpleNamespace(**values, clock_skew_ms=25)
    # _snapshot_key accepts dataclasses in production; fixture with matching
    # attribute surface is converted through a small dataclass below.
    from okx_demo_adapter import AccountSnapshot

    first_snapshot = AccountSnapshot(**vars(first))
    second_snapshot = AccountSnapshot(**vars(second))
    assert _snapshot_key(first_snapshot, "account-hash") == _snapshot_key(
        second_snapshot, "account-hash"
    )


def test_account_only_diagnostic_whitelists_counts_position_and_hashes() -> None:
    digest = "a" * 64
    adapter = SimpleNamespace(last_account_only_diagnostic={
        "signed_position_btc": "-0.010",
        "position_row_count": 1,
        "open_order_count": 2,
        "account_binding_sha256": digest,
        "position_identifiers_sha256": digest,
        "open_order_identifiers_sha256": digest,
        "raw_account_uid": "must-never-serialize",
        "raw_order_ids": ["must-never-serialize"],
    })

    diagnostic = _sanitized_account_only_diagnostic(adapter)

    assert diagnostic == {
        "signed_position_btc": "-0.010",
        "position_row_count": 1,
        "open_order_count": 2,
        "account_binding_sha256": digest,
        "position_identifiers_sha256": digest,
        "open_order_identifiers_sha256": digest,
    }
    serialized = json.dumps(diagnostic, sort_keys=True)
    assert "must-never-serialize" not in serialized


@pytest.mark.parametrize(
    "replacement",
    [
        {"signed_position_btc": "not-a-number"},
        {"position_row_count": -1},
        {"open_order_count": True},
        {"account_binding_sha256": "raw-uid"},
    ],
)
def test_invalid_account_only_diagnostic_is_omitted(
    replacement: dict[str, object],
) -> None:
    raw: dict[str, object] = {
        "signed_position_btc": "0",
        "position_row_count": 0,
        "open_order_count": 0,
        "account_binding_sha256": "a" * 64,
        "position_identifiers_sha256": "b" * 64,
        "open_order_identifiers_sha256": "c" * 64,
    }
    raw.update(replacement)
    assert _sanitized_account_only_diagnostic(
        SimpleNamespace(last_account_only_diagnostic=raw)
    ) is None


def test_failed_preflight_writes_sanitized_account_only_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "d" * 64

    class RejectingAdapter:
        last_account_only_diagnostic = {
            "signed_position_btc": "-0.01",
            "position_row_count": 1,
            "open_order_count": 0,
            "account_binding_sha256": digest,
            "position_identifiers_sha256": digest,
            "open_order_identifiers_sha256": digest,
            "raw_account_uid": "raw-account-must-not-serialize",
        }

        def preflight(self):
            raise DemoAdapterError("fresh session has unowned orders or exposure")

    monkeypatch.setattr(
        preflight,
        "verify_offline_evidence",
        lambda *args, **kwargs: {"passed": True, "evidence_kind": "fixture"},
    )
    monkeypatch.setattr(
        preflight,
        "_preflight_predecessor_audit",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        preflight,
        "verify_preflight_preparation",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(preflight, "preflight_source_hashes", lambda root: {})
    monkeypatch.setattr(
        preflight,
        "_credential_config",
        lambda: SimpleNamespace(
            api_key="fixture-api-key",
            api_secret="fixture-api-secret",
            api_passphrase="fixture-passphrase",
        ),
    )
    monkeypatch.setattr(
        preflight, "build_ccxt_demo_exchange", lambda **kwargs: FixtureExchange()
    )
    monkeypatch.setattr(
        preflight, "_adapter", lambda **kwargs: RejectingAdapter()
    )
    artifact_root = tmp_path / "preflight-artifacts"
    monkeypatch.setattr(preflight, "ARTIFACT_ROOT", artifact_root)
    run_id = "preflight-diagnostic-fixture"
    session_id = f"preflight:{run_id}:p0:fixture"

    with pytest.raises(ReadOnlyPreflightError, match="preflight failed"):
        preflight.run_read_only_preflight(
            root=ROOT,
            run_id=run_id,
            offline_run_id="offline-fixture",
            preparation_id="preflight-package-fixture",
            session_id=session_id,
            arm_token=f"OKX_DEMO:{session_id}",
        )

    output = artifact_root / run_id
    result = json.loads(
        (output / "preflight/preflight_result.json").read_text(encoding="utf-8")
    )
    assert result["account_only_diagnostic"] == {
        "signed_position_btc": "-0.01",
        "position_row_count": 1,
        "open_order_count": 0,
        "account_binding_sha256": digest,
        "position_identifiers_sha256": digest,
        "open_order_identifiers_sha256": digest,
    }
    assert result["mutation_attempts"] == 0
    assert result["orders_submitted"] == 0
    assert result["orders_amended"] == 0
    assert result["orders_cancelled"] == 0
    assert result["live_endpoint_attempts"] == 0
    serialized = b"".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    )
    assert b"raw-account-must-not-serialize" not in serialized
    assert b"fixture-api-secret" not in serialized


def test_market_bootstrap_failure_writes_authoritative_two_snapshot_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MarketBootstrapRejectingAdapter:
        last_account_only_diagnostic = None

        def __init__(self, exchange):
            self.exchange = exchange

        def preflight(self):
            self.exchange.fetch_markets()

    monkeypatch.setattr(
        preflight,
        "verify_offline_evidence",
        lambda *args, **kwargs: {"passed": True, "evidence_kind": "fixture"},
    )
    monkeypatch.setattr(
        preflight, "_preflight_predecessor_audit", lambda *args, **kwargs: {"passed": True}
    )
    monkeypatch.setattr(
        preflight, "verify_preflight_preparation", lambda *args, **kwargs: {"passed": True}
    )
    monkeypatch.setattr(preflight, "preflight_source_hashes", lambda root: {})
    monkeypatch.setattr(
        preflight,
        "_credential_config",
        lambda: SimpleNamespace(
            api_key="fixture-api-key",
            api_secret="fixture-api-secret",
            api_passphrase="fixture-passphrase",
        ),
    )
    monkeypatch.setattr(
        preflight, "build_ccxt_demo_exchange", lambda **kwargs: FixtureExchange()
    )
    monkeypatch.setattr(
        preflight,
        "_adapter",
        lambda **kwargs: MarketBootstrapRejectingAdapter(kwargs["exchange"]),
    )
    artifact_root = tmp_path / "preflight-artifacts"
    monkeypatch.setattr(preflight, "ARTIFACT_ROOT", artifact_root)
    run_id = "preflight-market-bootstrap-fixture"
    session_id = f"preflight:{run_id}:p0:fixture"

    with pytest.raises(ReadOnlyPreflightError, match="preflight failed"):
        preflight.run_read_only_preflight(
            root=ROOT,
            run_id=run_id,
            offline_run_id="offline-fixture",
            preparation_id="preflight-package-fixture",
            session_id=session_id,
            arm_token=f"OKX_DEMO:{session_id}",
        )

    output = artifact_root / run_id
    result = json.loads(
        (output / "preflight/preflight_result.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (output / "preflight/terminal_account_only_failure_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["failure_stage"] == "PRE_MARKET_BOOTSTRAP"
    assert result["primary_read_method"] == "fetch_markets"
    assert result["primary_error_category"] == "TIMEOUT"
    assert result["terminal_account_authoritative"] is True
    assert result["terminal_reconciliation_mode"] == "ACCOUNT_ONLY_TWO_SNAPSHOT"
    assert len(result["terminal_account_snapshots"]) == 2
    assert all(
        row["position_btc"] == "0" and row["open_orders"] == 0
        for row in result["terminal_account_snapshots"]
    )
    assert manifest["mutation_retry"] is False
    assert result["mutation_attempts"] == 0
    assert result["live_endpoint_attempts"] == 0
    serialized = b"".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    )
    assert b"fixture-demo-account" not in serialized
    assert b"fixture-api-secret" not in serialized


def test_market_bootstrap_terminal_network_failure_records_final_read_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TerminalNetworkExchange(FixtureExchange):
        def fetch_time(self):
            raise ConnectionError("fixture network unavailable")

    class MarketBootstrapRejectingAdapter:
        last_account_only_diagnostic = None

        def __init__(self, exchange):
            self.exchange = exchange

        def preflight(self):
            self.exchange.fetch_markets()

    monkeypatch.setattr(
        preflight, "verify_offline_evidence", lambda *args, **kwargs: {"passed": True, "evidence_kind": "fixture"}
    )
    monkeypatch.setattr(preflight, "_preflight_predecessor_audit", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(preflight, "verify_preflight_preparation", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(preflight, "preflight_source_hashes", lambda root: {})
    monkeypatch.setattr(
        preflight, "_credential_config",
        lambda: SimpleNamespace(api_key="fixture-api-key", api_secret="fixture-api-secret", api_passphrase="fixture-passphrase"),
    )
    monkeypatch.setattr(preflight, "build_ccxt_demo_exchange", lambda **kwargs: TerminalNetworkExchange())
    monkeypatch.setattr(preflight, "_adapter", lambda **kwargs: MarketBootstrapRejectingAdapter(kwargs["exchange"]))
    artifact_root = tmp_path / "preflight-artifacts"
    monkeypatch.setattr(preflight, "ARTIFACT_ROOT", artifact_root)
    run_id = "preflight-market-bootstrap-network-fixture"
    session_id = f"preflight:{run_id}:p0:fixture"

    with pytest.raises(ReadOnlyPreflightError, match="preflight failed"):
        preflight.run_read_only_preflight(
            root=ROOT, run_id=run_id, offline_run_id="offline-fixture",
            preparation_id="preflight-package-fixture", session_id=session_id,
            arm_token=f"OKX_DEMO:{session_id}",
        )

    result = json.loads(
        (artifact_root / run_id / "preflight/preflight_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["primary_read_method"] == "fetch_markets"
    assert result["terminal_reconciliation_read_method"] == "fetch_time"
    assert result["terminal_reconciliation_error_category"] == "NETWORK"
    assert result["transport_audit"]["read_call_count"] == 9
    assert result["transport_audit"]["read_methods"] == [
        "fetch_markets", "fetch_time", "privateGetAccountConfig",
        "privateGetAccountPositions", "privateGetTradeOrdersPending",
    ]
    assert result["mutation_attempts"] == 0


@pytest.mark.parametrize(
    "first_changes,second_changes,error",
    [
        ({"position_btc": -0.01}, {}, "not flat"),
        ({}, {"open_orders": ({"id": "never-serialized"},)}, "not flat"),
        ({}, {"account_binding": "b" * 64}, "binding changed"),
        ({"clock_skew_ms": 1_501}, {}, "clock skew"),
    ],
)
def test_pre_market_terminal_pair_fails_closed_on_unsafe_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    first_changes: dict[str, object],
    second_changes: dict[str, object],
    error: str,
) -> None:
    base = {
        "account_binding": "a" * 64,
        "position_btc": 0,
        "open_orders": (),
        "clock_skew_ms": 0,
    }

    def snapshot(changes: dict[str, object]):
        values = {**base, **changes}
        return SimpleNamespace(
            **values,
            public_dict=lambda: {
                "account_binding": values["account_binding"],
                "position_btc": str(values["position_btc"]),
                "open_orders": len(values["open_orders"]),
                "clock_skew_ms": values["clock_skew_ms"],
                "terminal_reconciliation_only": True,
            },
        )

    rows = iter((snapshot(first_changes), snapshot(second_changes)))
    monkeypatch.setattr(
        preflight,
        "FormalDemoGateway",
        lambda proxy: SimpleNamespace(fetch_terminal_account_only=lambda: next(rows)),
    )
    proxy = SimpleNamespace(
        mutation_attempts=0,
        live_endpoint_attempts=0,
    )
    with pytest.raises(ReadOnlyPreflightError, match=error):
        _pre_market_terminal_account_pair(proxy)


def test_stale_offline_evidence_is_rejected_before_preflight() -> None:
    with pytest.raises(ReadOnlyPreflightError, match="source hash mismatch"):
        verify_offline_evidence(ROOT, "offline-20260803T160436Z")
    hashes = preflight_source_hashes(ROOT)
    assert "okx_fill_restart_preflight.py" in hashes
    assert "tests/test_okx_fill_restart_preflight.py" in hashes


def test_fill_cursor_repair_evidence_is_stale_after_shutdown_successor() -> None:
    with pytest.raises(ReadOnlyPreflightError, match="source hash mismatch"):
        verify_offline_evidence(ROOT, "repair-offline-20260805T155548Z")


def test_shutdown_repair_evidence_is_stale_after_future_book_successor() -> None:
    with pytest.raises(ReadOnlyPreflightError, match="source hash mismatch"):
        verify_offline_evidence(
            ROOT, "shutdown-repair-offline-20260806T142614Z"
        )


def test_future_book_repair_evidence_is_stale_after_multi_session_successor() -> None:
    with pytest.raises(ReadOnlyPreflightError, match="source hash mismatch"):
        verify_offline_evidence(
            ROOT, "future-book-repair-offline-20260806T160215Z"
        )


def test_multi_session_a0_evidence_is_hash_bound_and_preflight_eligible(
    tmp_path: Path,
) -> None:
    evidence_id = "multi-session-a0-offline-fixture"
    output = (
        tmp_path
        / "artifacts"
        / "okx_demo_multi_session_economic_soak"
        / evidence_id
    )

    def write_json(relative: str, payload: object) -> Path:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    successor_text = "# fixture successor\n"
    (tmp_path / "AGENTS.md").write_text(successor_text, encoding="utf-8")
    successor = (
        tmp_path
        / "AGENTS_OKX_DEMO_MULTI_SESSION_ECONOMIC_SOAK_FAILURE_INJECTION.md"
    )
    successor.write_text(successor_text, encoding="utf-8")
    decision = write_json(
        "decision/a0_decision.json",
        {"status": "OKX_DEMO_MULTI_SESSION_A0_OFFLINE_SUPPORT"},
    )
    source = write_json(
        "specification/source_hashes.json",
        {"AGENTS.md": sha256(tmp_path / "AGENTS.md")},
    )
    formal = write_json(
        "predecessor/formal_audit.json",
        {
            "passed": True,
            "immutable": True,
            "package_id": "formal-package-20260810T123953Z",
            "rerun_allowed": False,
        },
    )
    soak = write_json(
        "predecessor/bounded_soak_audit.json",
        {
            "passed": True,
            "immutable": True,
            "package_id": "soak-package-20260811T140223Z",
            "economic_promotion_evidence": False,
            "rerun_allowed": False,
        },
    )
    failed_a2 = write_json(
        "predecessor/failed_a2_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260811T164910Z",
            "session_package_id": (
                "soak-package-20260811T164910Z-s01-1a69fe577c"
            ),
            "fixed_hashes": {"fixture": "a" * 64},
            "historical_campaign_omitted_attempted_session": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    failed_a2_repair = write_json(
        "predecessor/failed_a2_repair_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260812T082847Z",
            "session_package_id": (
                "soak-package-20260812T082847Z-s01-7a3c1b97ef"
            ),
            "fixed_hashes": {"fixture": "b" * 64},
            "historical_create_counter_mismatch": True,
            "historical_failed_accounting_components_omitted": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    failed_a2_clock_gate = write_json(
        "predecessor/failed_a2_clock_gate_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260812T103316Z",
            "session_package_id": (
                "soak-package-20260812T103316Z-s01-671d71cd43"
            ),
            "fixed_hashes": {"fixture": "c" * 64},
            "pre_mutation_failure": True,
            "normal_create_dispatches": 0,
            "terminal_account_authoritative": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    failed_a2_registry_projection = write_json(
        "predecessor/failed_a2_registry_projection_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260812T144639Z",
            "session_package_id": (
                "soak-package-20260812T144639Z-s01-0728ed7a96"
            ),
            "fixed_hashes": {"fixture": "d" * 64},
            "source_evidence_seal_valid": True,
            "registry_projection_seal_valid": False,
            "session_two_never_authorized": True,
            "session_two_never_started": True,
            "terminal_account_authoritative": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    failed_a2_causal_clock = write_json(
        "predecessor/failed_a2_causal_clock_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260812T153839Z",
            "session_package_id": (
                "soak-package-20260812T153839Z-s01-04d9bb3dce"
            ),
            "fixed_hashes": {"fixture": "e" * 64},
            "future_fill_within_clock_skew_budget": True,
            "clock_skew_ms": [1307, 1311],
            "maximum_clock_skew_ms": 1500,
            "historical_defense_timestamp_ms": 0,
            "session_two_never_authorized": True,
            "session_two_never_started": True,
            "normal_create_dispatches": 2,
            "normal_create_acknowledgements": 2,
            "normal_create_unresolved": 0,
            "mutation_retries": 0,
            "terminal_account_authoritative": True,
            "terminal_position_btc": "0",
            "terminal_open_orders": 0,
            "two_flat_empty_snapshots": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    failed_a2_pre_dispatch_cross = write_json(
        "predecessor/failed_a2_pre_dispatch_cross_audit.json",
        {
            "passed": True,
            "immutable": True,
            "campaign_package_id": "economic-package-20260813T133023Z",
            "session_package_id": (
                "soak-package-20260813T133023Z-s04-54411e2b57"
            ),
            "fixed_hashes": {"fixture": "f" * 64},
            "historical_pre_dispatch_cross_treated_as_session_failure": True,
            "historical_pre_dispatch_mutation_delta": 0,
            "historical_attempted_aggregate_component_mismatch": True,
            "historical_attempted_economic_attribution_reconciles": False,
            "later_slots_never_authorized_or_started": True,
            "normal_create_dispatches": 168,
            "normal_create_acknowledgements": 168,
            "normal_create_unresolved": 0,
            "mutation_retries": 0,
            "terminal_account_authoritative": True,
            "terminal_position_btc": "0",
            "terminal_open_orders": 0,
            "two_flat_empty_snapshots": True,
            "repair_requires_fresh_identifiers": True,
            "rerun_or_resume_allowed": False,
        },
    )
    interrupted_a2 = write_json(
        "predecessor/interrupted_a2_audit.json",
        {
            "passed": True,
            "immutable": True,
            "interrupted": True,
            "campaign_incomplete": True,
            "campaign_package_id": "economic-package-20260813T144426Z",
            "session_package_id": (
                "soak-package-20260813T144426Z-s08-dd72c9e660"
            ),
            "fixed_hashes": {"fixture": "1" * 64},
            "active_slot": 8,
            "completed_slots": [1, 2, 3, 4, 5, 6, 7],
            "failed_slots": [],
            "later_slots_never_authorized_or_started": True,
            "authoritative_terminal_account_unknown": True,
            "last_durable_local_inventory_btc": "-0.010",
            "last_durable_owned_orders": 0,
            "last_durable_pending_intent": False,
            "normal_create_dispatches": 31,
            "normal_create_acknowledgements": 31,
            "normal_create_unresolved": 0,
            "completed_session_count": 7,
            "completed_normal_bid_fills": 5,
            "completed_normal_ask_fills": 7,
            "completed_normal_fifo_round_trips": 4,
            "completed_normal_net_pnl_usdt": "0.4763120",
            "completed_special_flatten_sessions": 4,
            "same_generation_resume_rerun_or_recovery_allowed": False,
            "fresh_read_only_preflight_required": True,
            "fresh_identifiers_required": True,
        },
    )
    manifest = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in (
            decision, source, formal, soak, failed_a2, failed_a2_repair,
            failed_a2_clock_gate,
            failed_a2_registry_projection,
            failed_a2_causal_clock,
            failed_a2_pre_dispatch_cross,
            interrupted_a2,
        )
    }
    completion = write_json("completion_hashes.json", manifest)
    write_json(
        "A0_OFFLINE_BUILD_COMPLETED.json",
        {
            "status": "OKX_DEMO_MULTI_SESSION_A0_OFFLINE_SUPPORT",
            "evidence_id": evidence_id,
            "A0_passed": True,
            "preflight_prepared": False,
            "preflight_executed": False,
            "economic_campaign_executed": False,
            "operational_failure_campaign_executed": False,
            "network_attempts": 0,
            "credential_accesses": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "failure_group_count": 10,
            "completion_hashes_sha256": sha256(completion),
            "source_manifest_sha256": sha256(source),
        },
    )

    audit = verify_offline_evidence(tmp_path, evidence_id)

    assert audit["passed"] is True
    assert audit["evidence_kind"] == "multi_session_a0_offline_build"
    assert audit["successor_protocol_active"] is True
    assert audit["formal_predecessor_verified"] is True
    assert audit["soak_predecessor_verified"] is True
    assert audit["failed_a2_predecessor_verified"] is True
    assert audit["repair_failed_a2_predecessor_verified"] is True
    assert audit["clock_gate_failed_a2_predecessor_verified"] is True
    assert audit["registry_projection_failed_a2_predecessor_verified"] is True
    assert audit["causal_clock_failed_a2_predecessor_verified"] is True
    assert audit["pre_dispatch_cross_failed_a2_predecessor_verified"] is True
    assert audit["interrupted_a2_predecessor_verified"] is True


def test_multi_session_a1_uses_both_frozen_predecessors_not_legacy_root() -> None:
    audit = _preflight_predecessor_audit(
        ROOT,
        {
            "evidence_kind": "multi_session_a0_offline_build",
            "formal_predecessor_verified": True,
            "soak_predecessor_verified": True,
            "failed_a2_predecessor_verified": True,
            "repair_failed_a2_predecessor_verified": True,
            "clock_gate_failed_a2_predecessor_verified": True,
            "registry_projection_failed_a2_predecessor_verified": True,
            "causal_clock_failed_a2_predecessor_verified": True,
            "pre_dispatch_cross_failed_a2_predecessor_verified": True,
            "interrupted_a2_predecessor_verified": True,
            "formal_predecessor_package_id": "formal-package-20260810T123953Z",
            "formal_predecessor_run_id": "formal-20260810T123953Z",
            "formal_predecessor_fixed_hashes_checked": 6,
            "soak_predecessor_package_id": "soak-package-20260811T140223Z",
            "soak_predecessor_run_id": "soak-20260811T140223Z",
            "soak_predecessor_fixed_hashes_checked": 4,
            "repair_failed_a2_campaign_package_id": (
                "economic-package-20260812T082847Z"
            ),
            "repair_failed_a2_fixed_hashes_checked": 11,
            "clock_gate_failed_a2_campaign_package_id": (
                "economic-package-20260812T103316Z"
            ),
            "clock_gate_failed_a2_fixed_hashes_checked": 11,
            "registry_projection_failed_a2_campaign_package_id": (
                "economic-package-20260812T144639Z"
            ),
            "registry_projection_failed_a2_fixed_hashes_checked": 12,
            "causal_clock_failed_a2_campaign_package_id": (
                "economic-package-20260812T153839Z"
            ),
            "causal_clock_failed_a2_fixed_hashes_checked": 15,
            "pre_dispatch_cross_failed_a2_campaign_package_id": (
                "economic-package-20260813T133023Z"
            ),
            "pre_dispatch_cross_failed_a2_fixed_hashes_checked": 18,
            "interrupted_a2_campaign_package_id": (
                "economic-package-20260813T144426Z"
            ),
            "interrupted_a2_session_package_id": (
                "soak-package-20260813T144426Z-s08-dd72c9e660"
            ),
            "interrupted_a2_fixed_hashes_checked": 15,
            "interrupted_a2_authoritative_terminal_account_unknown": True,
            "interrupted_a2_last_durable_local_inventory_btc": "-0.010",
        },
    )

    assert audit["passed"] is True
    assert audit["formal_package_id"] == "formal-package-20260810T123953Z"
    assert audit["soak_package_id"] == "soak-package-20260811T140223Z"
    assert audit["authority"] == (
        "immutable_formal_bounded_soak_six_failed_and_one_interrupted_A2_generation_via_multi_session_A0_evidence"
    )
    assert audit["causal_clock_failed_a2_package_id"] == (
        "economic-package-20260812T153839Z"
    )
    assert audit["pre_dispatch_cross_failed_a2_package_id"] == (
        "economic-package-20260813T133023Z"
    )
    assert audit["interrupted_a2_package_id"] == (
        "economic-package-20260813T144426Z"
    )
    assert audit["interrupted_a2_authoritative_terminal_account_unknown"] is True

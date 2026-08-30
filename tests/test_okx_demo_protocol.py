from pathlib import Path
from types import SimpleNamespace

from okx_demo_profile import canonical_sha256, load_promoted_profile
from okx_demo_protocol import (
    _build_run_audits,
    _build_spec,
    _formal_execution_markers,
    _formal_raw_completions,
    _safe_preflight,
)
from okx_demo_runtime import DemoMatrixSpec


ROOT = Path(__file__).resolve().parents[1]


def _tests() -> dict:
    return {
        "root": {"returncode": 0, "passed": 1},
        "backtest_non_optuna": {"returncode": 0, "passed": 1},
    }


def test_run_audits_reconcile_normal_and_special_fills() -> None:
    result = SimpleNamespace(
        order_events=[
            {
                "event": "POST_ONLY_ACKNOWLEDGED",
                "order_id": "normal-1",
                "client_order_id": "client-1",
            },
            {"event": "CANCEL_CONFIRMED", "order_id": "normal-1"},
        ],
        trade_events=[
            {
                "trade_id": "trade-normal",
                "classification": "NORMAL_MAKER",
                "normal_activity_eligible": True,
                "fee_usdt": 0.10,
            },
            {
                "trade_id": "trade-flatten",
                "classification": "SPECIAL_REDUCE_ONLY",
                "normal_activity_eligible": False,
                "fee_usdt": 0.25,
            },
        ],
        acknowledged_normal_orders=1,
        cancelled_normal_orders=1,
        duplicate_orders=0,
        ambiguous_retries=0,
        final_open_order_count=0,
        normal_fill_count=1,
        special_fill_count=1,
        unknown_fill_count=0,
        bid_normal_fills=1,
        ask_normal_fills=0,
        actual_fees_usdt=0.35,
        final_position_btc=0.0,
        market_events=[{"event": "MARKET_ACCEPTED", "age_ms": 20}],
        stale_placements=0,
        flatten_submission_count=1,
        flatten_confirmation_count=1,
        gross_execution_pnl_usdt=1.0,
        net_execution_pnl_usdt=0.65,
    )
    audits = _build_run_audits(
        result=result,
        matrix=DemoMatrixSpec(),
        tests=_tests(),
    )
    assert all(audit["passed"] for audit in audits.values())
    assert audits["classification_audit"]["NORMAL_MAKER"] == 1
    assert audits["classification_audit"]["SPECIAL_REDUCE_ONLY"] == 1
    assert audits["classification_audit"][
        "special_exits_excluded_from_normal_activity"
    ] is True


def test_protocol_spec_hashes_runtime_configuration_and_readiness_source() -> None:
    profile = load_promoted_profile(ROOT)
    spec = _build_spec(
        root=ROOT,
        run_id="offline-spec-test",
        profile=profile,
        preflight={"market_fingerprint": "market-fixture"},
        matrix=DemoMatrixSpec(),
        tests=_tests(),
    )
    assert spec["runtime_configuration_sha256"] == canonical_sha256(
        spec["runtime_configuration"]
    )
    assert "okx_production_readiness.py" in spec["source_hashes"]
    assert "create_accepted_response_lost" in spec["offline_fixture_ids"]
    assert spec["artifact_contract"]["execution_armed_marker"] == (
        "EXECUTION_ARMED.json"
    )


def test_formal_raw_completion_disables_any_second_formal_execution(
    tmp_path: Path,
) -> None:
    completion = (
        tmp_path
        / "artifacts"
        / "okx_demo_execution_safety"
        / "formal-one"
        / "formal_run"
        / "RAW_COMPLETED.json"
    )
    completion.parent.mkdir(parents=True)
    completion.write_text("{}\n", encoding="utf-8")
    assert _formal_raw_completions(tmp_path) == [completion]
    assert _formal_execution_markers(tmp_path) == [completion]


def test_execution_armed_marker_blocks_rerun_after_midstream_crash(
    tmp_path: Path,
) -> None:
    marker = (
        tmp_path
        / "artifacts"
        / "okx_demo_execution_safety"
        / "formal-crash"
        / "formal_run"
        / "EXECUTION_ARMED.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("{}\n", encoding="utf-8")
    assert _formal_execution_markers(tmp_path) == [marker]


def test_safe_preflight_never_configures_or_mutates_exchange(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = {
        "preflight": 0,
        "configure": 0,
        "create": 0,
        "cancel": 0,
    }
    snapshot = SimpleNamespace(
        position_mode="net_mode",
        leverage=3.0,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        free_equity_usdt=10_000.0,
        open_orders=(),
        position_btc=0.0,
        public_dict=lambda: {
            "position_mode": "net_mode",
            "leverage": 3.0,
            "open_orders": 0,
            "position_btc": 0.0,
        },
    )
    profile = SimpleNamespace(
        binding_sha256="profile-binding",
        strategy=SimpleNamespace(
            maker_fee_rate=0.0002,
            taker_fee_rate=0.0005,
            fixed_lot_size_btc=0.01,
        ),
        capital_policy={
            "leverage": 3,
            "capital_usdt": 750.0,
            "maximum_margin_utilization": 0.8,
        },
    )
    exchange = SimpleNamespace(
        options={"sandboxMode": True},
        headers={"x-simulated-trading": "1"},
        fetch_order_book=lambda _symbol: {
            "timestamp": int(__import__("time").time() * 1000),
            "bids": [[49_999.0, 1.0]],
            "asks": [[50_001.0, 1.0]],
        },
    )

    class FakeAdapter:
        market_spec = SimpleNamespace(
            to_dict=lambda: {"symbol": "BTC/USDT:USDT"},
            fingerprint="market-fingerprint",
        )
        market_metadata_quarantine = ()

        def preflight(self):
            calls["preflight"] += 1
            return snapshot

        def configure_and_verify_account_mode(self):
            calls["configure"] += 1

        def submit_post_only(self, **_kwargs):
            calls["create"] += 1

        def cancel_all_owned(self):
            calls["cancel"] += 1

    monkeypatch.setattr(
        "okx_demo_protocol._credential_config",
        lambda: SimpleNamespace(
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
        ),
    )
    monkeypatch.setattr(
        "okx_demo_protocol.load_promoted_profile",
        lambda _root: profile,
    )
    monkeypatch.setattr(
        "okx_demo_protocol.build_ccxt_demo_exchange",
        lambda **_kwargs: exchange,
    )
    monkeypatch.setattr(
        "okx_demo_protocol._adapter",
        lambda **_kwargs: FakeAdapter(),
    )

    metadata, returned_exchange, returned_profile = _safe_preflight(
        root=tmp_path,
        output=tmp_path / "preflight",
        session_id="preflight:fixture",
        arm_token="OKX_DEMO:preflight:fixture",
    )

    assert calls == {
        "preflight": 2,
        "configure": 0,
        "create": 0,
        "cancel": 0,
    }
    assert metadata["verified_snapshot"]["position_mode"] == "net_mode"
    assert returned_exchange is exchange
    assert returned_profile is profile

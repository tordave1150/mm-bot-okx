import json
from pathlib import Path

import pytest

import okx_demo_state
from okx_demo_state import (
    DEMO_ENVIRONMENT,
    DemoRuntimeState,
    DemoStateError,
    DemoStateStore,
    FillCursor,
)


def _state() -> DemoRuntimeState:
    return DemoRuntimeState(
        environment=DEMO_ENVIRONMENT,
        account_uid="demo-account-1",
        symbol="BTC/USDT:USDT",
        market_fingerprint="market-hash",
        profile_binding_sha256="profile-hash",
        session_id="session-1",
        peak_equity_usdt=750.0,
        current_equity_usdt=750.0,
    )


def test_fill_cursor_handles_duplicate_reordered_and_late_trades() -> None:
    cursor = FillCursor()
    first = cursor.select_new([
        {"id": "b", "timestamp": 101},
        {"id": "a", "timestamp": 100},
        {"id": "b", "timestamp": 101},
    ])
    assert [row["id"] for row in first] == ["a", "b"]
    second = cursor.select_new([
        {"id": "a", "timestamp": 100},
        {"id": "b", "timestamp": 101},
        {"id": "c", "timestamp": 101},
    ])
    assert [row["id"] for row in second] == ["c"]
    assert cursor.timestamp_ms == 101
    assert cursor.ids_at_timestamp == ["b", "c"]


def test_state_round_trip_binds_environment_account_symbol_market_and_profile(tmp_path: Path) -> None:
    store = DemoStateStore(tmp_path / "state.json")
    state = _state()
    state.kill_switch.activate("test", now=100.0)
    store.save(state)
    restored = store.load(
        account_uid=state.account_uid,
        symbol=state.symbol,
        market_fingerprint=state.market_fingerprint,
        profile_binding_sha256=state.profile_binding_sha256,
    )
    assert restored is not None
    assert restored.kill_switch.active is True
    assert restored.kill_switch.activation_id == state.kill_switch.activation_id
    assert restored.net_realized_pnl_usdt == 0.0


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_uid", "other"),
        ("symbol", "ETH/USDT:USDT"),
        ("market_fingerprint", "other-market"),
        ("profile_binding_sha256", "other-profile"),
    ],
)
def test_wrong_state_identity_fails_closed(tmp_path: Path, field: str, value: str) -> None:
    store = DemoStateStore(tmp_path / "state.json")
    state = _state()
    store.save(state)
    expected = {
        "account_uid": state.account_uid,
        "symbol": state.symbol,
        "market_fingerprint": state.market_fingerprint,
        "profile_binding_sha256": state.profile_binding_sha256,
    }
    expected[field] = value
    with pytest.raises(DemoStateError, match="identity mismatch"):
        store.load(**expected)


def test_corrupt_state_fails_closed_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    store = DemoStateStore(path)
    with pytest.raises(DemoStateError, match="state load failed"):
        store.load(
            account_uid="a", symbol="s", market_fingerprint="m",
            profile_binding_sha256="p",
        )
    assert path.read_text(encoding="utf-8") == "{broken"


def test_stale_state_fails_closed_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = DemoStateStore(path)
    state = _state()
    store.save(state)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at_ms"] = 1_000
    original = json.dumps(payload, sort_keys=True)
    path.write_text(original, encoding="utf-8")
    with pytest.raises(DemoStateError, match="stale"):
        store.load(
            account_uid=state.account_uid,
            symbol=state.symbol,
            market_fingerprint=state.market_fingerprint,
            profile_binding_sha256=state.profile_binding_sha256,
            now_ms=1_000_000,
            maximum_age_ms=10_000,
        )
    assert path.read_text(encoding="utf-8") == original


def test_future_dated_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = DemoStateStore(path)
    state = _state()
    store.save(state)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at_ms"] = 20_000
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DemoStateError, match="future-dated"):
        store.load(
            account_uid=state.account_uid,
            symbol=state.symbol,
            market_fingerprint=state.market_fingerprint,
            profile_binding_sha256=state.profile_binding_sha256,
            now_ms=10_000,
        )


def test_persistence_failure_propagates_and_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    store = DemoStateStore(path)
    def fail_replace(source, target):
        raise OSError("injected replace failure")
    monkeypatch.setattr(okx_demo_state.os, "replace", fail_replace)
    with pytest.raises(DemoStateError, match="state save failed"):
        store.save(_state())
    assert not path.exists()
    assert not path.with_suffix(".json.tmp").exists()

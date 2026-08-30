import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_spec import MarketSpec
from okx_fill_restart_executor import (
    FormalExecutionDriver,
    FormalExecutionError,
    FrozenPackage,
    HashChainStream,
    load_frozen_package,
    start,
)
from okx_fill_restart_formal import make_fixture_spec
from okx_fill_restart_offline import _sha256
from okx_fill_restart_validation import OwnedOrder, ValidationSafetyError


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _market() -> MarketSpec:
    return MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.01"),
        amount_step=Decimal("1"),
        min_amount=Decimal("1"),
        min_notional=None,
        price_tick=Decimal("0.1"),
        amount_precision=0,
        price_precision=1,
        linear=True,
        inverse=False,
    )


def test_hash_chain_stream_round_trip_and_truncation_detection(tmp_path: Path) -> None:
    stream = HashChainStream(tmp_path / "events.jsonl")
    first = stream.append({"event": "one"})
    second = stream.append({"event": "two"})
    assert first != second
    assert stream.last_payload() == {"event": "two"}
    stream.path.write_text(
        stream.path.read_text(encoding="utf-8").rstrip("\n"),
        encoding="utf-8",
    )
    with pytest.raises(FormalExecutionError, match="truncated"):
        stream.last_payload()


def test_frozen_package_verifies_completion_and_runtime_source_hashes(
    tmp_path: Path,
) -> None:
    package_id = "formal-package-fixture-load"
    spec = make_fixture_spec(
        formal_run_id="formal-fixture-load",
        package_id=package_id,
    )
    output = tmp_path / "artifacts" / "okx_demo_fill_restart_validation" / package_id
    source = tmp_path / "runtime.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _json(output / "specification" / "formal_controller_spec.json", spec.to_dict())
    _json(output / "specification" / "source_hashes.json", {
        "runtime.py": _sha256(source),
    })
    _json(output / "FORMAL_PACKAGE_COMPLETED.json", {
        "phase_status": "FORMAL_PACKAGE_FROZEN_OFFLINE",
        "package_id": package_id,
        "formal_execution_armed": False,
    })
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in output.rglob("*") if path.is_file()
    }
    _json(output / "completion_hashes.json", completion)
    loaded = load_frozen_package(tmp_path, package_id)
    assert loaded.spec.formal_run_id == "formal-fixture-load"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(FormalExecutionError, match="source drift"):
        load_frozen_package(tmp_path, package_id)


def test_wrong_formal_arm_token_creates_no_execution_marker(tmp_path: Path) -> None:
    spec = make_fixture_spec(
        formal_run_id="formal-arm-fixture",
        package_id="formal-package-arm-fixture",
    )
    package = FrozenPackage(
        root=tmp_path,
        output=tmp_path / "package",
        package_id=spec.package_id,
        spec=spec,
        source_hashes={},
    )
    with pytest.raises(FormalExecutionError, match="arm token"):
        start(package, "wrong")
    assert not (package.output / "formal_run" / "FORMAL_EXECUTION_ARMED.json").exists()


def test_trade_conversion_requires_exact_owned_identity_and_contract_units() -> None:
    order = OwnedOrder(
        client_order_id="fr-owned",
        order_id="order-owned",
        side="buy",
        quantity_btc=Decimal("0.01"),
        remaining_btc=Decimal("0.01"),
        reduce_only=False,
        post_only_acknowledged=True,
    )
    driver = object.__new__(FormalExecutionDriver)
    driver.gateway = SimpleNamespace(market_spec=_market())
    driver.engine = SimpleNamespace(
        state=SimpleNamespace(owned_orders={"fr-owned": order})
    )
    trade = {
        "id": "trade-owned",
        "order": "order-owned",
        "timestamp": 1_000,
        "side": "buy",
        "price": 50_000,
        "amount": 1,
        "fee": {"cost": 0.1, "currency": "USDT"},
        "takerOrMaker": "maker",
        "info": {"clOrdId": "fr-owned"},
    }
    pages = driver._fill_pages([trade])
    assert pages[0]["trades"][0]["quantity_btc"] == "0.01"
    assert pages[0]["trades"][0]["reduce_only"] is False
    trade["info"] = {"clOrdId": "foreign"}
    with pytest.raises(ValidationSafetyError, match="unknown"):
        driver._fill_pages([trade])

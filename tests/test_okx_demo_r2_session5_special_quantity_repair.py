from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from okx_demo_multi_session_campaign import (
    CampaignError,
    _validate_special_closed_fill,
)


ROOT = Path(__file__).resolve().parents[1]
RUN = (
    ROOT
    / "artifacts/okx_demo_soak_validation"
    / "soak-package-20260830T123628Z-s05-0396d10308"
    / "soak_run"
)


def _historical_special_closed_fill() -> dict[str, object]:
    economics = json.loads(
        (RUN / "audits/economics.json").read_text(encoding="utf-8")
    )
    rows = list(economics["special_closed_causal_fills"])
    assert len(rows) == 1
    return dict(rows[0])


def test_historical_partial_workoff_residual_is_valid_terminal_closure() -> None:
    row = _historical_special_closed_fill()
    binding = dict(row["causal_binding"])
    assert binding["fill_quantity_btc"] == "0.010"
    assert binding["matched_workoff_btc"] == "0.0095"
    assert binding["remaining_workoff_btc"] == "0.0005"
    assert _validate_special_closed_fill(row) == "4378447404"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("remaining_workoff_btc", "0.0004"),
        ("matched_workoff_btc", "0.0094"),
        ("workoff_trade_ids", []),
        ("workoff_order_ids", []),
    ),
)
def test_historical_partial_workoff_forgery_remains_fail_closed(
    field: str, value: object
) -> None:
    row = deepcopy(_historical_special_closed_fill())
    row["causal_binding"][field] = value  # type: ignore[index]
    with pytest.raises(CampaignError, match="quantities do not reconcile"):
        _validate_special_closed_fill(row)

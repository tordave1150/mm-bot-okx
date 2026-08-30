"""Read-only attribution of the closed MM v1.5 winning profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from backtest.mm_v1_3c_evidence import normal_fifo_evidence


PROFILE_ID = "mm-v1-5-profile-08"
PROFILE_NAME = "COMPOSITE_DRAWDOWN_REPAIR"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit(root: Path, source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError("refusing closed-evidence audit overwrite")
    completed = json.loads((source / "COMPLETED.json").read_text())
    hashes_valid = all(
        (source / name).is_file() and _hash(source / name) == expected
        for name, expected in completed["files"].items()
    )
    if completed["status"] != "COMPLETED" or not hashes_valid:
        raise RuntimeError("closed v1.5 evidence hash verification failed")

    fills = [
        row for row in _load_jsonl(source / "fills.jsonl")
        if row["profile_id"] == PROFILE_ID
    ]
    trips = [
        row for row in _load_jsonl(source / "round_trips.jsonl")
        if row["profile_id"] == PROFILE_ID
    ]
    metas = [
        row for row in _load_jsonl(source / "path_results.jsonl")
        if row["profile_id"] == PROFILE_ID
    ]
    markouts = [
        row for row in _load_jsonl(source / "markouts.jsonl")
        if row["profile_id"] == PROFILE_ID
    ]
    defensive = [
        row for row in _load_jsonl(source / "defensive_events.jsonl")
        if row["profile_id"] == PROFILE_ID
    ]
    fifo_trips, special, reconciliation = normal_fifo_evidence(fills)
    if len(fifo_trips) != len(trips):
        raise RuntimeError("closed normal FIFO evidence changed")

    fill_by_id = {row["fill_id"]: row for row in fills}
    market = _load_jsonl(source / "market_events.jsonl")
    mid_by_path_tick = {
        (row["path_id"], int(row["tick"])): float(row["mid"])
        for row in market
    }
    gross_spread = 0.0
    inventory_pnl = 0.0
    phase_net: dict[str, float] = defaultdict(float)
    for trip in fifo_trips:
        entry = fill_by_id[trip["entry_fill_id"]]
        exit_fill = fill_by_id[trip["exit_fill_id"]]
        entry_mid = mid_by_path_tick[
            (trip["path_id"], int(entry["quote_created_tick"]))
        ]
        exit_mid = mid_by_path_tick[
            (trip["path_id"], int(exit_fill["quote_created_tick"]))
        ]
        quantity = float(trip["matched_quantity_btc"])
        if trip["entry_side"] == "buy":
            spread = (
                entry_mid - float(trip["entry_price"])
                + float(trip["exit_price"]) - exit_mid
            ) * quantity
        else:
            spread = (
                float(trip["entry_price"]) - entry_mid
                + exit_mid - float(trip["exit_price"])
            ) * quantity
        gross = float(trip["gross_execution_pnl"])
        gross_spread += spread
        inventory_pnl += gross - spread
        phase_net[exit_fill["regime_phase"]] += float(
            trip["net_execution_pnl"]
        )

    special_by_trigger: dict[str, dict[str, float]] = defaultdict(
        lambda: {"fills": 0, "gross_pnl_usdt": 0.0, "fees_usdt": 0.0}
    )
    for row in special:
        special_by_trigger[row["fill_trigger"]]["gross_pnl_usdt"] += float(
            row["gross_pnl_usdt"]
        )
    for row in fills:
        trigger = row["fill_trigger"]
        if row["special_exit"]:
            special_by_trigger[trigger]["fills"] += 1
            special_by_trigger[trigger]["fees_usdt"] += float(row["fee"])
    for trigger, values in special_by_trigger.items():
        values["net_contribution_usdt"] = (
            values["gross_pnl_usdt"] - values["fees_usdt"]
        )
    for row in special:
        fill = fill_by_id[row["special_exit_fill_id"]]
        phase_net[fill["regime_phase"]] += float(row["gross_pnl_usdt"])
    for row in fills:
        if row["special_exit"]:
            phase_net[row["regime_phase"]] -= float(row["fee"])

    fees_by_trigger: dict[str, float] = defaultdict(float)
    for row in fills:
        fees_by_trigger[row["fill_trigger"]] += float(row["fee"])
    net_pnl = sum(float(row["net_pnl"]) for row in metas)
    normal_gross = sum(float(row["gross_execution_pnl"]) for row in trips)
    total_fees = sum(float(row["fee"]) for row in fills)
    special_gross = sum(float(row["gross_pnl_usdt"]) for row in special)
    identity_value = normal_gross + special_gross - total_fees
    emergency = special_by_trigger.get(
        "EMERGENCY_EXECUTION",
        {"fills": 0, "gross_pnl_usdt": 0.0, "fees_usdt": 0.0,
         "net_contribution_usdt": 0.0},
    )
    net_without_emergency = net_pnl - float(
        emergency["net_contribution_usdt"]
    )
    severity_net: dict[str, float] = defaultdict(float)
    scenario_net: dict[str, float] = defaultdict(float)
    for row in metas:
        severity_net[row["severity"]] += float(row["net_pnl"])
        scenario_net[row["scenario"]] += float(row["net_pnl"])

    audit_payload = {
        "protocol": "MM_V1_6_CLOSED_V1_5_ATTRIBUTION",
        "source_profile_id": PROFILE_ID,
        "source_profile_name": PROFILE_NAME,
        "source_directory": str(source.relative_to(root)),
        "source_completion_hashes_valid": hashes_valid,
        "source_path_count": len(metas),
        "fees": {
            "by_trigger_usdt": dict(sorted(fees_by_trigger.items())),
            "normal_maker_fees_usdt": fees_by_trigger[
                "STRICT_TRADE_THROUGH"
            ],
            "terminal_fees_usdt": fees_by_trigger["TERMINAL_EXECUTION"],
            "hard_kill_fees_usdt": fees_by_trigger["HARD_KILL_EXECUTION"],
            "emergency_fees_usdt": fees_by_trigger["EMERGENCY_EXECUTION"],
            "total_fees_usdt": total_fees,
        },
        "execution_attribution": {
            "normal_gross_execution_pnl_usdt": normal_gross,
            "reconstructed_gross_spread_capture_usdt": gross_spread,
            "reconstructed_inventory_pnl_usdt": inventory_pnl,
            "normal_net_round_trip_pnl_usdt": sum(
                float(row["net_execution_pnl"]) for row in trips
            ),
            "special_exit_gross_pnl_usdt": special_gross,
            "special_by_trigger": dict(sorted(special_by_trigger.items())),
            "net_pnl_usdt": net_pnl,
            "accounting_identity_value_usdt": identity_value,
            "accounting_reconciles": abs(net_pnl - identity_value) <= 1e-7,
            "spread_inventory_reconciles": abs(
                normal_gross - gross_spread - inventory_pnl
            ) <= 1e-7,
        },
        "markout": {
            "normal_fill_count": sum(
                row["normal_activity_eligible"] for row in fills
            ),
            "average_quantity_weighted_5_tick_usdt": mean(
                float(row["quantity_weighted_markout_5"])
                for row in markouts
                if fill_by_id[row["fill_id"]]["normal_activity_eligible"]
            ),
        },
        "net_pnl_by_severity_usdt": dict(severity_net),
        "net_pnl_by_scenario_usdt": dict(scenario_net),
        "attributed_net_pnl_by_exit_phase_usdt": dict(phase_net),
        "drawdown_guard": {
            "activation_count": sum(
                row["event"] == "DRAWDOWN_GUARD_ENTRY"
                for row in defensive
            ),
            "emergency_exit_count": int(emergency["fills"]),
            "emergency_gross_pnl_usdt": emergency["gross_pnl_usdt"],
            "emergency_taker_fees_usdt": emergency["fees_usdt"],
            "emergency_net_contribution_usdt": emergency[
                "net_contribution_usdt"
            ],
            "net_pnl_without_emergency_contribution_usdt": (
                net_without_emergency
            ),
            "emergency_cost_alone_explains_total_loss": (
                net_without_emergency >= 0
            ),
        },
        "activity_and_reentry": {
            "normal_fills": sum(
                row["normal_activity_eligible"] for row in fills
            ),
            "bid_normal_fills": sum(
                row["normal_activity_eligible"] and row["side"] == "buy"
                for row in fills
            ),
            "ask_normal_fills": sum(
                row["normal_activity_eligible"] and row["side"] == "sell"
                for row in fills
            ),
            "normal_fifo_round_trips": len(trips),
            "defensive_exit_count": sum(
                row["event"] == "DEFENSIVE_MODE_EXIT" for row in defensive
            ),
            "completed_reentry_count": sum(
                row["event"] == "DEFENSIVE_REENTRY_READY"
                for row in defensive
            ),
        },
        "fifo_reconciliation": reconciliation,
        "conclusion": (
            "Emergency execution is material but does not alone explain the "
            "negative result; removing its gross loss and taker fees still "
            "leaves negative PnL. Normal/terminal adverse economics and fee "
            "coverage therefore remain in scope for v1.6."
        ),
    }
    output.mkdir(parents=True)
    _write_json(output / "closed_v1_5_attribution.json", audit_payload)
    report = (
        "# MM v1.6 Closed v1.5 Attribution\n\n"
        f"- Net PnL: {net_pnl:.6f} USDT\n"
        f"- Emergency net contribution: "
        f"{float(emergency['net_contribution_usdt']):.6f} USDT\n"
        f"- Net PnL without emergency contribution: "
        f"{net_without_emergency:.6f} USDT\n"
        f"- Emergency cost alone explains loss: "
        f"{net_without_emergency >= 0}\n"
        f"- Accounting reconciles: "
        f"{audit_payload['execution_attribution']['accounting_reconciles']}\n"
    )
    with (output / "closed_v1_5_attribution.md").open(
        "x", encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(report)
    _write_json(output / "COMPLETED.json", {
        "status": "COMPLETED",
        "files": {
            path.name: _hash(path)
            for path in sorted(output.iterdir())
            if path.name != "COMPLETED.json"
        },
    })
    return {
        "net_pnl_usdt": net_pnl,
        "emergency_net_contribution_usdt": emergency[
            "net_contribution_usdt"
        ],
        "net_pnl_without_emergency_usdt": net_without_emergency,
        "emergency_cost_alone_explains_total_loss": (
            net_without_emergency >= 0
        ),
        "accounting_reconciles": audit_payload[
            "execution_attribution"
        ]["accounting_reconciles"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = audit(root, arguments.source, arguments.output)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

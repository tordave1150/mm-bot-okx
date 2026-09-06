"""Child-process entrypoint for one supervised R2 Stage C session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from okx_demo_r2_stage_c_executor import execute_r2_stage_c


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one supervised R2 Stage C worker")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--r0-evidence-id", required=True)
    parser.add_argument("--r1-run-id", required=True)
    parser.add_argument("--canary-run-id", required=True)
    parser.add_argument("--stage-c-prep-ref", required=True)
    parser.add_argument("--execution-stage", required=True)
    parser.add_argument("--slot-json", required=True)
    parser.add_argument("--warmup-ticks", type=int, required=True)
    parser.add_argument("--tick-interval-s", type=float, required=True)
    parser.add_argument("--resting-s", type=float, required=True)
    args = parser.parse_args()
    slot = json.loads(args.slot_json)
    if not isinstance(slot, dict):
        raise ValueError("Stage C worker slot must be a JSON object")
    result = execute_r2_stage_c(
        root=args.root, stamp=args.stamp, campaign_id=args.campaign_id,
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION", slot_schedule=[slot],
        r0_closure_ref=args.r0_evidence_id, r1_run_id=args.r1_run_id,
        r2_canary_run_id=args.canary_run_id, stage_c_prep_ref=args.stage_c_prep_ref,
        execution_stage=args.execution_stage, warmup_ticks=args.warmup_ticks,
        tick_interval_s=args.tick_interval_s, resting_s=args.resting_s,
    )
    print(result)


if __name__ == "__main__":
    main()

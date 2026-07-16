"""
backtest/agent_loop.py — AI Agent backtest orchestration loop.

5 sub-loops per scenario:
    1. Scenario Designer  — picks parameters from config bounds
    2. Synthetic Data     — generates ticks
    3. Backtest Runner    — runs the simulation
    4. Metrics Tool       — computes risk metrics
    5. Evaluator          — checks against acceptance criteria

Stopping condition (measurable, not agent-stated):
    Pass N_CONSECUTIVE_PASS scenarios (from YAML) with ALL thresholds met.
    Any failure resets the consecutive counter to 0.

IMPORTANT: This loop NEVER modifies config.py.  It prints a proposal report
with suggested parameter changes for human review.

Usage::

    python -m backtest.agent_loop
    python -m backtest.agent_loop --config backtest/scenarios/default_scenarios.yaml
    python -m backtest.agent_loop --config backtest/scenarios/default_scenarios.yaml --max-scenarios 50
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import math
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ── Ensure project root importable ───────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config, load_config
from backtest.synthetic_data import (
    generate_regime_switching_gbm,
    generate_block_bootstrap,
    generate_garch_path,
)
from backtest.runner import BacktestRunner, BacktestResult
from backtest.metrics import compute_metrics, format_metrics_table

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── ScenarioSpec ──────────────────────────────────────────────────────────────

@dataclass
class ScenarioSpec:
    """Fully resolved parameters for one backtest scenario."""
    name: str
    generator: str            # "gbm", "block_bootstrap", "garch"
    seed: int
    n_days: int
    ticks_per_day: int
    start_price: float
    is_crash: bool = False
    expect_kill_switch: bool = False

    # GBM params
    vol_weekly: float = 0.25
    regime_duration_days: float = 3.0
    jump_freq: float = 0.2
    jump_size: float = 0.02

    # Block bootstrap params
    block_libraries: list[str] = field(default_factory=list)
    block_size: int = 5

    # GARCH params
    historical_prices: list[float] = field(default_factory=list)

    def __str__(self) -> str:
        base = (f"[{self.name}] generator={self.generator} seed={self.seed} "
                f"n_days={self.n_days} crash={self.is_crash}")
        if self.generator == "gbm":
            return f"{base} vol_weekly={self.vol_weekly:.3f} jump_freq={self.jump_freq:.2f}"
        if self.generator == "block_bootstrap":
            return f"{base} libs={self.block_libraries}"
        return base


# ── EvaluationResult ─────────────────────────────────────────────────────────

@dataclass
class EvaluationResult:
    scenario: ScenarioSpec
    metrics: dict[str, Any]
    passed: bool
    failures: list[str]   # descriptions of failed checks


# ── AgentLoop ────────────────────────────────────────────────────────────────

class AgentLoop:
    """Orchestrates scenario generation → backtest → metrics → evaluation.

    Parameters
    ----------
    config_path : str | None
        Path to scenario YAML config.  Defaults to
        ``backtest/scenarios/default_scenarios.yaml`` in the project root.
    bot_config : Config | None
        Bot configuration.  Uses ``load_config()`` if None.
    max_scenarios : int
        Hard cap on total scenarios to run (safety valve).
    """

    def __init__(
        self,
        config_path: str | None = None,
        bot_config: Config | None = None,
        max_scenarios: int = 200,
    ) -> None:
        self.bot_config = bot_config or load_config()
        self.max_scenarios = max_scenarios

        # Load scenario YAML
        if config_path is None:
            config_path = os.path.join(
                _PROJECT_ROOT, "backtest", "scenarios", "default_scenarios.yaml"
            )
        self.scenario_cfg = self._load_yaml(config_path)

        # Extract acceptance criteria
        ac = self.scenario_cfg.get("acceptance_criteria", {})
        self.max_dd_limit: float = float(ac.get("max_dd_limit", 0.15))
        self.cvar_95_limit: float = float(ac.get("cvar_95_limit", 0.08))
        self.allow_kill_switch_normal: bool = bool(ac.get("allow_kill_switch_in_normal", False))
        self.require_kill_switch_crash: bool = bool(ac.get("require_kill_switch_in_crash", True))
        self.n_consecutive_pass: int = int(
            self.scenario_cfg.get("stopping", {}).get("n_consecutive_pass", 20)
        )

        # Bounds for normal scenario sampling
        bounds = self.scenario_cfg.get("scenario_bounds", {})
        self.vol_min: float = float(bounds.get("vol_weekly_min", 0.15))
        self.vol_max: float = float(bounds.get("vol_weekly_max", 0.40))
        self.reg_dur_min: float = float(bounds.get("regime_duration_days_min", 1.0))
        self.reg_dur_max: float = float(bounds.get("regime_duration_days_max", 7.0))
        self.jump_freq_min: float = float(bounds.get("jump_freq_min", 0.0))
        self.jump_freq_max: float = float(bounds.get("jump_freq_max", 0.5))
        self.jump_size_min: float = float(bounds.get("jump_size_min", 0.005))
        self.jump_size_max: float = float(bounds.get("jump_size_max", 0.04))
        self.n_days: int = int(bounds.get("n_days", 30))
        self.ticks_per_day: int = int(bounds.get("ticks_per_day", 288))
        self.start_price: float = float(bounds.get("start_price", 50_000.0))

        # Seed sequence
        self.seeds: list[int] = [int(s) for s in self.scenario_cfg.get("seeds", list(range(1, 51)))]

        # Crash scenarios
        self.crash_scenarios: list[dict] = self.scenario_cfg.get("crash_scenarios", [])

        # State
        self._consecutive_pass: int = 0
        self._total_run: int = 0
        self._all_results: list[EvaluationResult] = []
        self._rng = np.random.default_rng(0)  # for sampling bounds

    # ── Main entry ────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the agent loop until stopping condition or max_scenarios."""
        print(self._header())

        # First, run all crash scenarios (always included)
        crash_specs = self._build_crash_specs()
        print(f"\n{'='*60}")
        print(f"  Running {len(crash_specs)} crash scenarios first...")
        print(f"{'='*60}")

        for spec in crash_specs:
            if self._total_run >= self.max_scenarios:
                break
            self._run_one(spec)

        # Then run normal scenarios until stopping condition
        seed_idx = 0
        print(f"\n{'='*60}")
        print(f"  Starting normal scenario sweep (target: {self.n_consecutive_pass} consecutive passes)")
        print(f"{'='*60}")

        while (
            self._consecutive_pass < self.n_consecutive_pass
            and self._total_run < self.max_scenarios
        ):
            # Cycle through seeds
            seed = self.seeds[seed_idx % len(self.seeds)]
            seed_idx += 1

            spec = self._design_normal_scenario(seed, self._total_run)
            self._run_one(spec)

        print(self._final_report())

    # ── Sub-loop 1: Scenario Designer ─────────────────────────────────────

    def _design_normal_scenario(self, seed: int, iteration: int) -> ScenarioSpec:
        """Sample a normal scenario from the configured bounds."""
        rng = np.random.default_rng(seed + iteration * 997)

        vol_weekly = float(rng.uniform(self.vol_min, self.vol_max))
        regime_dur = float(rng.uniform(self.reg_dur_min, self.reg_dur_max))
        jump_freq = float(rng.uniform(self.jump_freq_min, self.jump_freq_max))
        jump_size = float(rng.uniform(self.jump_size_min, self.jump_size_max))

        return ScenarioSpec(
            name=f"normal_s{self._total_run:04d}",
            generator="gbm",
            seed=seed,
            n_days=self.n_days,
            ticks_per_day=self.ticks_per_day,
            start_price=self.start_price,
            is_crash=False,
            expect_kill_switch=False,
            vol_weekly=vol_weekly,
            regime_duration_days=regime_dur,
            jump_freq=jump_freq,
            jump_size=jump_size,
        )

    def _build_crash_specs(self) -> list[ScenarioSpec]:
        """Convert YAML crash scenario entries to ScenarioSpec objects."""
        specs: list[ScenarioSpec] = []
        for cs in self.crash_scenarios:
            seed = self.seeds[len(specs) % len(self.seeds)]
            spec = ScenarioSpec(
                name=cs.get("name", f"crash_{len(specs)}"),
                generator=cs.get("generator", "gbm"),
                seed=seed,
                n_days=int(cs.get("n_days", self.n_days)),
                ticks_per_day=int(cs.get("ticks_per_day", self.ticks_per_day)),
                start_price=self.start_price,
                is_crash=True,
                expect_kill_switch=bool(cs.get("expect_kill_switch", True)),
                # GBM params
                vol_weekly=float(cs.get("vol_weekly", 0.25)),
                regime_duration_days=float(cs.get("regime_duration_days", 3.0)),
                jump_freq=float(cs.get("jump_freq", 0.2)),
                jump_size=float(cs.get("jump_size", 0.02)),
                # Block bootstrap
                block_libraries=list(cs.get("block_libraries", [])),
                block_size=int(cs.get("block_size", 5)),
            )
            specs.append(spec)
        return specs

    # ── Sub-loop 2: Synthetic Data ─────────────────────────────────────────

    def _generate_ticks(self, spec: ScenarioSpec) -> list[dict]:
        """Generate synthetic ticks for the given spec."""
        if spec.generator == "gbm":
            return generate_regime_switching_gbm(
                vol_weekly=spec.vol_weekly,
                regime_duration_days=spec.regime_duration_days,
                jump_freq=spec.jump_freq,
                jump_size=spec.jump_size,
                n_days=spec.n_days,
                ticks_per_day=spec.ticks_per_day,
                start_price=spec.start_price,
                seed=spec.seed,
            )
        elif spec.generator == "block_bootstrap":
            return generate_block_bootstrap(
                block_libraries=spec.block_libraries if spec.block_libraries else None,
                block_size=spec.block_size,
                n_days=spec.n_days,
                ticks_per_day=spec.ticks_per_day,
                start_price=spec.start_price,
                seed=spec.seed,
            )
        elif spec.generator == "garch":
            return generate_garch_path(
                historical_prices=spec.historical_prices,
                n_days=spec.n_days,
                ticks_per_day=spec.ticks_per_day,
                seed=spec.seed,
            )
        else:
            raise ValueError(f"Unknown generator: {spec.generator!r}")

    # ── Sub-loop 3 + 4 + 5: Run → Metrics → Evaluate ─────────────────────

    def _run_one(self, spec: ScenarioSpec) -> EvaluationResult:
        """Run a single scenario through the full pipeline."""
        self._total_run += 1
        scenario_num = self._total_run

        print(f"\n[#{scenario_num:03d}] {spec}")

        # Sub-loop 2: Generate ticks
        t0 = time.time()
        ticks = self._generate_ticks(spec)
        print(f"  Generated {len(ticks):,} ticks in {time.time()-t0:.1f}s")

        # Sub-loop 3: Run backtest
        t0 = time.time()
        runner = BacktestRunner(self.bot_config)
        result = runner.run(ticks)
        print(f"  Backtest complete in {time.time()-t0:.1f}s")

        # Sub-loop 4: Compute metrics
        metrics = compute_metrics(result, self.bot_config)

        # Sub-loop 5: Evaluate
        eval_result = self._evaluate(spec, result, metrics)
        self._all_results.append(eval_result)

        # Update consecutive pass counter
        if eval_result.passed:
            if not spec.is_crash:  # crash scenarios don't count toward consecutive
                self._consecutive_pass += 1
            status = "✅ PASS"
        else:
            if not spec.is_crash:
                self._consecutive_pass = 0  # reset on any normal-scenario failure
            status = "❌ FAIL"

        # Print brief result
        print(f"  {status} | MaxDD={metrics['max_drawdown']:.2%} | "
              f"CVaR95={metrics['cvar_95']:.4f} | "
              f"KillSwitch={metrics['kill_switch_count']} | "
              f"Consecutive={self._consecutive_pass}/{self.n_consecutive_pass}")

        if eval_result.failures:
            for f in eval_result.failures:
                print(f"    ⚠️  {f}")

        return eval_result

    def _evaluate(
        self,
        spec: ScenarioSpec,
        result: BacktestResult,
        metrics: dict[str, Any],
    ) -> EvaluationResult:
        """Check all acceptance criteria and return pass/fail."""
        failures: list[str] = []
        max_dd = metrics["max_drawdown"]
        cvar_95 = metrics["cvar_95"]
        ks_count = metrics["kill_switch_count"]

        # ── Check 1: MaxDD limit ──────────────────────────────────────────
        if max_dd > self.max_dd_limit:
            failures.append(
                f"MaxDD={max_dd:.2%} exceeds limit {self.max_dd_limit:.2%}"
            )

        # ── Check 2: CVaR limit ───────────────────────────────────────────
        # cvar_95 is positive (loss magnitude). Fail if too large.
        if cvar_95 > self.cvar_95_limit:
            failures.append(
                f"CVaR(95%)={cvar_95:.4f} exceeds limit {self.cvar_95_limit:.4f}"
            )

        # ── Check 3: Kill-switch logic ────────────────────────────────────
        if not spec.is_crash:
            # Normal scenario: kill-switch should NOT fire (unless allowed)
            if ks_count > 0 and not self.allow_kill_switch_normal:
                failures.append(
                    f"Kill-switch triggered {ks_count}× in a normal scenario "
                    f"(not allowed by config)"
                )
        else:
            # Crash scenario: kill-switch MUST fire if required
            if spec.expect_kill_switch and ks_count == 0 and self.require_kill_switch_crash:
                failures.append(
                    f"Kill-switch did NOT trigger in crash scenario "
                    f"'{spec.name}' — risk guard may be too loose"
                )

        return EvaluationResult(
            scenario=spec,
            metrics=metrics,
            passed=(len(failures) == 0),
            failures=failures,
        )

    # ── Proposal report ───────────────────────────────────────────────────

    def _final_report(self) -> str:
        """Generate the final summary + improvement proposals."""
        total = len(self._all_results)
        passed = sum(1 for r in self._all_results if r.passed)
        normal_results = [r for r in self._all_results if not r.scenario.is_crash]
        crash_results = [r for r in self._all_results if r.scenario.is_crash]
        normal_passed = sum(1 for r in normal_results if r.passed)
        crash_passed = sum(1 for r in crash_results if r.passed)

        goal_met = self._consecutive_pass >= self.n_consecutive_pass
        status = "🎯 GOAL MET" if goal_met else "⚠️  GOAL NOT MET"

        lines = [
            f"\n{'='*60}",
            f"  AGENT LOOP COMPLETE — {status}",
            f"{'='*60}",
            f"  Total scenarios run:   {total}",
            f"  Normal scenarios:      {len(normal_results)} ({normal_passed} passed)",
            f"  Crash scenarios:       {len(crash_results)} ({crash_passed} passed)",
            f"  Overall pass rate:     {passed}/{total}",
            f"  Consecutive passes:    {self._consecutive_pass}/{self.n_consecutive_pass}",
            "",
            f"  Acceptance thresholds used:",
            f"    MaxDD limit:         {self.max_dd_limit:.0%}",
            f"    CVaR(95%) limit:     {self.cvar_95_limit:.4f}",
            f"    Consecutive target:  {self.n_consecutive_pass}",
            "",
        ]

        # Aggregate metrics from normal scenarios
        if normal_results:
            all_max_dd = [r.metrics["max_drawdown"] for r in normal_results]
            all_cvar = [r.metrics["cvar_95"] for r in normal_results]
            all_ks = [r.metrics["kill_switch_count"] for r in normal_results]
            all_sortino = [r.metrics["sortino_ratio"] for r in normal_results]

            lines += [
                "  Aggregate metrics (normal scenarios):",
                f"    MaxDD:  mean={np.mean(all_max_dd):.2%}  max={np.max(all_max_dd):.2%}",
                f"    CVaR95: mean={np.mean(all_cvar):.4f}  max={np.max(all_cvar):.4f}",
                f"    Sortino mean:  {np.mean(all_sortino):.4f}",
                f"    KillSwitch total: {sum(all_ks)}",
                "",
            ]

            # ── Proposals (NEVER auto-applied) ────────────────────────────
            proposals = self._generate_proposals(normal_results, crash_results)
            if proposals:
                lines.append("  ⚙️  PROPOSALS FOR HUMAN REVIEW (NOT auto-applied):")
                lines.append("  " + "─" * 56)
                for i, p in enumerate(proposals, 1):
                    lines.append(f"  [{i}] {p}")
                lines.append("")
                lines.append("  To apply a proposal, manually edit config.py and")
                lines.append("  re-run the agent loop to validate.")
            else:
                lines.append("  ✅ No parameter change proposals — current config looks good.")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _generate_proposals(
        self,
        normal_results: list[EvaluationResult],
        crash_results: list[EvaluationResult],
    ) -> list[str]:
        """Analyse results and generate conservative parameter change proposals."""
        proposals: list[str] = []

        all_max_dd = [r.metrics["max_drawdown"] for r in normal_results]
        all_ks = [r.metrics["kill_switch_count"] for r in normal_results]
        all_bid_rate = [r.metrics["bid_fill_rate"] for r in normal_results]
        all_ask_rate = [r.metrics["ask_fill_rate"] for r in normal_results]
        all_sortino = [r.metrics["sortino_ratio"] for r in normal_results]

        mean_max_dd = float(np.mean(all_max_dd)) if all_max_dd else 0.0
        total_ks_normal = sum(all_ks)
        mean_bid_rate = float(np.mean(all_bid_rate)) if all_bid_rate else 0.0
        mean_ask_rate = float(np.mean(all_ask_rate)) if all_ask_rate else 0.0
        mean_sortino = float(np.mean(all_sortino)) if all_sortino else 0.0

        # Kill-switch fires in normal scenarios → drawdown limit too lenient
        if total_ks_normal > 0:
            proposals.append(
                f"Consider tightening max_drawdown_pct: "
                f"{self.bot_config.max_drawdown_pct:.0%} → "
                f"{self.bot_config.max_drawdown_pct * 0.8:.0%}  "
                f"(kill-switch fired {total_ks_normal}× in normal scenarios)"
            )

        # High MaxDD but no kill-switch → kill-switch not firing when it should
        if mean_max_dd > self.max_dd_limit * 0.8 and total_ks_normal == 0:
            proposals.append(
                f"MaxDD approaching limit (mean {mean_max_dd:.2%} vs limit {self.max_dd_limit:.2%}). "
                f"Consider tightening max_drawdown_pct from "
                f"{self.bot_config.max_drawdown_pct:.0%} → "
                f"{self.bot_config.max_drawdown_pct * 0.85:.0%}"
            )

        # Heavily imbalanced fill rates
        fill_imbalance = abs(mean_bid_rate - mean_ask_rate)
        if fill_imbalance > 0.02 and (mean_bid_rate + mean_ask_rate) > 0.001:
            dominant = "bid" if mean_bid_rate > mean_ask_rate else "ask"
            proposals.append(
                f"Fill rate imbalance: bid_rate={mean_bid_rate:.4%} ask_rate={mean_ask_rate:.4%}. "
                f"Bot fills more on {dominant} side.  "
                f"Consider adjusting inventory_skew_factor "
                f"(current: {self.bot_config.inventory_skew_factor}) to rebalance."
            )

        # Low Sortino — spreads too wide, not capturing enough flow
        if 0.0 < mean_sortino < 0.5:
            proposals.append(
                f"Sortino ratio is low (mean={mean_sortino:.4f}).  "
                f"Consider reducing gamma from {self.bot_config.gamma} → "
                f"{self.bot_config.gamma * 0.9:.4f} to tighten spreads and "
                f"increase fill rate (verify with re-run before applying)."
            )

        # Crash scenario not triggering kill-switch
        crash_no_ks = [
            r for r in crash_results
            if r.scenario.expect_kill_switch and r.metrics["kill_switch_count"] == 0
        ]
        if crash_no_ks:
            names = [r.scenario.name for r in crash_no_ks]
            proposals.append(
                f"Kill-switch did not trigger in crash scenarios: {names}.  "
                f"Consider tightening max_drawdown_pct or liquidation_distance_pct."
            )

        return proposals

    # ── Helpers ───────────────────────────────────────────────────────────

    def _header(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"  Backtest Agent Loop\n"
            f"  Acceptance criteria:\n"
            f"    MaxDD < {self.max_dd_limit:.0%}\n"
            f"    CVaR(95%) < {self.cvar_95_limit:.4f}\n"
            f"    {self.n_consecutive_pass} consecutive normal scenarios must pass\n"
            f"  Max scenarios cap: {self.max_scenarios}\n"
            f"{'='*60}"
        )

    def _load_yaml(self, path: str) -> dict:
        if not _YAML_AVAILABLE:
            logger.warning("PyYAML not installed — using empty scenario config defaults")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("Scenario config not found at %s — using defaults", path)
            return {}
        except Exception as e:
            logger.warning("Failed to parse scenario YAML: %s", e)
            return {}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "AI Agent backtest loop — runs synthetic scenarios and proposes "
            "parameter improvements WITHOUT modifying any production files."
        )
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to scenario YAML config (default: backtest/scenarios/default_scenarios.yaml)",
    )
    parser.add_argument(
        "--max-scenarios",
        type=int,
        default=200,
        help="Maximum total scenarios to run (safety cap)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    loop = AgentLoop(
        config_path=args.config,
        max_scenarios=args.max_scenarios,
    )
    loop.run()


if __name__ == "__main__":
    _cli()

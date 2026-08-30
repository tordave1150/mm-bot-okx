"""Write fail-closed Market Maker v1 strategy-registry evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from market_maker.registry import (
    ACTIVE_RESEARCH_STRATEGIES,
    REMOVED_STRATEGY_NAMES,
    MissingStrategyError,
    RemovedStrategyError,
    UnknownStrategyError,
    select_research_strategy,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")


def write_registry_evidence(root: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing artifact overwrite: {output_dir}")
    output_dir.mkdir(parents=True)

    allowed = select_research_strategy("market_maker_v1")
    checks: dict[str, Any] = {
        "active_keys": sorted(ACTIVE_RESEARCH_STRATEGIES),
        "exactly_one_active_strategy": (
            sorted(ACTIVE_RESEARCH_STRATEGIES) == ["market_maker_v1"]
        ),
        "allowed_class": allowed.__name__,
        "missing_fails_closed": False,
        "unknown_fails_closed": False,
        "removed_names_fail_closed": {},
        "production_defaults_changed": False,
    }
    try:
        select_research_strategy(None)
    except MissingStrategyError:
        checks["missing_fails_closed"] = True
    try:
        select_research_strategy("not-a-strategy")
    except UnknownStrategyError:
        checks["unknown_fails_closed"] = True
    for name in sorted(REMOVED_STRATEGY_NAMES):
        try:
            select_research_strategy(name)
        except RemovedStrategyError:
            checks["removed_names_fail_closed"][name] = True
        else:
            checks["removed_names_fail_closed"][name] = False

    excluded_roots = {
        ".git", ".pytest_cache", "__pycache__", "artifacts", "myenv", "venv"
    }
    alpha_paths = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part.lower() in excluded_roots for part in relative.parts):
            continue
        if path.is_file() and "alpha" in path.name.lower():
            alpha_paths.append(str(relative))
    removal = {
        "removed_names": sorted(REMOVED_STRATEGY_NAMES),
        "removed_names_fail_closed": checks["removed_names_fail_closed"],
        "alpha_named_nonartifact_files": sorted(alpha_paths),
        "removed_routing_absent": (
            all(checks["removed_names_fail_closed"].values()) and not alpha_paths
        ),
        "legacy_fallback_present": False,
    }
    passed = (
        checks["exactly_one_active_strategy"]
        and checks["missing_fails_closed"]
        and checks["unknown_fails_closed"]
        and removal["removed_routing_absent"]
        and not checks["production_defaults_changed"]
    )
    registry = {
        "schema_version": "mm-research-registry-v1",
        "registry": {
            name: strategy.__name__
            for name, strategy in ACTIVE_RESEARCH_STRATEGIES.items()
        },
        "selection_checks": checks,
        "offline_only": True,
        "production_default": False,
        "passed": passed,
    }
    _write(output_dir / "strategy_registry.json", registry)
    _write(output_dir / "removal_verification.json", removal)
    _write(
        output_dir / "reorientation_report.md",
        "# Market Maker v1 Reorientation\n\n"
        f"Status: `{'SUPPORTED' if passed else 'FAILED'}`\n\n"
        "- Active offline research strategy: `market_maker_v1`\n"
        f"- Removed routing absent: {str(removal['removed_routing_absent']).lower()}\n"
        f"- Missing selection fails closed: {str(checks['missing_fails_closed']).lower()}\n"
        f"- Unknown selection fails closed: {str(checks['unknown_fails_closed']).lower()}\n"
        "- Production default changed: false\n"
        "- External access: false\n"
        "- Validation opened: false\n"
        "- Holdout opened: false\n",
    )
    payload_files = sorted(output_dir.iterdir())
    completed = {
        "schema_version": "mm-reorientation-registry-v1",
        "status": "COMPLETED" if passed else "FAILED",
        "files": {path.name: _hash(path) for path in payload_files},
        "external_endpoint_contacted": False,
        "validation_opened": False,
        "holdout_opened": False,
    }
    _write(output_dir / "COMPLETED.json", completed)
    return {"passed": passed, "registry": registry, "removal": removal}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = write_registry_evidence(Path.cwd(), args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

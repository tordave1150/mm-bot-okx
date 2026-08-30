"""Fail-closed offline research-strategy registry."""

from __future__ import annotations

from typing import Any

from market_maker.as_strategy import MarketMakerV1Strategy


class StrategySelectionError(ValueError):
    """Base class for fail-closed research-strategy selection."""


class MissingStrategyError(StrategySelectionError):
    pass


class RemovedStrategyError(StrategySelectionError):
    pass


class UnknownStrategyError(StrategySelectionError):
    pass


ACTIVE_RESEARCH_STRATEGIES = {
    "market_maker_v1": MarketMakerV1Strategy,
}

REMOVED_STRATEGY_NAMES = frozenset({
    "alpha",
    "alpha_v1",
    "alpha_v2",
    "alpha_v2_1",
    "alpha_v2_2",
    "alpha_v3",
    "alpha_v3_smoke",
})


def select_research_strategy(name: str | None) -> type[Any]:
    """Return the one offline strategy or fail closed."""
    if name is None or not str(name).strip():
        raise MissingStrategyError("research strategy is required")
    normalized = str(name).strip().lower()
    if normalized in REMOVED_STRATEGY_NAMES or normalized.startswith("alpha"):
        raise RemovedStrategyError(
            f"removed research strategy cannot be selected: {name!r}"
        )
    try:
        return ACTIVE_RESEARCH_STRATEGIES[normalized]
    except KeyError as exc:
        raise UnknownStrategyError(f"unknown research strategy: {name!r}") from exc

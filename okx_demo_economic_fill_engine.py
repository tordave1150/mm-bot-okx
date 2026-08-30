"""Economic-session specialization of the proven fill accounting engine.

The formal R1/R2 engine remains unchanged.  This specialization uses the same
owned-order, cursor, fee, FIFO, snapshot and hash-chain machinery, but releases
the per-fill placement latch only after authoritative fill/cancel reconciliation
inside an economic session.  It never fabricates or consumes a formal restart
checkpoint or resume token.
"""

from __future__ import annotations

from typing import Iterable

from okx_fill_restart_validation import (
    AuthoritativeSnapshot,
    FillRestartEngine,
    ValidationSafetyError,
)


class EconomicSessionEngine(FillRestartEngine):
    """Allow causal maker re-entry after a fully reconciled normal fill."""

    def confirm_fill_reconciliation(
        self,
        *,
        snapshot: AuthoritativeSnapshot,
    ) -> None:
        """Release the economic fill latch without inventing an R1 restart.

        This path is used when every prior owned quote filled, so there is no
        remaining order to pass through ``confirm_cancellations``.  The latch
        is released only after an exact account/order/fee snapshot reconciles
        with the durable ledger.  The formal engine deliberately has no such
        method and therefore retains its R1/R2 restart semantics.
        """
        if not self.state.placement_halted_for_fill:
            raise ValidationSafetyError(
                "economic fill reconciliation requires a closed placement latch"
            )
        if self.state.open_orders:
            raise ValidationSafetyError(
                "economic fill reconciliation requires zero prior open orders"
            )
        self.record_authoritative_snapshot(snapshot)
        if self.state.pending_position_reconciliation:
            raise ValidationSafetyError(
                "economic fill remains pending authoritative reconciliation"
            )
        if self.state.last_reconciled_position_btc != self.state.ledger.inventory_btc:
            raise ValidationSafetyError(
                "economic fill inventory/account mismatch"
            )
        self.state.placement_halted_for_fill = False
        self._commit_or_halt()

    def confirm_cancellations(
        self,
        *,
        confirmed_client_ids: Iterable[str],
        snapshot: AuthoritativeSnapshot,
    ) -> None:
        super().confirm_cancellations(
            confirmed_client_ids=confirmed_client_ids,
            snapshot=snapshot,
        )
        if self.state.pending_position_reconciliation:
            raise ValidationSafetyError(
                "economic fill remains pending authoritative reconciliation"
            )
        if self.state.open_orders:
            raise ValidationSafetyError(
                "economic re-entry requires zero prior open orders"
            )
        if self.state.last_reconciled_position_btc != self.state.ledger.inventory_btc:
            raise ValidationSafetyError(
                "economic re-entry inventory/account mismatch"
            )
        if self.state.placement_halted_for_fill:
            self.state.placement_halted_for_fill = False
            self._commit_or_halt()

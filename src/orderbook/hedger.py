"""
hedger.py - Dynamic hedging for the inventory death-spiral problem.

Problem
-------
A market maker accumulates a one-sided position in contract A and cannot
unwind it because the spread cost exceeds the edge.  The position becomes
a directional bet - exactly what a MM should never hold.

Solution: hedge in a *correlated* contract B.

    hedge_size_B = inventory_A * correlation(A, B) * hedge_fraction

If you hold $5 000 long "Trump wins Pennsylvania" (A) and
"Trump wins Michigan" (B) has correlation 0.85:

    hedge_B = 5 000 * 0.85 * 1.0 = $4 250 short in B

This reduces directional exposure by 85% at the cost of the B spread.

Correlation estimation
-----------------------
On Polymarket you can observe correlated contracts (same election, same topic).
Use a rolling Pearson correlation on price returns as the estimate.
When the rolling correlation drops below 0.5 the hedge becomes unreliable
and you should reduce the hedge ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ── contract descriptors ──────────────────────────────────────────────────────

@dataclass
class HedgeContract:
    """
    A correlated contract that can absorb inventory risk from the primary.

    Attributes
    ----------
    name        : human-readable label
    correlation : Pearson correlation with the primary (−1 to 1)
    mid_price   : current YES price of the hedge contract
    capacity    : max hedge size available at reasonable slippage ($)
    """
    name: str
    correlation: float
    mid_price: float
    capacity: float = 10_000.0


@dataclass(frozen=True)
class HedgePlan:
    """Output of DynamicHedger.compute_plan()."""
    primary_inventory: float    # current inventory in primary contract
    hedges: list[tuple[str, float]]   # (contract_name, recommended_size $)
    residual_risk: float        # fraction of primary inventory still unhedged
    total_hedge_cost: float     # estimated spread cost of all hedge trades

    def summary(self) -> str:
        lines = [f"  Primary inventory        : ${self.primary_inventory:+,.2f}"]
        for name, size in self.hedges:
            direction = "SHORT" if size < 0 else "LONG"
            lines.append(f"  Hedge {name:<20}: {direction} ${abs(size):,.0f}")
        lines.append(f"  Residual risk            : {self.residual_risk:.1%}")
        lines.append(f"  Est. hedge cost          : ${self.total_hedge_cost:.2f}")
        return "\n".join(lines)


# ── hedger ────────────────────────────────────────────────────────────────────

class DynamicHedger:
    """
    Compute hedge trades for a primary inventory position.

    The hedger allocates hedge capacity across correlated contracts in
    order of |correlation| (highest first), until residual risk falls
    below ``target_residual`` or all contracts are exhausted.

    Parameters
    ----------
    hedge_fraction  : fraction of correlated exposure to actually hedge
                      (1.0 = full hedge, 0.5 = half hedge).
                      Lower values preserve some directional exposure.
    target_residual : stop hedging when unhedged fraction < this value.
    avg_spread      : assumed average bid-ask spread on hedge contracts
                      (used to estimate hedge execution cost).
    """

    def __init__(
        self,
        hedge_fraction: float = 0.80,
        target_residual: float = 0.10,
        avg_spread: float = 0.03,
    ) -> None:
        self.hedge_fraction = hedge_fraction
        self.target_residual = target_residual
        self.avg_spread = avg_spread

    def compute_plan(
        self,
        primary_inventory: float,
        hedge_contracts: list[HedgeContract],
    ) -> HedgePlan:
        """
        Compute the optimal hedge plan for a given primary inventory.

        The hedge direction is always *opposite* to the primary position
        (sell hedge if primary is long, buy hedge if primary is short).

        Returns a HedgePlan with per-contract recommended sizes.
        """
        if abs(primary_inventory) < 1e-6:
            return HedgePlan(
                primary_inventory=primary_inventory,
                hedges=[],
                residual_risk=0.0,
                total_hedge_cost=0.0,
            )

        # Sort by |correlation|, highest first
        sorted_contracts = sorted(
            hedge_contracts,
            key=lambda c: abs(c.correlation),
            reverse=True,
        )

        remaining = abs(primary_inventory)
        direction = -np.sign(primary_inventory)   # opposite to primary
        hedges: list[tuple[str, float]] = []
        total_cost = 0.0

        for contract in sorted_contracts:
            if remaining / abs(primary_inventory) < self.target_residual:
                break
            if abs(contract.correlation) < 0.30:
                break  # too weakly correlated to be useful

            # How much of the primary risk this hedge can absorb
            ideal_size = remaining * abs(contract.correlation) * self.hedge_fraction
            actual_size = min(ideal_size, contract.capacity)

            if actual_size < 1.0:
                continue

            hedge_size = direction * actual_size
            hedges.append((contract.name, hedge_size))

            # Cost: half-spread on each side (buy or sell)
            total_cost += actual_size * self.avg_spread * 0.5

            # Reduce remaining by the correlation-adjusted absorption
            remaining -= actual_size * abs(contract.correlation)
            remaining = max(remaining, 0.0)

        residual = remaining / abs(primary_inventory)
        return HedgePlan(
            primary_inventory=primary_inventory,
            hedges=hedges,
            residual_risk=residual,
            total_hedge_cost=total_cost,
        )


# ── rolling correlation estimator ─────────────────────────────────────────────

class RollingCorrelation:
    """
    Pearson correlation between two price series over a sliding window.

    Use this to update hedge ratios as market conditions change.

    >>> rc = RollingCorrelation(window=100)
    >>> for p_a, p_b in zip(prices_a, prices_b):
    ...     corr = rc.update(p_a, p_b)
    """

    def __init__(self, window: int = 100) -> None:
        self.window = window
        self._a: list[float] = []
        self._b: list[float] = []

    def update(self, price_a: float, price_b: float) -> float | None:
        self._a.append(float(price_a))
        self._b.append(float(price_b))
        if len(self._a) > self.window:
            self._a.pop(0)
            self._b.pop(0)
        if len(self._a) < 10:
            return None
        ret_a = np.diff(self._a)
        ret_b = np.diff(self._b)
        if ret_a.std() < 1e-10 or ret_b.std() < 1e-10:
            return None
        return float(np.corrcoef(ret_a, ret_b)[0, 1])

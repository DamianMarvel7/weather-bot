"""
market_maker.py - Avellaneda-Stoikov market maker for binary prediction markets.

Model (Avellaneda & Stoikov 2008)
----------------------------------
The MM quotes a *reservation price* that skews away from mid to manage inventory
risk, then places bid and ask symmetrically around it:

    r(s, q, t) = s - q * gamma * sigma² * (T - t)

        s   = current mid price
        q   = inventory (positive = long YES, negative = short)
        gamma   = risk-aversion parameter  (higher -> wider, more skewed quotes)
        sigma   = price volatility (per day in probability space)
        T-t = time to resolution (days)

Optimal half-spread:
    δ*(T-t) = gamma * sigma² * (T-t) + (2/gamma) * ln(1 + gamma/kappa)

        kappa   = order-arrival intensity (orders per unit time)

Bid  = r - δ*/2,  Ask = r + δ*/2

Prediction-market adaptations
------------------------------
1. Prices clipped to [min_price, max_price] - probabilities can't exceed 0 or 1.
2. Kyle's lambda adjustment: if informed flow is detected (high lambda), the half-
   spread is widened by an adverse-selection premium.
3. Inventory skew guard: when |q| > 80% of max_inventory the reservation price
   is shifted more aggressively to rebalance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np


# ── quote type ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    mid: float
    spread: float
    reservation_price: float
    half_spread: float
    kyle_adj: float     # additional spread from adverse-selection

    def __str__(self) -> str:
        return (
            f"bid={self.bid:.4f}  mid={self.mid:.4f}  ask={self.ask:.4f}  "
            f"spread={self.spread:.4f}  (kyle_adj={self.kyle_adj:.4f})"
        )


# ── market maker ──────────────────────────────────────────────────────────────

class AvellanedaStoikovMM:
    """
    Optimal market maker for binary prediction markets.

    Parameters
    ----------
    gamma : float
        Risk-aversion coefficient.  Higher values -> wider spreads, stronger
        inventory skew.  Typical range: 0.05 - 0.5.
    kappa : float
        Order-arrival rate parameter kappa.  Higher -> narrower base spread.
        Typical range: 0.5 - 5.0.
    sigma : float
        Daily volatility of the mid price in probability space.
    max_inventory : float
        Hard position limit in contract-units.  Fills that would breach this
        are rejected.
    min_price, max_price : float
        Valid quoting range (default 0.01 - 0.99 for binary markets).
    """

    def __init__(
        self,
        gamma: float = 0.10,
        kappa: float = 100.0,
        sigma: float = 0.02,
        max_inventory: float = 100.0,
        min_price: float = 0.01,
        max_price: float = 0.99,
    ) -> None:
        self.gamma = gamma
        self.kappa = kappa
        self.sigma = sigma
        self.max_inventory = max_inventory
        self.min_price = min_price
        self.max_price = max_price

        # ── mutable state ─────────────────────────────────────────────────────
        self.inventory: float = 0.0   # net long position in contracts
        self.cash: float = 0.0        # running cash P&L
        self.trades: list[dict] = []

    # ── quoting ───────────────────────────────────────────────────────────────

    def quote(
        self,
        mid_price: float,
        time_to_resolution: float,
        kyle_lambda: float = 0.0,
    ) -> Quote:
        """
        Compute optimal bid and ask for the current market state.

        Parameters
        ----------
        mid_price : float
            Current mid/fair-value price in (0, 1).
        time_to_resolution : float
            Days remaining until the contract resolves.
        kyle_lambda : float
            Rolling Kyle's lambda estimate.  Positive values widen the spread
            to compensate for adverse selection from informed traders.

        Returns
        -------
        Quote
        """
        T = max(time_to_resolution, 1.0 / 1_440)   # floor at 1 minute

        # 1. Reservation price - shift mid away from current inventory
        reservation = (
            mid_price - self.inventory * self.gamma * (self.sigma ** 2) * T
        )

        # 2. Base half-spread from the A-S closed-form solution
        base_half = 0.5 * (
            self.gamma * (self.sigma ** 2) * T
            + (2.0 / self.gamma) * np.log(1.0 + self.gamma / self.kappa)
        )

        # 3. Adverse-selection premium from Kyle's lambda
        #    Scale: lambda is in $/unit-volume; convert to a price-unit adjustment.
        #    Heuristic: assume average order size ~500 units.
        kyle_adj = max(kyle_lambda * 500.0, 0.0)

        total_half = base_half + kyle_adj

        # 4. Inventory guard: aggressive skew when position > 80% of limit
        inv_ratio = abs(self.inventory) / self.max_inventory
        if inv_ratio > 0.80:
            direction = np.sign(self.inventory)
            extra_skew = direction * total_half * 0.5
            reservation -= extra_skew

        bid = reservation - total_half
        ask = reservation + total_half

        # Clip to valid probability range while preserving spread direction
        bid = float(np.clip(bid, self.min_price, mid_price - 1e-4))
        ask = float(np.clip(ask, mid_price + 1e-4, self.max_price))

        return Quote(
            bid=bid,
            ask=ask,
            mid=mid_price,
            spread=ask - bid,
            reservation_price=reservation,
            half_spread=total_half,
            kyle_adj=kyle_adj,
        )

    # ── trade fills ───────────────────────────────────────────────────────────

    def fill(
        self,
        side: Literal["buy", "sell"],
        price: float,
        size: float,
    ) -> bool:
        """
        Record a trade fill and update inventory/cash.

        From the MM's perspective:
          "buy"  = a market participant bought YES from us  -> our inventory ↓
          "sell" = a market participant sold YES to us      -> our inventory ↑

        Parameters
        ----------
        side  : "buy" (MM sells) or "sell" (MM buys)
        price : fill price (bid or ask)
        size  : contracts transacted (positive)

        Returns
        -------
        bool : True if fill was accepted, False if it would breach limits.
        """
        if side == "buy":
            new_inventory = self.inventory - size
        else:
            new_inventory = self.inventory + size

        if abs(new_inventory) > self.max_inventory:
            return False  # reject - hard limit

        if side == "buy":
            self.inventory -= size
            self.cash += price * size   # received cash
        else:
            self.inventory += size
            self.cash -= price * size   # paid cash

        self.trades.append(
            {
                "side": side,
                "price": price,
                "size": size,
                "inventory_after": self.inventory,
                "cash_after": self.cash,
            }
        )
        return True

    # ── P&L ───────────────────────────────────────────────────────────────────

    def mark_to_market(self, mid_price: float) -> float:
        """Total P&L = cash + inventory * mid_price."""
        return self.cash + self.inventory * mid_price

    def realised_pnl(self) -> float:
        """Cash component only (closed-out P&L)."""
        return self.cash

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def inventory_utilisation(self) -> float:
        """Fraction of max_inventory currently used (0-1)."""
        return abs(self.inventory) / self.max_inventory

    def reset(self) -> None:
        """Reset state for a new simulation run."""
        self.inventory = 0.0
        self.cash = 0.0
        self.trades.clear()

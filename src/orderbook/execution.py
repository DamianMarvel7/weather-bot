"""
execution.py - Almgren-Chriss optimal execution model (2001).

Problem
-------
You want to buy (or sell) a large position Q in a prediction-market contract.
If you trade it all at once your own order moves the price against you.
If you spread it over time the price can drift adversely before you finish.

The Almgren-Chriss model finds the trade schedule {q_1, ..., q_N} that
minimises expected total cost subject to an explicit risk-aversion penalty.

Key formulas
------------
Optimal remaining position at step k:

    X_k* = X_0 * sinh(kappa * (T - t_k)) / sinh(kappa * T)

where the urgency parameter kappa is:

    kappa = sqrt(risk_aversion * sigma^2 / eta)

Cost decomposition:
    Permanent impact   = 0.5 * eta * X_0^2          (unavoidable; pay once)
    Temporary impact   = gamma * sum(q_k^2) / tau    (depends on trade schedule)
    Variance           = sigma^2 * tau * sum(X_k^2)  (position held overnight risk)

Implementation shortfall = total_cost / X_0  (cost per $ of position)

Intuition
---------
- risk_aversion -> 0  :  spread trade evenly (VWAP-like)
- risk_aversion -> inf :  trade immediately (market order)
- kappa ~ 0           :  VWAP / linear schedule
- kappa >> 1/T        :  front-loaded, aggressive early execution
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ── parameter type ────────────────────────────────────────────────────────────

@dataclass
class AlmgrenChrissParams:
    """
    Parameters for the Almgren-Chriss optimal execution model.

    All monetary units should be consistent (e.g. all in USD).

    Attributes
    ----------
    total_shares : float
        Total position size to execute, in dollars.
    T : float
        Execution horizon in hours.
    N : int
        Number of equally-spaced execution intervals.
    sigma : float
        Contract price volatility in units per hour (e.g. 0.02 = 2%/hour).
    eta : float
        Permanent market-impact coefficient ($ price move per $ of volume).
        Roughly: 0.0005-0.005 for liquid prediction markets.
    gamma : float
        Temporary impact coefficient ($ of slippage per $ traded per interval).
        Reflects bid-ask spread + short-lived impact.
    risk_aversion : float
        Lambda: trade-off between expected cost and variance of cost.
        0   -> minimise expected cost only (slow, spread evenly).
        1e-3 -> moderate urgency.
        1e-1 -> high urgency (front-loaded schedule).
    """

    total_shares: float
    T: float
    N: int
    sigma: float
    eta: float
    gamma: float
    risk_aversion: float


# ── result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionSchedule:
    """Output of almgren_chriss_schedule."""
    times: np.ndarray         # start time of each interval  (length N)
    remaining: np.ndarray     # position remaining at start of each interval
    trade_sizes: np.ndarray   # dollars traded in each interval
    permanent_impact: float   # cost from permanent price impact
    temporary_impact: float   # cost from temporary price impact
    expected_cost: float      # total expected cost
    implementation_shortfall: float   # cost as fraction of position
    variance: float           # variance of total cost
    kappa: float              # urgency parameter
    urgency: str

    @property
    def N(self) -> int:
        return len(self.times)

    def summary(self) -> str:
        lines = [
            f"  kappa (urgency)          : {self.kappa:.4f}  [{self.urgency}]",
            f"  Permanent impact cost    : ${self.permanent_impact:,.2f}",
            f"  Temporary impact cost    : ${self.temporary_impact:,.2f}",
            f"  Total expected cost      : ${self.expected_cost:,.2f}",
            f"  Implementation shortfall : {self.implementation_shortfall*100:.3f}%",
        ]
        return "\n".join(lines)

    def print_schedule(self, max_rows: int = 10) -> None:
        print(f"  {'Hour':>5}  {'Trade $':>10}  {'Remaining $':>12}")
        print("  " + "-" * 32)
        step = max(1, self.N // max_rows)
        for i in range(0, self.N, step):
            print(
                f"  {self.times[i]:>5.1f}  "
                f"{self.trade_sizes[i]:>10,.0f}  "
                f"{self.remaining[i]:>12,.0f}"
            )


# ── core model ────────────────────────────────────────────────────────────────

def almgren_chriss_schedule(p: AlmgrenChrissParams) -> ExecutionSchedule:
    """
    Compute the optimal execution schedule via Almgren-Chriss closed form.

    The schedule minimises  E[cost] + risk_aversion * Var[cost].

    Returns an ExecutionSchedule with per-interval trade sizes and summary costs.
    """
    tau = p.T / p.N                                 # interval length (hours)
    kappa_sq = p.risk_aversion * p.sigma ** 2 / p.eta
    kappa = float(np.sqrt(max(kappa_sq, 1e-12)))    # urgency (avoid sqrt(0))

    times = np.linspace(0.0, p.T, p.N + 1)         # N+1 boundary points

    # ── optimal remaining trajectory ─────────────────────────────────────────
    denom = np.sinh(kappa * p.T)
    if abs(denom) < 1e-12:
        # kappa ≈ 0: linear (VWAP) schedule
        remaining = p.total_shares * (1.0 - times / p.T)
    else:
        remaining = p.total_shares * np.sinh(kappa * (p.T - times)) / denom

    trade_sizes = -np.diff(remaining)               # positive = buying

    # ── cost decomposition ────────────────────────────────────────────────────
    permanent_impact = 0.5 * p.eta * p.total_shares ** 2
    temporary_impact = p.gamma * np.sum(trade_sizes ** 2) / tau
    variance = (p.sigma ** 2) * tau * float(np.sum(remaining[:-1] ** 2))

    expected_cost = permanent_impact + temporary_impact

    shortfall = expected_cost / p.total_shares if p.total_shares != 0 else 0.0

    if kappa > 2.0:
        urgency = "High - trade fast (front-loaded)"
    elif kappa < 0.5:
        urgency = "Low - spread evenly (VWAP-like)"
    else:
        urgency = "Moderate"

    return ExecutionSchedule(
        times=times[:-1],
        remaining=remaining[:-1],
        trade_sizes=trade_sizes,
        permanent_impact=permanent_impact,
        temporary_impact=temporary_impact,
        expected_cost=expected_cost,
        implementation_shortfall=shortfall,
        variance=variance,
        kappa=kappa,
        urgency=urgency,
    )


# ── multi-lambda comparison ────────────────────────────────────────────────────

def compare_risk_aversions(
    p: AlmgrenChrissParams,
    risk_aversions: list[float],
) -> list[ExecutionSchedule]:
    """
    Compute schedules for multiple risk-aversion levels.

    Useful for plotting the trade-off: aggressive vs. patient execution.
    """
    schedules = []
    for lam in risk_aversions:
        p_copy = AlmgrenChrissParams(
            total_shares=p.total_shares,
            T=p.T,
            N=p.N,
            sigma=p.sigma,
            eta=p.eta,
            gamma=p.gamma,
            risk_aversion=lam,
        )
        schedules.append(almgren_chriss_schedule(p_copy))
    return schedules

"""
monte_carlo.py - Monte Carlo simulation for binary prediction markets.

Price Process
-------------
Standard GBM is unsuitable for prediction markets because probabilities must
stay in (0, 1).  We instead model the *logit* of the probability:

    logit(p) = log(p / (1 - p))

and let logit(p_t) follow arithmetic Brownian motion (Wiener process):

    d logit(p_t) = sigma dW_t

This is the *logit-normal* process.  Converting back via the sigmoid function
always yields a value strictly in (0, 1).

Usage
-----
>>> from src.monte_carlo import simulate_prediction_market
>>> result = simulate_prediction_market(
...     current_price=0.62,
...     volatility=0.15,           # daily logit-space std-dev
...     time_to_resolution=7.0,    # days
...     n_sims=10_000,
... )
>>> print(f"Fair value : {result.fair_value:.4f}")
>>> print(f"P(YES)     : {result.prob_yes:.2%}")
>>> print(f"95% VaR    : {result.var_95:.4f}")
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ── result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SimulationResult:
    paths: np.ndarray           # (n_sims, n_steps + 1) - full price paths
    final_prices: np.ndarray    # (n_sims,) - terminal prices
    expected_value: float       # E[final price] over all simulations
    prob_yes: float             # fraction of paths resolving > 0.5
    var_95: float               # 95% VaR (loss from current price, per contract)
    var_99: float               # 99% VaR
    vol_annualised: float       # annualised logit-space volatility used

    @property
    def fair_value(self) -> float:
        """Expected terminal price under the simulated distribution."""
        return self.expected_value

    @property
    def percentile_5(self) -> float:
        return float(np.percentile(self.final_prices, 5))

    @property
    def percentile_95(self) -> float:
        return float(np.percentile(self.final_prices, 95))

    def summary(self) -> str:
        lines = [
            f"  Fair value (E[p_T])  : {self.fair_value:.4f}",
            f"  P(resolves YES)      : {self.prob_yes:.2%}",
            f"  5th / 95th pctile    : {self.percentile_5:.4f} / {self.percentile_95:.4f}",
            f"  95% VaR              : {self.var_95:.4f}",
            f"  99% VaR              : {self.var_99:.4f}",
            f"  vol (annualised)     : {self.vol_annualised:.4f}",
        ]
        return "\n".join(lines)


# ── simulator ─────────────────────────────────────────────────────────────────

def simulate_prediction_market(
    current_price: float,
    volatility: float,
    time_to_resolution: float,
    n_sims: int = 10_000,
    n_steps: int = 100,
    seed: int | None = None,
) -> SimulationResult:
    """
    Simulate a binary prediction market via a logit-normal process.

    Parameters
    ----------
    current_price : float
        Current YES probability, must be in (0, 1) exclusive.
    volatility : float
        Daily standard deviation in *logit space*.  A typical active market has
        sigma ≈ 0.05-0.20 per day.  Calibrate from historical data or set
        conservatively to 0.15.
    time_to_resolution : float
        Days until the market resolves.  Can be fractional (e.g. 0.5 = 12 hours).
    n_sims : int
        Number of Monte Carlo paths.
    n_steps : int
        Discretisation steps per path (more steps -> smoother paths).
    seed : int | None
        Random seed for reproducibility.

    Returns
    -------
    SimulationResult
        Contains all paths, summary statistics, and VaR figures.
    """
    if not (0 < current_price < 1):
        raise ValueError(f"current_price must be in (0, 1), got {current_price}")
    if volatility <= 0:
        raise ValueError(f"volatility must be positive, got {volatility}")
    if time_to_resolution <= 0:
        raise ValueError(f"time_to_resolution must be positive, got {time_to_resolution}")

    rng = np.random.default_rng(seed)
    dt = time_to_resolution / n_steps

    # ── logit-normal process ──────────────────────────────────────────────────
    logit_p0 = np.log(current_price / (1.0 - current_price))

    # Brownian increments:  shape (n_sims, n_steps)
    dW = rng.standard_normal(size=(n_sims, n_steps)) * np.sqrt(dt)

    # Cumulative increments -> full logit path including t=0
    logit_increments = volatility * dW
    logit_paths = np.concatenate(
        [np.full((n_sims, 1), logit_p0), logit_p0 + np.cumsum(logit_increments, axis=1)],
        axis=1,
    )  # shape (n_sims, n_steps + 1)

    # Map back to probability space via sigmoid
    paths = 1.0 / (1.0 + np.exp(-logit_paths))

    final_prices = paths[:, -1]

    # ── statistics ────────────────────────────────────────────────────────────
    expected_value = float(final_prices.mean())
    prob_yes = float((final_prices > 0.5).mean())

    pnl = final_prices - current_price
    var_95 = float(-np.percentile(pnl, 5))
    var_99 = float(-np.percentile(pnl, 1))

    vol_ann = volatility * np.sqrt(365.0)

    return SimulationResult(
        paths=paths,
        final_prices=final_prices,
        expected_value=expected_value,
        prob_yes=prob_yes,
        var_95=var_95,
        var_99=var_99,
        vol_annualised=vol_ann,
    )


# ── volatility calibration ────────────────────────────────────────────────────

def estimate_logit_volatility(prices: np.ndarray, dt_days: float = 1.0) -> float:
    """
    Estimate daily logit-space volatility from a price time-series.

    Parameters
    ----------
    prices  : array of YES prices in (0, 1)
    dt_days : time between observations in days (e.g. 1/24 for hourly)

    Returns
    -------
    float : estimated daily sigma in logit space
    """
    prices = np.clip(np.asarray(prices, dtype=float), 1e-6, 1 - 1e-6)
    logit_prices = np.log(prices / (1.0 - prices))
    logit_returns = np.diff(logit_prices)
    return float(np.std(logit_returns) / np.sqrt(dt_days))

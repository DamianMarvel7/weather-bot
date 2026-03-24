"""
kyle_model.py - Kyle's lambda: price-impact / informed-trading estimator.

Theory (Kyle 1985)
------------------
In Kyle's model, the equilibrium price update is linear in aggregate order flow y:

    p_t = μ + lambda * y_t

where lambda (lambda) is the *price-impact coefficient*:

    lambda = sigma_v / (2 * sigma_u)

    sigma_v = std-dev of the asset's fundamental value
    sigma_u = std-dev of noise-trader order flow

Estimating lambda from data is a simple OLS regression:

    Δp_t = lambda * Q_t + ε_t          (Q_t = signed order flow at tick t)

Interpretation
--------------
- High lambda  (> 0.002) + R² > 0.15 -> informed traders are active; widen spread.
- Low  lambda  (≈ 0)     + R² < 0.05 -> normal liquidity; noise traders dominate.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.stats import linregress


# ── result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KyleLambdaResult:
    lambda_: float       # price-impact slope  (Δprice per unit signed volume)
    r_squared: float     # OLS R²
    std_error: float     # std-error of slope estimate
    p_value: float       # two-sided p-value for H0: lambda = 0
    n_obs: int           # number of non-zero tick observations used

    @property
    def is_high_informed(self) -> bool:
        """
        True when order flow explains enough price variation to flag informed flow.

        Uses R² > 0.15 as the primary criterion (scale-independent), matching
        the article's recommendation.  A significant p-value guards against
        false positives in short windows.
        """
        return self.r_squared > 0.15 and self.p_value < 0.05

    @property
    def interpretation(self) -> str:
        if self.is_high_informed:
            return "High informed trading - widen spread"
        if self.r_squared > 0.08 and self.p_value < 0.10:
            return "Moderate informed trading - monitor closely"
        return "Normal liquidity - noise traders dominate"


# ── one-shot estimator ────────────────────────────────────────────────────────

def estimate_kyle_lambda(
    prices: np.ndarray,
    volumes: np.ndarray,
    signs: np.ndarray,
) -> KyleLambdaResult:
    """
    Estimate Kyle's lambda via OLS:  Δp_t = lambda * Q_t + ε_t

    Parameters
    ----------
    prices  : 1-D array of price observations  [p_0, p_1, …, p_T]
    volumes : 1-D array of trade sizes          (length T, positive floats)
    signs   : 1-D array of trade directions     (length T, +1 buy / -1 sell)

    Returns
    -------
    KyleLambdaResult with lambda, R², std_error, p_value, n_obs
    """
    prices = np.asarray(prices, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    signs = np.asarray(signs, dtype=float)

    signed_volume = volumes * signs          # Q_t
    price_changes = np.diff(prices)          # Δp_t  (length T)

    # price_changes has len(prices)-1 elements; signed_volume may be longer
    # when the deque sizes differ by 1.  Truncate to the shared length.
    min_len = min(len(price_changes), len(signed_volume))
    price_changes = price_changes[:min_len]
    signed_volume = signed_volume[:min_len]

    # Drop zero-change ticks - they carry no price-discovery signal
    mask = price_changes != 0
    x = signed_volume[mask]
    y = price_changes[mask]

    n_obs = len(x)
    if n_obs < 10:
        return KyleLambdaResult(
            lambda_=0.0, r_squared=0.0, std_error=0.0, p_value=1.0, n_obs=n_obs
        )

    slope, _intercept, r_value, p_value, std_err = linregress(x, y)

    return KyleLambdaResult(
        lambda_=float(slope),
        r_squared=float(r_value ** 2),
        std_error=float(std_err),
        p_value=float(p_value),
        n_obs=n_obs,
    )


# ── rolling estimator ─────────────────────────────────────────────────────────

class RollingKyleLambda:
    """
    Sliding-window estimator of Kyle's lambda.

    Each call to ``update()`` appends the latest tick and re-estimates lambda
    over the most recent ``window`` observations.

    Example
    -------
    >>> rkl = RollingKyleLambda(window=200)
    >>> for price, vol, sign in tick_stream:
    ...     result = rkl.update(price, vol, sign)
    ...     if result and result.is_high_informed:
    ...         widen_spread()
    """

    def __init__(self, window: int = 200, min_obs: int = 20) -> None:
        if window < min_obs:
            raise ValueError(f"window ({window}) must be >= min_obs ({min_obs})")
        self.window = window
        self.min_obs = min_obs
        self._prices: deque[float] = deque(maxlen=window + 1)  # +1 for np.diff
        self._volumes: deque[float] = deque(maxlen=window)
        self._signs: deque[float] = deque(maxlen=window)

    def update(
        self, price: float, volume: float, sign: float
    ) -> KyleLambdaResult | None:
        """
        Append one tick and return the latest estimate.

        Returns None if fewer than ``min_obs`` ticks have been seen.
        """
        self._prices.append(float(price))
        self._volumes.append(float(volume))
        self._signs.append(float(sign))

        if len(self._prices) <= self.min_obs:
            return None

        return estimate_kyle_lambda(
            np.array(self._prices),
            np.array(self._volumes),
            np.array(self._signs),
        )

    @property
    def latest_lambda(self) -> float:
        """Last estimated lambda, or 0.0 if not enough data yet."""
        if len(self._prices) <= self.min_obs:
            return 0.0
        result = estimate_kyle_lambda(
            np.array(self._prices),
            np.array(self._volumes),
            np.array(self._signs),
        )
        return result.lambda_

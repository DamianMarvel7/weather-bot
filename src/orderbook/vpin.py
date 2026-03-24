"""
vpin.py - Volume-synchronized Probability of Informed Trading (VPIN).

Theory
------
VPIN (Easley, Lopez de Prado & O'Hara 2012) measures order-flow imbalance
in *volume time* rather than clock time - each bucket covers exactly V trades,
so noisy low-volume periods don't dilute the signal.

    VPIN = |V_buy - V_sell| / (V_buy + V_sell)    (per bucket)

Interpretation
--------------
  VPIN < 0.40   - healthy, balanced flow
  0.40-0.65     - mild imbalance, monitor
  0.65-0.80     - elevated informed flow -> widen spread 2x
  > 0.80        - dangerous -> pull quotes entirely

Spread action table (used by the MM)
-------------------------------------
  vpin_action(vpin) -> SpreadAction
    NORMAL  : multiply spread by 1.0
    WIDEN   : multiply spread by 2.0  (article: "double your spread")
    PULL    : block all new quotes     (article: "pull quotes entirely")
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np


# ── spread action ─────────────────────────────────────────────────────────────

class SpreadAction(Enum):
    NORMAL = auto()
    WIDEN  = auto()
    PULL   = auto()


VPIN_WIDEN_THRESHOLD = 0.65
VPIN_PULL_THRESHOLD  = 0.80


def vpin_action(vpin: float) -> SpreadAction:
    """Return the spread action triggered by a VPIN reading."""
    if vpin > VPIN_PULL_THRESHOLD:
        return SpreadAction.PULL
    if vpin > VPIN_WIDEN_THRESHOLD:
        return SpreadAction.WIDEN
    return SpreadAction.NORMAL


def vpin_spread_multiplier(vpin: float) -> float:
    """
    Continuous spread multiplier in [1.0, 3.0].

    Grows linearly from 1 at the widen threshold to 3 at and above the pull
    threshold, giving a smooth transition rather than a hard step.
    """
    if vpin <= VPIN_WIDEN_THRESHOLD:
        return 1.0
    if vpin >= VPIN_PULL_THRESHOLD:
        return 3.0
    frac = (vpin - VPIN_WIDEN_THRESHOLD) / (VPIN_PULL_THRESHOLD - VPIN_WIDEN_THRESHOLD)
    return 1.0 + 2.0 * frac


# ── one-shot VPIN ─────────────────────────────────────────────────────────────

def compute_vpin(
    buy_volumes: np.ndarray,
    sell_volumes: np.ndarray,
    bucket_size: int = 50,
) -> np.ndarray:
    """
    Compute VPIN over equal-size volume buckets.

    Parameters
    ----------
    buy_volumes  : per-trade buyer-initiated volume
    sell_volumes : per-trade seller-initiated volume
    bucket_size  : number of trades per bucket

    Returns
    -------
    1-D array of VPIN values, one per bucket.
    """
    buy_volumes  = np.asarray(buy_volumes,  dtype=float)
    sell_volumes = np.asarray(sell_volumes, dtype=float)

    n_buckets = len(buy_volumes) // bucket_size
    vpin_values = np.empty(n_buckets)

    for i in range(n_buckets):
        s = i * bucket_size
        e = s + bucket_size
        V_buy   = buy_volumes[s:e].sum()
        V_sell  = sell_volumes[s:e].sum()
        V_total = V_buy + V_sell
        vpin_values[i] = abs(V_buy - V_sell) / V_total if V_total > 0 else 0.0

    return vpin_values


# ── rolling VPIN (tick-by-tick) ───────────────────────────────────────────────

@dataclass(frozen=True)
class VPINResult:
    vpin: float
    action: SpreadAction
    spread_multiplier: float
    buy_volume: float
    sell_volume: float

    @property
    def description(self) -> str:
        labels = {
            SpreadAction.NORMAL: "Normal market",
            SpreadAction.WIDEN:  "Widen spread 2x",
            SpreadAction.PULL:   "PULL QUOTES",
        }
        return f"VPIN={self.vpin:.3f} -> {labels[self.action]}"


class RollingVPIN:
    """
    Tick-by-tick VPIN estimator using a sliding volume window.

    At each tick, the signed volume (positive = buy, negative = sell) is
    appended.  Once the deque holds at least ``min_ticks`` observations,
    VPIN is computed and returned.

    Example
    -------
    >>> rvpin = RollingVPIN(window=100)
    >>> for vol, sign in tick_stream:
    ...     result = rvpin.update(vol * sign)
    ...     if result and result.action != SpreadAction.NORMAL:
    ...         adjust_spread(result.spread_multiplier)
    """

    def __init__(self, window: int = 200, min_ticks: int = 50) -> None:
        self.window = window
        self.min_ticks = min_ticks
        self._signed: deque[float] = deque(maxlen=window)

    def update(self, signed_volume: float) -> VPINResult | None:
        """
        Append one trade and return the current VPIN estimate.

        signed_volume : positive = buyer-initiated, negative = seller-initiated.
        Returns None until min_ticks observations have been collected.
        """
        self._signed.append(float(signed_volume))
        if len(self._signed) < self.min_ticks:
            return None

        arr = np.array(self._signed)
        V_buy  = arr[arr > 0].sum()
        V_sell = abs(arr[arr < 0].sum())
        V_total = V_buy + V_sell

        vpin = abs(V_buy - V_sell) / V_total if V_total > 0 else 0.0
        action = vpin_action(vpin)

        return VPINResult(
            vpin=vpin,
            action=action,
            spread_multiplier=vpin_spread_multiplier(vpin),
            buy_volume=V_buy,
            sell_volume=V_sell,
        )

    @property
    def n_obs(self) -> int:
        return len(self._signed)

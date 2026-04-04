"""
Portfolio management: EV/Kelly math, market persistence, calibration,
and BotState (balance + position lifecycle).
"""

import glob
import json
import math
import os

from datetime import datetime, timezone

from .config import (
    DATA_DIR, CALIBRATION_PATH, STATE_PATH,
    INITIAL_BALANCE, KELLY_FRACTION, CALIBRATION_MIN,
    LOCATIONS, SIGMA_MULTIPLIER, BIAS_SCALE,
)
from .polymarket import parse_bucket_bounds

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Pure math: EV, Kelly, normal distribution
# ---------------------------------------------------------------------------

def calc_ev(p: float, price: float) -> float:
    """EV = p × (1/price − 1) − (1 − p). Positive = edge exists."""
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p: float, price: float) -> float:
    """Fractional Kelly bet size as fraction of balance. Clamped to [0, 1]."""
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return min(max(0.0, f) * KELLY_FRACTION, 1.0)


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _bucket_probability(lo: float, hi: float, mu: float, sigma: float) -> float:
    """P(lo ≤ actual ≤ hi) assuming actual ~ N(mu, sigma)."""
    lo_clamp = mu - 15 * sigma if lo == -999.0 else lo
    hi_clamp = mu + 15 * sigma if hi == 999.0 else hi
    return max(0.0, min(1.0, _normal_cdf(hi_clamp, mu, sigma) - _normal_cdf(lo_clamp, mu, sigma)))

# ---------------------------------------------------------------------------
# Market persistence
# ---------------------------------------------------------------------------

def _market_path(city_slug: str, date_str: str) -> str:
    return os.path.join(DATA_DIR, f"{city_slug}_{date_str}.json")


def load_market(city_slug: str, date_str: str) -> dict | None:
    path = _market_path(city_slug, date_str)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_market(market: dict) -> None:
    path = _market_path(market["city"], market["date"])
    with open(path, "w") as f:
        json.dump(market, f, indent=2)


def new_market(city_slug: str, date_str: str, event_title: str, hours: float) -> dict:
    return {
        "city":               city_slug,
        "city_name":          LOCATIONS[city_slug]["name"],
        "date":               date_str,
        "event":              event_title,
        "status":             "open",
        "position":           None,
        "actual_temp":        None,
        "resolved_outcome":   None,
        "pnl":                None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }


def append_forecast_snapshot(market: dict, hours_left: float, forecast: dict) -> None:
    if hours_left <= 24:
        horizon = "D+0"
    elif hours_left <= 48:
        horizon = "D+1"
    else:
        horizon = f"D+{int(hours_left // 24)}"
    market["forecast_snapshots"].append({
        "ts":          _now_iso(),
        "horizon":     horizon,
        "hours_left":  round(hours_left, 1),
        "ecmwf":       forecast.get("ecmwf"),
        "hrrr":        forecast.get("hrrr"),
        "metar":       forecast.get("metar"),
        "best":        forecast.get("best"),
        "best_source": forecast.get("best_source"),
    })


def append_market_snapshot(market: dict, hours_left: float,
                           bucket: str, bid: float, ask: float) -> None:
    market["market_snapshots"].append({
        "ts":         _now_iso(),
        "hours_left": round(hours_left, 1),
        "bucket":     bucket,
        "bid":        bid,
        "ask":        ask,
    })

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _resolved_temp_estimate(mkt: dict) -> float | None:
    """
    Return best available actual temperature for a resolved market.

    Priority:
      1. Polymarket resolved_outcome midpoint — the source Polymarket itself used,
         so calibration trains against the right target.
      2. VC/ERA5 actual_temp — fallback when resolved_outcome is absent or a tail bucket.

    Tail buckets ("X or below" / "X or above") are skipped for midpoint because the
    true value is unknown; actual_temp is used instead when available.
    """
    label = mkt.get("resolved_outcome")
    if label:
        lo, hi = parse_bucket_bounds(label)
        # Use midpoint for non-tail, narrow buckets (width ≤ 3 units)
        if lo > -999 and hi < 999 and (hi - lo) <= 3:
            return (lo + hi) / 2
    # Fall back to VC/ERA5 actual_temp
    actual = mkt.get("actual_temp")
    if actual is not None:
        return actual
    if label:
        lo, hi = parse_bucket_bounds(label)
        if lo <= -999:
            return hi
        if hi >= 999:
            return lo
    return None


def run_calibration() -> dict:
    """
    Compute MAE and BIAS per (city, source) from resolved markets.

    bias = mean(forecast − actual): positive → model runs warm, negative → model runs cold.
    Bias is used in get_probability() to shift the forecast before computing bucket probability,
    directly correcting systematic station offsets.

    Falls back to resolved bucket midpoint when VC actual_temp is unavailable.
    """
    all_markets = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            all_markets.append(json.load(f))

    resolved = [m for m in all_markets if m.get("status") == "resolved"]

    calib: dict[str, dict] = {}
    for source in ("ecmwf", "hrrr"):
        for city_slug in LOCATIONS:
            city_markets = [m for m in resolved if m["city"] == city_slug]
            abs_errors: list[float] = []
            signed_errors: list[float] = []
            for mkt in city_markets:
                actual = _resolved_temp_estimate(mkt)
                if actual is None:
                    continue
                snaps = [s for s in mkt.get("forecast_snapshots", [])
                         if s.get(source) is not None]
                if not snaps:
                    continue
                closest = min(snaps, key=lambda s: s["hours_left"])
                err = closest[source] - actual   # positive = model ran warm
                abs_errors.append(abs(err))
                signed_errors.append(err)

            if len(abs_errors) >= CALIBRATION_MIN:
                calib[f"{city_slug}_{source}"] = {
                    "mae":  round(sum(abs_errors) / len(abs_errors), 3),
                    "bias": round(sum(signed_errors) / len(signed_errors), 3),
                    "n":    len(abs_errors),
                }

    os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(calib, f, indent=2)
    print(f"[calibration] {len(calib)} entries written to {CALIBRATION_PATH}")
    return calib


def load_calibration() -> dict:
    if os.path.exists(CALIBRATION_PATH):
        with open(CALIBRATION_PATH) as f:
            return json.load(f)
    return {}


def get_probability(city_slug: str, bucket_lo: float, bucket_hi: float,
                    forecast_temp: float, best_source: str, calibration: dict) -> float:
    """
    P(actual in bucket) using a bias-corrected, calibrated normal distribution.

    sigma default: 5°F / 2.5°C — based on published ECMWF 48h max-temp MAE.
    bias correction: subtracts the model's historical systematic offset from the
    forecast before computing bucket probability (e.g. if the model runs 2°C warm
    at this station, the corrected forecast is shifted 2°C cooler).
    """
    unit    = LOCATIONS[city_slug]["unit"]
    default = 5.0 if unit == "F" else 2.5
    entry   = calibration.get(f"{city_slug}_{best_source}") or calibration.get(f"{city_slug}_ecmwf")
    sigma   = (entry["mae"]  if entry else default) * SIGMA_MULTIPLIER
    bias    = (entry["bias"] if entry else 0.0)     * BIAS_SCALE
    corrected_temp = forecast_temp - bias
    return _bucket_probability(bucket_lo, bucket_hi, corrected_temp, sigma)

# ---------------------------------------------------------------------------
# BotState — owns balance and all position mutations
# ---------------------------------------------------------------------------

class BotState:
    """
    Wraps bot_state.json and owns all balance-mutating operations.

    Usage:
        state = BotState.load()
        state.open_position(market, outcome, size, ev, kelly)
        state.save()
    """

    def __init__(self, balance: float) -> None:
        self.balance = balance

    @classmethod
    def load(cls) -> "BotState":
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                data = json.load(f)
            return cls(balance=data.get("balance", INITIAL_BALANCE))
        return cls(balance=INITIAL_BALANCE)

    def save(self) -> None:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump({"balance": self.balance}, f, indent=2)

    def reconcile(self) -> None:
        """
        Recalculate balance from market files and fix any discrepancy.

        Computes: INITIAL_BALANCE + sum(pnl for all resolved markets)
                + sum(size for all still-open positions, since that cash is still deployed)

        Prints a warning if the stored balance differs by more than $0.01.
        """
        resolved_pnl  = 0.0
        open_deployed = 0.0
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("pnl") is not None:
                resolved_pnl += mkt["pnl"]
            elif mkt.get("position") and mkt["position"].get("close_reason") is None:
                open_deployed += mkt["position"].get("size", 0.0)

        correct = round(INITIAL_BALANCE + resolved_pnl - open_deployed, 2)
        if abs(correct - self.balance) > 0.01:
            print(f"[reconcile] Balance mismatch: stored=${self.balance:.2f} "
                  f"correct=${correct:.2f} — fixing.")
            self.balance = correct
            self.save()
        else:
            print(f"[reconcile] Balance OK: ${self.balance:.2f}")

    def open_position(self, market: dict, outcome: dict,
                      size_dollars: float, ev: float, kelly: float,
                      fill_price: float | None = None) -> None:
        """Record a new YES position. Entry is at fill_price (defaults to outcome ask)."""
        ask = fill_price if fill_price is not None else outcome["ask"]
        market["position"] = {
            "bucket":       outcome["label"],
            "token_id":     outcome["token_id"],
            "entry_ask":    ask,
            "peak_bid":     ask,
            "size":         round(size_dollars, 2),
            "ev":           ev,
            "kelly":        kelly,
            "opened_at":    _now_iso(),
            "close_reason": None,
        }
        self.balance = round(self.balance - size_dollars, 2)
        print(f"  OPEN  {market['city']} {market['date']} | "
              f"bucket={outcome['label']} ask={ask:.3f} "
              f"size=${size_dollars:.2f} ev={ev:.4f} "
              f"at={market['position']['opened_at']}")

    def close_position(self, market: dict, bid_price: float, reason: str) -> None:
        """Close open position at current bid. Realise P&L."""
        pos = market["position"]
        if pos is None:
            return
        proceeds = pos["size"] / pos["entry_ask"] * bid_price
        pnl      = round(proceeds - pos["size"], 2)
        self.balance         = round(self.balance + proceeds, 2)
        pos["close_reason"]  = reason
        pos["close_bid"]     = bid_price
        pos["closed_at"]     = _now_iso()
        market["pnl"]        = pnl
        print(f"  CLOSE {market['city']} {market['date']} | "
              f"reason={reason} bid={bid_price:.3f} pnl=${pnl:+.2f} "
              f"at={pos['closed_at']}")

    @staticmethod
    def check_stops(market: dict, current_bid: float,
                    forecast_temp: float | None) -> str | None:
        """
        Return stop reason or None.

        Conditions (priority order):
          stop_loss      — bid dropped ≥20% below entry ask
          trailing_stop  — bid reached +20% then fell back to ≤ entry ask
          forecast_change — forecast moved outside bought bucket (±2°F/1°C buffer)
        """
        pos = market["position"]
        if pos is None:
            return None

        entry  = pos["entry_ask"]
        peak   = pos.get("peak_bid", entry)
        unit   = LOCATIONS[market["city"]]["unit"]

        if current_bid > peak:
            pos["peak_bid"] = current_bid

        if current_bid <= entry * 0.65:
            return "stop_loss"

        if peak >= entry * 1.20 and current_bid <= entry:
            return "trailing_stop"

        if forecast_temp is not None:
            drift = 2.0 if unit == "F" else 1.0
            lo, hi = parse_bucket_bounds(pos["bucket"])
            if lo != -999.0:
                lo -= drift
            if hi != 999.0:
                hi += drift
            if not (lo <= forecast_temp <= hi):
                return "forecast_change"

        return None

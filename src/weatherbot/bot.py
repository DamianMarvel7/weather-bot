"""
WeatherBot: scan orchestrator, auto-resolver, and CLI reporting.
"""

import glob
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from .config import (
    DATA_DIR, LOCATIONS, VC_KEY,
    MAX_BET, MIN_EV, MIN_PRICE, MAX_PRICE, MAX_SLIPPAGE,
)
from .forecast import get_best_forecast
from .polymarket import get_polymarket_event, get_clob_prices
from .portfolio import (
    BotState,
    load_market, save_market, new_market,
    append_forecast_snapshot, append_market_snapshot,
    load_calibration, run_calibration, get_probability,
    calc_ev, calc_kelly,
    _now_iso,
)


class WeatherBot:
    """
    Orchestrates the full scan/update cycle and position management.

    Typical usage:
        bot = WeatherBot()
        bot.scan_and_update()   # every SCAN_INTERVAL seconds
        bot.monitor_stops()     # background thread every 10 min
    """

    def __init__(self) -> None:
        self.state       = BotState.load()
        self.calibration = load_calibration()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def scan_and_update(self) -> None:
        """Full hourly scan: forecasts → events → position management."""
        self.calibration = load_calibration()

        for city_slug in LOCATIONS:
            for date_str in self._scan_dates():
                try:
                    self._process_city_date(city_slug, date_str)
                except Exception as exc:
                    print(f"  ERROR {city_slug} {date_str}: {exc}")

        self._auto_resolve_all()
        run_calibration()
        self.state.save()

    def monitor_stops(self) -> None:
        """
        Lightweight stop check — no forecast or new-event API calls.
        Reads open market files and triggers stops based on current CLOB prices.
        """
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("status") != "open":
                continue
            pos = mkt.get("position")
            if not pos or pos.get("close_reason") is not None:
                continue
            token_id = pos.get("token_id")
            if not token_id:
                continue
            cur_bid, _ = get_clob_prices(token_id)
            if cur_bid is None:
                continue
            stop = BotState.check_stops(mkt, cur_bid, None)
            if stop:
                self.state.close_position(mkt, cur_bid, stop)
                save_market(mkt)
        self.state.save()

    # ------------------------------------------------------------------
    # CLI commands
    # ------------------------------------------------------------------

    def cmd_status(self) -> None:
        print(f"\nBalance: ${self.state.balance:.2f}")
        open_positions = []
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            pos = mkt.get("position")
            if pos and pos.get("close_reason") is None and mkt.get("status") == "open":
                open_positions.append((mkt["city"], mkt["date"], pos))

        print(f"Open positions: {len(open_positions)}")
        for city, date, pos in open_positions:
            print(f"  {city} {date}  bucket={pos['bucket']}  "
                  f"size=${pos['size']:.2f}  entry={pos['entry_ask']:.3f}  "
                  f"ev={pos['ev']:.4f}")
        print()

    @staticmethod
    def cmd_report() -> None:
        rows = []
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("status") == "resolved" and mkt.get("pnl") is not None:
                rows.append(mkt)

        if not rows:
            print("No resolved markets yet.")
            return

        rows.sort(key=lambda m: m["date"])
        wins      = sum(1 for r in rows if r["pnl"] > 0)
        losses    = sum(1 for r in rows if r["pnl"] <= 0)
        total_pnl = sum(r["pnl"] for r in rows)

        print(f"\nResolved markets: {len(rows)}  W/L: {wins}/{losses}  "
              f"Total P&L: ${total_pnl:+.2f}\n")
        print(f"{'City':<14} {'Date':<12} {'Bucket':<20} {'Actual':>8} {'P&L':>8}  Reason")
        print("-" * 80)
        for r in rows:
            pos    = r.get("position", {}) or {}
            bucket = pos.get("bucket", r.get("resolved_outcome", "-"))
            actual = f"{r['actual_temp']:.1f}" if r.get("actual_temp") else "-"
            reason = pos.get("close_reason", "-")
            print(f"{r['city']:<14} {r['date']:<12} {bucket:<20} {actual:>8} "
                  f"${r['pnl']:>+7.2f}  {reason}")
        print()

    # ------------------------------------------------------------------
    # Auto-resolution
    # ------------------------------------------------------------------

    def get_actual_temp_vc(self, city_slug: str, date_str: str) -> float | None:
        """Fetch actual max temperature from Visual Crossing historical API."""
        if not VC_KEY or VC_KEY == "YOUR_KEY_HERE":
            return None
        loc  = LOCATIONS[city_slug]
        unit = "us" if loc["unit"] == "F" else "metric"
        url  = (
            f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services"
            f"/timeline/{loc['lat']},{loc['lon']}/{date_str}"
            f"?unitGroup={unit}&include=days&key={VC_KEY}&contentType=json"
        )
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            days = data.get("days", [])
            return days[0].get("tempmax") if days else None
        except Exception:
            return None

    def _auto_resolve(self, market: dict) -> bool:
        """
        Check Polymarket for resolution. Returns True if resolved.
        YES price ≥ 0.95 → outcome WON; YES price ≤ 0.05 → outcome LOST.
        """
        if market["status"] != "open":
            return False

        resolved_bucket = None
        for outcome in market.get("all_outcomes", []):
            token_id = outcome.get("token_id")
            if not token_id:
                continue
            bid, ask = get_clob_prices(token_id)
            if bid is None:
                continue
            mid = (bid + ask) / 2 if ask else bid
            if mid >= 0.95:
                resolved_bucket = outcome["label"]
                break

        if resolved_bucket is None:
            return False

        market["status"]           = "resolved"
        market["resolved_outcome"] = resolved_bucket
        market["actual_temp"]      = self.get_actual_temp_vc(market["city"], market["date"])

        pos = market.get("position")
        if pos and pos.get("close_reason") is None:
            final_bid = 0.99 if resolved_bucket == pos["bucket"] else 0.01
            self.state.close_position(market, final_bid, "resolved")

        print(f"  RESOLVED {market['city']} {market['date']} → {resolved_bucket} "
              f"actual={market['actual_temp']}")
        return True

    def _auto_resolve_all(self) -> None:
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("status") == "open" and self._auto_resolve(mkt):
                save_market(mkt)

    # ------------------------------------------------------------------
    # Scan internals
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_dates() -> list[str]:
        today = datetime.now(timezone.utc).date()
        return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

    def _process_city_date(self, city_slug: str, date_str: str) -> None:
        event = get_polymarket_event(city_slug, date_str)
        if event is None:
            return

        hours_left = event["hours_left"]
        forecast   = get_best_forecast(city_slug, date_str, hours_left)
        if forecast["best"] is None:
            return

        mkt = load_market(city_slug, date_str) or new_market(
            city_slug, date_str, event["title"], hours_left
        )
        append_forecast_snapshot(mkt, hours_left, forecast)
        mkt["all_outcomes"] = event["outcomes"]

        pos = mkt.get("position")
        if pos and pos.get("close_reason") is None:
            token_id = pos.get("token_id")
            cur_bid, cur_ask = get_clob_prices(token_id) if token_id else (None, None)
            if cur_bid is not None:
                append_market_snapshot(mkt, hours_left, pos["bucket"],
                                       cur_bid, cur_ask or cur_bid)
                stop = BotState.check_stops(mkt, cur_bid, forecast["best"])
                if stop:
                    self.state.close_position(mkt, cur_bid, stop)

        if mkt.get("position") is None or mkt["position"].get("close_reason") is not None:
            self._maybe_open(mkt, event, forecast, hours_left)

        save_market(mkt)

    def _maybe_open(self, market: dict, event: dict, forecast: dict,
                    hours_left: float) -> None:
        """Evaluate all buckets and open the best-EV position if it clears thresholds."""
        city_slug   = market["city"]
        temp        = forecast["best"]
        best_source = forecast["best_source"] or "ecmwf"
        balance     = self.state.balance
        best_ev     = MIN_EV
        best_out    = None
        best_p      = 0.0

        for outcome in event["outcomes"]:
            ask = outcome.get("ask")
            bid = outcome.get("bid")
            if ask is None or ask < MIN_PRICE or ask >= MAX_PRICE:
                continue
            if bid is not None and (ask - bid) > MAX_SLIPPAGE:
                continue

            p  = get_probability(city_slug, outcome["lo"], outcome["hi"],
                                 temp, best_source, self.calibration)
            ev = calc_ev(p, ask)
            if ev > best_ev:
                best_ev, best_out, best_p = ev, outcome, p

        if best_out is None:
            return

        kelly_frac   = calc_kelly(best_p, best_out["ask"])
        size_dollars = min(kelly_frac * balance, MAX_BET)
        if size_dollars < 1.0 or size_dollars > balance:
            return

        self.state.open_position(market, best_out, size_dollars, best_ev, kelly_frac)

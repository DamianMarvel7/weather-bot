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
    MAX_BET, MIN_EV, MAX_EV, MIN_PRICE, MAX_PRICE, MAX_SLIPPAGE, MAX_POSITIONS,
)
from .execution import PaperExecutor
from .forecast import get_best_forecast
from .polymarket import get_polymarket_event, get_clob_prices, check_gamma_resolved
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

    def __init__(self, executor=None) -> None:
        self.state       = BotState.load()
        self.state.reconcile()
        self.calibration = load_calibration()
        self.tg          = None   # TelegramNotifier — set by weatherbet.py if configured
        self.executor    = executor or PaperExecutor()

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
                pos  = mkt.get("position", {})
                fill = self.executor.exit(token_id, cur_bid)
                if not fill.filled:
                    continue
                self.state.close_position(mkt, fill.fill_price, stop)
                save_market(mkt)
                if self.tg:
                    from .telegram_bot import notify_closed
                    notify_closed(self.tg, mkt, pos, stop, cur_bid)
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
                  f"ev={pos['ev']:.4f}  opened={pos.get('opened_at', '-')}")
        print()

    @staticmethod
    def cmd_report(last_n: int | None = None) -> None:
        rows = []
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("status") == "resolved" and mkt.get("pnl") is not None:
                rows.append(mkt)

        if not rows:
            print("No resolved markets yet.")
            return

        rows.sort(key=lambda m: (m.get("position") or {}).get("closed_at", m["date"]))
        if last_n is not None:
            rows = rows[-last_n:]

        wins      = sum(1 for r in rows if r["pnl"] > 0)
        losses    = sum(1 for r in rows if r["pnl"] <= 0)
        total_pnl = sum(r["pnl"] for r in rows)

        label = f"Last {last_n} trades" if last_n is not None else f"Resolved markets: {len(rows)}"
        print(f"\n{label}  W/L: {wins}/{losses}  "
              f"Total P&L: ${total_pnl:+.2f}\n")
        print(f"{'City':<14} {'Mkt Date':<12} {'Our Bucket':<20} {'PM Resolved':<20} {'VC Actual':>10} {'P&L':>8}  {'Reason':<18} {'Opened At':<22} Closed At")
        print("-" * 142)
        for r in rows:
            pos        = r.get("position", {}) or {}
            bucket     = pos.get("bucket", "-")
            pm_resolved = r.get("resolved_outcome", "-")
            actual     = f"{r['actual_temp']:.1f}" if r.get("actual_temp") else "-"
            reason     = pos.get("close_reason", "-")
            opened_at  = pos.get("opened_at", "-")
            closed_at  = pos.get("closed_at", "-")
            print(f"{r['city']:<14} {r['date']:<12} {bucket:<20} {pm_resolved:<20} {actual:>10} "
                  f"${r['pnl']:>+7.2f}  {reason:<18} {opened_at:<22} {closed_at}")
        print()

    def cmd_scan_dry(self) -> None:
        """
        Dry-run scan: shows every city/date evaluated and why buckets
        pass or fail the entry filter. No positions are opened.
        """
        from .config import MIN_HOURS, MAX_HOURS, MIN_EV, MAX_EV, MIN_PRICE, MAX_PRICE, MAX_SLIPPAGE
        self.calibration = load_calibration()
        today = datetime.now(timezone.utc).date()
        scan_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
        open_count = self._count_open_positions()
        print(f"\nDry-run scan  (open positions: {open_count}/{MAX_POSITIONS})\n")

        for city_slug in LOCATIONS:
            for date_str in scan_dates:
                event = get_polymarket_event(city_slug, date_str)
                if event is None:
                    continue
                hours_left = event["hours_left"]
                forecast   = get_best_forecast(city_slug, date_str, hours_left)
                if forecast["best"] is None:
                    print(f"  {city_slug} {date_str}  SKIP no forecast")
                    continue

                temp        = forecast["best"]
                best_source = forecast["best_source"] or "ecmwf"
                print(f"  {city_slug} {date_str}  forecast={temp:.1f} ({best_source})  hours_left={hours_left:.1f}")

                for outcome in event["outcomes"]:
                    ask = outcome.get("ask")
                    bid = outcome.get("bid")
                    label = outcome.get("label", "?")
                    if ask is None:
                        print(f"    {label:<22} SKIP  no ask price")
                        continue
                    if ask < MIN_PRICE or ask >= MAX_PRICE:
                        print(f"    {label:<22} SKIP  ask={ask:.3f} outside [{MIN_PRICE},{MAX_PRICE})")
                        continue
                    if bid is not None and (ask - bid) > MAX_SLIPPAGE:
                        print(f"    {label:<22} SKIP  spread={ask-bid:.3f} > {MAX_SLIPPAGE}")
                        continue
                    p  = get_probability(city_slug, outcome["lo"], outcome["hi"],
                                         temp, best_source, self.calibration)
                    ev = calc_ev(p, ask)
                    flag = ""
                    if p < 0.05:
                        flag = f"SKIP  p={p:.3f} < 0.05"
                    elif ev <= MIN_EV:
                        flag = f"SKIP  ev={ev:.4f} <= {MIN_EV}"
                    elif ev > MAX_EV:
                        flag = f"SKIP  ev={ev:.4f} > {MAX_EV} (artifact)"
                    else:
                        flag = f"PASS  p={p:.3f} ev={ev:.4f} ask={ask:.3f}"
                    print(f"    {label:<22} {flag}")
        print()

    @staticmethod
    def cmd_edge() -> None:
        """
        Model edge report: checks whether estimated EV predicts actual returns.
        Includes EV calibration, probability calibration, and breakdown by city
        and close reason.
        """
        rows = []
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            pos = mkt.get("position")
            if not pos or pos.get("close_reason") is None or mkt.get("pnl") is None:
                continue
            ev  = pos.get("ev", 0.0)
            ask = pos.get("entry_ask", 0.5)
            # p back-calculated from ev = p/ask - 1  →  p = (ev + 1) * ask
            p_est = min(max((ev + 1) * ask, 0.0), 1.0)
            rows.append({
                "city":         mkt["city"],
                "ev":           ev,
                "p_est":        p_est,
                "ask":          ask,
                "pnl":          mkt["pnl"],
                "size":         pos.get("size", 0.0),
                "won":          mkt["pnl"] > 0,
                "close_reason": pos.get("close_reason", "-"),
            })

        if not rows:
            print("No closed positions yet.")
            return

        def _table(section_rows: list[dict], buckets: list[tuple],
                   labels: list[str], key: str, header: str) -> None:
            print(f"\n--- {header} ({len(section_rows)} trades) ---")
            print(f"{'Bucket':<14} {'N':>5} {'Win%':>6} {'Mean PnL':>10} {'Mean EV':>9} {'Total PnL':>10}")
            print("-" * 58)
            for (lo, hi), label in zip(buckets, labels):
                br = [r for r in section_rows if lo <= r[key] < hi]
                if not br:
                    continue
                n         = len(br)
                win_pct   = 100 * sum(r["won"] for r in br) / n
                mean_pnl  = sum(r["pnl"] for r in br) / n
                mean_ev   = 100 * sum(r["ev"] for r in br) / n
                total_pnl = sum(r["pnl"] for r in br)
                print(f"{label:<14} {n:>5} {win_pct:>5.0f}% {mean_pnl:>+9.2f}  {mean_ev:>7.1f}% {total_pnl:>+9.2f}")

        # EV calibration
        _table(rows,
               [(0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)],
               ["0–5%", "5–10%", "10–20%", "20%+"],
               "ev", "EV Calibration")

        # Probability calibration
        print(f"\n--- Probability Calibration ---")
        p_buckets = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
        p_labels  = ["<50%", "50–60%", "60–70%", "70–80%", "80%+"]
        print(f"{'Est. P':<10} {'N':>5} {'Actual win%':>12} {'Expected':>10}")
        print("-" * 42)
        for (lo, hi), label in zip(p_buckets, p_labels):
            br = [r for r in rows if lo <= r["p_est"] < hi]
            if not br:
                continue
            n           = len(br)
            actual_win  = 100 * sum(r["won"] for r in br) / n
            expected    = 100 * sum(r["p_est"] for r in br) / n
            print(f"{label:<10} {n:>5} {actual_win:>11.0f}% {expected:>9.0f}%")

        # By city
        print(f"\n--- Edge by City ---")
        print(f"{'City':<16} {'N':>5} {'Win%':>6} {'Total PnL':>10}")
        print("-" * 40)
        for city in sorted(set(r["city"] for r in rows)):
            br        = [r for r in rows if r["city"] == city]
            n         = len(br)
            win_pct   = 100 * sum(r["won"] for r in br) / n
            total_pnl = sum(r["pnl"] for r in br)
            print(f"{city:<16} {n:>5} {win_pct:>5.0f}% {total_pnl:>+9.2f}")

        # By close reason
        print(f"\n--- Edge by Close Reason ---")
        print(f"{'Reason':<18} {'N':>5} {'Win%':>6} {'Total PnL':>10}")
        print("-" * 42)
        for reason in sorted(set(r["close_reason"] for r in rows)):
            br        = [r for r in rows if r["close_reason"] == reason]
            n         = len(br)
            win_pct   = 100 * sum(r["won"] for r in br) / n
            total_pnl = sum(r["pnl"] for r in br)
            print(f"{reason:<18} {n:>5} {win_pct:>5.0f}% {total_pnl:>+9.2f}")
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

        Strategy (in order):
        1. CLOB orderbook: YES mid price ≥ 0.95 → that bucket won.
        2. Gamma API fallback: if CLOB is inconclusive, check each child
           market's `closed` flag + `outcomePrices`. This handles markets
           that have passed their end date but haven't settled CLOB prices yet.
        """
        if market["status"] != "open":
            return False

        resolved_bucket = None

        # --- Pass 1: Gamma API closed flag (handles post-deadline markets) ---
        if resolved_bucket is None:
            for outcome in market.get("all_outcomes", []):
                market_id = outcome.get("market_id")
                if not market_id:
                    continue
                won = check_gamma_resolved(str(market_id))
                if won is True:
                    resolved_bucket = outcome["label"]
                    break

        # --- Pass 2: Historical closed events search (fallback when market_id missing) ---
        if resolved_bucket is None:
            from .polymarket import get_polymarket_historical_resolved
            label, _ = get_polymarket_historical_resolved(market["city"], market["date"])
            if label:
                resolved_bucket = label

        if resolved_bucket is None:
            return False

        market["status"]           = "resolved"
        market["resolved_outcome"] = resolved_bucket
        market["actual_temp"]      = self.get_actual_temp_vc(market["city"], market["date"])

        pos = market.get("position")
        if pos and pos.get("close_reason") is None:
            final_bid = 0.99 if resolved_bucket == pos["bucket"] else 0.01
            self.state.close_position(market, final_bid, "resolved")
            if self.tg:
                from .telegram_bot import notify_closed
                notify_closed(self.tg, market, pos, "resolved", final_bid)

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
                    fill = self.executor.exit(pos["token_id"], cur_bid)
                    if not fill.filled:
                        return
                    self.state.close_position(mkt, fill.fill_price, stop)
                    if self.tg:
                        from .telegram_bot import notify_closed
                        notify_closed(self.tg, mkt, pos, stop, cur_bid)

        if mkt.get("position") is None or mkt["position"].get("close_reason") is not None:
            self._maybe_open(mkt, event, forecast, hours_left)

        save_market(mkt)

    def _count_open_positions(self) -> int:
        """Count currently open positions across all market files."""
        count = 0
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            pos = mkt.get("position")
            if pos and pos.get("close_reason") is None and mkt.get("status") == "open":
                count += 1
        return count

    def _maybe_open(self, market: dict, event: dict, forecast: dict,
                    hours_left: float) -> None:
        """Evaluate all buckets and open the best-EV position if it clears thresholds."""
        # Don't open more positions than the configured maximum
        if self._count_open_positions() >= MAX_POSITIONS:
            return

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
            # Require a real CLOB ask — skip if no live ask price
            if ask is None or ask < MIN_PRICE or ask >= MAX_PRICE:
                continue
            if bid is not None and (ask - bid) > MAX_SLIPPAGE:
                continue

            p  = get_probability(city_slug, outcome["lo"], outcome["hi"],
                                 temp, best_source, self.calibration)
            # Skip buckets our model considers very unlikely regardless of ask price.
            # Protects against buying 1-cent lottery tickets the model barely believes in.
            if p < 0.05:
                continue
            ev = calc_ev(p, ask)
            # Cap EV — anything above MAX_EV is likely a model artifact from tiny prices
            if ev > best_ev and ev <= MAX_EV:
                best_ev, best_out, best_p = ev, outcome, p

        if best_out is None:
            return

        kelly_frac   = calc_kelly(best_p, best_out["ask"])
        size_dollars = min(kelly_frac * balance, MAX_BET)
        if size_dollars < 1.0 or size_dollars > balance:
            return

        fill = self.executor.enter(best_out["token_id"], best_out["ask"], best_out.get("bid"))
        if not fill.filled:
            return

        self.state.open_position(market, best_out, size_dollars, best_ev, kelly_frac,
                                 fill_price=fill.fill_price)
        if self.tg:
            from .telegram_bot import notify_opened
            notify_opened(self.tg, market, market["position"])

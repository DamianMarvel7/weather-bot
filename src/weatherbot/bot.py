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
    CITY_BLACKLIST, MAX_FORECAST_SPREAD_F, MAX_FORECAST_SPREAD_C,
    MAX_LEGS_PER_EVENT, MAX_EXPOSURE_PER_EVENT,
)
from .execution import PaperExecutor
from .forecast import get_best_forecast, prefetch_forecasts, clear_forecast_cache
from .polymarket import (
    get_polymarket_event, get_clob_prices, check_gamma_resolved,
    prefetch_events, clear_events_cache,
)
from .portfolio import (
    BotState,
    load_market, save_market, new_market, migrate_positions,
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
        scan_dates = self._scan_dates()
        active_cities = [c for c in LOCATIONS if c not in CITY_BLACKLIST]

        # Batch-prefetch: 1 Gamma call + 1 Open-Meteo call per city + 1 METAR per city
        prefetch_events()
        prefetch_forecasts(active_cities, scan_dates)

        for city_slug in active_cities:
            for date_str in scan_dates:
                try:
                    self._process_city_date(city_slug, date_str)
                except Exception as exc:
                    print(f"  ERROR {city_slug} {date_str}: {exc}")

        clear_events_cache()
        clear_forecast_cache()
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
            migrate_positions(mkt)
            changed = False
            for pos in mkt.get("positions", []):
                if pos.get("close_reason") is not None:
                    continue
                token_id = pos.get("token_id")
                if not token_id:
                    continue
                cur_bid, _ = get_clob_prices(token_id)
                if cur_bid is None:
                    continue
                stop = BotState.check_stops(mkt, pos, cur_bid, None,
                                            calibration=self.calibration)
                if stop:
                    fill = self.executor.exit(token_id, cur_bid)
                    if not fill.filled:
                        continue
                    self.state.close_position(mkt, pos, fill.fill_price, stop)
                    changed = True
                    if self.tg:
                        from .telegram_bot import notify_closed
                        notify_closed(self.tg, mkt, pos, stop, cur_bid)
            if changed:
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
            migrate_positions(mkt)
            if mkt.get("status") != "open":
                continue
            for pos in mkt.get("positions", []):
                if pos.get("close_reason") is None:
                    open_positions.append((mkt["city"], mkt["date"], pos))

        print(f"Open positions: {len(open_positions)}")
        for city, date, pos in open_positions:
            print(f"  {city} {date}  bucket={pos['bucket']}  "
                  f"size=${pos['size']:.2f}  entry={pos['entry_ask']:.3f}  "
                  f"ev={pos['ev']:.4f}  opened={pos.get('opened_at', '-')}")
        print()

    @staticmethod
    def _close_detail_str(pos: dict) -> str:
        """Return a compact detail string explaining *why* the position was closed."""
        reason = pos.get("close_reason", "")
        entry  = pos.get("entry_ask") or 0
        bid    = pos.get("close_bid") or 0
        if reason == "stop_loss":
            drop = (entry - bid) / entry * 100 if entry else 0
            return f"-{drop:.0f}% drop ({entry:.3f}→{bid:.3f})"
        if reason == "trailing_stop":
            peak     = pos.get("peak_bid") or entry
            peak_pct = (peak - entry) / entry * 100 if entry else 0
            return f"+{peak_pct:.0f}% peak, fell to {bid:.3f}"
        if reason == "forecast_change":
            d   = pos.get("close_detail") or {}
            fc  = d.get("forecast_temp")
            src = d.get("best_source", "")
            if fc is not None:
                src_str = f" ({src})" if src else ""
                return f"fc={fc:.1f}°{src_str}"
            return ""
        return ""

    @staticmethod
    def cmd_report(last_n: int | None = None) -> None:
        rows = []
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            migrate_positions(mkt)
            if mkt.get("status") != "resolved":
                continue
            for pos in mkt.get("positions", []):
                if pos.get("close_reason") is None:
                    continue
                pnl = pos.get("pnl", 0)
                rows.append({
                    "city":             mkt["city"],
                    "date":             mkt["date"],
                    "resolved_outcome": mkt.get("resolved_outcome", "-"),
                    "actual_temp":      mkt.get("actual_temp"),
                    "pos":              pos,
                    "pnl":              pnl,
                })

        if not rows:
            print("No resolved markets yet.")
            return

        rows.sort(key=lambda r: r["pos"].get("closed_at", r["date"]))
        if last_n is not None:
            rows = rows[-last_n:]

        wins      = sum(1 for r in rows if r["pnl"] > 0)
        losses    = sum(1 for r in rows if r["pnl"] <= 0)
        total_pnl = sum(r["pnl"] for r in rows)

        label = f"Last {last_n} legs" if last_n is not None else f"Resolved legs: {len(rows)}"
        print(f"\n{label}  W/L: {wins}/{losses}  "
              f"Total P&L: ${total_pnl:+.2f}\n")
        print(f"{'City':<14} {'Mkt Date':<12} {'Our Bucket':<20} {'PM Resolved':<20} {'VC Actual':>10} {'P&L':>8}  {'Reason':<18} {'Close Detail':<30} {'Opened At':<22} Closed At")
        print("-" * 172)
        for r in rows:
            pos         = r["pos"]
            bucket      = pos.get("bucket", "-")
            pm_resolved = r["resolved_outcome"]
            actual      = f"{r['actual_temp']:.1f}" if r.get("actual_temp") else "-"
            reason      = pos.get("close_reason", "-")
            detail      = WeatherBot._close_detail_str(pos)
            opened_at   = pos.get("opened_at", "-")
            closed_at   = pos.get("closed_at", "-")
            print(f"{r['city']:<14} {r['date']:<12} {bucket:<20} {pm_resolved:<20} {actual:>10} "
                  f"${r['pnl']:>+7.2f}  {reason:<18} {detail:<30} {opened_at:<22} {closed_at}")
        print()

    def cmd_scan_dry(self) -> None:
        """
        Dry-run scan: shows every city/date evaluated and which buckets
        would form the ladder. No positions are opened.
        """
        from .config import MIN_HOURS, MAX_HOURS, MIN_EV, MAX_EV, MIN_PRICE, MAX_PRICE, MAX_SLIPPAGE
        self.calibration = load_calibration()
        today = datetime.now(timezone.utc).date()
        scan_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
        active_cities = [c for c in LOCATIONS if c not in CITY_BLACKLIST]
        open_count = self._count_open_positions()
        print(f"\nDry-run scan  (open legs: {open_count}/{MAX_POSITIONS}, "
              f"max {MAX_LEGS_PER_EVENT} legs/event, ${MAX_EXPOSURE_PER_EVENT:.0f} max/event)\n")

        # Batch-prefetch all data upfront
        prefetch_events()
        prefetch_forecasts(active_cities, scan_dates)

        for city_slug in active_cities:
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

                # Normalize probabilities across all buckets
                raw_probs: dict[str, float] = {}
                for outcome in event["outcomes"]:
                    p = get_probability(city_slug, outcome["lo"], outcome["hi"],
                                        temp, best_source, self.calibration)
                    raw_probs[outcome.get("label", "?")] = p
                total_p = sum(raw_probs.values())
                if total_p <= 0:
                    print(f"    (all buckets p=0, skipping)")
                    continue

                # Collect candidates for ladder display
                candidates = []
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
                    if bid is not None and ask > 0 and (ask - bid) / ask > 0.20:
                        print(f"    {label:<22} SKIP  spread_ratio={(ask-bid)/ask:.2f} > 0.20")
                        continue
                    p  = raw_probs[label] / total_p
                    ev = calc_ev(p, ask)
                    if p < 0.01:
                        print(f"    {label:<22} SKIP  p={p:.3f} < 0.01")
                    elif ev <= MIN_EV:
                        print(f"    {label:<22} SKIP  ev={ev:.4f} <= {MIN_EV}")
                    elif ev > MAX_EV:
                        print(f"    {label:<22} SKIP  ev={ev:.4f} > {MAX_EV} (artifact)")
                    else:
                        candidates.append((label, p, ev, ask))
                        print(f"    {label:<22} CANDIDATE  p={p:.3f} ev={ev:.4f} ask={ask:.3f}")

                # Show which candidates would form the ladder
                if candidates:
                    candidates.sort(key=lambda c: c[2], reverse=True)
                    ladder = candidates[:MAX_LEGS_PER_EVENT]
                    labels = [c[0] for c in ladder]
                    total_cost = sum(c[3] for c in ladder)
                    print(f"    >>> LADDER ({len(ladder)} legs): {', '.join(labels)}")

        clear_events_cache()
        clear_forecast_cache()
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
            migrate_positions(mkt)
            for pos in mkt.get("positions", []):
                if pos.get("close_reason") is None:
                    continue
                pnl = pos.get("pnl", 0)
                ev  = pos.get("ev", 0.0)
                ask = pos.get("entry_ask", 0.5)
                # p back-calculated from ev = p/ask - 1  →  p = (ev + 1) * ask
                p_est = min(max((ev + 1) * ask, 0.0), 1.0)
                rows.append({
                    "city":         mkt["city"],
                    "ev":           ev,
                    "p_est":        p_est,
                    "ask":          ask,
                    "pnl":          pnl,
                    "size":         pos.get("size", 0.0),
                    "won":          pnl > 0,
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

        # EV calibration (wider buckets for ladder strategy with cheap asks)
        _table(rows,
               [(0.0, 0.10), (0.10, 0.50), (0.50, 1.0), (1.0, 5.0)],
               ["0–10%", "10–50%", "50–100%", "100%+"],
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

        migrate_positions(market)
        for pos in market.get("positions", []):
            if pos.get("close_reason") is not None:
                continue
            final_bid = 0.99 if resolved_bucket == pos["bucket"] else 0.01
            self.state.close_position(market, pos, final_bid, "resolved")
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
            migrate_positions(mkt)
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

        # Check stops on each open leg
        had_forecast_close = False
        for pos in list(mkt.get("positions", [])):
            if pos.get("close_reason") is not None:
                continue
            token_id = pos.get("token_id")
            cur_bid, cur_ask = get_clob_prices(token_id) if token_id else (None, None)
            if cur_bid is None:
                continue
            append_market_snapshot(mkt, hours_left, pos["bucket"],
                                   cur_bid, cur_ask or cur_bid)
            stop = BotState.check_stops(mkt, pos, cur_bid, forecast["best"],
                                        metar_temp=forecast.get("metar"),
                                        hours_left=hours_left,
                                        calibration=self.calibration)
            if stop:
                fill = self.executor.exit(pos["token_id"], cur_bid)
                if not fill.filled:
                    continue
                detail = None
                if stop == "forecast_change":
                    detail = {
                        "forecast_temp": forecast["best"],
                        "best_source":   forecast.get("best_source", ""),
                    }
                    had_forecast_close = True
                self.state.close_position(mkt, pos, fill.fill_price, stop, detail=detail)
                if self.tg:
                    from .telegram_bot import notify_closed
                    notify_closed(self.tg, mkt, pos, stop, cur_bid)

        # Don't re-open immediately after a forecast_change close
        if had_forecast_close:
            save_market(mkt)
            return

        # Open a new ladder if no open positions remain
        open_legs = [p for p in mkt.get("positions", []) if p.get("close_reason") is None]
        if not open_legs:
            self._maybe_open(mkt, event, forecast, hours_left)

        save_market(mkt)

    def _count_open_positions(self) -> int:
        """Count currently open position legs across all market files."""
        count = 0
        for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
            with open(path) as f:
                mkt = json.load(f)
            if mkt.get("status") != "open":
                continue
            migrate_positions(mkt)
            for pos in mkt.get("positions", []):
                if pos.get("close_reason") is None:
                    count += 1
        return count

    def _maybe_open(self, market: dict, event: dict, forecast: dict,
                    hours_left: float) -> None:
        """
        Ladder strategy: evaluate all buckets and open up to MAX_LEGS_PER_EVENT
        positive-EV positions, sized so total exposure stays under MAX_EXPOSURE_PER_EVENT.

        Instead of betting big on one bucket, we spread small bets across multiple
        buckets where our probability estimate exceeds the market price. Most legs
        expire worthless, but winners at cheap prices (1-10c) pay 10-100x.
        """
        open_count = self._count_open_positions()
        if open_count >= MAX_POSITIONS:
            return

        # Skip when forecast models disagree too much
        spread = forecast.get("spread")
        if spread is not None:
            unit = LOCATIONS[market["city"]]["unit"]
            max_spread = MAX_FORECAST_SPREAD_F if unit == "F" else MAX_FORECAST_SPREAD_C
            if spread > max_spread:
                return

        city_slug   = market["city"]
        temp        = forecast["best"]
        best_source = forecast["best_source"] or "ecmwf"
        balance     = self.state.balance

        # --- Compute raw probabilities for ALL buckets and normalize to sum=1 ---
        raw_probs: dict[str, float] = {}
        for outcome in event["outcomes"]:
            p = get_probability(city_slug, outcome["lo"], outcome["hi"],
                                temp, best_source, self.calibration)
            raw_probs[outcome["label"]] = p

        total_p = sum(raw_probs.values())
        if total_p <= 0:
            return

        # --- Score all positive-EV buckets ---
        candidates = []
        for outcome in event["outcomes"]:
            ask = outcome.get("ask")
            bid = outcome.get("bid")
            if ask is None or ask < MIN_PRICE or ask >= MAX_PRICE:
                continue
            if bid is not None and (ask - bid) > MAX_SLIPPAGE:
                continue
            if bid is not None and ask > 0 and (ask - bid) / ask > 0.20:
                continue

            p = raw_probs[outcome["label"]] / total_p
            if p < 0.01:
                continue
            ev = calc_ev(p, ask)
            if ev > MIN_EV and ev <= MAX_EV:
                kelly = calc_kelly(p, ask)
                candidates.append({
                    "outcome": outcome,
                    "p":       p,
                    "ev":      ev,
                    "kelly":   kelly,
                })

        if not candidates:
            return

        # --- Sort by EV descending, take top N legs ---
        candidates.sort(key=lambda c: c["ev"], reverse=True)
        available_slots = MAX_POSITIONS - open_count
        max_legs = min(MAX_LEGS_PER_EVENT, available_slots, len(candidates))
        legs = candidates[:max_legs]

        # --- Size each leg with Kelly, then scale to fit exposure cap ---
        raw_sizes = []
        for leg in legs:
            size = min(leg["kelly"] * balance, MAX_BET)
            raw_sizes.append(max(size, 0.0))

        total_raw = sum(raw_sizes)
        if total_raw <= 0:
            return

        # Scale down if total exceeds max exposure per event
        if total_raw > MAX_EXPOSURE_PER_EVENT:
            scale = MAX_EXPOSURE_PER_EVENT / total_raw
            raw_sizes = [s * scale for s in raw_sizes]

        # --- Open each leg ---
        for i, leg in enumerate(legs):
            size = round(raw_sizes[i], 2)
            if size < 1.0 or size > self.state.balance:
                continue
            outcome = leg["outcome"]
            fill = self.executor.enter(outcome["token_id"], outcome["ask"],
                                       outcome.get("bid"))
            if not fill.filled:
                continue
            self.state.open_position(market, outcome, size, leg["ev"], leg["kelly"],
                                     fill_price=fill.fill_price)
            if self.tg:
                from .telegram_bot import notify_opened
                notify_opened(self.tg, market, market["positions"][-1])

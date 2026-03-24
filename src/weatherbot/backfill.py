"""
Backfill historical calibration data — no Polymarket API, no paid services.

Uses Open-Meteo ERA5 reanalysis archive (completely free):
  - actual_temp  : ERA5 recorded max temperature for each day
  - forecast proxy: ERA5 temperature from D-1 (simulates a D+1 prediction)

The D-1 proxy gives a realistic sigma because day-to-day ERA5 variability
closely tracks real D+1 forecast uncertainty. Once the live bot accumulates
30+ real ECMWF forecast snapshots per city, run_calibration() will replace
these proxy sigmas with measured ones automatically.

Visual Crossing (optional): if vc_key is set, uses station-accurate actuals
instead of ERA5 for actual_temp — closer to Polymarket's resolution source.

Usage:
    uv run src/weatherbot/backfill.py              # last 90 days
    uv run src/weatherbot/backfill.py --days 60
"""

import argparse
import os
import sys
import requests
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.weatherbot.weatherbet import (
    LOCATIONS,
    TIMEZONES,
    CONFIG,
    DATA_DIR,
    VC_KEY,
    load_market,
    save_market,
    run_calibration,
    _now_iso,
)

# ---------------------------------------------------------------------------
# ERA5 reanalysis archive — free, no key, goes back to 1940
# archive-api.open-meteo.com returns actual recorded temperatures (reanalysis)
# ---------------------------------------------------------------------------

def get_era5_bulk(city_slug: str, start: str, end: str) -> dict:
    """
    Fetch ERA5 daily max temperature for a date range.
    Returns {date_str: temp}.
    """
    loc       = LOCATIONS[city_slug]
    temp_unit = "fahrenheit" if loc["unit"] == "F" else "celsius"
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&start_date={start}&end_date={end}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&timezone={TIMEZONES.get(city_slug, 'UTC')}"
    )
    try:
        resp = requests.get(url, timeout=(10, 60))
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        return dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
    except Exception as e:
        print(f"[warn] ERA5 failed for {city_slug}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Visual Crossing — actual recorded temps (optional, closer to Polymarket source)
# ---------------------------------------------------------------------------

def get_actual_temps_vc_bulk(city_slug: str, start: str, end: str) -> dict:
    """
    Fetch actual daily max temperatures from Visual Crossing.
    Returns {date_str: temp}. Falls back to ERA5 if key not set.
    """
    if not VC_KEY or VC_KEY == "YOUR_KEY_HERE":
        return {}
    loc  = LOCATIONS[city_slug]
    unit = "us" if loc["unit"] == "F" else "metric"
    url  = (
        "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services"
        f"/timeline/{loc['lat']},{loc['lon']}/{start}/{end}"
        f"?unitGroup={unit}&include=days&key={VC_KEY}&contentType=json"
    )
    try:
        resp = requests.get(url, timeout=(10, 60))
        resp.raise_for_status()
        days = resp.json().get("days", [])
        return {d["datetime"]: d["tempmax"] for d in days
                if "datetime" in d and "tempmax" in d}
    except Exception as e:
        print(f"[warn] Visual Crossing failed for {city_slug}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Build synthetic market record
# ---------------------------------------------------------------------------

def build_synthetic_market(city_slug: str, date_str: str,
                            forecast_temp: float | None,
                            actual_temp: float | None) -> dict:
    """
    Resolved market record with one forecast snapshot.
    forecast_temp = ERA5[D-1] — proxy for a D+1 prediction.
    actual_temp   = ERA5[D] or Visual Crossing[D].
    """
    return {
        "city":             city_slug,
        "city_name":        LOCATIONS[city_slug]["name"],
        "date":             date_str,
        "event":            f"[backfill] {LOCATIONS[city_slug]['name']} {date_str}",
        "status":           "resolved",
        "position":         None,
        "actual_temp":      actual_temp,
        "resolved_outcome": None,
        "pnl":              None,
        "forecast_snapshots": [
            {
                "ts":          date_str + "T00:00:00Z",
                "horizon":     "D+1",
                "hours_left":  24.0,
                "ecmwf":       forecast_temp,   # ERA5[D-1] proxy
                "hrrr":        None,
                "metar":       None,
                "best":        forecast_temp,
                "best_source": "ecmwf",
            }
        ] if forecast_temp is not None else [],
        "market_snapshots": [],
        "all_outcomes":     [],
        "created_at":       _now_iso(),
        "backfilled":       True,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def backfill(days: int = 90) -> None:
    today    = datetime.now(timezone.utc).date()
    end_date = (today - timedelta(days=1)).isoformat()
    # Fetch one extra day at the start so we have ERA5[D-1] for every target date
    start_date        = (today - timedelta(days=days)).isoformat()
    start_date_minus1 = (today - timedelta(days=days + 1)).isoformat()

    print(f"Backfilling {start_date} → {end_date} ({days} days, {len(LOCATIONS)} cities)")
    print("Source: ERA5 reanalysis archive (free) — no API key required\n")

    has_vc = bool(VC_KEY and VC_KEY != "YOUR_KEY_HERE")
    if has_vc:
        print("Visual Crossing key found — using station actuals for actual_temp.\n")
    else:
        print("No vc_key set — using ERA5 for actual_temp (still useful for calibration).\n")

    new_total  = 0
    skip_total = 0

    for city_slug, loc in LOCATIONS.items():
        print(f"  {loc['name']:<20}", end=" ", flush=True)

        # One bulk ERA5 call covers start-1 through end (for the D-1 proxy)
        era5_map   = get_era5_bulk(city_slug, start_date_minus1, end_date)
        actual_map = get_actual_temps_vc_bulk(city_slug, start_date, end_date) \
                     if has_vc else {}

        city_new = 0
        current  = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d").date()

        while current <= end_dt:
            date_str    = current.isoformat()
            prev_date   = (current - timedelta(days=1)).isoformat()
            current    += timedelta(days=1)

            existing = load_market(city_slug, date_str)
            # Skip if already has a real actual_temp (not a backfill placeholder)
            if existing is not None and existing.get("actual_temp") is not None \
                    and not existing.get("backfilled"):
                skip_total += 1
                continue
            # Re-write backfilled records that still have null actual_temp
            if existing is not None and existing.get("actual_temp") is not None \
                    and VC_KEY in ("YOUR_KEY_HERE", ""):
                skip_total += 1
                continue

            forecast_temp = era5_map.get(prev_date)   # ERA5[D-1] as forecast proxy
            actual_temp   = actual_map.get(date_str) or era5_map.get(date_str)

            if forecast_temp is None and actual_temp is None:
                continue

            mkt = build_synthetic_market(city_slug, date_str, forecast_temp, actual_temp)
            save_market(mkt)
            city_new  += 1
            new_total += 1

        print(f"{city_new} records")

    print(f"\nBackfill complete — {new_total} new records, {skip_total} already existed.")
    print("\nRunning calibration…")
    calib = run_calibration()

    if not calib:
        print("\nNo calibration entries yet — need at least "
              f"{CONFIG['calibration_min']} resolved records per city.")
        print(f"Current records per city: ~{new_total // len(LOCATIONS)}")
        print(f"Needed: {CONFIG['calibration_min']}  →  run with --days {CONFIG['calibration_min'] + 5}")
        return

    print(f"\n{'City + source':<35} {'MAE':>6}  {'N':>5}")
    print("-" * 50)
    for key, val in sorted(calib.items()):
        city_part = key.rsplit("_", 1)[0]
        unit = "°F" if LOCATIONS.get(city_part, {}).get("unit") == "F" else "°C"
        print(f"  {key:<33} {val['mae']:>5.2f}{unit}  {val['n']:>5}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90,
                        help="Days to backfill (default: 90)")
    args = parser.parse_args()
    backfill(args.days)

"""
Live diagnostic: Munich April 4 forecast temperature + Polymarket bid/ask.

Fetches real data from Open-Meteo and Polymarket CLOB — requires internet.

Run with:
    uv run python tests/test_munich_live.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.weatherbot.forecast import get_ecmwf
from src.weatherbot.polymarket import get_polymarket_event, find_matching_bucket


def test_munich_april4_bid_ask() -> None:
    city  = "dallas"
    date  = "2026-04-05"

    # --- Forecast ---
    print(f"\n=== Munich {date} ===\n")
    ecmwf_map = get_ecmwf(city, [date])
    forecast_temp = ecmwf_map.get(date)
    print(f"ECMWF forecast max temp : {forecast_temp} °C")

    # --- Polymarket event + CLOB prices ---
    event = get_polymarket_event(city, date)
    if event is None:
        print("Polymarket event        : NOT FOUND (market may be closed/not active)")
        return

    print(f"Polymarket event title  : {event['title']}")
    print(f"Hours left              : {event['hours_left']:.1f} h")
    print(f"Total volume            : ${event['volume']:,.0f}")
    print()

    # --- All buckets ---
    print(f"{'Bucket':<25} {'Bid':>7} {'Ask':>7}")
    print("-" * 42)
    for o in event["outcomes"]:
        bid_str = f"{o['bid']:.3f}" if o["bid"] is not None else "  N/A"
        ask_str = f"{o['ask']:.3f}" if o["ask"] is not None else "  N/A"
        marker  = "  ← forecast" if (forecast_temp is not None
                                      and o["lo"] <= forecast_temp <= o["hi"]) else ""
        print(f"  {o['label']:<23} {bid_str:>7} {ask_str:>7}{marker}")

    # --- Matching bucket summary ---
    if forecast_temp is not None:
        match = find_matching_bucket(event["outcomes"], forecast_temp)
        print()
        if match:
            print(f"Forecast {forecast_temp}°C falls in bucket : {match['label']}")
            print(f"  Bid : {match['bid']}")
            print(f"  Ask : {match['ask']}")
            spread = (match["ask"] - match["bid"]) if (match["ask"] and match["bid"]) else None
            if spread is not None:
                print(f"  Spread (ask-bid) : {spread:.3f}")
        else:
            print(f"No bucket covers forecast temp {forecast_temp}°C")
    print()


if __name__ == "__main__":
    test_munich_april4_bid_ask()

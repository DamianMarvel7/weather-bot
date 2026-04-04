"""
PnL CLI — daily profit/loss report from market files.

Usage:
    uv run pnl.py                    # all time
    uv run pnl.py --from 2026-03-01  # from date
    uv run pnl.py --to 2026-04-01    # up to date
    uv run pnl.py --city nyc         # filter by city
    uv run pnl.py --summary          # totals only, no daily rows
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "markets")


def load_markets() -> list[dict]:
    markets = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        with open(path) as f:
            markets.append(json.load(f))
    return markets


def settlement_date(mkt: dict) -> str | None:
    """Return the market weather date (YYYY-MM-DD), or None if no closed position."""
    if mkt.get("position") is None:
        return None
    if mkt.get("pnl") is None:
        return None
    return mkt["date"]


def build_daily(markets: list[dict], from_date: str | None, to_date: str | None,
                city: str | None) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for mkt in markets:
        if city and mkt["city"] != city:
            continue
        if mkt.get("pnl") is None:
            continue
        day = settlement_date(mkt)
        if day is None:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        days[day].append(mkt)
    return days


def fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}"


def print_report(days: dict[str, list[dict]], summary_only: bool) -> None:
    if not days:
        print("No resolved positions found for the given filters.")
        return

    sorted_days = sorted(days.keys())

    # Column widths
    col_date   = 12
    col_trades = 7
    col_won    = 5
    col_lost   = 5
    col_day    = 12
    col_cum    = 12

    header = (
        f"{'Date':<{col_date}} {'Trades':>{col_trades}} {'Won':>{col_won}} "
        f"{'Lost':>{col_lost}} {'Day PnL':>{col_day}} {'Cumul PnL':>{col_cum}}"
    )
    sep = "-" * len(header)

    if not summary_only:
        print(header)
        print(sep)

    cumulative  = 0.0
    total_trades = 0
    total_won    = 0
    total_lost   = 0
    total_pnl    = 0.0

    for day in sorted_days:
        mkts      = days[day]
        pnls      = [m["pnl"] for m in mkts]
        day_pnl   = sum(pnls)
        won       = sum(1 for p in pnls if p > 0)
        lost      = sum(1 for p in pnls if p <= 0)
        cumulative = round(cumulative + day_pnl, 2)

        total_trades += len(mkts)
        total_won    += won
        total_lost   += lost
        total_pnl     = cumulative

        if not summary_only:
            print(
                f"{day:<{col_date}} {len(mkts):>{col_trades}} {won:>{col_won}} "
                f"{lost:>{col_lost}} {fmt_pnl(day_pnl):>{col_day}} {fmt_pnl(cumulative):>{col_cum}}"
            )

    if not summary_only:
        print(sep)

    win_rate = (total_won / total_trades * 100) if total_trades else 0.0
    print(
        f"{'TOTAL':<{col_date}} {total_trades:>{col_trades}} {total_won:>{col_won}} "
        f"{total_lost:>{col_lost}} {'':>{col_day}} {fmt_pnl(total_pnl):>{col_cum}}"
    )
    print(f"\nWin rate : {win_rate:.1f}%  ({total_won}W / {total_lost}L)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily PnL report from market files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="Only include settlements on or after this date")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                        help="Only include settlements on or before this date")
    parser.add_argument("--city", metavar="SLUG",
                        help="Filter by city slug (e.g. nyc, london)")
    parser.add_argument("--summary", action="store_true",
                        help="Print totals only, skip daily rows")
    args = parser.parse_args()

    markets = load_markets()
    days    = build_daily(markets, args.from_date, args.to_date, args.city)
    print_report(days, args.summary)


if __name__ == "__main__":
    main()

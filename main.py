"""
main.py — Polymarket ML data pipeline entry point.

Stages
------
1. Fetch all resolved markets from the Gamma API              (cached)
2. Fetch per-market YES price histories from the CLOB API     (cached)
3. Parse + clean → data/processed/markets_clean.csv
4. Engineer features → data/processed/markets_features.csv
5. EDA summary + plots → data/processed/eda_plots.png

Usage
-----
    uv run main.py                     # full pipeline (uses caches when warm)
    uv run main.py --force-refresh     # re-fetch everything from the APIs
    uv run main.py --skip-prices       # skip CLOB price history (fast dev mode)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.pipeline.clean import build_dataframe
from src.pipeline.eda import run_eda
from src.pipeline.features import engineer_features
from src.pipeline.fetch import fetch_all_price_histories, fetch_gamma_markets

# ── paths ─────────────────────────────────────────────────────────────────────
RAW_MARKETS = "data/raw/markets_raw.json"
RAW_PRICES = "data/raw/prices_raw.json"
CLEAN_CSV = "data/processed/markets_clean.csv"
FEATURES_CSV = "data/processed/markets_features.csv"


def main(force_refresh: bool = False, skip_prices: bool = False) -> None:
    # Ensure output directories exist
    Path("data/raw").mkdir(parents=True, exist_ok=True)
    Path("data/processed").mkdir(parents=True, exist_ok=True)

    # ── Stage 1: fetch markets ────────────────────────────────────────────────
    raw_markets = fetch_gamma_markets(RAW_MARKETS, force_refresh=force_refresh)

    # ── Stage 2: fetch price histories ───────────────────────────────────────
    if skip_prices:
        print("[main] --skip-prices: using Gamma outcomePrices as final price only.")
        price_histories: dict = {}
    else:
        price_histories = fetch_all_price_histories(
            raw_markets, RAW_PRICES, force_refresh=force_refresh
        )

    # ── Stage 3: clean ────────────────────────────────────────────────────────
    # Relax start-price requirement when prices weren't fetched — lets the
    # pipeline still produce a partial dataset useful for inspection.
    df_clean = build_dataframe(
        raw_markets,
        price_histories,
        min_volume=1_000.0,
        require_start_price=not skip_prices,
    )

    if df_clean.empty:
        print("[main] No clean markets to process. Exiting.")
        return

    df_clean.to_csv(CLEAN_CSV, index=False)
    print(f"[main] Clean dataset saved → {CLEAN_CSV}  ({len(df_clean):,} rows)")

    # ── Stage 4: feature engineering ─────────────────────────────────────────
    df_features = engineer_features(df_clean)
    df_features.to_csv(FEATURES_CSV, index=False)
    print(f"[main] Features dataset saved → {FEATURES_CSV}  ({len(df_features):,} rows)")

    # ── Stage 5: EDA ─────────────────────────────────────────────────────────
    run_eda(df_features, output_dir="data/processed")

    # ── Final summary ─────────────────────────────────────────────────────────
    vol = df_features["volume_usd"]
    yes_rate = df_features["resolved_yes"].mean() * 100

    dates = df_features["start_date"].dropna()
    date_range = (
        f"{dates.min()[:10]} → {dates.max()[:10]}" if not dates.empty else "n/a"
    )

    sep = "=" * 57
    print(f"\n{sep}")
    print("  PIPELINE COMPLETE")
    print(sep)
    print(f"  Total rows              : {len(df_features):,}")
    print(f"  Date range              : {date_range}")
    print(f"  Volume range (USD)      : ${vol.min():,.0f} – ${vol.max():,.0f}")
    print(f"  YES resolution rate     : {yes_rate:.1f}%")
    print(f"  {CLEAN_CSV}")
    print(f"  {FEATURES_CSV}")
    print(f"  data/processed/eda_plots.png")
    print(sep)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Polymarket resolved-market ML data pipeline"
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-fetch from APIs even if caches already exist",
    )
    parser.add_argument(
        "--skip-prices",
        action="store_true",
        help=(
            "Skip CLOB price-history fetch (much faster; start_price_yes will be "
            "missing and price-dependent features will be NaN)"
        ),
    )
    args = parser.parse_args()
    main(force_refresh=args.force_refresh, skip_prices=args.skip_prices)

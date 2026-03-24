"""
eda.py — Standalone EDA script.

Reads the features CSV produced by the main pipeline and re-runs the full
exploratory analysis without re-fetching or re-cleaning data.

Usage
-----
    uv run eda.py
    uv run eda.py --input data/processed/markets_clean.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.pipeline.eda import run_eda

DEFAULT_INPUT = "data/processed/markets_features.csv"


def main(input_path: str = DEFAULT_INPUT) -> None:
    p = Path(input_path)
    if not p.exists():
        print(f"ERROR: {p} not found. Run `uv run main.py` first.")
        sys.exit(1)

    df = pd.read_csv(p)
    print(f"[eda] Loaded {len(df):,} rows from {p}")
    run_eda(df, output_dir=str(p.parent))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone Polymarket EDA")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"CSV to analyse (default: {DEFAULT_INPUT})",
    )
    args = parser.parse_args()
    main(args.input)

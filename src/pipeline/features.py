"""
features.py — Engineer ML-ready features from the cleaned market DataFrame.

All new columns are appended to a copy of the input; the original is unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add the following feature columns to the cleaned market DataFrame:

    price_drift          — final_price_yes - start_price_yes
    time_weighted_price  — simple average of start and final YES price
    log_volume           — log1p(volume_usd)  (handles volume = 0 safely)
    days_open_bucket     — categorical: "short" (<7 d) / "medium" (7–30 d) / "long" (>30 d)
    category_encoded     — integer label-encoding of market category

    Returns a new DataFrame (input is not modified).
    """
    df = df.copy()

    # 1. Price drift: direction and magnitude of market movement
    df["price_drift"] = df["final_price_yes"] - df["start_price_yes"]

    # 2. Time-weighted price: simple midpoint approximation
    df["time_weighted_price"] = (df["start_price_yes"] + df["final_price_yes"]) / 2

    # 3. Log volume: compresses the long right tail common in prediction markets
    df["log_volume"] = np.log1p(df["volume_usd"])

    # 4. Days-open bucket
    def _bucket(d) -> str:
        if pd.isna(d):
            return "unknown"
        if d < 7:
            return "short"
        if d <= 30:
            return "medium"
        return "long"

    df["days_open_bucket"] = df["days_open"].apply(_bucket)

    # 5. Category label encoding (no external dependencies — uses pandas codes)
    cats = df["category"].fillna("Other").astype(str)
    df["category_encoded"] = pd.Categorical(cats).codes

    return df

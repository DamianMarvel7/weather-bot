"""
eda.py — Exploratory analysis of the cleaned + engineered market DataFrame.

Prints a text summary and saves a 4-panel PNG to *output_dir*.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns


def run_eda(df: pd.DataFrame, output_dir: str = "data/processed") -> None:
    """
    Print summary statistics and save EDA plots for *df*.

    Panels produced
    ---------------
    1. YES vs NO outcome split (bar chart)
    2. Distribution of final_price_yes  (histogram)
    3. Volume distribution in log₁₀ scale (histogram)
    4. Calibration check: mean final price per decile vs actual resolution rate
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    sns.set_theme(style="whitegrid", palette="muted", font_scale=1.05)

    total = len(df)
    if total == 0:
        print("[eda] DataFrame is empty — nothing to plot.")
        return

    yes_n = int(df["resolved_yes"].sum())
    no_n = total - yes_n
    yes_pct = 100 * yes_n / total

    # ── Console summary ───────────────────────────────────────────────────────
    bar = "=" * 57
    print(f"\n{bar}")
    print("  POLYMARKET RESOLVED MARKETS — EDA SUMMARY")
    print(bar)
    print(f"  Total resolved markets  : {total:,}")
    print(f"  Resolved YES            : {yes_n:,}  ({yes_pct:.1f}%)")
    print(f"  Resolved NO             : {no_n:,}  ({100 - yes_pct:.1f}%)")

    dates = df["start_date"].dropna()
    if not dates.empty:
        print(f"  Date range              : {dates.min()[:10]} → {dates.max()[:10]}")

    vol = df["volume_usd"]
    print(f"  Volume range (USD)      : ${vol.min():,.0f} – ${vol.max():,.0f}")
    print(f"  Median volume (USD)     : ${vol.median():,.0f}")
    print(f"  YES resolution rate     : {yes_pct:.1f}%")
    print(bar)

    # ── Calibration table ─────────────────────────────────────────────────────
    df2 = df.copy()
    try:
        df2["price_decile"] = pd.qcut(
            df2["final_price_yes"], q=10, duplicates="drop", labels=False
        )
        calib = (
            df2.groupby("price_decile", observed=True)
            .agg(
                mean_price=("final_price_yes", "mean"),
                actual_rate=("resolved_yes", "mean"),
                count=("resolved_yes", "count"),
            )
            .reset_index()
        )
        print("\nCalibration check (final_price_yes deciles):")
        print(f"  {'Bucket':>6}  {'Mean Price':>11}  {'Actual Rate':>11}  {'N':>7}")
        print("  " + "-" * 43)
        for _, row in calib.iterrows():
            print(
                f"  {int(row['price_decile']):>6}  "
                f"{row['mean_price']:>11.3f}  "
                f"{row['actual_rate']:>11.3f}  "
                f"{int(row['count']):>7,}"
            )
    except Exception as exc:
        calib = None
        print(f"\n[eda] Calibration table skipped: {exc}")

    # ── Plots ─────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Polymarket Resolved Markets — EDA", fontsize=15, y=1.01)

    # Panel 1 — YES / NO split ────────────────────────────────────────────────
    ax = axes[0, 0]
    bars = ax.bar(
        ["Resolved YES", "Resolved NO"],
        [yes_n, no_n],
        color=["#4CAF50", "#F44336"],
        width=0.5,
    )
    ax.set_title("Outcome Split")
    ax.set_ylabel("Market count")
    for bar_obj, count in zip(bars, [yes_n, no_n]):
        ax.text(
            bar_obj.get_x() + bar_obj.get_width() / 2,
            bar_obj.get_height() + total * 0.005,
            f"{count:,}",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    # Panel 2 — Final price distribution ─────────────────────────────────────
    ax = axes[0, 1]
    ax.hist(
        df["final_price_yes"].dropna(),
        bins=50,
        color="#2196F3",
        edgecolor="white",
        linewidth=0.4,
        alpha=0.85,
    )
    ax.set_title("Final YES Price Distribution")
    ax.set_xlabel("final_price_yes")
    ax.set_ylabel("Count")
    ax.axvline(0.5, color="red", linestyle="--", linewidth=1.2, alpha=0.7, label="0.5")
    ax.legend()

    # Panel 3 — Volume distribution (log₁₀) ──────────────────────────────────
    ax = axes[1, 0]
    log_vol = np.log10(df["volume_usd"].clip(lower=1))
    ax.hist(
        log_vol,
        bins=50,
        color="#FF9800",
        edgecolor="white",
        linewidth=0.4,
        alpha=0.85,
    )
    ax.set_title("Volume Distribution (log₁₀ scale)")
    ax.set_xlabel("Volume USD")
    ax.set_ylabel("Count")
    # Show human-readable tick labels
    tick_vals = [3, 4, 5, 6, 7]
    ax.set_xticks([v for v in tick_vals if log_vol.min() <= v <= log_vol.max() + 1])
    ax.xaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"$10^{{{int(v)}}}")
    )

    # Panel 4 — Calibration check ─────────────────────────────────────────────
    ax = axes[1, 1]
    ax.plot([0, 1], [0, 1], "k--", linewidth=1.0, alpha=0.5, label="Perfect calibration")

    if calib is not None and not calib.empty:
        sizes = calib["count"] / calib["count"].max() * 400
        sc = ax.scatter(
            calib["mean_price"],
            calib["actual_rate"],
            s=sizes,
            color="#9C27B0",
            alpha=0.8,
            zorder=5,
        )
        ax.annotate(
            "Bubble size ∝ market count",
            xy=(0.04, 0.91),
            xycoords="axes fraction",
            fontsize=8,
            color="gray",
        )
    else:
        ax.text(0.5, 0.5, "Insufficient data", ha="center", va="center",
                transform=ax.transAxes, color="gray")

    ax.set_title("Calibration: Final Price vs Actual Resolution Rate")
    ax.set_xlabel("Mean final_price_yes (decile bucket)")
    ax.set_ylabel("Actual YES resolution rate")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.legend(loc="upper left")

    plt.tight_layout()
    plot_path = out / "eda_plots.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n[eda] Plots saved → {plot_path}")

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`polymarket-bot` is a Python 3.13 pipeline (managed with [uv](https://github.com/astral-sh/uv)) focused on **weather prediction markets**. It pulls resolved Polymarket weather-related prediction markets, cleans them, engineers ML features, and produces EDA plots for calibration analysis.

## Commands

```bash
# Install dependencies
uv sync

# Run the full pipeline (fetch → clean → features → EDA)
uv run main.py

# Skip CLOB price-history fetching (fast dev mode — start_price_yes will be NaN)
uv run main.py --skip-prices

# Force re-fetch from APIs even if caches exist
uv run main.py --force-refresh

# Re-run EDA on already-processed data (no API calls)
uv run eda.py
uv run eda.py --input data/processed/markets_clean.csv
```

## Architecture

```
main.py          — orchestrator; runs all 5 stages in sequence
eda.py           — standalone EDA script (reads existing CSV, no API calls)
src/
  fetch.py       — Stage 1+2: Gamma API pagination + CLOB price-history batch fetch
  clean.py       — Stage 3: parse raw JSON → flat DataFrame, apply drop filters
  features.py    — Stage 4: add price_drift, log_volume, buckets, category encoding
  eda.py         — Stage 5: 4-panel plot + calibration table to stdout
data/
  raw/           — JSON caches (markets_raw.json, prices_raw.json); re-used across runs
  processed/     — markets_clean.csv, markets_features.csv, eda_plots.png
```

## Data Flow

1. **Gamma API** (`https://gamma-api.polymarket.com/markets?closed=true`) — paginated with offset; returns market metadata, volume, resolution outcome, and `outcomePrices`.
2. **CLOB API** (`https://clob.polymarket.com/prices-history`) — called once per market using the YES `clobTokenIds[0]` token; returns `{"history": [{"t": unix_ts, "p": price}]}`. First point → `start_price_yes`; last point → `final_price_yes`.
3. Both raw responses are cached to `data/raw/` so the expensive fetch only happens once. The price-history cache is incremental: interrupted runs resume from where they left off.

## Documenting Bot Changes

The knowledge base lives in `memory/` (Obsidian wiki). Use the `/update-bot-memory` skill to document changes — it handles bot_changes entries, log.md, insights, and index updates in one pass.

Invoke it when:
- A session of changes is complete and worth keeping
- Something was learned about edge, risk, or market behaviour
- The user explicitly asks to save or log something

Do **not** auto-write to `memory/` after every individual edit — wait for the user to invoke `/update-bot-memory` when they're satisfied with the session's work.

## Key Design Decisions

- **`--skip-prices` mode**: runs everything using only Gamma data; `start_price_yes` is NaN and rows that require it are kept (not dropped). Useful when iterating on cleaning or feature logic.
- **Cleaning filters** (in `src/clean.py`): drop cancelled/annulled, non-binary, missing final price, missing start price (unless `--skip-prices`), volume < $1 000, prices outside [0, 1].
- **No heavy dependencies**: uses only `requests`, `pandas`, `numpy`, `matplotlib`, `seaborn`. Label encoding is done with `pd.Categorical` codes instead of scikit-learn.
- **Calibration plot** (panel 4 of EDA): buckets `final_price_yes` into deciles and plots mean bucket price vs actual YES resolution rate. Diagonal = perfect calibration.

# GEMINI.md

This file provides architectural overview, development workflows, and operational instructions for the `polymarket-bot` project.

## Project Overview

`polymarket-bot` is a Python-based ecosystem for interacting with Polymarket prediction markets. It combines historical data analysis, quantitative strategy simulation, and live trading execution.

The project is divided into three primary pillars:
1.  **ML Data Pipeline**: Analyzes historical resolved markets, fetches price histories (via Gamma and CLOB APIs), and generates feature-rich datasets for calibration and machine learning.
2.  **Order-Book Simulation**: A research-focused simulation engine for market-making (MM) strategies. It implements advanced quantitative models (Kyle's Lambda, Avellaneda-Stoikov, Hawkes Processes, VPIN, Almgren-Chriss) to test liquidity provision and risk management.
3.  **Weather Betting Bot**: A live trading system that identifies edges in weather-related markets using Open-Meteo forecasts and the Kelly Criterion. It includes a Streamlit dashboard for real-time monitoring.

## Core Technologies

- **Language**: Python 3.13
- **Dependency Management**: [uv](https://github.com/astral-sh/uv)
- **Data Science**: `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `plotly`
- **Web/UI**: `streamlit` (Dashboard), `requests` (API integration)
- **APIs**: Polymarket (Gamma & CLOB), Open-Meteo (Weather), Visual Crossing (Historical Weather)

## Project Structure

```text
├── main.py               # ML Pipeline entry point
├── orderbook_bot.py      # Order-book simulation entry point
├── weatherbet.py         # Weather bot launcher (delegates to src/weatherbot)
├── dashboard.py          # Streamlit monitoring dashboard
├── src/
│   ├── pipeline/         # Data engineering: fetch, clean, features, eda
│   ├── orderbook/        # Quant models: kyle, hawkes, vpin, market_maker, etc.
│   └── weatherbot/       # Live bot: bot, forecast, portfolio, telegram_bot
├── data/
│   ├── raw/              # API response caches (JSON)
│   ├── processed/        # Pipeline outputs (CSV, PNG)
│   └── markets/          # Weather bot per-market state (JSON)
└── logs/                 # Operational logs
```

## Getting Started

### Installation
Ensure you have `uv` installed.
```bash
uv sync
```

### 1. Running the ML Pipeline
Fetches, cleans, and analyzes historical market data.
```bash
uv run main.py                     # Full pipeline
uv run main.py --skip-prices       # Fast mode (skips CLOB price histories)
uv run main.py --force-refresh     # Re-fetch everything from APIs
```

### 2. Running Order-Book Simulations
Simulates market-making strategies with quantitative risk layers.
```bash
uv run orderbook_bot.py            # Standard simulation
uv run orderbook_bot.py --calibrate # Calibrate using pipeline data
```

### 3. Running the Weather Betting Bot
Operates the live (or dry-run) weather trading bot.
```bash
# Setup: Backfill calibration data
uv run src/weatherbot/backfill.py

# Start the bot
uv run weatherbet.py

# Launch the dashboard
uv run streamlit run dashboard.py
```

## Development Conventions

- **Tooling**: Always use `uv run <script>` to ensure the correct environment and dependencies.
- **Data Flow**: APIs are cached in `data/raw/` to minimize external calls and speed up iterations. Price history fetching is incremental.
- **Architecture**:
    - `src/pipeline` is strictly for data engineering and EDA.
    - `src/orderbook` is a stateless model library used by the simulation engine.
    - `src/weatherbot` handles live state, API polling, and Telegram integration.
- **Type Safety**: Prefer using type hints (`from __future__ import annotations`) and modular imports.
- **Environment**: Configuration resides in `src/weatherbot/config.json` and `.env` (see `.env.example`).

## Key Design Decisions

- **Kelly Criterion**: The weather bot uses a fractional Kelly multiplier (`kelly_fraction` in config) to size bets based on forecast confidence vs. market implied probability.
- **Spread Protection**: The order-book simulation uses three multiplicative layers:
    1.  **Kyle's Lambda**: Widens when informed flow is detected via OLS.
    2.  **Hawkes Factor**: Widens during "hot" markets (high arrival clustering).
    3.  **VPIN**: Widens or pulls quotes when toxic volume exceeds thresholds.
- **Incremental Cache**: The CLOB price history fetcher (`src/pipeline/fetch.py`) maintains a local state to resume interrupted fetches.

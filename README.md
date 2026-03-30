# Polymarket Weather Bot

Automated trading bot for Polymarket weather prediction markets.

---

## Setup

```bash
# Install dependencies
uv sync

# Copy and fill in your API keys
cp .env.example .env
```

Required keys in `.env`:
- `VC_KEY` — Visual Crossing API key (free at visualcrossing.com)
- `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` — optional, for notifications

---

## Running the Bot

### Start / Stop (background, survives terminal close)

```powershell
.\start_bot.ps1       # Start bot as background process
.\stop_bot.ps1        # Stop the background bot
.\bot_logs.ps1        # Tail live logs (last 50 lines + follow)
```

### Run in foreground (dev mode)

```bash
uv run weatherbet.py
```

---

## CLI Commands

```bash
# Check balance and all open positions
uv run weatherbet.py status

# Show resolved trade history + P&L
uv run weatherbet.py report

# Show edge/calibration breakdown by EV bucket, city, and close reason
uv run weatherbet.py edge
```

---

## Configuration

Edit `src/weatherbot/config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `max_bet` | 20.0 | Max bet size per position ($) |
| `min_ev` | 0.05 | Minimum EV to open a position |
| `max_ev` | 1.5 | EV cap (above this = likely model artifact) |
| `min_price` | 0.05 | Minimum ask price to buy |
| `max_price` | 0.45 | Maximum ask price to buy |
| `max_slippage` | 0.03 | Max allowed bid-ask spread |
| `max_positions` | 10 | Max simultaneous open positions |
| `kelly_fraction` | 0.25 | Fractional Kelly sizing |
| `min_volume` | 2000 | Minimum market volume ($) to trade |
| `min_hours` | 2.0 | Min hours before resolution to trade |
| `max_hours` | 72.0 | Max hours before resolution to scan |
| `scan_interval` | 3600 | Seconds between full scans |
| `calibration_min` | 15 | Min resolved markets needed for calibration |

---

## Data

```
data/
  markets/        — one JSON file per city+date (position, forecasts, snapshots)
  bot_state.json  — current balance
  calibration.json — per-city forecast bias/MAE used for probability model
  raw/            — API response caches
  processed/      — EDA outputs
logs/
  bot.log         — stdout from background process
  bot_err.log     — stderr from background process
```

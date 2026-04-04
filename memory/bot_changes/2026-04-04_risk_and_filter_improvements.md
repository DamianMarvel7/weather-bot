---
date: 2026-04-04
type: bot-change
tags: [bot-change]
---

# Bot Change — 2026-04-04

## Summary
Raised min EV threshold, tightened stop-loss, and blacklisted 6 consistently losing cities.

## What Changed
- **File(s):** `src/weatherbot/config.json`, `src/weatherbot/config.py`, `src/weatherbot/portfolio.py`, `src/weatherbot/bot.py`
- **Component:** `model` / `executor` / `config-tweak`
- **Change type:** config-tweak / model-update

## Why
Analysis of 162 closed trades (win rate 13.6%) revealed three root causes:
1. Stop-losses fired at -35% drop with 0% win rate — positions would have recovered if held to resolution
2. min_ev=0.10 was too low, allowing too many weak-conviction bets to enter
3. London, Paris, Hong Kong, Shanghai, Sao Paulo, Tokyo had 0% win rate across all trades

## Before / After
| | Before | After |
|---|---|---|
| `min_ev` | 0.10 | 0.25 |
| `stop_loss_threshold` | 0.65 (exit at -35% drop) | 0.35 (exit at -65% drop) |
| City blacklist | none | london, paris, hong-kong, shanghai, sao-paulo, tokyo |

## Impact on Live Trading
- [x] Affects position sizing
- [x] Affects entry/exit logic
- [ ] Affects probability model
- [x] Affects EV threshold
- [ ] No direct trading impact

## Test / Verification
- Verified config loads correctly: `uv run python3 -c "from src.weatherbot.config import MIN_EV, STOP_LOSS_THRESHOLD, CITY_BLACKLIST; print(MIN_EV, STOP_LOSS_THRESHOLD, CITY_BLACKLIST)"`
- Output confirmed: min_ev=0.25, stop_loss_threshold=0.35, blacklist=6 cities

## Notes
- March 26-27 were the only profitable days (+$2,312) — those positions were held to resolution because the PC was off (no stop-loss fired). This confirmed stop-loss was the biggest issue.
- Forecast accuracy was fine (MAE 1.8° winners vs 1.7° losers) — the problem was risk management, not prediction quality.
- City blacklist is adjustable in config.json without code changes.
- Further improvements planned: seasonal bucket filter, EV recalibration.

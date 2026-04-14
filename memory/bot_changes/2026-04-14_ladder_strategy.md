---
date: 2026-04-14
type: bot-change
tags: [bot-change, strategy, ladder, config]
---

# Bot Change — 2026-04-14

## Summary
> Switched from single-bucket betting to ladder strategy: buy multiple cheap buckets per event.

## What Changed
- **File(s):** `bot.py`, `portfolio.py`, `config.json`, `config.py`, `telegram_bot.py`
- **Component:** `executor` / `model` / `portfolio`
- **Change type:** new-feature / refactor

## Why
Losses were shrinking but edge was thin ($36 best profit day on $1000 deployed). Research into profitable Polymarket weather traders (neobrother, HondaCivic, ColdMath, etc.) revealed they all use **laddering** — buying 3-5 cheap buckets across the distribution instead of one expensive bucket. The math favors this approach: most legs expire worthless, but winners at 1-5c pay 20-100x, smoothing variance and amplifying returns.

The old strategy (single bucket at 15-45c ask) required ~50-60% win rate to profit. Ladder strategy only needs ~10-20% of legs to hit.

## Before / After
| | Before | After |
|---|---|---|
| Buckets per event | 1 (best EV) | Up to 5 (sorted by EV) |
| Price range (ask) | 0.15 - 0.45 | 0.01 - 0.50 |
| Per-position size | Up to $20 | Up to $5/leg |
| Total per event | $20 | $15 (capped) |
| min_ev | 0.15 | 0.05 |
| max_ev | 1.5 | 5.0 |
| max_positions | 10 | 30 (total legs) |
| Payout multiples | 2-6x | 2-100x |
| Data model | `position` (single dict) | `positions` (list of dicts) |
| P&L tracking | Market-level only | Per-position + market-level sum |

## Impact on Live Trading
- [x] Affects position sizing
- [x] Affects entry/exit logic
- [x] Affects probability model
- [x] Affects EV threshold
- [ ] No direct trading impact

## Test / Verification
- All imports verified (`uv run python -c "from src.weatherbot.bot import WeatherBot"`)
- EV/Kelly math validated for ladder scenarios (cheap asks produce correct high-EV, low-Kelly values)
- Backward compat: `migrate_positions()` auto-converts old market files on load
- Dry-run scan updated to show ladder candidates with `>>> LADDER (N legs)` output

## Notes
- New config params: `max_legs_per_event` (5), `max_exposure_per_event` ($15)
- `max_bet` now means per-leg cap ($5), not per-event
- min probability threshold lowered from 0.05 to 0.01 (cheap buckets have low p but huge payout)
- Ladder only opens when no open positions exist for the event (all-or-nothing at open time)
- EV calibration buckets in `cmd_edge` widened to [0-10%, 10-50%, 50-100%, 100%+] for the new range

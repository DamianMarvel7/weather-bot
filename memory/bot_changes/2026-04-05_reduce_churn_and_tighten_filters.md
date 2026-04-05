---
date: 2026-04-05
type: bot-change
tags: [bot-change]
---

# Bot Change — 2026-04-05

## Summary
> Raise min_price/min_ev, lower max_positions, widen stop, and fix forecast_change churn that was paying the spread twice per scan.

## What Changed
- **File(s):** `src/weatherbot/config.json`, `src/weatherbot/bot.py`
- **Component:** config / executor / entry-exit logic
- **Change type:** bug-fix + config-tweak

## Why
> Bot was consistently losing on nearly every trade. Root causes:
> 1. **Churn bug**: after a `forecast_change` close, `_maybe_open` fired in the same scan iteration, re-entering the same market and paying the bid-ask spread twice.
> 2. **Thin-price entries**: `min_price=0.05` allowed buying 5-8 cent buckets where the bid-ask spread (2-4 cents) is 40-60% of entry price. Any exit resulted in a large percentage loss regardless of EV.
> 3. **Too many positions**: `max_positions=10` encouraged over-trading on marginal opportunities.
> 4. **Stop-loss too tight at thin prices**: 0.35 threshold fired on normal market noise at 5-cent entries.

## Before / After
| | Before | After |
|---|---|---|
| `min_price` | 0.05 | 0.12 |
| `min_ev` | 0.25 | 0.50 |
| `max_positions` | 10 | 4 |
| `stop_loss_threshold` | 0.35 | 0.25 |
| forecast_change churn | re-opens same scan | waits until next scan |

## Impact on Live Trading
- [x] Affects position sizing
- [x] Affects entry/exit logic
- [x] Affects EV threshold
- [ ] Affects probability model
- [ ] No direct trading impact

## Test / Verification
> Config values verified in config.json. Churn fix: `bot.py` returns early after `forecast_change` close before calling `_maybe_open`.

## Notes
- `min_price=0.12` means we only trade buckets where the bid-ask spread is a smaller fraction of entry price.
- The churn fix adds one line: `if stop == "forecast_change": save_market(mkt); return` — the market will be re-evaluated cleanly next hourly scan.

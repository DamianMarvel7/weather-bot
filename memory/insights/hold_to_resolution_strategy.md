---
date: 2026-04-09
type: insight
tags: [insight, stop-loss, strategy]
---

# Insight: Hold to Resolution Is the Correct Strategy

## What We Learned

Stop-loss (and trailing-stop) is the wrong tool for weather prediction markets. Evidence from 162+ trades:

- **60 stop-loss exits had 0% win rate** — every single position would have recovered by resolution
- The only profitable days (2026-03-26/27, +$2,312) were when the PC was off and stop-loss never fired
- Weather market prices are highly noisy intraday; a position can drop 35-40% on an intra-day forecast shift and snap back as resolution approaches

## Why Stop-Loss Hurts

Unlike stocks, weather markets have a **hard resolution event** in 6-36 hours. The price converging to 0 or 1 at resolution is not risk — it's the designed outcome. Intraday price drops reflect market maker adjustments to forecast noise, not new information about what the actual temperature will be.

Stop-loss treats noise as signal. In weather markets, noise is the dominant short-term price driver.

## The Correct Exit Logic

Only exit early when there is **physical evidence** that contradicts the position:

1. **`metar_diverge`**: hours_left < 12 and live weather station observation is outside the bucket (with buffer). The METAR is the same source Polymarket uses for resolution — this is real signal.

2. **`forecast_change`**: the numerical weather model has moved more than 1.5σ away from the bucket. This means the probabilistic case for the trade has genuinely changed, not just intraday noise.

## Current Config

- No stop-loss
- No trailing-stop  
- `forecast_change` buffer: 1.5 * calibrated sigma (not fixed 3°F/2°C)
- `metar_diverge` buffer: 3°F / 1.5°C with hours_left < 12

## Watch For

If a future analysis shows many `forecast_change` exits are also recovering (like stop-losses did), widen the buffer to 2.0σ or 2.5σ.

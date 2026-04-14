---
date: 2026-04-09
type: bot-change
tags: [bot-change, model, probability, calibration]
---

# Bot Change — 2026-04-09

## Summary
> Complete overhaul of the probability model: fix sigma calculation, remove stop-loss entirely, add multi-model forecast ensemble, normalize bucket probabilities, and fix circular calibration.

## What Changed
- **File(s):** `src/weatherbot/portfolio.py`, `src/weatherbot/bot.py`, `src/weatherbot/forecast.py`, `src/weatherbot/config.json`, `src/weatherbot/config.py`
- **Component:** probability model / calibration / entry-exit logic / forecast
- **Change type:** model + bug-fix + config-tweak

## Why
> Root cause analysis of continued losses identified that the bot had no real edge — it was disagreeing with well-calibrated market prices because its own model was miscalibrated. Three cascading problems:
>
> 1. **Overconfident sigma**: `sigma = MAE * 1.8` is mathematically wrong. Correct conversion is MAE * 1.253 for normal distributions, and the 1.8x multiplier inflated sigma 44% beyond even that. This made cheap tail buckets look like bargains when markets correctly priced them as unlikely.
>
> 2. **Stop-loss destroys edge**: 60 stop-loss exits had 0% win rate — every one would have recovered by resolution. Only profitable days were when stop-loss never fired.
>
> 3. **Circular calibration**: When VC actual_temp was unavailable, calibration trained on Polymarket's own resolved bucket midpoints, creating a feedback loop where model errors were hidden.

## Before / After

### Probability Model
| | Before | After |
|---|---|---|
| Sigma formula | `MAE * 1.8` | `std` (stdev of errors) directly |
| Default sigma | 5°F * 1.8 = 9°F / 2.5°C * 1.8 = 4.5°C | 3.5°F / 2.0°C |
| `SIGMA_MULTIPLIER` | 1.8 | removed |
| Bucket normalization | each bucket computed independently (can sum >1) | normalized to sum=1 |

### Stop-Loss
| | Before | After |
|---|---|---|
| Stop-loss | exits at bid ≤ 40% of entry | removed |
| Trailing-stop | exits at peak +20% then fell to +5% | removed |
| Forecast-change buffer | fixed 3°F / 2°C | 1.5 * calibrated sigma |
| City cooldown | 48h after stop-loss | removed |

### Calibration
| | Before | After |
|---|---|---|
| Actual temp source | VC first, falls back to bucket midpoint | VC only (no fallback) |
| New field | — | `std` stored alongside `mae` and `bias` |

### Forecast
| | Before | After |
|---|---|---|
| Models used | 1 (HRRR or ECMWF) | 3-model ensemble (GFS Seamless + ECMWF + GFS Global / ICON) |
| Forecast output | single `best` value | ensemble average + `spread` (max-min) |
| High-disagreement filter | none | skip if spread > 4°F / 2°C |
| Spread-ratio filter | none | skip if (ask-bid)/ask > 0.20 |

### Config
| | Before | After |
|---|---|---|
| `kelly_fraction` | 0.25 | 0.10 |
| `min_price` | 0.12 | 0.15 |
| `max_hours` | 36.0 | 24.0 |
| `stop_loss_threshold` | 0.40 | removed |
| `sigma_multiplier` | 1.8 | removed |
| `max_forecast_spread_f` | — | 4.0 |
| `max_forecast_spread_c` | — | 2.0 |

## Impact on Live Trading
- [x] Affects probability model (sigma, normalization)
- [x] Affects entry/exit logic (stop-loss removed, spread filter, spread-ratio)
- [x] Affects position sizing (kelly reduced)
- [x] Affects calibration (VC-only ground truth, stores std)
- [x] Affects forecast (3-model ensemble, spread filter)

## Expected Outcome
- `cmd_scan_dry` should show far fewer PASS results — most old PASS trades were phantom edge from overconfident sigma
- `cmd_edge()` probability calibration table should show estimated P closer to actual win rate (within 10pp) after 50+ trades
- Win rate may drop in the short term (bot trades less) but PnL should improve because it only enters genuine edge

## Test / Verification
> All modules import cleanly. Run `weatherbet.py scan-dry` to verify fewer PASSes. Paper trade 50+ trades then run `weatherbet.py edge` to check probability calibration.

## Notes
- The `default_std` (3.5°F / 2.0°C) is a conservative starting point. If calibration table still shows P >> actual win rate after 50 trades, raise the default.
- Hold-to-resolution is now the core strategy. The only early exits are `metar_diverge` (physical observation near resolution) and `forecast_change` (model moved >1.5σ from bucket).
- Commit: `8e09e66`

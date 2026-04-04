---
date: 2026-04-04
type: bot-change
tags: [bot-change]
---

# Bot Change — 2026-04-04 (2)

## Summary
Expanded city blacklist from 6 to 17 cities based on edge report analysis.

## What Changed
- **File(s):** `src/weatherbot/config.json`
- **Component:** config-tweak
- **Change type:** config-tweak

## Why
Ran `uv run weatherbet.py edge` and identified 11 more cities with negative total PnL and ≤25% win rate across ≥3 trades.

## Before / After
| | Before | After |
|---|---|---|
| Blacklisted cities | 6 | 17 |
| Added | — | seattle, beijing, chicago, madrid, chengdu, milan, singapore, seoul, wellington, tel-aviv, buenos-aires |
| Active cities | ~30 | 18 |

Remaining active cities: ankara, atlanta, austin, chongqing, dallas, denver, houston, los-angeles, lucknow, miami, munich, nyc, san-francisco, shenzhen, taipei, toronto, warsaw, wuhan

## Impact on Live Trading
- [x] Affects entry/exit logic
- [ ] Affects position sizing
- [ ] Affects probability model
- [ ] Affects EV threshold
- [ ] No direct trading impact

## Test / Verification
- Verified: `uv run python3 -c "from src.weatherbot.config import CITY_BLACKLIST; print(len(CITY_BLACKLIST), sorted(CITY_BLACKLIST))"`
- Output: 17 cities blacklisted ✓

## Notes
- austin (2 trades) and wuhan (3 trades, 33% win, -$31) were kept due to small/borderline samples
- Revisit after 10+ more trades per city
- Cities kept despite marginal PnL: miami (+$0.53), san-francisco (-$3.88), munich (+$27.88)

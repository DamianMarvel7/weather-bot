---
date: 2026-04-05
type: bot-change
tags: [bot-change, config-tweak]
---

# Bot Change — 2026-04-05 (2)

## Summary
Reverted max_positions from 4 back to 10 to adopt a high-volume, jackpot-style strategy.

## What Changed
- **File(s):** `src/weatherbot/config.json`
- **Component:** config-tweak
- **Change type:** config-tweak

## Why
Strategy shift: accept that most trades will lose, but run enough volume so that one large win (jackpot) covers all cumulative losses. With max_positions=4, the bot trades too infrequently to hit those rare high-payout outcomes.

## Before / After
| | Before | After |
|---|---|---|
| `max_positions` | 4 | 10 |

## Impact on Live Trading
- [x] Affects position sizing
- [x] Affects entry/exit logic
- [ ] Affects probability model
- [ ] Affects EV threshold
- [ ] No direct trading impact

## Test / Verification
- Config value verified in config.json.

## Notes
- This strategy requires that per-trade EV remains genuinely positive — if filters let in negative-EV trades, more positions will amplify losses. Monitor edge report closely after next batch resolves.
- Previous reduction to 4 was motivated by over-trading concerns; this reverts that in favour of volume.

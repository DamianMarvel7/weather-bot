---
date: 2026-04-04
type: strategy
tags: [strategy, roadmap, improvement]
---

# Bot Improvement Roadmap

Generated after loss analysis on 2026-04-04.  
Context: 162 trades, 13.6% win rate, only profitable on 2026-03-26 and 2026-03-27.

---

## Phase 1 — Immediate Fixes (DONE 2026-04-04)

- [x] **Raise stop-loss threshold** — from 0.65 to 0.35 (exit only at -65% drop, not -35%)
  - Root cause: 60 stop-loss exits with 0% win rate; positions would have recovered at resolution
- [x] **Raise min EV threshold** — from 0.10 to 0.25
  - Root cause: weak-conviction bets (EV < 0.25) had <10% win rate
- [x] **City blacklist** — london, paris, hong-kong, shanghai, sao-paulo, tokyo
  - Root cause: all 6 cities had 0% win rate across all trades
- [x] **Restart bot** with new config

---

## Phase 2 — Monitor & Validate (this week)

- [ ] **Watch next 10–20 trades** — confirm stop-loss fires less, EV filter rejects weak trades
  - Use: `uv run pnl.py` and `uv run weatherbet.py report`
- [ ] **Dry-run scan** — verify blacklisted cities are skipped and EV threshold works
  - Use: `uv run weatherbet.py scan-dry`
- [ ] **Check edge report** — run `uv run weatherbet.py edge` weekly to track win rate trend

---

## Phase 3 — Seasonal Bucket Filter (next)

- [ ] **Build historical temperature distribution per city/month**
  - Source: already have `actual_temp` in 3,490 market files (backfill data)
  - Goal: know the realistic temperature range for each city in each month
- [ ] **Add bucket plausibility check before EV calc**
  - Skip any bucket whose midpoint is more than 2 sigma outside the historical monthly distribution
  - Prevents betting on temperatures that almost never happen (e.g. London 29°C in March)
- [ ] **Add `seasonal_filter` flag to config.json** so it can be toggled off for testing

---

## Phase 4 — EV Recalibration (next week)

- [ ] **Compare estimated P vs actual resolution rate per city**
  - Winners had 4.4x higher EV than losers despite identical entry prices → probability model is miscalibrated
  - Run: compare `p_est` (back-calculated from EV) vs actual win rate per city/bucket
- [ ] **Tune sigma per city** — current `sigma_multiplier: 1.8` may be too wide for some cities, too narrow for others
  - Look at cities where estimated P >> actual win rate (model is overconfident)
- [ ] **Tune bias_scale per city** — check if bias correction is actually reducing error or adding noise
- [ ] **Separate sigma per region** — US cities (°F) vs EU/Asia (°C) may need different defaults

---

## Phase 5 — Position Sizing (after Phase 4)

- [ ] **Review Kelly fraction** — currently 0.25 (quarter-Kelly); may need adjustment based on actual edge
- [ ] **Add per-city max bet** — limit exposure to any single city until edge is proven
- [ ] **Scale bet size with confidence** — higher EV = larger bet, instead of flat $20 cap for all

---

## Phase 6 — Backtest Framework (ongoing)

- [ ] **Replay historical trades with new parameters** — test Phase 1 changes against the 162 historical trades to estimate expected improvement
- [ ] **Simulate stop-loss threshold sweep** — test 0.35, 0.40, 0.50, 0.65 against historical data
- [ ] **Simulate EV threshold sweep** — test 0.10, 0.20, 0.25, 0.30 against historical data

---

## Notes

- Do NOT add more cities until win rate consistently > 25%
- Do NOT go live until paper trading shows positive edge for 30+ trades
- Check `uv run weatherbet.py edge` after every 20 new trades to track progress

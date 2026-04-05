---
date: 2026-04-04
type: insight
tags: [risk-management, stop-loss, edge]
---

# Insight: Stop-Loss Fires Kill Edge on Weather Markets

## Finding
The only two profitable days (2026-03-26/27, +$2,312) occurred when the PC was off and no stop-loss fired. All 60 stop-loss exits in the 162-trade sample had a 0% win rate — every single one of those positions would have recovered by resolution.

## Why It Happens
Weather market prices are noisy intraday. A position can drop 35% mid-life as the forecast shifts, then snap back as the resolution date approaches. Stop-losses treat this noise as signal.

## Implication
For weather markets with binary resolution, holding to resolution is the correct strategy when the original EV case is intact. Stop-losses should only fire at extreme drops (≥65%), not at routine intraday volatility.

## Action Taken
- 2026-04-04: `stop_loss_threshold` 0.65 → 0.35 (exit only at -65% drop)
- 2026-04-05: threshold widened further to 0.25

## Open Question
Should we replace stop-loss with a "re-evaluate EV" check instead? If EV at current price is still positive, hold regardless of mark-to-market loss.

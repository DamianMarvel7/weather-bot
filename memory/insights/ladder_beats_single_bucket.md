---
date: 2026-04-14
type: insight
tags: [insight, strategy, ladder, variance]
---

# Insight: Laddering Beats Single-Bucket Betting

## Finding
Research into profitable Polymarket weather traders (neobrother, HondaCivic, ColdMath, Handsanitizer23, BeefSlayer) revealed they all use **temperature laddering**: buying 3-5 cheap buckets (1-10c) across the distribution instead of one expensive bucket (15-45c).

Key profiles:
- neobrother: 3000+ daily-high markets, 5-figure cumulative profit, systematic ladder across global cities
- HondaCivic: $1k to $80k using aggressive sizing + laddering
- Handsanitizer23: ~$600 to $16k on a single temperature market

## Why It Works
1. **Variance smoothing**: 4 legs at $3 each vs 1 leg at $12. If one hits at 0.03 ask, payout is $100 on a $3 bet (33x). Three losers cost $9. Net: +$88.
2. **Win rate math**: old strategy needed ~50-60% win rate (at 3-6x payout). Ladder needs ~10-20% (at 20-100x payout). The model doesn't need to be very accurate — just better than the market's implied probability.
3. **Convexity**: cheap buckets have asymmetric payoff. Max loss = cost of leg. Max gain = 100-142x. The Kelly criterion naturally sizes these small.

## Why Single-Bucket Failed
Our bot at min_price=0.15 was buying buckets where the market was already fairly efficient. At 30c ask with our model saying 40% probability, EV = 0.33 — thin edge that gets eaten by the few percent of times our model is wrong.

## Implication
The bot's edge is not in being more accurate than the market at the peak of the distribution — it's in finding cheap tail buckets where the market slightly underprices probability. A 3% actual probability on a 1% priced bucket is a 200% EV trade.

## Action Taken
- Implemented ladder strategy: up to 5 legs per event, $15 max exposure per event
- min_price lowered to 0.01, max_bet to $5/leg
- Data model changed from single position to positions list

## Open Question
- What's the optimal number of legs? 3? 5? 7? Need data to calibrate.
- Should leg sizing be equal (simple) or EV-weighted (current Kelly approach)?
- Should we also sell overpriced buckets (the article mentions this)?

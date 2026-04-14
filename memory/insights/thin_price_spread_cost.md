---
date: 2026-04-14
type: insight
tags: [execution, spread-cost, entry-filter, ladder]
---

# Insight: Thin-Price Spread Cost — Reframed by Ladder Strategy

## Finding (Original — 2026-04-05)
With the old single-bucket strategy, entries at 5-8c had 40-60% spread cost, destroying any EV edge. This led to raising min_price to 0.12.

## Finding (Updated — 2026-04-14)
With **laddering**, thin prices are the whole point. When you buy at 1-5c and hold to resolution, spread cost is irrelevant because you never exit early — the position resolves to $1.00 or $0.00. The 142x payout on a 0.7c winner more than covers the 3-5 losers that go to zero.

## Why the Old Rule Was Wrong for Laddering
The old analysis assumed mid-market exit. Ladder legs are **hold-to-resolution** — you never sell back into the spread. The only "cost" is the leg expiring worthless, which is priced into the strategy (most legs lose, one winner covers all).

## Current Filters
- `min_price`: 0.01 (allow very cheap buckets)
- `max_slippage`: 0.03 (absolute spread cap still applies — protects against illiquid books)
- Spread-ratio filter: skip if `(ask - bid) / ask > 0.20` (still active, catches extreme cases)

## Rule of Thumb (Revised)
For hold-to-resolution strategies, **absolute spread** matters more than **spread ratio**. A 3c spread on a 5c bucket is fine if you're holding to resolution. A 3c spread on a 30c bucket matters more because you might exit early on forecast_change.

## Open Question
Track ladder leg outcomes: what's the actual win rate on sub-5c entries? If it's systematically below the model's probability estimate, the cheap buckets may be correctly priced by the market.

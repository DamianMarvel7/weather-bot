---
date: 2026-04-05
type: insight
tags: [execution, spread-cost, entry-filter]
---

# Insight: Thin-Price Entries Destroy Edge via Spread Cost

## Finding
With `min_price=0.05`, the bot was entering buckets priced at 5-8 cents. Bid-ask spread at these prices is typically 2-4 cents, meaning 40-60% of entry price is immediately lost to spread on any exit. No EV calculation can survive this cost structure.

## Why It Matters
EV is calculated on mid-price. But execution happens at the ask (entry) and bid (exit). At thin prices, this gap is proportionally enormous. A bucket at 6¢ mid with a 3¢ spread needs to move to ~9¢ just to break even on exit — before any resolution gain.

## Action Taken
- 2026-04-05: `min_price` raised from 0.05 → 0.12
- This means we only trade buckets where the spread is a smaller fraction of entry price

## Rule of Thumb
Avoid any bucket where estimated bid-ask spread > 15% of entry price. At `min_price=0.12` and typical 2-4¢ spreads, spread cost is ~17-33% — still high, so this threshold may need to go higher.

## Open Question
Can we fetch the actual order book depth before entering, and only enter when the spread is below a threshold? Would require CLOB API order book endpoint.

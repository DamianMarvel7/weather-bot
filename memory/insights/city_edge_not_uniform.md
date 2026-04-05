---
date: 2026-04-04
type: insight
tags: [city-selection, edge, geographic]
---

# Insight: Edge Varies Dramatically by City

## Finding
Out of ~30 active cities, 17 had negative PnL and ≤25% win rate after 162 trades. The 6 worst (london, paris, hong-kong, shanghai, sao-paulo, tokyo) had 0% win rate across all trades. Only 18 cities remain active after blacklisting.

## Why It Varies
Likely causes (not yet confirmed):
1. **Forecast model quality** — HRRR (US-centric) is more accurate for US cities than for EU/Asia, where ECMWF fallback is used.
2. **Market liquidity** — EU/Asia markets may have thinner books and worse prices.
3. **Temperature regime** — °C cities may need different sigma tuning than °F cities.

## Surviving Cities (as of 2026-04-04)
ankara, atlanta, austin, chongqing, dallas, denver, houston, los-angeles, lucknow, miami, munich, nyc, san-francisco, shenzhen, taipei, toronto, warsaw, wuhan

## Implication
Do not add new cities until win rate is consistently >25%. Revisit borderline cities (austin 2 trades, wuhan 3 trades) after 10+ more trades each.

## Open Question
Run separate sigma/bias calibration per city. US cities (HRRR data, °F) likely need different parameters than EU/Asia cities (ECMWF fallback, °C). See [[strategies/improvement_roadmap]] Phase 4.

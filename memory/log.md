# Bot Change Log

Append-only. One entry per session. Format: `## [YYYY-MM-DD] <type> | <title>`  
Types: `config` · `bug-fix` · `model` · `analysis` · `insight` · `trade`

---

## [2026-04-04] analysis | 162-trade loss analysis
Win rate 13.6%. Only profitable days were 2026-03-26/27 — PC was off, stop-loss never fired. Identified 3 root causes: stop-loss too tight, min_ev too low, 6 cities with 0% win rate. → See [[bot_changes/2026-04-04_risk_and_filter_improvements]]

## [2026-04-04] config | Raise min_ev 0.10→0.25, stop-loss 0.65→0.35, blacklist 6 cities
→ See [[bot_changes/2026-04-04_risk_and_filter_improvements]]

## [2026-04-04] config | Expand city blacklist from 6 to 17 cities
Edge report (`weatherbet.py edge`) found 11 more cities with negative PnL and ≤25% win rate. → See [[bot_changes/2026-04-04_expand_city_blacklist]]

## [2026-04-05] bug-fix + config | Fix forecast_change churn, raise min_price 0.05→0.12, min_ev 0.25→0.50, max_positions 10→4
Churn bug caused same market to be re-entered in same scan after forecast_change close (paid spread twice). Thin-price entries (5-8 cent buckets) had 40-60% spread cost. → See [[bot_changes/2026-04-05_reduce_churn_and_tighten_filters]]

## [2026-04-05] config | Revert max_positions 4→10, high-volume jackpot strategy
Accept most trades lose; run enough volume so one large win covers all cumulative losses. → See [[bot_changes/2026-04-05_revert_max_positions_to_10]]

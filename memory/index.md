# Polymarket Bot — Knowledge Base

## Templates
- [[templates/bot_change_log]] — use whenever the bot code or config changes
- [[templates/trade_log]] — use for every trade (entry + post-resolution)

## Bot Changes
- [[bot_changes/2026-04-04_risk_and_filter_improvements]] — raise min_ev 0.10→0.25, stop-loss 0.65→0.35, blacklist 6 losing cities
- [[bot_changes/2026-04-04_expand_city_blacklist]] — expand blacklist from 6 to 17 cities based on edge report
- [[bot_changes/2026-04-05_reduce_churn_and_tighten_filters]] — min_price 0.05→0.12, min_ev 0.25→0.50, max_positions 10→4, fix forecast_change churn
- [[bot_changes/2026-04-05_revert_max_positions_to_10]] — revert max_positions 4→10, high-volume jackpot strategy
- [[bot_changes/2026-04-09_probability_model_overhaul]] — fix sigma (MAE*1.8→std), remove stop-loss, add 3-model ensemble, normalize bucket probs, fix circular calibration
- [[bot_changes/2026-04-14_ladder_strategy]] — switch to ladder: buy up to 5 cheap buckets per event instead of 1 expensive one

## Log
- [[log]] — append-only chronological record of all sessions (config changes, analyses, insights)

## Trades
> Link each trade log here.

## Strategies
- [[strategies/improvement_roadmap]] — 6-phase improvement plan based on 2026-04-04 loss analysis

## Insights
- [[insights/stop_loss_kills_edge]] — stop-loss fires always lost; holding to resolution is correct for weather markets
- [[insights/city_edge_not_uniform]] — 17/30 cities blacklisted; HRRR quality and spread liquidity likely cause variation
- [[insights/thin_price_spread_cost]] — entries below 0.12 have 40-60% spread cost; EV calc meaningless at thin prices
- [[insights/probability_model_was_overconfident]] — sigma = MAE*1.8 inflated tails 44%; use std directly; normalize to sum=1
- [[insights/hold_to_resolution_strategy]] — no stop-loss; only exit on metar_diverge or forecast_change >1.5σ
- [[insights/ladder_beats_single_bucket]] — profitable PM traders ladder 3-5 cheap buckets; 10-20% win rate at 20-100x beats 50-60% at 3-6x
